#!/usr/bin/env python3
"""Deterministic portfolio check for the user's actual holdings (NDQ.AX + IOO.AX).

WHY THIS EXISTS
---------------
The weekly routine was dispatching 3 Opus agents (~$1.50, ~20 min) even when
nothing changed. Most of that work is arithmetic an LLM should never do:
P&L, allocation drift, single-name look-through, trigger-line distances, and
the Monte Carlo distribution. This script does ALL of the math deterministically
(zero hallucination risk), then emits a verdict:

  - "HOLD — no trigger fired"   -> no agent dispatch needed (save cost)
  - "TRIGGER: <name>"           -> dispatch advisor/PM agents for a re-decision

The LLM layer interprets *only when something actually happened*.

WHAT IT DOES NOT DO
-------------------
It does not predict direction. The embedded Monte Carlo quantifies the
short-horizon distribution (typically ~50/50 — that is the honest answer).
Accuracy gains here are in MEASUREMENT (deterministic math, pre-committed
trigger lines), not in prophecy. No code can buy forecast alpha.

INPUTS
------
- data/positions.md  "## Open Positions" lines:  TICKER | units | avg_cost | ...
- live prices passed as CLI flags (the orchestrator pulls them via MCP; this
  script stays dependency-free and offline-deterministic)

USAGE
-----
    python scripts/portfolio_check.py --ndq 62.45 --ioo 196.87 --vix 21.51
    python scripts/portfolio_check.py --ndq 62.45 --ioo 196.87 --json
    python scripts/portfolio_check.py --self-test     # synthetic holdings only

TESTABILITY
-----------
``run_check`` takes an injectable ``holdings_source`` and ``run_cli`` takes an
injectable state path, so the money arithmetic and the exit-code contract can be
exercised against SYNTHETIC holdings. The self-test never reads
``data/positions.md`` and never writes the real state file.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TextIO

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import force_utf8_stdio  # noqa: E402
from montecarlo import simulate  # noqa: E402
from trigger_state import (  # noqa: E402
    DEFAULT_TTL_HOURS,
    STATE_PATH,
    clear_state,
    filter_new_triggers,
    load_state,
    record_fires,
    save_state,
)

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
POSITIONS = ROOT / "data" / "positions.md"

# ---- Targets & risk parameters (CLI-overridable) --------------------------
TARGET = {"NDQ.AX": 0.60, "IOO.AX": 0.30, "CASH": 0.10}
BUDGET_DEFAULT = 8000.0

# NVDA index weights drift over time — override via flags when they move.
NVDA_WEIGHT_DEFAULT = {"NDQ.AX": 0.09, "IOO.AX": 0.139}
SINGLE_NAME_GATE = 0.05  # 5% look-through cap per the PM pre-trade risk gate

# Pre-committed trigger lines (from the latest PM decision; override weekly
# as moving averages drift).
TRIGGERS_DEFAULT = {
    "ndq_stop": 60.00,      # NDQ daily close below -> trend damage signal
    "ndq_50d": 56.61,       # NDQ 50d SMA -> mid-term support
    "ioo_50d": 186.00,      # IOO 50d SMA -> support
    "vix_max": 25.0,        # sustained above -> systemic risk, pause adds
}

MC_VOL = {"NDQ.AX": 0.28, "IOO.AX": 0.16}  # annualized, VIX-era defaults
MC_DAYS = 10  # ~2 trading weeks

# ATR-adaptive trigger widening. A pre-committed line sitting closer to price
# than this many ATRs fires on ordinary daily noise — exactly the waffle the
# pre-commit cards exist to prevent. ATR therefore only ever LOOSENS a line
# (moves it further below price); it never tightens one. Opt-in: with no
# --*-atr-pct flag the effective lines are byte-identical to the fixed ones.
ATR_MULTIPLE = 1.5


@dataclass(frozen=True)
class Holding:
    ticker: str
    units: float
    avg_cost: float

    def cost(self) -> float:
        return self.units * self.avg_cost

    def value(self, price: float) -> float:
        return self.units * price

    def pnl(self, price: float) -> float:
        return self.value(price) - self.cost()


def parse_positions(path: Path = POSITIONS) -> list[Holding]:
    """Parse '## Open Positions' lines: TICKER | units | avg_cost | ..."""
    if not path.exists():
        raise FileNotFoundError(f"positions file not found: {path}")
    holdings: list[Holding] = []
    in_section = False
    line_re = re.compile(r"^([A-Z0-9.\-=^]+)\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)")
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_section = stripped.lower().startswith("## open positions")
            continue
        if not in_section or not stripped or stripped.startswith("#"):
            continue
        m = line_re.match(stripped)
        if m:
            holdings.append(
                Holding(m.group(1), float(m.group(2)), float(m.group(3)))
            )
    return holdings


def widen_for_atr(
    line: float,
    price: float | None,
    atr_pct: float | None,
    multiple: float = ATR_MULTIPLE,
) -> float:
    """Return the effective trigger line after ATR widening.

    ``atr_pct`` is the daily ATR expressed as a percentage of price (e.g. 1.8
    for 1.8%). The line is pushed down to ``price - multiple * ATR`` whenever
    the fixed line would otherwise sit inside that noise band. Passing None
    (the default) returns ``line`` unchanged, so behaviour is opt-in.
    """
    if price is None or atr_pct is None or atr_pct <= 0.0:
        return line
    noise_floor = price * (1.0 - multiple * atr_pct / 100.0)
    return min(line, noise_floor)


def effective_triggers(
    triggers: dict[str, float],
    prices: dict[str, float],
    atr_pct: dict[str, float] | None,
) -> dict[str, float]:
    """Apply ATR widening to the price lines. VIX is a level, not a distance."""
    atr_pct = atr_pct or {}
    ndq_price, ioo_price = prices.get("NDQ.AX"), prices.get("IOO.AX")
    ndq_atr, ioo_atr = atr_pct.get("NDQ.AX"), atr_pct.get("IOO.AX")
    return {
        "ndq_stop": widen_for_atr(triggers["ndq_stop"], ndq_price, ndq_atr),
        "ndq_50d": widen_for_atr(triggers["ndq_50d"], ndq_price, ndq_atr),
        "ioo_50d": widen_for_atr(triggers["ioo_50d"], ioo_price, ioo_atr),
        "vix_max": triggers["vix_max"],
    }


def _position_rows(
    holdings: Sequence[Holding], prices: dict[str, float]
) -> list[dict]:
    """Per-holding P&L rows. Raises ValueError when a live price is missing."""
    rows: list[dict] = []
    for h in holdings:
        if h.ticker not in prices:
            raise ValueError(f"no live price supplied for {h.ticker} (use --ndq/--ioo)")
        p = prices[h.ticker]
        cost = h.cost()
        rows.append({
            "ticker": h.ticker,
            "units": h.units,
            "avg_cost": h.avg_cost,
            "price": p,
            "cost": round(cost, 2),
            "value": round(h.value(p), 2),
            "pnl": round(h.pnl(p), 2),
            # A zero-cost row (gifted units, bad data) must not crash the report.
            "pnl_pct": round(h.pnl(p) / cost * 100, 2) if cost else 0.0,
        })
    return rows


def _allocation(
    rows: Sequence[dict], budget: float
) -> tuple[dict[str, float], dict[str, float], float]:
    """Return (allocation fractions, drift vs TARGET in pp, cash).

    MAINTAINER DECISION PENDING (finding F-05): the denominator is COST BASIS
    (``row["cost"] / budget``), not market value, so a price move can never
    register as allocation drift — only a buy or a sell can. Switching to
    ``row["value"] / (total_value + cash)`` would make drift respond to prices,
    but it changes every number in the maintainer's existing reports, so the
    choice is deliberately left to the maintainer. Do not "fix" this in passing.
    """
    if budget <= 0:
        raise ValueError("budget must be positive")
    total_cost = sum(r["cost"] for r in rows)
    cash = budget - total_cost
    alloc = {r["ticker"]: r["cost"] / budget for r in rows}
    alloc["CASH"] = cash / budget
    drift = {k: round((alloc.get(k, 0.0) - t) * 100, 1) for k, t in TARGET.items()}
    return alloc, drift, cash


def _nvda_look_through(
    rows: Sequence[dict], nvda_weights: dict[str, float], total_value: float
) -> float:
    """NVDA exposure held indirectly through the ETFs, as a fraction of book."""
    exposure = sum(r["value"] * nvda_weights.get(r["ticker"], 0.0) for r in rows)
    return exposure / total_value if total_value else 0.0


def _evaluate_triggers(
    prices: dict[str, float],
    vix: float | None,
    eff: dict[str, float],
    nvda_pct_book: float,
) -> list[str]:
    """Pre-committed trigger lines, evaluated against effective (ATR-widened) levels."""
    fired: list[str] = []
    ndq_price = prices.get("NDQ.AX")
    ioo_price = prices.get("IOO.AX")
    if ndq_price is not None:
        if ndq_price < eff["ndq_50d"]:
            fired.append(f"NDQ below 50d SMA {eff['ndq_50d']:.2f} — mid-term support lost")
        elif ndq_price < eff["ndq_stop"]:
            fired.append(f"NDQ below stop {eff['ndq_stop']:.2f} — trend damage")
    if ioo_price is not None and ioo_price < eff["ioo_50d"]:
        fired.append(f"IOO below 50d SMA {eff['ioo_50d']:.2f} — support lost")
    if vix is not None and vix > eff["vix_max"]:
        fired.append(f"VIX {vix:.1f} > {eff['vix_max']:.0f} — systemic risk regime")
    if nvda_pct_book > SINGLE_NAME_GATE:
        fired.append(
            f"NVDA look-through {nvda_pct_book:.1%} > {SINGLE_NAME_GATE:.0%} gate "
            f"(standing breach — diversify new money, do not add tech)"
        )
    return fired


def _monte_carlo(
    prices: dict[str, float], holdings: Sequence[Holding], paths: int = 50_000
) -> dict[str, dict]:
    """Deterministic 2-week terminal distribution per priced ticker."""
    mc: dict[str, dict] = {}
    for t, p in prices.items():
        avg_cost = next((h.avg_cost for h in holdings if h.ticker == t), None)
        r = simulate(
            price0=p,
            annual_vol=MC_VOL.get(t, 0.20),
            horizon_days=MC_DAYS,
            annual_drift=0.0,
            cost_basis=avg_cost,
            paths=paths,
        )
        mc[t] = {
            "p_up": round(r.p_up, 3),
            "p_below_cost": round(r.p_below_cost, 3) if r.p_below_cost is not None else None,
            "pct5": round(r.pct[5], 2),
            "median": round(r.pct[50], 2),
            "pct95": round(r.pct[95], 2),
        }
    return mc


def run_check(
    prices: dict[str, float],
    vix: float | None,
    budget: float,
    nvda_weights: dict[str, float],
    triggers: dict[str, float],
    atr_pct: dict[str, float] | None = None,
    holdings_source: Callable[[], Sequence[Holding]] = parse_positions,
    mc_paths: int = 50_000,
) -> dict:
    """Run every deterministic check. ``holdings_source`` is injectable for tests."""
    holdings = list(holdings_source())
    if not holdings:
        raise ValueError("no holdings parsed from positions.md")

    rows = _position_rows(holdings, prices)
    total_cost = sum(r["cost"] for r in rows)
    total_value = sum(r["value"] for r in rows)
    alloc, drift, cash = _allocation(rows, budget)
    nvda_pct_book = _nvda_look_through(rows, nvda_weights, total_value)

    # ATR widening is applied first so a stale-tight line cannot fire on noise.
    eff = effective_triggers(triggers, prices, atr_pct)
    fired = _evaluate_triggers(prices, vix, eff, nvda_pct_book)
    mc = _monte_carlo(prices, holdings, mc_paths)

    # Verdict: action only on a fired PRICE/VIX trigger. The NVDA breach is a
    # standing constraint (shapes where NEW money goes), not a sell signal.
    price_triggers = [f for f in fired if not f.startswith("NVDA")]
    verdict = "TRIGGER — re-run agents for a decision" if price_triggers else \
              "HOLD — no price/VIX trigger fired; no agent dispatch needed"

    return {
        "holdings": rows,
        "totals": {
            "cost": round(total_cost, 2),
            "value": round(total_value, 2),
            "pnl": round(total_value - total_cost, 2),
            "pnl_pct": (
                round((total_value - total_cost) / total_cost * 100, 2)
                if total_cost else 0.0
            ),
            "cash": round(cash, 2),
            "budget": budget,
        },
        "allocation_pct": {k: round(v * 100, 1) for k, v in alloc.items()},
        "drift_vs_target_pp": drift,
        "nvda_look_through_pct": round(nvda_pct_book * 100, 1),
        "triggers_fired": fired,
        "price_triggers": price_triggers,
        "effective_trigger_lines": {k: round(v, 2) for k, v in eff.items()},
        "monte_carlo_2wk": mc,
        "verdict": verdict,
    }


def apply_trigger_state(
    result: dict,
    now: datetime,
    ttl_hours: float,
    state_path: Path = STATE_PATH,
) -> dict:
    """Suppress price triggers that already dispatched within ``ttl_hours``.

    This is the money-saving layer: without it, running the check twice in one
    day re-dispatches the same paid Opus agents for the same unchanged breach.
    Suppressed triggers are still reported — they just do not flip the exit
    code. ``now`` is injected by the caller; only ``main`` reads the clock.
    """
    state = load_state(state_path)
    to_dispatch, suppressed = filter_new_triggers(
        result["price_triggers"], state, now, ttl_hours
    )
    if to_dispatch:
        save_state(record_fires(state, to_dispatch, now), state_path)

    result["dispatch"] = [d.message for d in to_dispatch]
    result["suppressed"] = [
        {
            "message": d.message,
            "trigger_id": d.trigger_id,
            "hours_since_last_fire": (
                round(d.hours_since_last_fire, 1)
                if d.hours_since_last_fire is not None
                else None
            ),
            "fire_count": d.fire_count,
        }
        for d in suppressed
    ]
    if to_dispatch:
        result["verdict"] = "TRIGGER — re-run agents for a decision"
    elif suppressed:
        result["verdict"] = (
            f"HOLD — {len(suppressed)} trigger(s) already dispatched within "
            f"{ttl_hours:.0f}h; no new agent dispatch needed"
        )
    else:
        result["verdict"] = "HOLD — no price/VIX trigger fired; no agent dispatch needed"
    return result


def format_report(r: dict, vix: float | None) -> str:
    lines = ["=== Portfolio Check (deterministic — zero LLM) ===", ""]
    for h in r["holdings"]:
        lines.append(
            f"{h['ticker']:8} {h['units']:>6.4g}u @ {h['avg_cost']:<9.4f} "
            f"now {h['price']:<9.2f} value {h['value']:>9,.2f}  "
            f"P&L {h['pnl']:+8,.2f} ({h['pnl_pct']:+.2f}%)"
        )
    t = r["totals"]
    lines += [
        "",
        f"Total: cost {t['cost']:,.2f} -> value {t['value']:,.2f}  "
        f"P&L {t['pnl']:+,.2f} ({t['pnl_pct']:+.2f}%)   cash {t['cash']:,.2f}",
        f"Allocation: " + "  ".join(
            f"{k} {v:.1f}% (drift {r['drift_vs_target_pp'].get(k, 0):+.1f}pp)"
            for k, v in r["allocation_pct"].items()
        ),
        f"NVDA look-through: {r['nvda_look_through_pct']}% of book "
        f"(gate {SINGLE_NAME_GATE:.0%})",
        f"VIX: {vix if vix is not None else 'n/a'}",
        "",
        "Monte Carlo (2wk, deterministic seed):",
    ]
    for tkr, m in r["monte_carlo_2wk"].items():
        below = f", P(below cost) {m['p_below_cost']:.0%}" if m["p_below_cost"] is not None else ""
        lines.append(
            f"  {tkr}: P(up) {m['p_up']:.0%}{below}  "
            f"[5th {m['pct5']} | med {m['median']} | 95th {m['pct95']}]"
        )
    lines += ["", "Triggers:"]
    if r["triggers_fired"]:
        lines += [f"  !! {f}" for f in r["triggers_fired"]]
    else:
        lines.append("  (none fired)")
    for item in r.get("suppressed", []):
        age = item["hours_since_last_fire"]
        age_text = f"{age:.1f}h ago" if age is not None else "recently"
        lines.append(
            f"  -- suppressed ({item['trigger_id']}): already dispatched "
            f"{age_text}, fired {item['fire_count']}x"
        )
    lines += ["", f"VERDICT: {r['verdict']}"]
    return "\n".join(lines)


def _build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Deterministic NDQ/IOO portfolio check")
    # --ndq/--ioo are not argparse-required so `--self-test` can run alone;
    # run_cli validates them and returns exit code 2 when they are missing.
    ap.add_argument("--ndq", type=float, default=None, help="NDQ.AX live price")
    ap.add_argument("--ioo", type=float, default=None, help="IOO.AX live price")
    ap.add_argument("--vix", type=float, default=None, help="current VIX (optional)")
    ap.add_argument("--budget", type=float, default=BUDGET_DEFAULT)
    ap.add_argument("--ndq-nvda-weight", type=float, default=NVDA_WEIGHT_DEFAULT["NDQ.AX"])
    ap.add_argument("--ioo-nvda-weight", type=float, default=NVDA_WEIGHT_DEFAULT["IOO.AX"])
    ap.add_argument("--ndq-stop", type=float, default=TRIGGERS_DEFAULT["ndq_stop"])
    ap.add_argument("--ndq-50d", type=float, default=TRIGGERS_DEFAULT["ndq_50d"])
    ap.add_argument("--ioo-50d", type=float, default=TRIGGERS_DEFAULT["ioo_50d"])
    ap.add_argument("--vix-max", type=float, default=TRIGGERS_DEFAULT["vix_max"])
    ap.add_argument("--ndq-atr-pct", type=float, default=None,
                    help="NDQ daily ATR as %% of price; widens its trigger lines")
    ap.add_argument("--ioo-atr-pct", type=float, default=None,
                    help="IOO daily ATR as %% of price; widens its trigger line")
    ap.add_argument("--state-ttl-hours", type=float, default=DEFAULT_TTL_HOURS,
                    help="suppress a repeat dispatch of the same trigger within N hours")
    ap.add_argument("--no-state", action="store_true",
                    help="bypass dedup entirely (testing/CI)")
    ap.add_argument("--reset-state", action="store_true",
                    help="clear stored trigger history, then run")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--self-test", action="store_true",
                    help="run built-in unit tests (synthetic holdings, temp state)")
    return ap


def run_cli(
    args: argparse.Namespace,
    now: datetime,
    holdings_source: Callable[[], Sequence[Holding]] = parse_positions,
    state_path: Path = STATE_PATH,
    stream: TextIO | None = None,
    mc_paths: int = 50_000,
) -> int:
    """Execute one check and return the process exit code.

    Exit-code contract (load-bearing — ``.claude/commands/portfolio.md`` depends
    on it): 0 = nothing to dispatch, 1 = dispatch agents, 2 = input error.
    A trigger suppressed by the TTL exits 0 — that is the cost saved.

    ``now``, ``holdings_source`` and ``state_path`` are injected so the whole
    contract is testable without the maintainer's real positions or state file.
    """
    out = stream if stream is not None else sys.stdout
    if args.ndq is None or args.ioo is None:
        print("error: --ndq and --ioo are required", file=sys.stderr)
        return 2

    if args.reset_state:
        clear_state(state_path)

    try:
        result = run_check(
            prices={"NDQ.AX": args.ndq, "IOO.AX": args.ioo},
            vix=args.vix,
            budget=args.budget,
            nvda_weights={"NDQ.AX": args.ndq_nvda_weight, "IOO.AX": args.ioo_nvda_weight},
            triggers={
                "ndq_stop": args.ndq_stop,
                "ndq_50d": args.ndq_50d,
                "ioo_50d": args.ioo_50d,
                "vix_max": args.vix_max,
            },
            atr_pct={"NDQ.AX": args.ndq_atr_pct, "IOO.AX": args.ioo_atr_pct},
            holdings_source=holdings_source,
            mc_paths=mc_paths,
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.no_state:
        result["dispatch"] = result["price_triggers"]
        result["suppressed"] = []
    else:
        result = apply_trigger_state(result, now, args.state_ttl_hours, state_path)

    if args.json:
        json.dump(result, out, indent=2, ensure_ascii=False)
        print(file=out)
    else:
        print(format_report(result, args.vix), file=out)
    return 1 if result["dispatch"] else 0


# --------------------------------------------------------------------------
# Built-in unit tests. Synthetic holdings + a temp state path — this NEVER
# reads data/positions.md and NEVER writes data/portfolio_state.json.
# Run: python scripts/portfolio_check.py --self-test
# --------------------------------------------------------------------------
_FAKE_HOLDINGS = (
    Holding("NDQ.AX", 100.0, 50.00),   # cost 5000
    Holding("IOO.AX", 10.0, 150.00),   # cost 1500
)
_FAKE_BUDGET = 10_000.0                # -> cash 3500, alloc 50/15/35
_CALM = {"NDQ.AX": 60.00, "IOO.AX": 200.00}    # both well above every line
_CRASH = {"NDQ.AX": 50.00, "IOO.AX": 200.00}   # NDQ below the 50d line


def _fake_args(
    parser: argparse.ArgumentParser, prices: dict[str, float], **overrides: object
) -> argparse.Namespace:
    argv = ["--ndq", str(prices["NDQ.AX"]), "--ioo", str(prices["IOO.AX"])]
    for key, value in overrides.items():
        flag = "--" + key.replace("_", "-")
        argv += [flag] if value is True else [flag, str(value)]
    args = parser.parse_args(argv)
    args.budget = _FAKE_BUDGET
    return args


def _self_test() -> int:  # noqa: C901 - flat list of assertions, no nesting
    import contextlib
    import io
    import tempfile

    src: Callable[[], Sequence[Holding]] = lambda: _FAKE_HOLDINGS
    weights = {"NDQ.AX": 0.09, "IOO.AX": 0.139}
    base_triggers = dict(TRIGGERS_DEFAULT)
    parser = _build_parser()
    t0 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)

    calm = run_check(_CALM, None, _FAKE_BUDGET, weights, base_triggers,
                     holdings_source=src, mc_paths=2000)
    crash = run_check(_CRASH, None, _FAKE_BUDGET, weights, base_triggers,
                      holdings_source=src, mc_paths=2000)
    ndq_row = calm["holdings"][0]
    alloc = calm["allocation_pct"]

    cases: list[tuple[str, bool]] = [
        # --- money arithmetic --------------------------------------------
        ("P&L: 100u NDQ 50 -> 60 is +1000", ndq_row["pnl"] == 1000.0),
        ("P&L %: +20.0", ndq_row["pnl_pct"] == 20.0),
        ("cost basis: 100 * 50", ndq_row["cost"] == 5000.0),
        ("market value: 100 * 60", ndq_row["value"] == 6000.0),
        ("totals: cost 6500", calm["totals"]["cost"] == 6500.0),
        ("totals: value 8000", calm["totals"]["value"] == 8000.0),
        ("totals: pnl 1500", calm["totals"]["pnl"] == 1500.0),
        ("totals: cash = budget - cost", calm["totals"]["cash"] == 3500.0),
        # --- allocation ---------------------------------------------------
        ("allocation: NDQ 50%", alloc["NDQ.AX"] == 50.0),
        ("allocation: IOO 15%", alloc["IOO.AX"] == 15.0),
        ("allocation: CASH 35%", alloc["CASH"] == 35.0),
        ("allocation sums to 100%", abs(sum(alloc.values()) - 100.0) < 1e-9),
        ("drift vs target is signed pp", calm["drift_vs_target_pp"]["NDQ.AX"] == -10.0),
        ("budget <= 0 is rejected", _raises(ValueError, _allocation, calm["holdings"], 0.0)),
        ("missing price is rejected",
            _raises(ValueError, _position_rows, _FAKE_HOLDINGS, {"NDQ.AX": 60.0})),
        ("empty holdings rejected",
            _raises(ValueError, run_check, _CALM, None, _FAKE_BUDGET, weights,
                    base_triggers, None, list)),
        # --- look-through --------------------------------------------------
        ("NVDA look-through = weighted ETF exposure",
            calm["nvda_look_through_pct"] == round(
                (6000 * 0.09 + 2000 * 0.139) / 8000 * 100, 1)),
        # --- triggers -------------------------------------------------------
        ("calm prices fire nothing", calm["price_triggers"] == []),
        ("calm verdict is HOLD", calm["verdict"].startswith("HOLD")),
        ("NDQ under the 50d line fires", len(crash["price_triggers"]) == 1),
        ("fired message names the 50d line", "50d SMA" in crash["price_triggers"][0]),
        ("crash verdict is TRIGGER", crash["verdict"].startswith("TRIGGER")),
        ("VIX above the cap fires",
            any("VIX" in f for f in run_check(_CALM, 30.0, _FAKE_BUDGET, weights,
                base_triggers, holdings_source=src, mc_paths=500)["price_triggers"])),
        ("NVDA breach is NOT a price trigger",
            run_check(_CALM, None, _FAKE_BUDGET, {"NDQ.AX": 0.9, "IOO.AX": 0.9},
                      base_triggers, holdings_source=src, mc_paths=500)["price_triggers"] == []),
        # --- ATR widening only ever loosens ---------------------------------
        ("ATR widening loosens a too-tight line",
            widen_for_atr(59.0, 60.0, 3.0) < 59.0),
        ("ATR never tightens a far line", widen_for_atr(40.0, 60.0, 3.0) == 40.0),
        ("no ATR flag is a byte-identical no-op", widen_for_atr(56.61, 60.0, None) == 56.61),
        ("zero/negative ATR is a no-op", widen_for_atr(56.61, 60.0, 0.0) == 56.61),
        ("ATR widening can silence a noise trigger",
            run_check(_CRASH, None, _FAKE_BUDGET, weights, base_triggers,
                      atr_pct={"NDQ.AX": 12.0, "IOO.AX": None},
                      holdings_source=src, mc_paths=500)["price_triggers"] == []),
        ("VIX line is a level, ATR must not move it",
            effective_triggers(base_triggers, _CRASH, {"NDQ.AX": 12.0})["vix_max"]
            == base_triggers["vix_max"]),
        # --- determinism ----------------------------------------------------
        ("same inputs -> same Monte Carlo block",
            run_check(_CALM, None, _FAKE_BUDGET, weights, base_triggers,
                      holdings_source=src, mc_paths=2000)["monte_carlo_2wk"]
            == calm["monte_carlo_2wk"]),
    ]

    # --- exit-code contract, against a temp state path ----------------------
    # stderr is captured too: the exit-2 cases legitimately print errors, and
    # letting them through makes a passing self-test look like a failing one.
    sink = io.StringIO()
    with tempfile.TemporaryDirectory() as tmpdir, contextlib.redirect_stderr(sink):
        state = Path(tmpdir) / "portfolio_state.json"

        def run(prices: dict[str, float], now: datetime, **kw: object) -> int:
            return run_cli(_fake_args(parser, prices, **kw), now, src, state,
                           stream=sink, mc_paths=500)

        first_fire = run(_CRASH, t0)
        repeat = run(_CRASH, t0.replace(hour=14))          # +4h, inside TTL
        after_ttl = run(_CRASH, datetime(2026, 3, 3, 12, 0, tzinfo=timezone.utc))
        no_state_run = run(_CRASH, t0.replace(hour=15), no_state=True)
        missing_price = run_cli(parser.parse_args(["--ioo", "200"]), t0, src, state,
                                stream=sink, mc_paths=500)
        bad_budget_args = _fake_args(parser, _CALM)
        bad_budget_args.budget = 0.0
        bad_budget = run_cli(bad_budget_args, t0, src, state, stream=sink, mc_paths=500)
        missing_file = run_cli(
            _fake_args(parser, _CALM), t0,
            lambda: parse_positions(Path(tmpdir) / "nope" / "positions.md"),
            state, stream=sink, mc_paths=500,
        )

        cases += [
            ("exit 0: calm run", run(_CALM, t0.replace(hour=16)) == 0),
            ("exit 1: first trigger dispatches", first_fire == 1),
            ("exit 0: same trigger suppressed inside TTL", repeat == 0),
            ("exit 1: same trigger re-fires after TTL", after_ttl == 1),
            ("exit 1: --no-state bypasses dedup", no_state_run == 1),
            ("exit 2: missing --ndq", missing_price == 2),
            ("exit 2: bad budget", bad_budget == 2),
            ("exit 2: unreadable positions file", missing_file == 2),
            ("state file written to the injected path, not the real one", state.exists()),
            ("real state path untouched by the self-test", state != STATE_PATH),
        ]

    passed = 0
    for label, ok in cases:
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {label}")
    print(f"\n{passed}/{len(cases)} portfolio_check unit tests passed.")
    return 0 if passed == len(cases) else 1


def _raises(exc_type: type[BaseException], fn: Callable, *args: object) -> bool:
    """True iff ``fn(*args)`` raises ``exc_type``."""
    try:
        fn(*args)
    except exc_type:
        return True
    except Exception:
        return False
    return False


def main() -> int:
    args = _build_parser().parse_args()
    if args.self_test:
        return _self_test()
    # The clock is read HERE and injected — the filter itself stays pure.
    return run_cli(args, datetime.now(timezone.utc))


if __name__ == "__main__":
    sys.exit(main())

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
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import force_utf8_stdio  # noqa: E402
from montecarlo import simulate  # noqa: E402

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


def run_check(
    prices: dict[str, float],
    vix: float | None,
    budget: float,
    nvda_weights: dict[str, float],
    triggers: dict[str, float],
) -> dict:
    holdings = parse_positions()
    if not holdings:
        raise ValueError("no holdings parsed from positions.md")

    rows = []
    total_cost = 0.0
    total_value = 0.0
    for h in holdings:
        if h.ticker not in prices:
            raise ValueError(f"no live price supplied for {h.ticker} (use --ndq/--ioo)")
        p = prices[h.ticker]
        rows.append({
            "ticker": h.ticker,
            "units": h.units,
            "avg_cost": h.avg_cost,
            "price": p,
            "cost": round(h.cost(), 2),
            "value": round(h.value(p), 2),
            "pnl": round(h.pnl(p), 2),
            "pnl_pct": round(h.pnl(p) / h.cost() * 100, 2),
        })
        total_cost += h.cost()
        total_value += h.value(p)

    cash = budget - total_cost
    alloc = {r["ticker"]: r["cost"] / budget for r in rows}
    alloc["CASH"] = cash / budget
    drift = {k: round((alloc.get(k, 0.0) - t) * 100, 1) for k, t in TARGET.items()}

    # NVDA look-through (the binding concentration constraint)
    nvda_exposure = sum(
        r["value"] * nvda_weights.get(r["ticker"], 0.0) for r in rows
    )
    nvda_pct_book = nvda_exposure / total_value if total_value else 0.0

    # Trigger-line evaluation (pre-committed, anti-emotion)
    fired: list[str] = []
    ndq_price = prices.get("NDQ.AX")
    ioo_price = prices.get("IOO.AX")
    if ndq_price is not None:
        if ndq_price < triggers["ndq_50d"]:
            fired.append(f"NDQ below 50d SMA {triggers['ndq_50d']:.2f} — mid-term support lost")
        elif ndq_price < triggers["ndq_stop"]:
            fired.append(f"NDQ below stop {triggers['ndq_stop']:.2f} — trend damage")
    if ioo_price is not None and ioo_price < triggers["ioo_50d"]:
        fired.append(f"IOO below 50d SMA {triggers['ioo_50d']:.2f} — support lost")
    if vix is not None and vix > triggers["vix_max"]:
        fired.append(f"VIX {vix:.1f} > {triggers['vix_max']:.0f} — systemic risk regime")
    if nvda_pct_book > SINGLE_NAME_GATE:
        fired.append(
            f"NVDA look-through {nvda_pct_book:.1%} > {SINGLE_NAME_GATE:.0%} gate "
            f"(standing breach — diversify new money, do not add tech)"
        )

    # Monte Carlo distributions (deterministic seed — same inputs, same output)
    mc = {}
    for t, p in prices.items():
        avg_cost = next((h.avg_cost for h in holdings if h.ticker == t), None)
        r = simulate(
            price0=p,
            annual_vol=MC_VOL.get(t, 0.20),
            horizon_days=MC_DAYS,
            annual_drift=0.0,
            cost_basis=avg_cost,
            paths=50_000,
        )
        mc[t] = {
            "p_up": round(r.p_up, 3),
            "p_below_cost": round(r.p_below_cost, 3) if r.p_below_cost is not None else None,
            "pct5": round(r.pct[5], 2),
            "median": round(r.pct[50], 2),
            "pct95": round(r.pct[95], 2),
        }

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
            "pnl_pct": round((total_value - total_cost) / total_cost * 100, 2),
            "cash": round(cash, 2),
            "budget": budget,
        },
        "allocation_pct": {k: round(v * 100, 1) for k, v in alloc.items()},
        "drift_vs_target_pp": drift,
        "nvda_look_through_pct": round(nvda_pct_book * 100, 1),
        "triggers_fired": fired,
        "monte_carlo_2wk": mc,
        "verdict": verdict,
    }


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
    lines += ["", f"VERDICT: {r['verdict']}"]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic NDQ/IOO portfolio check")
    ap.add_argument("--ndq", type=float, required=True, help="NDQ.AX live price")
    ap.add_argument("--ioo", type=float, required=True, help="IOO.AX live price")
    ap.add_argument("--vix", type=float, default=None, help="current VIX (optional)")
    ap.add_argument("--budget", type=float, default=BUDGET_DEFAULT)
    ap.add_argument("--ndq-nvda-weight", type=float, default=NVDA_WEIGHT_DEFAULT["NDQ.AX"])
    ap.add_argument("--ioo-nvda-weight", type=float, default=NVDA_WEIGHT_DEFAULT["IOO.AX"])
    ap.add_argument("--ndq-stop", type=float, default=TRIGGERS_DEFAULT["ndq_stop"])
    ap.add_argument("--ndq-50d", type=float, default=TRIGGERS_DEFAULT["ndq_50d"])
    ap.add_argument("--ioo-50d", type=float, default=TRIGGERS_DEFAULT["ioo_50d"])
    ap.add_argument("--vix-max", type=float, default=TRIGGERS_DEFAULT["vix_max"])
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

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
        )
    except (FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.json:
        json.dump(result, sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(format_report(result, args.vix))
    # exit 1 when a price/VIX trigger fired -> orchestrator dispatches agents
    return 1 if "TRIGGER" in result["verdict"] else 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Monte Carlo price-path simulator — the honest physics bridge into this project.

WHY THIS EXISTS
---------------
The pipeline kept emitting hand-waved point probabilities ("~60% lower in 2-4
weeks"). Those are unfalsifiable guesses. This module replaces them with a
*distribution* derived from a real stochastic model.

THE PHYSICS (real, not woo)
---------------------------
- **Brownian motion** (Einstein 1905, Wiener process): the mathematics of a
  particle diffusing in fluid. Bachelier (1900) and later Black-Scholes-Merton
  applied the *same* differential equation to asset prices.
- **Geometric Brownian Motion (GBM)**: dS = mu*S*dt + sigma*S*dW, where dW is a
  Wiener increment ~ N(0, dt). Prices can't go negative, returns are log-normal.
- **Monte Carlo** (Ulam/von Neumann/Metropolis, Manhattan Project, 1940s):
  simulate the process N times, read the empirical distribution. Same method
  physicists used for neutron diffusion in fission — here applied to price paths.

WHAT IT DOES NOT DO (the honest part)
-------------------------------------
- It does NOT predict direction. `mu` (drift) is an *assumption you supply*, not
  a forecast. Garbage drift in, garbage distribution out.
- It does NOT give "certain" signals. Quantum mechanics gives no market edge.
  The ONLY honest quantum/physics borrow here is the *uncertainty analogy*:
  just as you cannot simultaneously know a particle's exact position and
  momentum, you cannot simultaneously know an asset's exact entry timing and
  its direction. The model quantifies that irreducible uncertainty — it does
  not remove it. The correct response to irreducible uncertainty is DCA, not
  a bigger bet.

DETERMINISM
-----------
Fixed RNG seed → same inputs always yield the same distribution. This enforces
the pipeline's anti-waffle rule: identical inputs must produce identical output.

CLI
---
    python scripts/montecarlo.py --price 4479 --vol 0.18 --days 14 \\
        --cost 4650 --drift 0.0 --paths 100000

    # gold example (GC=F): annualized vol ~16-20%, 2-week horizon
    python scripts/montecarlo.py --price 4479 --vol 0.18 --days 14 --drift 0.04

    # built-in unit tests (no arguments needed)
    python scripts/montecarlo.py --self-test
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass

try:
    from runtime import force_utf8_stdio

    force_utf8_stdio()
except Exception:
    pass


# Deterministic seed — anti-waffle: same inputs → same distribution, every run.
_SEED = 20260527

TRADING_DAYS_PER_YEAR = 252


@dataclass(frozen=True)
class SimResult:
    price0: float
    horizon_days: int
    annual_vol: float
    annual_drift: float
    paths: int
    p_up: float            # P(end price > start price)
    p_down: float          # P(end price < start price)
    p_below_cost: float | None   # P(end price < cost basis), if cost supplied
    pct: dict[int, float]  # percentile -> price
    expected: float        # mean end price


def simulate(
    price0: float,
    annual_vol: float,
    horizon_days: int,
    annual_drift: float = 0.0,
    cost_basis: float | None = None,
    paths: int = 100_000,
) -> SimResult:
    """Geometric Brownian Motion Monte Carlo over `horizon_days`.

    Closed-form single-step GBM to the horizon (no need to step daily for a
    terminal-distribution question):
        S_T = S_0 * exp((mu - 0.5*sigma^2) * T + sigma * sqrt(T) * Z)
    with T in years, Z ~ N(0,1).
    """
    if price0 <= 0:
        raise ValueError("price0 must be positive")
    if annual_vol < 0:
        raise ValueError("annual_vol must be non-negative")
    if horizon_days <= 0:
        raise ValueError("horizon_days must be positive")
    if paths <= 0:
        raise ValueError("paths must be positive")

    rng = random.Random(_SEED)
    T = horizon_days / TRADING_DAYS_PER_YEAR
    drift_term = (annual_drift - 0.5 * annual_vol**2) * T
    diffusion = annual_vol * math.sqrt(T)

    ups = 0
    below_cost = 0
    ends: list[float] = []
    total = 0.0
    for _ in range(paths):
        z = rng.gauss(0.0, 1.0)
        s_t = price0 * math.exp(drift_term + diffusion * z)
        ends.append(s_t)
        total += s_t
        if s_t > price0:
            ups += 1
        if cost_basis is not None and s_t < cost_basis:
            below_cost += 1

    ends.sort()

    def percentile(p: int) -> float:
        idx = min(paths - 1, max(0, int(round((p / 100.0) * (paths - 1)))))
        return ends[idx]

    pct = {p: percentile(p) for p in (5, 10, 25, 50, 75, 90, 95)}

    return SimResult(
        price0=price0,
        horizon_days=horizon_days,
        annual_vol=annual_vol,
        annual_drift=annual_drift,
        paths=paths,
        p_up=ups / paths,
        p_down=1.0 - ups / paths,
        p_below_cost=(below_cost / paths) if cost_basis is not None else None,
        pct=pct,
        expected=total / paths,
    )


def format_result(r: SimResult, cost_basis: float | None, currency: str = "") -> str:
    cur = (currency + " ") if currency else ""
    lines = [
        "=== Monte Carlo (Geometric Brownian Motion) ===",
        f"Start price:      {cur}{r.price0:,.2f}",
        f"Horizon:          {r.horizon_days} trading days (~{r.horizon_days/5:.1f} weeks)",
        f"Annualized vol:   {r.annual_vol:.1%}",
        f"Annualized drift: {r.annual_drift:+.1%}  (ASSUMPTION, not a forecast)",
        f"Paths simulated:  {r.paths:,}",
        "",
        f"P(higher than start): {r.p_up:.1%}",
        f"P(lower than start):  {r.p_down:.1%}",
    ]
    if r.p_below_cost is not None and cost_basis is not None:
        lines.append(f"P(below cost {cur}{cost_basis:,.2f}): {r.p_below_cost:.1%}")
    lines += [
        "",
        "Terminal price distribution (percentiles):",
        f"   5th : {cur}{r.pct[5]:,.2f}   (1-in-20 worse than this)",
        f"  10th : {cur}{r.pct[10]:,.2f}",
        f"  25th : {cur}{r.pct[25]:,.2f}",
        f"  50th : {cur}{r.pct[50]:,.2f}   (median)",
        f"  75th : {cur}{r.pct[75]:,.2f}",
        f"  90th : {cur}{r.pct[90]:,.2f}",
        f"  95th : {cur}{r.pct[95]:,.2f}   (1-in-20 better than this)",
        f"  mean : {cur}{r.expected:,.2f}",
        "",
        "INTERPRETATION:",
        "  Near 50/50 up/down over short horizons is the EXPECTED result of an",
        "  efficient market — it confirms short-term timing is not forecastable.",
        "  The honest response to this distribution is dollar-cost-averaging,",
        "  NOT a larger directional bet. The model quantifies uncertainty; it",
        "  does not remove it. (No quantum signal can. Anyone selling one lies.)",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Built-in unit tests (deterministic). Run: python scripts/montecarlo.py --self-test
# --------------------------------------------------------------------------
def _raises_value_error(**kwargs: object) -> bool:
    """True iff ``simulate`` rejects these arguments with ValueError."""
    try:
        simulate(**kwargs)  # type: ignore[arg-type]
    except ValueError:
        return True
    except Exception:  # any other failure is still a defect
        return False
    return False


def _monotonic(values: list[float]) -> bool:
    return all(a <= b for a, b in zip(values, values[1:]))


def _self_test() -> int:
    # A modest path count keeps the test fast; the seed is fixed so the
    # sampling error below is itself deterministic, not a flaky tolerance.
    paths = 20_000
    price0, vol, days = 100.0, 0.20, 21
    flat = simulate(price0, vol, days, annual_drift=0.0, paths=paths)
    repeat = simulate(price0, vol, days, annual_drift=0.0, paths=paths)
    with_cost = simulate(price0, vol, days, annual_drift=0.0, cost_basis=95.0, paths=paths)

    # Closed-form GBM median: S_0 * exp((mu - 0.5*sigma^2) * T). With 20k fixed
    # -seed paths the empirical median lands within ~0.5% of it.
    horizon_years = days / TRADING_DAYS_PER_YEAR
    closed_median = price0 * math.exp((0.0 - 0.5 * vol**2) * horizon_years)
    median_err = abs(flat.pct[50] - closed_median) / closed_median

    cases: list[tuple[str, bool]] = [
        ("determinism: same inputs -> identical result", flat == repeat),
        ("determinism: identical formatted output",
            format_result(flat, None) == format_result(repeat, None)),
        ("drift-free median matches the closed form (<0.5%)", median_err < 0.005),
        ("p_up + p_down == 1", abs(flat.p_up + flat.p_down - 1.0) < 1e-12),
        ("zero drift -> p_up < 0.5 (the -0.5*sigma^2 median drag)", flat.p_up < 0.5),
        ("zero drift -> p_up still near coin-flip", flat.p_up > 0.45),
        ("quantiles are monotonic",
            _monotonic([flat.pct[p] for p in (5, 10, 25, 50, 75, 90, 95)])),
        ("cost_basis=None -> p_below_cost is None", flat.p_below_cost is None),
        ("cost_basis supplied -> probability in [0, 1]",
            with_cost.p_below_cost is not None and 0.0 <= with_cost.p_below_cost <= 1.0),
        ("cost basis below the 5th pct is unlikely",
            with_cost.p_below_cost is not None and with_cost.p_below_cost < 0.5),
        ("positive drift raises p_up",
            simulate(price0, vol, days, annual_drift=0.50, paths=paths).p_up > flat.p_up),
        ("mean end price is near the risk-neutral forward",
            abs(flat.expected - price0) / price0 < 0.01),
        ("zero vol collapses the distribution",
            simulate(price0, 0.0, days, paths=100).pct[5] == price0),
        ("reported inputs are echoed back",
            (flat.price0, flat.horizon_days, flat.annual_vol, flat.paths)
            == (price0, days, vol, paths)),
        ("rejects price0 <= 0",
            _raises_value_error(price0=0.0, annual_vol=vol, horizon_days=days)),
        ("rejects negative price0",
            _raises_value_error(price0=-1.0, annual_vol=vol, horizon_days=days)),
        ("rejects negative vol",
            _raises_value_error(price0=price0, annual_vol=-0.1, horizon_days=days)),
        ("rejects horizon_days <= 0",
            _raises_value_error(price0=price0, annual_vol=vol, horizon_days=0)),
        ("rejects negative horizon_days",
            _raises_value_error(price0=price0, annual_vol=vol, horizon_days=-5)),
        ("rejects paths <= 0",
            _raises_value_error(price0=price0, annual_vol=vol, horizon_days=days, paths=0)),
    ]

    passed = 0
    for label, ok in cases:
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {label}")
    print(f"\n{passed}/{len(cases)} montecarlo unit tests passed.")
    return 0 if passed == len(cases) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="GBM Monte Carlo price-path simulator")
    # --price/--vol/--days are NOT argparse-required so `--self-test` can run
    # alone; main validates them below instead.
    ap.add_argument("--price", type=float, default=None, help="current price")
    ap.add_argument("--vol", type=float, default=None, help="annualized volatility, e.g. 0.18 for 18%")
    ap.add_argument("--days", type=int, default=None, help="horizon in trading days")
    ap.add_argument("--drift", type=float, default=0.0, help="annualized drift ASSUMPTION (default 0 = no edge)")
    ap.add_argument("--cost", type=float, default=None, help="your cost basis (for P(below cost))")
    ap.add_argument("--paths", type=int, default=100_000, help="number of simulated paths")
    ap.add_argument("--currency", type=str, default="", help="currency label for display, e.g. CNY or USD")
    ap.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    missing = [
        name for name, value in
        (("--price", args.price), ("--vol", args.vol), ("--days", args.days))
        if value is None
    ]
    if missing:
        print(f"error: missing required argument(s): {', '.join(missing)}", file=sys.stderr)
        return 2

    try:
        r = simulate(
            price0=args.price,
            annual_vol=args.vol,
            horizon_days=args.days,
            annual_drift=args.drift,
            cost_basis=args.cost,
            paths=args.paths,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(format_result(r, args.cost, args.currency))
    return 0


if __name__ == "__main__":
    sys.exit(main())

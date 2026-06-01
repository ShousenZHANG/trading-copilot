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


def main() -> int:
    ap = argparse.ArgumentParser(description="GBM Monte Carlo price-path simulator")
    ap.add_argument("--price", type=float, required=True, help="current price")
    ap.add_argument("--vol", type=float, required=True, help="annualized volatility, e.g. 0.18 for 18%")
    ap.add_argument("--days", type=int, required=True, help="horizon in trading days")
    ap.add_argument("--drift", type=float, default=0.0, help="annualized drift ASSUMPTION (default 0 = no edge)")
    ap.add_argument("--cost", type=float, default=None, help="your cost basis (for P(below cost))")
    ap.add_argument("--paths", type=int, default=100_000, help="number of simulated paths")
    ap.add_argument("--currency", type=str, default="", help="currency label for display, e.g. CNY or USD")
    args = ap.parse_args()

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

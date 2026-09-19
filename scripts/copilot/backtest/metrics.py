"""Risk and return metrics. Pure functions over an equity curve.

Formula sources: annualized return, volatility and max drawdown follow
microsoft/qlib `contrib/evaluate.py::risk_analysis` (MIT), re-implemented in
the stdlib. Nothing is vendored.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from .frame import DAYS_PER_YEAR

TRADING_DAYS_PER_YEAR = 252

#: Declared, not assumed. Sharpe here is excess return over zero. A reader who
#: wants a T-bill benchmark must say so; silently baking one in makes two
#: backtests from different years incomparable.
RISK_FREE_RATE = 0.0

Curve = Sequence[tuple[date, float]]


@dataclass(frozen=True)
class Drawdown:
    depth: float
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    duration_days: int


def _validated(curve: Curve) -> list[tuple[date, float]]:
    """Reject a curve that cannot carry a meaningful metric.

    Non-positive values make CAGR a complex number and volatility meaningless;
    duplicate or unordered dates make drawdown recovery ambiguous. Raising here
    beats returning a number that looks like evidence. A curve shorter than two
    bars is degenerate, not malformed -- every caller here already defines what
    a single point or an empty curve means (0.0, or a zero-depth Drawdown) --
    so it passes through unchecked and the caller's own guard handles it.
    """
    curve = list(curve)
    if len(curve) < 2:
        return curve
    for i, (when, value) in enumerate(curve):
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"curve bar {i} ({when}): value must be finite and positive, got {value}")
        if i > 0 and when <= curve[i - 1][0]:
            raise ValueError(
                f"curve bar {i} ({when}): dates must be strictly increasing, previous bar was {curve[i - 1][0]}")
    return curve


def max_drawdown(curve: Curve) -> Drawdown:
    """Deepest peak-to-trough fall, with the days from peak to recovery.

    `duration_days` runs peak to recovery when recovery happened, peak to the
    end of the curve when it has not. Reporting only the trough understates how
    long a holder actually spent underwater, which is the number that decides
    whether someone abandons a strategy.
    """
    curve = _validated(curve)
    if len(curve) < 2:
        return Drawdown(0.0, None, None, None, 0)
    peak_value, peak_date = curve[0][1], curve[0][0]
    best = Drawdown(0.0, None, None, None, 0)
    for when, value in curve[1:]:
        if value > peak_value:
            peak_value, peak_date = value, when
            continue
        depth = (peak_value - value) / peak_value
        # >= (not >): of two equally deep drawdowns, the later one wins. A
        # gate reads recovery_date to decide whether the worst drawdown is
        # behind us; the later of two ties is the one more likely still open,
        # so reporting it is the conservative choice. (The loop starts at
        # curve[1:], not curve[0:], because bar 0 seeded peak_value/peak_date
        # already -- comparing it to itself is a zero-depth no-op that would
        # otherwise tie against the initial sentinel too.)
        if depth >= best.depth:
            best = Drawdown(depth, peak_date, when, None, 0)
    if best.peak_date is None:
        return best
    recovery = next((w for w, v in curve
                     if w > best.trough_date and v >= _value_at(curve, best.peak_date)), None)
    end = recovery or curve[-1][0]
    return Drawdown(best.depth, best.peak_date, best.trough_date, recovery,
                    (end - best.peak_date).days)


def _value_at(curve: Curve, when: date) -> float:
    for w, v in curve:
        if w == when:
            return v
    raise KeyError(when)


def cagr(curve: Curve) -> float:
    curve = _validated(curve)
    if len(curve) < 2:
        return 0.0
    years = (curve[-1][0] - curve[0][0]).days / DAYS_PER_YEAR
    if years <= 0 or curve[0][1] <= 0:
        return 0.0
    return (curve[-1][1] / curve[0][1]) ** (1 / years) - 1


def daily_returns(curve: Curve) -> list[float]:
    curve = _validated(curve)
    return [(curve[i][1] / curve[i - 1][1]) - 1 for i in range(1, len(curve))]


def annual_volatility(curve: Curve) -> float:
    curve = _validated(curve)
    returns = daily_returns(curve)
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def sharpe(curve: Curve) -> float:
    curve = _validated(curve)
    vol = annual_volatility(curve)
    if vol == 0:
        return 0.0
    return (cagr(curve) - RISK_FREE_RATE) / vol


def annual_turnover(*, traded_notional: float, average_value: float, years: float) -> float:
    """One-way traded notional over average book value, per year.

    The denominator is the plain arithmetic mean of the equity curve --
    average net assets, the conventional denominator for published fund
    turnover ratios, so our numbers are comparable to a fund's reported
    turnover. It is NOT comparable across two of our own strategies with very
    different growth: on a curve that grows 10x over its window, the average
    sits near 39% of the ending value, which inflates turnover computed this
    way by roughly 2.55x versus an ending-value denominator on that same
    curve. Only compare turnover across strategies measured over the same
    window with similar growth.
    """
    if average_value <= 0 or years <= 0:
        return 0.0
    return traded_notional / average_value / years


def window(curve: Curve, year: int) -> list[tuple[date, float]]:
    return [(w, v) for w, v in curve if w.year == year]

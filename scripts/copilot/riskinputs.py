"""The measured risk inputs policy needs, computed from real data or omitted.

WHY THIS MODULE EXISTS
`policy`'s `executable` requires every one of its five limits to be `pass` or
`not_applicable`. An absent input becomes `unknown`, which blocks execution, so
the engine cannot produce a quantity without supplying real measurements. Four
of the five are computable from what the repository already collects; the fifth,
correlation, has no informative threshold for an equity-only sleeve and is marked
`not_applicable` in policy itself (ADR-0007 clause 7).

WHAT IS DELIBERATELY ABSENT RATHER THAN ZERO
A value that cannot be measured is left out of the returned mapping. Returning
0.0 would read as a measured pass. Policy turns an absent key into `unknown`,
which blocks -- the conservative direction.

EVERY VALUE IS CLAMPED TO [0, 1]
Policy treats a value outside that range as `unknown` rather than `fail`
(policy.py:324), so an unclamped ratio above 1 would silently block instead of
failing the limit it breached. Clamping keeps the failure legible.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

#: 20 sessions is the window `compute_indicators` already uses for average
#: volume, so the liquidity denominator matches the one the snapshot reports.
ADV_SESSIONS = 20


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"risk input must be finite, got {value!r}")
    return max(0.0, min(1.0, value))


def average_dollar_volume(bars: Sequence[Mapping], *, sessions: int = ADV_SESSIONS) -> float | None:
    """Mean close-times-volume over the most recent sessions, or None.

    None when no bar carries a volume: the Nasdaq equity record has none at all,
    and treating that as zero volume would make every order look illiquid.
    """
    if not bars:
        return None
    window = list(bars)[-max(1, int(sessions)):]
    values = []
    for bar in window:
        close, volume = bar.get("close"), bar.get("volume")
        if isinstance(close, bool) or not isinstance(close, (int, float)):
            continue
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            continue
        values.append(float(close) * float(volume))
    if not values:
        return None
    return sum(values) / len(values)


def portfolio_drawdown(value_history: Sequence[float]) -> tuple[float, int]:
    """Drawdown from the running peak, and how many observations it rests on.

    With one observation the drawdown genuinely is zero. The count is returned so
    a caller can report that the measurement is young rather than implying depth
    the history cannot support.
    """
    values = [float(v) for v in value_history
              if not isinstance(v, bool) and isinstance(v, (int, float))
              and math.isfinite(v) and v > 0]
    if not values:
        raise ValueError("value_history must carry at least one positive value")
    peak, deepest = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        deepest = max(deepest, (peak - value) / peak)
    return deepest, len(values)


def compute(*, symbol: str, target_shares: int, price: float, total_value: float,
            sector_values: Mapping[str, float], sector_of: str,
            average_dollar_volume: float | None, value_history: Sequence[float],
            delta_shares: int | None = None) -> dict:
    """The measured inputs for one post-trade position.

    `sector_values` holds the post-trade market value of OTHER holdings per
    sector, so this position's own value is added here rather than double
    counted. `delta_shares` defaults to `target_shares`, which is correct for an
    empty book; pass it explicitly so liquidity measures what is traded rather
    than what is held.
    """
    if not math.isfinite(total_value) or total_value <= 0:
        raise ValueError(f"total_value must be a finite positive number, got {total_value!r}")
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"price must be a finite positive number, got {price!r}")
    position_value = float(target_shares) * float(price)
    traded = float(target_shares if delta_shares is None else delta_shares)
    drawdown, samples = portfolio_drawdown(value_history)
    inputs = {
        "post_trade_weight": _clamp(position_value / total_value),
        "post_trade_sector_weight": _clamp(
            (position_value + float(sector_values.get(sector_of, 0.0))) / total_value),
        "drawdown": _clamp(drawdown),
        "drawdown_sample_count": samples,
        "sector": sector_of,
    }
    if average_dollar_volume is not None and average_dollar_volume > 0:
        inputs["position_adv_fraction"] = _clamp(
            abs(traded) * float(price) / float(average_dollar_volume))
    return inputs

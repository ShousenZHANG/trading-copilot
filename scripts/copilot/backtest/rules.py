"""Three rule families, implemented from their primary sources.

Sources, per Q39: Bogleheads wiki "Rebalancing" for the 5/25 band definition;
Faber, "A Quantitative Approach to Tactical Asset Allocation" (2007) and
Antonacci, "Dual Momentum Investing" (2014) for the 12-1 formation window;
inverse-volatility weighting from its textbook definition. Cross-checked against
bt by scripts/_cross_check_bt.py; a disagreement is a release blocker.

Scope, per Q23=A plus the user's Q38 amendment: equity ETFs and cash only. No
bond or gold leg exists here. The structural cash reserve is an engine
parameter, deliberately not a rule parameter -- it is a comfort constraint the
user sets, not something a backtest fits, and folding it in would consume one of
Q29's three parameter slots.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, replace
from typing import ClassVar, Sequence

from .frame import PriceFrame

#: A genuinely zero-volatility asset is a data artifact, not a riskless one.
#: Without a floor, 1/vol is a division by zero, and mapping zero volatility to
#: zero weight would invert the strategy exactly where it matters most.
MIN_DAILY_VOLATILITY = 1e-6


def _returns(series: Sequence[float]) -> list[float]:
    """Simple returns between consecutive prices. Raises rather than drops.

    A previous version silently skipped a non-positive price instead of
    raising -- the same silent-drop failure MomentumTopN.weights() below
    refuses, and for the same reason: a shortened, still-plausible-looking
    returns list is indistinguishable from a real one to every caller
    downstream. Unreachable today given frame.PriceFrame's own
    construction-time validation, but a stranger copying this file's other
    pattern deserves the consistent one, not a silent exception to it.
    """
    returns = []
    for i in range(1, len(series)):
        if series[i - 1] <= 0:
            raise ValueError(f"non-positive price {series[i - 1]} at index {i - 1}; cannot "
                             "compute a return, and dropping it would silently shorten the series")
        returns.append((series[i] / series[i - 1]) - 1)
    return returns


@dataclass(frozen=True)
class FixedWeightBands:
    """Constant targets, rebalanced on the Bogleheads 5/25 rule.

    Both halves are required. The relative band catches drift on large holdings;
    the absolute band catches drift on small ones, where a 5-percentage-point
    move can be a 100% relative move. bt 1.2.3's `RunIfOutOfBounds` implements
    only the relative half (`algos.py:408` divides by the target weight), which
    is why this family is not delegated to it.

    `calendar_days` is true calendar time (a date difference), not a bar count
    -- unlike `InverseVolatility.rebalance_days` and `MomentumTopN.skip_days`
    below, which count bars. This family needs no lookback, so it needs no
    warm-up.
    """
    targets: dict[str, float]
    relative_band: float = 0.25
    absolute_band: float = 0.05
    calendar_days: int = 365
    name: str = "fixed_weight_bands"

    #: No caveat: a constant-weight, band-rebalanced sleeve is exactly what it
    #: says it is, unlike the other two families below (Q22=C).
    caveats: ClassVar[tuple[str, ...]] = ()

    @property
    def parameters(self) -> dict[str, float]:
        return {"relative_band": self.relative_band, "absolute_band": self.absolute_band,
                "calendar_days": float(self.calendar_days)}

    @property
    def warmup_bars(self) -> int:
        return 0

    def with_parameters(self, params: dict[str, float]) -> "FixedWeightBands":
        """A new instance with these parameter fields replaced.

        Q29 rule 4's divergence check (sensitivity_grid in backtest_cli.py)
        hands back float-valued neighbour dicts even for an int-typed field
        (calendar_days); coerce before dataclasses.replace() so the field
        keeps its declared type. There is no __post_init__ here to reject a
        nonsensical neighbour -- every value this family accepts is a plain
        band width or day count with no cross-field constraint.
        """
        coerced = {k: (int(v) if k == "calendar_days" else v) for k, v in params.items()}
        return replace(self, **coerced)

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        return dict(self.targets)

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        # Bootstrap. bt 1.2.3's RunIfOutOfBounds returns False here because it
        # iterates children that Rebalance has not created yet, and the whole
        # backtest then sits in cash. This branch is that bug's absence.
        if last_rebalance_index is None or not current:
            return True
        if (frame.dates[i] - frame.dates[last_rebalance_index]).days >= self.calendar_days:
            return True
        # Union, not just self.targets: a holding present in `current` but
        # absent from `targets` (a rogue position -- stock spun off into the
        # book, a manual trade, a bug upstream) has an implicit target of 0.0
        # and must be able to trigger a rebalance like any other drifted
        # symbol. Iterating self.targets alone made such a holding invisible.
        for symbol in set(self.targets) | set(current):
            target = self.targets.get(symbol, 0.0)
            drift = abs(current.get(symbol, 0.0) - target)
            if drift >= self.absolute_band or (target > 0 and drift / target >= self.relative_band):
                return True
        return False


@dataclass(frozen=True)
class InverseVolatility:
    """Weight inversely to trailing volatility, rebalanced on a fixed interval.

    Note for the README (Q22=C): over an equity-only universe this is NOT risk
    parity. Every holding is equity beta, so the portfolio's market exposure
    stays near 100% and the weighting only re-ranks within that exposure.

    `lookback_days` and `rebalance_days` both count bars, not calendar days --
    this family assumes trading-day-spaced input, which `history.py` always
    provides. `warmup_bars` equals `lookback_days`: the engine must not call
    `weights()` before a full lookback window of bars exists, so it never sees
    a partial window silently treated as a complete one.
    """
    universe: tuple[str, ...]
    lookback_days: int = 63
    rebalance_days: int = 21
    name: str = "inverse_volatility"

    #: Q22=C's required caveat for this family, carried here so run_family can
    #: copy it into the report and the CLI can print it -- a docstring reaches
    #: a reader of the source, not a user reading `... ADMITTED, CAGR +11%`.
    caveats: ClassVar[tuple[str, ...]] = (
        "Over an equity-only universe, inverse-volatility weighting is NOT risk "
        "parity: every holding is equity beta, so portfolio market exposure stays "
        "near 100% and the weighting only re-ranks within that exposure.",
    )

    def __post_init__(self) -> None:
        # statistics.stdev needs at least two returns, so the window needs at
        # least three prices. A shorter lookback would raise from deep inside
        # weights() on the first real bar rather than at construction.
        if self.lookback_days < 2:
            raise ValueError(f"lookback_days must be at least 2 to measure a standard "
                             f"deviation, got {self.lookback_days}")
        if self.rebalance_days < 1:
            raise ValueError(f"rebalance_days must be at least 1, got {self.rebalance_days}")
        if not self.universe:
            raise ValueError("universe must hold at least one symbol")

    @property
    def parameters(self) -> dict[str, float]:
        return {"lookback_days": float(self.lookback_days),
                "rebalance_days": float(self.rebalance_days)}

    @property
    def warmup_bars(self) -> int:
        return self.lookback_days

    def with_parameters(self, params: dict[str, float]) -> "InverseVolatility":
        """A new instance with these parameter fields replaced.

        Both parameter fields are int-typed; sensitivity_grid's neighbour
        dicts are always float-valued, so every value is coerced. __post_init__
        still runs inside dataclasses.replace() and refuses a nonsensical
        neighbour (lookback_days < 2, rebalance_days < 1) at construction
        rather than accepting it silently.
        """
        return replace(self, **{k: int(v) for k, v in params.items()})

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        start = i - self.lookback_days
        if start < 0:
            raise ValueError(
                f"{self.name}: bar {i} is before warmup_bars={self.warmup_bars}; "
                "the engine must not call weights() until the lookback window is full")
        inverse: dict[str, float] = {}
        for symbol in self.universe:
            series = frame.column(symbol)[start:i + 1]
            vol = statistics.stdev(_returns(series)) if len(series) > 2 else 0.0
            inverse[symbol] = 1.0 / max(vol, MIN_DAILY_VOLATILITY)
        total = sum(inverse.values())
        return {s: v / total for s, v in inverse.items()}

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        if last_rebalance_index is None:
            return True
        return i - last_rebalance_index >= self.rebalance_days


@dataclass(frozen=True)
class MomentumTopN:
    """Hold the N strongest names over a 12-1 formation window, equal-weighted.

    The one-month skip is the point: without it the signal is short-term
    reversal, not momentum. There is no cash exit -- Q38 fixed this family as
    rotation only, so in a 2008- or 2022-shaped decline it decides which
    equities you lose in, not whether you are in equities. That sentence belongs
    in the README next to this strategy's backtest (Q22=C).

    `lookback_days` and `skip_days` both count bars, not calendar days -- this
    family assumes trading-day-spaced input, which `history.py` always
    provides. `skip_days` is reused as the rebalance interval below: that is a
    deliberate parameter-budget economy (Jegadeesh-Titman / Faber / Antonacci
    all rebalance the formation strategy monthly, the same cadence as the
    skip), not an oversight. `warmup_bars` equals `lookback_days`: the engine
    must not call `weights()` before a full formation window is available.
    """
    universe: tuple[str, ...]
    top_n: int = 5
    lookback_days: int = 252
    skip_days: int = 21
    name: str = "momentum_top_n"

    #: Q22=C's required caveat for this family, carried here so run_family can
    #: copy it into the report and the CLI can print it -- a docstring reaches
    #: a reader of the source, not a user reading `... ADMITTED, CAGR +11%`.
    caveats: ClassVar[tuple[str, ...]] = (
        "This family has no cash exit: in a 2008- or 2022-shaped decline it "
        "decides which equities you lose in, not whether you are in equities.",
    )

    def __post_init__(self) -> None:
        # Without this, skip_days >= lookback_days makes formation_window's end
        # index land at or before its start -- and a negative end wraps through
        # Python's negative indexing to a bar near the END of the series, which
        # is look-ahead dressed as a formation return. Refuse the construction
        # rather than report the number it would produce.
        if self.skip_days >= self.lookback_days:
            raise ValueError(f"skip_days ({self.skip_days}) must be smaller than "
                             f"lookback_days ({self.lookback_days}); the formation window "
                             "would otherwise be empty or reversed")
        if self.skip_days < 0:
            raise ValueError(f"skip_days must not be negative, got {self.skip_days}")
        # top_n larger than the universe is fine and means "hold them all";
        # the slice in weights() already truncates. Zero or negative is not.
        if self.top_n < 1:
            raise ValueError(f"top_n must be at least 1, got {self.top_n}")

    @property
    def parameters(self) -> dict[str, float]:
        return {"top_n": float(self.top_n), "lookback_days": float(self.lookback_days),
                "skip_days": float(self.skip_days)}

    @property
    def warmup_bars(self) -> int:
        return self.lookback_days

    def with_parameters(self, params: dict[str, float]) -> "MomentumTopN":
        """A new instance with these parameter fields replaced.

        All three parameter fields are int-typed; sensitivity_grid's neighbour
        dicts are always float-valued, so every value is coerced.
        __post_init__ still runs inside dataclasses.replace() and refuses a
        nonsensical neighbour (skip_days >= lookback_days, non-positive
        top_n) at construction rather than accepting it silently.
        """
        return replace(self, **{k: int(v) for k, v in params.items()})

    def formation_window(self, i: int) -> tuple[int, int]:
        return (i - self.lookback_days, i - self.skip_days)

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        start, end = self.formation_window(i)
        if start < 0:
            raise ValueError(
                f"{self.name}: bar {i} is before warmup_bars={self.warmup_bars}; "
                "the engine must not call weights() until the formation window is full")
        scored = []
        for symbol in self.universe:
            series = frame.column(symbol)
            price_at_start = series[start]
            if price_at_start <= 0:
                raise ValueError(
                    f"{symbol}: non-positive price ({price_at_start}) at the formation "
                    f"start {frame.dates[start]}; cannot compute a momentum return, and "
                    "dropping the symbol would silently under-fill the portfolio")
            scored.append(((series[end] / price_at_start) - 1, symbol))
        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        chosen = [symbol for _, symbol in scored[:self.top_n]]
        share = 1.0 / len(chosen)
        return {symbol: share for symbol in chosen}

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        if last_rebalance_index is None:
            return True
        return i - last_rebalance_index >= self.skip_days

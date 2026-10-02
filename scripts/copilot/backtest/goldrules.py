"""The gold sleeve's rule family. One asset, so the decision is WHEN to add.

WHY NOT REUSE THE ETF FAMILIES
FixedWeightBands, InverseVolatility and MomentumTopN all answer "how do I
divide a book across N names". Gold is one instrument, so at N=1 every one of
them returns {GOLD.CNY: 1.0} on every bar forever -- a rule with no decision
in it, which would then be backtested, admitted and adopted as though it had
one. The decision that genuinely exists for a single accumulating asset is the
contribution schedule.

THE TREND FILTER IS ONE-WAY BY CONSTRUCTION
`pause_below_trend` can only ever SKIP a scheduled contribution. It can never
create one, and `should_rebalance` returns False for any bar the schedule did
not already open. This mirrors brake.py's rule: a signal with thin evidence
behind it gets a veto, never an accelerator. A 200-day trend filter on gold is
a well-published idea, but it is one parameter fitted on one asset over ten
years, and that does not earn the right to pull a purchase forward.
"""
from __future__ import annotations

from dataclasses import dataclass

from .frame import PriceFrame

SYMBOL = "GOLD.CNY"


@dataclass(frozen=True)
class ScheduledAccumulation:
    """Add to the position every `interval_days` bars, optionally skipping dips below trend.

    `interval_days` counts BARS, not calendar days. The vendored SGE series is
    trading-day spaced, and a calendar-day interval would let a Spring Festival
    or National Day closure shorten the gap between contributions -- the holiday
    would silently buy sooner.

    Frozen, and holding no state. The ETF families were made frozen after a
    draft cached its own `last_rebalance_index` on the instance and leaked it
    between backtest runs; the engine owns that index and passes it in.
    """

    interval_days: int = 21
    trend_days: int = 200
    pause_below_trend: bool = False
    name: str = "scheduled_accumulation"

    def __post_init__(self) -> None:
        if not isinstance(self.interval_days, int) or self.interval_days < 1:
            raise ValueError(f"interval_days must be a positive integer, got {self.interval_days!r}")
        if self.pause_below_trend and (not isinstance(self.trend_days, int) or self.trend_days < 2):
            raise ValueError(f"trend_days must be an integer of at least 2 when "
                             f"pause_below_trend is on, got {self.trend_days!r}")

    @property
    def parameters(self) -> dict[str, float]:
        # Exactly three. admission.MAX_PARAMETERS is 3 and is structurally
        # unwaivable, so a fourth parameter is a design change, not a tweak.
        return {"interval_days": float(self.interval_days),
                "trend_days": float(self.trend_days),
                "pause_below_trend": 1.0 if self.pause_below_trend else 0.0}

    @property
    def warmup_bars(self) -> int:
        # Zero when no history is read: making a pure schedule wait 200 bars
        # would discard the first year of every backtest to protect a window
        # nothing looks at.
        return self.trend_days if self.pause_below_trend else 0

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        return {SYMBOL: 1.0}

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None, *, cash_floor_pct: float = 0.0) -> bool:
        if last_rebalance_index is None or not current:
            return True                      # bootstrap: the first contribution
        if i - last_rebalance_index < self.interval_days:
            return False                     # the schedule has not opened
        if not self.pause_below_trend:
            return True
        window = [frame.closes[j][0] for j in range(max(0, i - self.trend_days + 1), i + 1)]
        if len(window) < self.trend_days:
            # An incomplete window is not evidence of anything. Contribute:
            # the filter may only ever remove a purchase on positive evidence,
            # never on a window it could not measure.
            return True
        return frame.closes[i][0] >= sum(window) / len(window)


FAMILIES: dict[str, type] = {"scheduled_accumulation": ScheduledAccumulation}

#: The contribution schedule is NOT backtested, and cannot be by this engine:
#: engine.run rebalances to a target weight, and at one instrument that weight
#: is 1.0, so every scheduled contribution after the first trades zero. Measured
#: 2026-09-20 over the vendored series, interval_days 21 and interval_days 252
#: end 3 CNY apart in 360,000 -- the backtest cannot tell them apart, nor tell
#: either from buy and hold. Carried on every adoption and every evaluation for
#: the same reason brake.DISCLOSURE is: an unbacktested input has to say so
#: where the number is read, not in a document the reader may never open.
#:
#: This is a disclosure, not an apology. A contribution schedule makes no alpha
#: claim -- DCA is a cash-deployment policy. pause_below_trend DOES make one,
#: which is why ruleset refuses to adopt it at all rather than disclosing it.
DISCLOSURE = "定投节奏未回测：回测只证明这十年持有黄金的表现，不证明任何投入节奏更优"

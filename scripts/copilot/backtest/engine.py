"""Walk-forward portfolio loop. Deterministic: no clock, no network, no random.

The loop deliberately never reads frame.closes[i + 1]. Every rule receives the
index of the bar being traded and may look only backwards. The `Spy` test in
scripts/_test_backtest.py enforces that with a recorded call log rather than a
comment, because look-ahead is the failure that makes a backtest look good.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Protocol, Sequence

from .frame import PriceFrame
from ..sizing import plan_orders

WEIGHT_TOLERANCE = 1e-6


class Rule(Protocol):
    """Rules are frozen and stateless.

    `last_rebalance_index` is passed in rather than remembered, so the engine
    owns all mutable state. A rule that remembered its own last rebalance would
    silently carry it into the next backtest run, and the second result would
    differ from the first for no visible reason.
    """
    name: str
    parameters: dict[str, float]

    #: Bars `i < warmup_bars` are never shown to this rule at all -- the engine
    #: skips them outright, with no curve point, no cash entry, no positions
    #: entry, and no `should_rebalance`/`weights` call. A rule whose `weights`
    #: needs a trailing window (a lookback) declares that window's length here
    #: rather than filling the gap with a fallback basket that looks like a
    #: real answer but is not one. Default 0: a rule with no lookback needs no
    #: warm-up.
    warmup_bars: int = 0

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        """Target weights for bar `i`, summing to 1.0. Look backwards only."""

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None, *, cash_floor_pct: float = 0.0) -> bool:  # pragma: no cover - default
        return True


@dataclass(frozen=True)
class StaticWeights:
    """Test fixture rule: constant targets, rebalanced every bar."""
    targets: dict[str, float]
    name: str = "static"
    warmup_bars: int = 0

    @property
    def parameters(self) -> dict[str, float]:
        return {}

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        return dict(self.targets)

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None, *, cash_floor_pct: float = 0.0) -> bool:
        return True


@dataclass(frozen=True)
class CostModel:
    """IBKR Tiered-like US equity costs, plus half the quoted spread.

    Defaults are the published IBKR Pro tiered schedule as of 2026-09:
    USD 0.0035 per share, USD 1.00 minimum per order, capped at 1% of trade
    value. The spread term is a modelling assumption, not a fee: 2bp round-trip
    on liquid US ETFs, charged as half on each side.

    `max_pct_of_notional` is `float | None`: `None` means uncapped, and any
    float -- including `0.0` -- is applied as a real cap. Treating `0.0` as
    falsy ("no cap" instead of "cap at zero") let a deliberately zero-capped
    model silently charge full commission instead of nothing.
    """
    per_share_usd: float = 0.0035
    minimum_usd: float = 1.00
    max_pct_of_notional: float | None = 0.01
    spread_bps: float = 2.0

    @classmethod
    def free(cls) -> "CostModel":
        return cls(per_share_usd=0.0, minimum_usd=0.0, max_pct_of_notional=None, spread_bps=0.0)

    def commission(self, *, shares: float, notional: float) -> float:
        if shares <= 0 or notional <= 0:
            return 0.0
        fee = max(self.minimum_usd, self.per_share_usd * shares)
        if self.max_pct_of_notional is None:
            return fee
        return min(fee, self.max_pct_of_notional * notional)

    def spread(self, *, notional: float) -> float:
        return notional * (self.spread_bps / 10000.0) / 2.0

    def total(self, *, shares: float, notional: float) -> float:
        return self.commission(shares=shares, notional=notional) + self.spread(notional=notional)


@dataclass
class Result:
    rule_name: str
    parameters: dict[str, float]
    #: The symbols the frame actually held, in the frame's own order. Added so
    #: a Result is self-describing about what it ran over: rule_name and
    #: parameters alone cannot distinguish two runs of the same family and
    #: knobs over two different baskets, which is exactly what let a caller
    #: claim a genuine, admitted Result for a universe it never ran (see
    #: ruleset.build_adoption's binding check). Defaults to an empty tuple so
    #: every existing hand-built Result in the admission-gate tests, which
    #: predate this field and never touch universe, keeps constructing.
    universe: tuple[str, ...] = field(default_factory=tuple)
    curve: list[tuple[date, float]] = field(default_factory=list)
    cash_history: list[float] = field(default_factory=list)
    positions_history: list[dict[str, float]] = field(default_factory=list)
    traded_notional: float = 0.0
    total_costs: float = 0.0
    rebalance_count: int = 0
    # None distinguishes old/hand-built metric fixtures from engine-produced
    # results. Such fixtures remain useful for admission tests but cannot be
    # adopted without the execution settings that produced their curve.
    targets: dict[str, float] | None = None
    cost_model: dict | None = None
    cash_floor_pct: float | None = None
    integer_shares: bool | None = None
    execution_assumptions: dict = field(default_factory=dict)

    @property
    def average_value(self) -> float:
        return sum(v for _, v in self.curve) / len(self.curve) if self.curve else 0.0

    @property
    def years(self) -> float:
        if len(self.curve) < 2:
            return 0.0
        return (self.curve[-1][0] - self.curve[0][0]).days / 365.25


def _validate(targets: dict[str, float], frame: PriceFrame) -> None:
    total = sum(targets.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"target weights must sum to 1.0, got {total:.9f}")
    for symbol, weight in targets.items():
        if not math.isfinite(weight):
            raise ValueError(f"{symbol}: weight must be finite, got {weight}")
        if weight < 0:
            raise ValueError(f"{symbol}: negative weight {weight}; this sleeve is long-only")
        frame.index_of(symbol)


def run(frame: PriceFrame, *, rule: Rule, start_cash: float, cost_model: CostModel,
        cash_floor_pct: float, integer_shares: bool = True) -> Result:
    """Trade at each bar's close, paying costs on the traded notional."""
    if not 0.0 <= cash_floor_pct < 1.0:
        raise ValueError(f"cash_floor_pct must be in [0, 1), got {cash_floor_pct}")
    result = Result(rule_name=rule.name, parameters=dict(rule.parameters), universe=frame.symbols,
                    targets=(dict(rule.targets) if getattr(rule, "targets", None) is not None else None),
                    cost_model=asdict(cost_model), cash_floor_pct=float(cash_floor_pct),
                    integer_shares=integer_shares)
    result.execution_assumptions = {
        "execution_price": "same_bar_close", "signal_cutoff": "includes_current_bar",
        "price_basis": frame.price_basis,
        "cash_reserve": "fraction_of_pretrade_NAV_reserved_before_trade_costs",
        "disclosures": [
            "Signals may use the current close and trade at that close; the next executable time, gaps and fill availability are not modeled.",
            "When prices are adjusted, share counts and per-share fees are synthetic approximations; historical as-traded quantities are not reconstructed.",
        ],
    }
    cash = float(start_cash)
    positions: dict[str, float] = {}
    last_rebalance_index: int | None = None
    for i, when in enumerate(frame.dates):
        if i < rule.warmup_bars:
            # No curve point, no cash entry, no positions entry: a bar the
            # rule was never shown must not appear as if it had been traded.
            continue
        prices = frame.row(i)
        value = cash + sum(qty * prices[sym] for sym, qty in positions.items())
        current = {sym: (qty * prices[sym]) / value for sym, qty in positions.items()} if value else {}
        if rule.should_rebalance(frame, i, current, last_rebalance_index,
                                 cash_floor_pct=cash_floor_pct):
            last_rebalance_index = i
            targets = rule.weights(frame, i)
            _validate(targets, frame)
            plan = plan_orders(weights=targets, prices=prices, held_shares=positions,
                               investable_cash=cash, cash_floor_pct=cash_floor_pct,
                               cost_model=cost_model, integer_shares=integer_shares)
            desired = {order["instrument_id"]: order["target_shares"] for order in plan["orders"]}
            # rebalance_count reports trades, not attempts: should_rebalance
            # firing every bar while every target rounds to zero shares (a
            # tiny book against expensive holdings) is zero rebalances, not
            # one per bar. Tracked locally because the delta loop is the only
            # place that knows whether a share actually moved.
            executed_trade = False
            # sorted(...), not a bare set union: iteration order of a set of
            # strings varies with PYTHONHASHSEED, and float addition is not
            # associative, so cash/total_costs/traded_notional silently
            # depended on the interpreter's hash seed. Demonstrated: the same
            # frame and rule produced different bit patterns for all three
            # under different hash seeds. This module's own docstring opens
            # with "Deterministic: no clock, no network, no random" -- sorting
            # here is load-bearing, not cosmetic.
            for symbol in sorted(set(positions) | set(desired)):
                delta = desired.get(symbol, 0.0) - positions.get(symbol, 0.0)
                if abs(delta) < 1e-9:
                    continue
                executed_trade = True
                notional = abs(delta) * prices[symbol]
                cost = cost_model.total(shares=abs(delta), notional=notional)
                cash -= delta * prices[symbol] + cost
                result.traded_notional += notional
                result.total_costs += cost
            positions = {s: q for s, q in desired.items() if q > 0}
            if executed_trade:
                result.rebalance_count += 1
            value = cash + sum(qty * prices[sym] for sym, qty in positions.items())
        # This is the invariant metrics._validated depends on downstream, so
        # it is enforced where the value is produced, not only where it is
        # consumed. Costs have already been reserved before orders are sized.
        if not math.isfinite(value) or value <= 0:
            raise ValueError(
                f"bar {i} ({when}): portfolio value must be finite and positive, got {value}")
        result.curve.append((when, value))
        result.cash_history.append(cash)
        result.positions_history.append(dict(positions))
    return result

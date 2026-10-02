"""Research-only execution timing, separate from adoptable same-close Results.

At a signal close only a validated prefix is given to the rule. Its target
weights are frozen until the next common published row's close. Quantities are
then sized against that close and NAV with the shared funded sizing model.
This is a target-weight close simulation, not a market-on-open order or proof
that a broker could fill those quantities. Delaying execution can help or hurt.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from . import engine, metrics
from .frame import PriceFrame


def _signal_prefix(frame: PriceFrame, i: int) -> PriceFrame:
    """A trusted subset of an already validated immutable frame.

    A nonempty prefix preserves every PriceFrame invariant: symbols, row
    widths, positive finite prices and strict date ordering. Copying the tuple
    prefixes exposes no future rows, and avoids validating the same historical
    prices quadratically. The source frame is not retained on the new object.
    """
    prefix = object.__new__(PriceFrame)
    object.__setattr__(prefix, "dates", frame.dates[:i + 1])
    object.__setattr__(prefix, "symbols", frame.symbols)
    object.__setattr__(prefix, "closes", frame.closes[:i + 1])
    object.__setattr__(prefix, "price_basis", frame.price_basis)
    return prefix


@dataclass
class NextSessionResult:
    """Deliberately not an engine.Result; admission/adoption must not consume it."""
    rule_name: str
    parameters: dict[str, float]
    universe: tuple[str, ...]
    execution_scope: str = "research_only"
    curve: list[tuple[date, float]] = field(default_factory=list)
    cash_history: list[float] = field(default_factory=list)
    positions_history: list[dict[str, float]] = field(default_factory=list)
    signal_history: list[dict] = field(default_factory=list)
    execution_history: list[dict] = field(default_factory=list)
    unexecuted_tail_signal: dict | None = None
    traded_notional: float = 0.0
    total_costs: float = 0.0
    rebalance_count: int = 0
    rebalance_attempt_count: int = 0
    execution_assumptions: dict = field(default_factory=dict)

    @property
    def years(self) -> float:
        return ((self.curve[-1][0] - self.curve[0][0]).days / 365.25
                if len(self.curve) > 1 else 0.0)

    @property
    def average_value(self) -> float:
        return sum(value for _, value in self.curve) / len(self.curve) if self.curve else 0.0


def run_next_session_close(frame: PriceFrame, *, rule: engine.Rule, start_cash: float,
                           cost_model: engine.CostModel, cash_floor_pct: float,
                           integer_shares: bool = True) -> NextSessionResult:
    """Form targets at s, execute at the close of row s+1, never invent a tail fill."""
    if not isinstance(frame, PriceFrame):
        raise ValueError("execution sensitivity requires a validated PriceFrame")
    if type(rule.warmup_bars) is not int or rule.warmup_bars < 0:
        raise ValueError("warmup_bars must be a nonnegative integer")
    if not math.isfinite(start_cash) or start_cash <= 0:
        raise ValueError("start_cash must be finite and positive")
    if not 0 <= cash_floor_pct < 1:
        raise ValueError("cash_floor_pct must be in [0, 1)")
    result = NextSessionResult(rule_name=rule.name, parameters=dict(rule.parameters),
                               universe=frame.symbols)
    result.execution_assumptions = {
        "execution_price": "next_session_close", "signal_cutoff": "prior_session_close",
        "session_step": "next_common_published_row",
        "sizing": "frozen_target_weights_sized_at_execution_close",
        "rebalance_cadence": "completed_nonzero_trades",
        "price_basis": frame.price_basis,
        "cash_reserve": "fraction_of_pretrade_NAV_reserved_before_trade_costs",
        "disclosures": [
            "Research-only sensitivity: this result cannot be admitted or adopted.",
            "Targets use only the signal session and earlier closes; execution uses the next common published row's close, not the next open.",
            "Target weights are frozen, but funded quantities are calculated at the execution close; gaps, intraday prices and real fill availability are not modeled.",
            "Unexecuted final-session signals are retained as pending and incur no trades or costs.",
            "Cadence advances only on a nonzero fill at its execution session; the delay may also change subsequent rebalance triggers.",
            "Adjusted prices still imply synthetic share counts and approximate per-share fees; historical as-traded quantities are not reconstructed.",
            "Delayed execution may improve or worsen returns; these results are not a lower bound on performance.",
        ],
    }
    cash, positions, pending = float(start_cash), {}, None
    last_execution_index = None
    for i in range(rule.warmup_bars, len(frame)):
        prices = frame.row(i)
        if pending is not None:
            pretrade_value = cash + sum(q * prices[s] for s, q in positions.items())
            fill = engine._execute_at_close(
                targets=pending["targets"], prices=prices, positions=positions,
                cash=cash, cost_model=cost_model, cash_floor_pct=cash_floor_pct,
                integer_shares=integer_shares)
            cash, positions = fill.cash, fill.positions
            result.rebalance_attempt_count += 1
            result.traded_notional += fill.traded_notional
            result.total_costs += fill.total_costs
            if fill.executed:
                result.rebalance_count += 1
                last_execution_index = i
            result.execution_history.append({
                "signal_session": pending["signal_session"],
                "execution_session": frame.dates[i].isoformat(),
                "targets": dict(pending["targets"]), "executed": fill.executed,
                "pretrade_value": pretrade_value, "cash_after": cash,
                "cash_reserve": pretrade_value * cash_floor_pct,
                "traded_notional": fill.traded_notional, "costs": fill.total_costs,
            })
            pending = None
        value = cash + sum(q * prices[s] for s, q in positions.items())
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"session {frame.dates[i]}: portfolio value must be finite and positive")
        # A PriceFrame with no accessible future dates, rows or columns.
        signal_frame = _signal_prefix(frame, i)
        current = {s: q * prices[s] / value for s, q in positions.items()}
        if rule.should_rebalance(signal_frame, i, current, last_execution_index,
                                 cash_floor_pct=cash_floor_pct):
            targets = dict(rule.weights(signal_frame, i))
            engine._validate(targets, signal_frame)
            pending = {"signal_session": frame.dates[i].isoformat(), "targets": targets}
            result.signal_history.append({**pending, "targets": dict(targets)})
        result.curve.append((frame.dates[i], value))
        result.cash_history.append(cash)
        result.positions_history.append(dict(positions))
    result.unexecuted_tail_signal = pending
    return result


def _comparison_metrics(result, start_cash: float) -> dict:
    """Use the same initial cash anchor, including each mode's initial costs."""
    if not result.curve:
        return {"status": "insufficient_sessions"}
    final_value = result.curve[-1][1]
    peak, depth = float(start_cash), 0.0
    for _, value in result.curve:
        peak = max(peak, value)
        depth = max(depth, (peak - value) / peak)
    return {
        "status": "measured" if len(result.curve) > 1 else "one_session_only",
        "final_nav": final_value, "net_return": final_value / start_cash - 1,
        "cagr": ((final_value / start_cash) ** (1 / result.years) - 1
                 if result.years else None),
        "max_drawdown": depth, "total_costs": result.total_costs,
        "rebalance_count": result.rebalance_count,
        "rebalance_attempt_count": result.rebalance_attempt_count,
        "annual_turnover": metrics.annual_turnover(
            traded_notional=result.traded_notional, average_value=result.average_value,
            years=result.years),
    }


def compare_execution_timing(frame: PriceFrame, *, rule: engine.Rule, start_cash: float,
                             cost_model: engine.CostModel, cash_floor_pct: float,
                             integer_shares: bool = True) -> dict:
    """A paired research report; it deliberately contains no admission verdict."""
    # Both runs are built here: accepting an outside Result would leave its
    # starting cash and price matrix unbound to the proposed comparison.
    delayed = run_next_session_close(frame, rule=rule, start_cash=start_cash,
                                     cost_model=cost_model, cash_floor_pct=cash_floor_pct,
                                     integer_shares=integer_shares)
    same_bar_result = engine.run(frame, rule=rule, start_cash=start_cash,
                                 cost_model=cost_model, cash_floor_pct=cash_floor_pct,
                                 integer_shares=integer_shares)
    same, next_close = (_comparison_metrics(value, start_cash)
                        for value in (same_bar_result, delayed))
    delta = {key: next_close[key] - same[key] for key in ("final_nav", "net_return", "total_costs")
             if key in same and key in next_close}
    return {
        "execution_scope": "research_only", "adoption_eligible": False,
        "metric_basis": "same_initial_cash_including_entry_costs",
        "common_window": {"first_signal_session": delayed.curve[0][0].isoformat()
                          if delayed.curve else None,
                          "last_valuation_session": delayed.curve[-1][0].isoformat()
                          if delayed.curve else None, "sessions": len(delayed.curve)},
        "same_bar_close": same, "next_session_close": next_close,
        "next_minus_same": delta, "signal_count": len(delayed.signal_history),
        "unexecuted_tail_signal": delayed.unexecuted_tail_signal,
        "execution_assumptions": delayed.execution_assumptions,
    }

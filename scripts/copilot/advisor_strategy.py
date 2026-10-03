"""Deterministic advisor templates and separately frozen historical evidence.

These templates are candidates until current-code validation and an explicit
user adoption. Historical replay is evidence about that dataset, never a promise
of profit. Raw execution prices, adjusted signal prices and actual cash dividends
are separate. This module does not access accounts, send orders or adopt itself.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .backtest.engine import CostModel
from .backtest.metrics import annual_turnover, annual_volatility, cagr, max_drawdown, sharpe
from .data_calendar import CalendarUnavailable, MarketCalendar, iso, parse_time, utc_now
from .instruments import get_research_instrument, normalize_research_instrument, sector_of
from .signals import analyze

SCHEMA_VERSION = 1
SIZING_PROFILE = {
    "long_term_nav_cap_pct": .90, "swing_nav_cap_pct": .10,
    "max_stock_nav_pct": .05, "max_etf_nav_pct": .25,
    "max_swing_loss_nav_pct": .005, "integer_shares": True,
    "max_oneway_fee_pct": .01,
    "cash_floor_pct": .05, "sale_settlement_lag_sessions": 5,
    "max_drawdown_nav_pct": .10,
}
HISTORY_ATTESTATIONS = (
    "我已审核历史数据来源、交易日历、分红拆股、时点标的与冻结样本外区间",
    "I have reviewed the historical data sources, calendar, corporate actions, point-in-time universe and frozen holdout",
)
ADOPTION_CONFIRMATIONS = ("我明确采用此顾问策略", "I explicitly adopt this advisor strategy")
DEFAULT_COST_MODEL = CostModel(max_pct_of_notional=None)
_DEFAULTS = {
    "long_term_trend": {"trend_sessions": 200, "rebalance_sessions": 21},
    "swing_breakout": {"breakout_sessions": 20, "atr_stop_multiple": 2.0,
                       "max_holding_sessions": 20},
}


def _finite(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (not positive or value > 0))


def _hash(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                    ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _affirmation(statement, choices):
    return isinstance(statement, str) and statement.strip().rstrip(".。").strip() in choices


@dataclass(frozen=True)
class TemplateSpec:
    family: str
    universe: tuple[str, ...]
    parameters: dict | None = None

    def __post_init__(self):
        if self.family not in _DEFAULTS:
            raise ValueError("unsupported advisor template family")
        if not isinstance(self.universe, (tuple, list)) or not 1 <= len(self.universe) <= 24:
            raise ValueError("advisor universe must contain 1 to 24 US stocks/ETFs")
        universe = tuple(normalize_research_instrument(symbol) for symbol in self.universe)
        if len(set(universe)) != len(universe):
            raise ValueError("advisor universe must be unique")
        for symbol in universe:
            if get_research_instrument(symbol)["asset_class"] not in {"stock", "etf"}:
                raise ValueError("advisor templates accept US stocks/ETFs only")
        supplied = self.parameters if self.parameters is not None else {}
        if not isinstance(supplied, dict) or set(supplied) - set(_DEFAULTS[self.family]):
            raise ValueError("unknown template parameters")
        params = {**_DEFAULTS[self.family], **supplied}
        for key, value in params.items():
            if not _finite(value, positive=True):
                raise ValueError("template parameters must be finite positive numbers")
            if key != "atr_stop_multiple" and (not isinstance(value, int) or not 2 <= value <= 252):
                raise ValueError("session parameters must be integers from 2 to 252")
            if key == "atr_stop_multiple" and not .5 <= value <= 5:
                raise ValueError("ATR stop multiple must be between .5 and 5")
        object.__setattr__(self, "universe", universe)
        object.__setattr__(self, "parameters", params)

    @property
    def mode(self):
        return "long_term" if self.family == "long_term_trend" else "swing"

    def to_dict(self):
        return {"family": self.family, "universe": list(self.universe),
                "parameters": dict(self.parameters)}


def _spec(value) -> TemplateSpec:
    if isinstance(value, TemplateSpec):
        # Revalidate even if a caller mutated the parameters dictionary.
        return TemplateSpec(value.family, value.universe, value.parameters)
    if not isinstance(value, dict) or set(value) != {"family", "universe", "parameters"}:
        raise ValueError("malformed advisor template spec")
    return TemplateSpec(value["family"], value["universe"], value["parameters"])


def spec_hash(spec) -> str:
    return _hash({"schema_version": SCHEMA_VERSION, "spec": _spec(spec).to_dict(),
                  "sizing_profile": SIZING_PROFILE})


def history_hash(bundle) -> str:
    return _hash(bundle)


def runtime_fingerprint() -> str:
    """Hash public calculation dependencies, never personal files or config."""
    root = Path(__file__).resolve().parent
    paths = ("advisor_strategy.py", "signals.py", "market_data.py", "instruments.py", "trade_plan.py", "config.py",
             "data_calendar.py", "backtest/engine.py", "backtest/metrics.py", "backtest/frame.py")
    dependencies = {}
    for name in ("exchange_calendars", "tzdata"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = "unavailable"
    return _hash({"source": {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in paths},
                  "calendar_dependencies": dependencies})


def _weight(spec, symbol):
    asset = get_research_instrument(symbol)["asset_class"]
    cap = SIZING_PROFILE["long_term_nav_cap_pct" if spec.mode == "long_term" else "swing_nav_cap_pct"]
    single = SIZING_PROFILE["max_stock_nav_pct" if asset == "stock" else "max_etf_nav_pct"]
    # Denominator is the approved universe, not whichever names happen to enter.
    return min(cap / len(spec.universe), single)


def evaluate_candidate(spec, snapshot, *, now=None) -> dict:
    """One sealed completed-session snapshot; no quantities or broker limits."""
    spec = _spec(spec)
    observed = analyze(snapshot, list(spec.universe), spec.mode, now=now)
    results = {}
    evidence = {entry["evidence_id"]: entry for entry in snapshot["evidence"]}
    for symbol in spec.universe:
        item, signal = snapshot["instruments"][symbol], observed["signals"][symbol]
        metrics = item.get("indicators", {})
        ready = signal["signal_status"] != "data_insufficient"
        factor = (item["price"] / metrics["reference_close"]) if ready else None
        trigger, invalidation, protective, technical_exit = None, None, None, False
        direction, extra_missing = "wait", []
        if ready:
            reference = metrics["reference_close"]
            primary = item["verification"]["primary_evidence_id"]
            bars = evidence[primary]["bars"]
            closes = [bar["adjusted_close"] if metrics["basis"] == "total_return_adjusted" else bar["close"]
                      for bar in bars]
            lookback = spec.parameters.get("trend_sessions", spec.parameters.get("breakout_sessions"))
            if len(closes) < lookback + (1 if spec.mode == "swing" else 0):
                ready, extra_missing = False, ["template_lookback_insufficient"]
            elif spec.mode == "long_term":
                trigger = invalidation = sum(closes[-lookback:]) / lookback
                technical_exit = reference <= trigger
                direction = "exit" if technical_exit else "enter"
            else:
                trigger = max(closes[-lookback - 1:-1])
                invalidation = metrics.get("sma50")
                atr, sma200 = metrics.get("atr14"), metrics.get("sma200")
                if not all(_finite(value, positive=True) for value in (invalidation, atr, sma200)):
                    ready, extra_missing = False, ["template_indicator_missing"]
                else:
                    technical_exit = reference <= invalidation or reference <= sma200
                    protective = reference - spec.parameters["atr_stop_multiple"] * atr
                    if protective <= 0:
                        ready, extra_missing = False, ["protective_reference_nonpositive"]
                    else:
                        direction = "exit" if technical_exit else "enter" if reference > trigger and reference > invalidation > sma200 else "wait"
        if not ready:
            direction, factor, trigger, invalidation, protective, technical_exit = "unknown", None, None, None, None, False
        results[symbol] = {
            "instrument_id": symbol, "asset_class": item["asset_class"],
            "direction": direction, "technical_exit": technical_exit,
            "target_account_weight": _weight(spec, symbol),
            "reference_trigger_raw": trigger * factor if trigger is not None else None,
            "reference_invalidation_raw": invalidation * factor if invalidation is not None else None,
            "protective_reference_raw": protective * factor if protective is not None else None,
            "risk_loss_per_share": (metrics["reference_close"] - protective) * factor if protective is not None else None,
            "reference_conversion_factor": factor, "reference_basis": metrics.get("basis"),
            "reference_is_executable": False, "protective_policy": "fixed_at_entry_adjusted_for_splits" if spec.mode == "swing" else None,
            "cadence_sessions": spec.parameters.get("rebalance_sessions", 1),
            "max_holding_sessions": spec.parameters.get("max_holding_sessions"),
            "evaluation_session": item.get("latest_session"), "evidence_ids": signal["evidence_ids"],
            "missing": list(dict.fromkeys(signal["missing"] + extra_missing)),
            "sector": sector_of(symbol) if item["asset_class"] == "etf" else "unknown",
        }
    return {"schema_version": SCHEMA_VERSION, "kind": "advisor_strategy_candidate",
            "spec": spec.to_dict(), "spec_hash": spec_hash(spec), "mode": spec.mode,
            "snapshot_id": snapshot["snapshot_id"], "decision_at": snapshot["decision_at"],
            "valid_until": snapshot["valid_until"], "symbols": results,
            "evaluation_session": next(iter(results.values()))["evaluation_session"],
            "weight_policy": "equal_approved_universe_then_single_name_cap",
            "sizing_profile": dict(SIZING_PROFILE), "execution_scope": "research_only",
            "adoption_eligible": False,
            "disclosure": "研究参考经本session调整因子映射到raw价；不是实时报价或LMT。固定entry保护位不保证最大实际亏损。"}


def _cost(value):
    if value is None:
        value = DEFAULT_COST_MODEL
    if isinstance(value, dict):
        if set(value) != set(asdict(DEFAULT_COST_MODEL)):
            raise ValueError("cost model must declare all commission/spread fields")
        value = CostModel(**value)
    if not isinstance(value, CostModel):
        raise ValueError("cost_model must be CostModel or its complete field mapping")
    fields = asdict(value)
    if any(not _finite(number) or number < 0 for key, number in fields.items()
           if key != "max_pct_of_notional"):
        raise ValueError("cost fields must be finite and nonnegative")
    if value.max_pct_of_notional is not None:
        raise ValueError("advisor cost contract uses uncapped explicit commissions")
    if value.minimum_usd <= 0 or value.per_share_usd <= 0 or value.spread_bps <= 0:
        raise ValueError("advisor evidence requires nonzero commission and spread assumptions")
    return value


def cost_hash(cost_model=None) -> str:
    return _hash(asdict(_cost(cost_model)))


def _features(bars, spec):
    """Bounded prefix windows and Wilder ATR, using the public indicator basis."""
    adjusted = [bar["adjusted_close"] for bar in bars]
    atr = None
    ranges, values = [], []
    lookback = spec.parameters.get("trend_sessions", 200)
    breakout = spec.parameters.get("breakout_sessions", 20)
    for i, bar in enumerate(bars):
        close = adjusted[i]
        factor = close / bar["close"]
        high, low = bar["high"] * factor, bar["low"] * factor
        previous = adjusted[i - 1] if i else close
        ranges.append(max(high - low, abs(high - previous), abs(low - previous)))
        # Same Wilder seed as market_data: first 14 true ranges AFTER bar zero.
        if i == 14:
            atr = sum(ranges[1:15]) / 14
        elif i > 14:
            atr = (atr * 13 + ranges[-1]) / 14
        def mean(n):
            return sum(adjusted[i + 1 - n:i + 1]) / n if i + 1 >= n else None
        values.append({"close": close, "trend": mean(lookback), "sma50": mean(50),
                       "sma200": mean(200), "atr_raw": atr / factor if atr is not None else None,
                       "prior_high": max(adjusted[i - breakout:i]) if i >= breakout else None})
    return values


def _metrics(curve, trades=()):
    if len(curve) < 2:
        return {"sessions": len(curve), "net_return": 0.0, "cagr": 0.0,
                "max_drawdown": 0.0, "annual_volatility": 0.0, "sharpe_zero_rf": 0.0,
                "drawdown_duration_days": 0, "drawdown_peak": None, "drawdown_trough": None,
                "drawdown_recovery": None, "annual_turnover": 0.0,
                "total_traded_notional": 0.0, "total_cost": 0.0}
    drawdown = max_drawdown(curve)
    notional = sum(trade["quantity"] * trade["price"] for trade in trades)
    total_cost = sum(trade["fee"] for trade in trades)
    years = (curve[-1][0] - curve[0][0]).days / 365.2425
    return {"sessions": len(curve), "start": curve[0][0].isoformat(), "end": curve[-1][0].isoformat(),
            "net_return": curve[-1][1] / curve[0][1] - 1, "cagr": cagr(curve),
            "max_drawdown": drawdown.depth, "annual_volatility": annual_volatility(curve),
            "sharpe_zero_rf": sharpe(curve), "drawdown_duration_days": drawdown.duration_days,
            "drawdown_peak": drawdown.peak_date.isoformat() if drawdown.peak_date else None,
            "drawdown_trough": drawdown.trough_date.isoformat() if drawdown.trough_date else None,
            "drawdown_recovery": drawdown.recovery_date.isoformat() if drawdown.recovery_date else None,
            "annual_turnover": annual_turnover(traded_notional=notional,
                average_value=sum(nav for _, nav in curve) / len(curve), years=years),
            "total_traded_notional": notional, "total_cost": total_cost}


def _window_metrics(curve, trades, begin, end):
    before = [(day, nav) for day, nav in curve if day < begin]
    points = before[-1:] + [(day, nav) for day, nav in curve if begin <= day <= end]
    result = _metrics(points, [trade for trade in trades if begin.isoformat() <= trade["execution_session"] <= end.isoformat()])
    result.update(requested_start=begin.isoformat(), requested_end=end.isoformat(),
                  valuation_baseline_session=before[-1][0].isoformat() if before else None)
    return result


def _summary(report):
    return {key: value for key, value in report.items() if key != "trades"}


def _replay(spec, bars_by_symbol, cost, *, timing="next_session_close", cost_multiple=1, buy_and_hold=False,
            dividend_events=None, session_closes=None):
    """Close-only cash/share ledger. No leverage, intraday fills or auto orders.

    Split ratios multiply shares on their effective session. Ordinary cash
    entitlement freezes pre-ex raw shares, with payment released only after its
    explicit cash-availability clock. Adjusted closes form
    signals only; fees and valuation always use raw prices and actual shares.
    """
    symbols = spec.universe
    dates = [date.fromisoformat(bar["session"]) for bar in bars_by_symbol[symbols[0]]]
    values = {symbol: _features(bars_by_symbol[symbol], spec) for symbol in symbols}
    warmup = spec.parameters["trend_sessions"] - 1 if spec.mode == "long_term" else max(199, spec.parameters["breakout_sessions"])
    cash, settled_cash, settlements = 100_000.0, 100_000.0, []
    highwater_nav, drawdown_brake_count = 100_000.0, 0
    shares, stops, opened = dict.fromkeys(symbols, 0), {}, {}
    events = dividend_events if dividend_events is not None else {symbol: [] for symbol in symbols}
    if any(events.get(symbol) for symbol in symbols) and session_closes is None:
        raise ValueError("dividend replay requires verified session close clocks")
    if any(bar.get("dividend_cash_per_share", 0) != 0 for symbol in symbols for bar in bars_by_symbol[symbol]):
        raise ValueError("legacy per-payment-share scalar cannot establish dividend entitlement")
    dividend_receivables, dividend_audit, event_sources = {}, {}, {}
    fees, distributions, trades, curve, pending, last_fill = 0.0, 0.0, [], [(dates[warmup - 1], cash)], None, None

    def charge(quantity, price):
        return math.ceil(cost.total(shares=quantity, notional=quantity * price) * cost_multiple * 100 - 1e-10) / 100 if quantity else 0.0

    def execution(plan, i):
        nonlocal cash, settled_cash, fees, last_fill, drawdown_brake_count
        prices = {symbol: bars_by_symbol[symbol][i]["close"] for symbol in symbols}
        nav = cash + sum(shares[symbol] * prices[symbol] for symbol in symbols) + sum(dividend_receivables.values())
        starting_exposure = sum(shares[symbol] * prices[symbol] for symbol in symbols)
        starting_shares = dict(shares)
        starting_stops = dict(stops)
        fees_before = fees
        desired = {}
        for symbol in symbols:
            target = plan["targets"][symbol]
            # Swing hold is a lot, not daily NAV rebalancing or a moving stop.
            desired[symbol] = shares[symbol] if target == "hold" else math.floor(nav * target / prices[symbol])
            trigger = plan["triggers"].get(symbol)
            if trigger is not None and i > plan["index"]:
                trigger /= bars_by_symbol[symbol][i]["split_ratio"]
            invalidation = plan["invalidations"].get(symbol)
            if invalidation is not None and i > plan["index"]:
                invalidation /= bars_by_symbol[symbol][i]["split_ratio"]
            if ((trigger is not None and prices[symbol] < trigger) or (invalidation is not None and prices[symbol] <= invalidation)) and desired[symbol] > shares[symbol]:
                desired[symbol] = shares[symbol]
            if symbol in plan["stops"]:
                stop = plan["stops"][symbol]
                # Pending signal predates execution-day split; compare raw units.
                if i > plan["index"]:
                    stop /= bars_by_symbol[symbol][i]["split_ratio"]
                if prices[symbol] <= stop:
                    desired[symbol] = 0
                else:
                    low, high = 0, desired[symbol]
                    while low < high:
                        mid = (low + high + 1) // 2
                        loss = mid * (prices[symbol] - stop) + charge(mid, prices[symbol]) + charge(mid, stop)
                        if loss <= nav * SIZING_PROFILE["max_swing_loss_nav_pct"]:
                            low = mid
                        else:
                            high = mid - 1
                    desired[symbol] = low
        any_fill = False
        for symbol in symbols:  # sell first, then fund buys without spending another mode's NAV
            quantity = max(0, shares[symbol] - desired[symbol])
            if not quantity:
                continue
            fee = charge(quantity, prices[symbol])
            net = quantity * prices[symbol] - fee
            if net < 0 and settled_cash + net < 0:
                continue
            cash += net
            if net < 0:
                settled_cash += net
            else:
                settlements.append((i + SIZING_PROFILE["sale_settlement_lag_sessions"], net))
            shares[symbol] -= quantity
            fees += fee
            any_fill = True
            trades.append({"symbol": symbol, "side": "sell", "quantity": quantity, "price": prices[symbol],
                           "fee": fee, "signal_session": dates[plan["index"]].isoformat(),
                           "execution_session": dates[i].isoformat()})
            if shares[symbol] == 0:
                stops.pop(symbol, None)
                opened.pop(symbol, None)
        mode_cap = SIZING_PROFILE["long_term_nav_cap_pct" if spec.mode == "long_term" else "swing_nav_cap_pct"]
        reserve = nav * SIZING_PROFILE["cash_floor_pct"]
        requested = {symbol: max(0, desired[symbol] - shares[symbol]) for symbol in symbols}
        if not buy_and_hold and nav <= highwater_nav * (1 - SIZING_PROFILE["max_drawdown_nav_pct"]):
            if any(requested.values()):
                drawdown_brake_count += 1
            requested = dict.fromkeys(symbols, 0)

        def funded(scale):
            quantities = {symbol: math.floor(requested[symbol] * scale) for symbol in symbols}
            notional = sum(quantities[symbol] * prices[symbol] for symbol in symbols)
            buy_fees = sum(charge(quantities[symbol], prices[symbol]) for symbol in symbols)
            post_nav = nav - (fees - fees_before) - buy_fees
            if post_nav <= 0 or notional + buy_fees > max(0, settled_cash - reserve) or starting_exposure + notional > post_nav * mode_cap:
                return False, quantities
            loss = sum(max(0, prices[symbol] - starting_stops[symbol]) * starting_shares[symbol]
                       + charge(starting_shares[symbol], starting_stops[symbol]) for symbol in starting_stops)
            for symbol, quantity in quantities.items():
                if not quantity:
                    continue
                asset = get_research_instrument(symbol)["asset_class"]
                single = SIZING_PROFILE["max_stock_nav_pct" if asset == "stock" else "max_etf_nav_pct"]
                if (starting_shares[symbol] + quantity) * prices[symbol] > post_nav * single:
                    return False, quantities
                if symbol in plan["stops"]:
                    stop = plan["stops"][symbol] / (bars_by_symbol[symbol][i]["split_ratio"] if i > plan["index"] else 1)
                    loss += quantity * (prices[symbol] - stop) + charge(quantity, prices[symbol]) + charge(quantity, stop)
            if spec.mode == "swing" and loss > nav * SIZING_PROFILE["max_swing_loss_nav_pct"]:
                return False, quantities
            return True, quantities

        ok, quantities = funded(1.)
        if not ok:
            lower, upper = 0., 1.
            for _ in range(64):
                middle = (lower + upper) / 2
                if funded(middle)[0]:
                    lower = middle
                else:
                    upper = middle
            _, quantities = funded(lower)
        for symbol, quantity in quantities.items():
            if not quantity:
                continue
            fee = charge(quantity, prices[symbol])
            if fee > quantity * prices[symbol] * SIZING_PROFILE["max_oneway_fee_pct"]:
                continue
            cash -= quantity * prices[symbol] + fee
            settled_cash -= quantity * prices[symbol] + fee
            if shares[symbol] == 0:
                opened[symbol] = i
                if symbol in plan["stops"]:
                    stops[symbol] = plan["stops"][symbol] / (bars_by_symbol[symbol][i]["split_ratio"] if i > plan["index"] else 1)
            shares[symbol] += quantity
            fees += fee
            any_fill = True
            trades.append({"symbol": symbol, "side": "buy", "quantity": quantity, "price": prices[symbol],
                           "fee": fee, "signal_session": dates[plan["index"]].isoformat(),
                           "execution_session": dates[i].isoformat(),
                           "frozen_trigger_raw": plan["triggers"].get(symbol),
                           "fixed_protective_raw": stops.get(symbol)})
        if any_fill:
            last_fill = i

    def signal(i):
        if buy_and_hold:
            return {"index": i, "targets": {symbol: _weight(spec, symbol) for symbol in symbols}, "stops": {}, "triggers": {}, "invalidations": {}} if i == warmup else None
        if spec.mode == "long_term" and last_fill is not None and i - last_fill < spec.parameters["rebalance_sessions"]:
            return None
        targets, entry_stops, triggers, invalidations = {}, {}, {}, {}
        for symbol in symbols:
            feature = values[symbol][i]
            if spec.mode == "long_term":
                targets[symbol] = _weight(spec, symbol) if feature["close"] > feature["trend"] else 0.0
                if targets[symbol] > 0:
                    triggers[symbol] = feature["trend"] * bars_by_symbol[symbol][i]["close"] / feature["close"]
                    invalidations[symbol] = triggers[symbol]
                continue
            raw_close = bars_by_symbol[symbol][i]["close"]
            technical_exit = feature["close"] <= feature["sma50"] or feature["close"] <= feature["sma200"]
            if shares[symbol]:
                exit_lot = technical_exit or raw_close <= stops[symbol] or i - opened[symbol] >= spec.parameters["max_holding_sessions"]
                targets[symbol] = 0.0 if exit_lot else "hold"
            elif feature["close"] > feature["prior_high"] and feature["close"] > feature["sma50"] > feature["sma200"]:
                stop = raw_close - spec.parameters["atr_stop_multiple"] * feature["atr_raw"]
                targets[symbol] = _weight(spec, symbol) if stop > 0 else 0.0
                if stop > 0:
                    entry_stops[symbol] = stop
                    triggers[symbol] = feature["prior_high"] * raw_close / feature["close"]
                    invalidations[symbol] = feature["sma50"] * raw_close / feature["close"]
            else:
                targets[symbol] = 0.0
        return {"index": i, "targets": targets, "stops": entry_stops, "triggers": triggers, "invalidations": invalidations}

    for i in range(warmup, len(dates)):
        matured = [amount for release, amount in settlements if release <= i]
        settled_cash += sum(matured)
        settlements[:] = [(release, amount) for release, amount in settlements if release > i]
        for symbol in symbols:
            for event in events.get(symbol, []):
                if event["ex_session"] != dates[i].isoformat():
                    continue
                eid = event["event_id"]
                if eid in dividend_audit:
                    raise ValueError("duplicate dividend event")
                amount = shares[symbol] * event["amount_per_pre_ex_share"]
                dividend_receivables[eid] = amount
                event_sources[eid] = event
                dividend_audit[eid] = {"event_id": eid, "symbol": symbol, "ex_session": event["ex_session"],
                    "payment_session": event["payment_session"], "known_at": event["known_at"],
                    "cash_available_at": event["cash_available_at"], "eligible_pre_ex_raw_shares": shares[symbol],
                    "amount_per_pre_ex_share": event["amount_per_pre_ex_share"], "entitlement_usd": amount,
                    "cash_credited_usd": 0., "credited_session": None,
                    "status": "receivable" if amount else "not_entitled"}
        if i > warmup:
            for symbol in symbols:
                bar = bars_by_symbol[symbol][i]
                shares[symbol] *= int(bar["split_ratio"])
                if symbol in stops:
                    stops[symbol] /= bar["split_ratio"]
        for eid, amount in list(dividend_receivables.items()):
            if parse_time(event_sources[eid]["cash_available_at"]) <= parse_time(session_closes[dates[i].isoformat()]):
                cash += amount
                settled_cash += amount
                distributions += amount
                del dividend_receivables[eid]
                dividend_audit[eid].update(cash_credited_usd=amount, credited_session=dates[i].isoformat(),
                                          status="credited" if amount else "not_entitled")
        if pending is not None:
            marked_nav = cash + sum(shares[symbol] * bars_by_symbol[symbol][i]["close"] for symbol in symbols) + sum(dividend_receivables.values())
            highwater_nav = max(highwater_nav, marked_nav)
            execution(pending, i)
            pending = None
        else:
            highwater_nav = max(highwater_nav, cash + sum(shares[symbol] * bars_by_symbol[symbol][i]["close"] for symbol in symbols) + sum(dividend_receivables.values()))
        plan = signal(i)
        if timing == "same_session_close" and plan is not None:
            execution(plan, i)
        elif plan is not None:
            pending = plan
        nav = cash + sum(shares[symbol] * bars_by_symbol[symbol][i]["close"] for symbol in symbols) + sum(dividend_receivables.values())
        if not math.isfinite(nav) or nav <= 0 or cash < -1e-7:
            raise ValueError("historical ledger became insolvent")
        curve.append((dates[i], nav))
    report = {**_metrics(curve, trades), "fees_paid": fees, "cash_distributions_received": distributions,
              "trade_count": len(trades), "trades": trades, "terminal_cash": cash,
              "terminal_settled_cash": settled_cash,
              "terminal_unsettled_receivables": sum(amount for _, amount in settlements),
              "dividend_entitlements_usd": sum(item["entitlement_usd"] for item in dividend_audit.values()),
              "terminal_dividend_receivables_usd": sum(dividend_receivables.values()),
              "dividend_entitlement_events": list(dividend_audit.values()),
              "terminal_shares": shares, "tail_pending_unexecuted": pending is not None,
              "highwater_nav_usd": highwater_nav, "drawdown_brake_count": drawdown_brake_count,
              "terminal_liquidation": False}
    return report, curve


def _local_midnight(calendar, instrument, session):
    if hasattr(calendar, "local_midnight"):
        # An explicit isolated calendar seam, never exposed by CLI/MCP.
        return parse_time(calendar.local_midnight(instrument, session))
    try:
        return parse_time(datetime.combine(date.fromisoformat(session), time.min, ZoneInfo(instrument["timezone"])))
    except ZoneInfoNotFoundError as exc:
        raise CalendarUnavailable("verified exchange timezone unavailable") from exc


def _dividend_issues(spec, bundle, current, calendar):
    events = bundle.get("dividend_events")
    if not isinstance(events, dict) or set(events) != set(spec.universe):
        return ["dividend_events_explicit_universe_missing"]
    provenance = bundle.get("provenance", {})
    sources = provenance.get("source_evidence", []) if isinstance(provenance, dict) else []
    source_ids = [entry.get("id") for entry in sources if isinstance(entry, dict) and isinstance(entry.get("id"), str)] if isinstance(sources, list) else []
    issues, seen = [], set()
    if len(set(source_ids)) != len(source_ids):
        issues.append("dividend_source_evidence_ambiguous")
    for symbol in spec.universe:
        declared = events[symbol]
        if not isinstance(declared, list):
            issues.append("dividend_events_malformed:" + symbol)
            continue
        bars = bundle["bars"][symbol]
        lookup = {bar["session"]: i for i, bar in enumerate(bars) if isinstance(bar, dict) and isinstance(bar.get("session"), str)} if isinstance(bars, list) else {}
        daily_rates = {}
        for event in declared:
            try:
                if not isinstance(event, dict) or not isinstance(event.get("event_id"), str) or not event["event_id"]:
                    raise ValueError("missing event identity")
                if event["event_id"] in seen:
                    issues.append("dividend_event_id_duplicate:" + symbol)
                seen.add(event["event_id"])
                if (event.get("distribution_type") != "ordinary_cash" or event.get("currency") != "USD"
                        or event.get("unit") != "USD_per_pre_ex_raw_share"):
                    issues.append("dividend_distribution_or_share_basis_unsupported:" + symbol)
                if event.get("source_evidence_id") not in source_ids:
                    issues.append("dividend_source_evidence_missing:" + symbol)
                amount = event["amount_per_pre_ex_share"]
                if not _finite(amount, positive=True):
                    raise ValueError("dividend amount unknown")
                ex, payment = date.fromisoformat(event["ex_session"]), date.fromisoformat(event["payment_session"])
                if event["ex_session"] != ex.isoformat() or event["payment_session"] != payment.isoformat():
                    raise ValueError("noncanonical corporate action date")
                position = lookup.get(event["ex_session"])
                if position is None or position == 0:
                    issues.append("dividend_pre_ex_history_missing:" + symbol)
                    continue
                instrument = get_research_instrument(symbol)
                if payment < ex or not calendar.is_session(instrument, ex.isoformat()) or not calendar.is_session(instrument, payment.isoformat()):
                    issues.append("dividend_dates_or_due_bill_unsupported:" + symbol)
                known, available = parse_time(event["known_at"]), parse_time(event["cash_available_at"])
                ex_boundary = _local_midnight(calendar, instrument, ex.isoformat())
                pay_start = _local_midnight(calendar, instrument, payment.isoformat())
                pay_end = _local_midnight(calendar, instrument, (payment + timedelta(days=1)).isoformat())
                if known >= ex_boundary or known > current:
                    issues.append("dividend_amount_not_known_before_ex:" + symbol)
                if not pay_start <= available < pay_end or available > current or available < known:
                    issues.append("dividend_cash_availability_unknown:" + symbol)
                daily_rates[ex.isoformat()] = daily_rates.get(ex.isoformat(), 0.) + amount
            except CalendarUnavailable:
                issues.append("dividend_calendar_or_timezone_unavailable:" + symbol)
            except (KeyError, TypeError, ValueError, AttributeError, OverflowError):
                issues.append("dividend_event_semantics_unknown:" + symbol)
        for ex, amount in daily_rates.items():
            prior_bar = bars[lookup[ex] - 1]
            prior = prior_bar.get("close") if isinstance(prior_bar, dict) else None
            if not _finite(prior, positive=True) or amount >= prior * .25:
                issues.append("dividend_large_distribution_unsupported:" + symbol)
    return list(dict.fromkeys(issues))


def _history_issues(spec, bundle, expected, freeze, current, calendar=None):
    issues = []
    if not isinstance(bundle, dict) or bundle.get("schema_version") != 1 or not isinstance(bundle.get("bars"), dict):
        return ["history_schema_invalid"], None
    bars_by_symbol = bundle["bars"]
    if set(bars_by_symbol) != set(spec.universe):
        return ["history_universe_mismatch"], None
    dates = None
    calendar = calendar if calendar is not None else MarketCalendar()
    for symbol in spec.universe:
        bars = bars_by_symbol[symbol]
        if not isinstance(bars, list) or len(bars) < 253:
            issues.append("history_too_short:" + symbol)
            continue
        local_dates = []
        for bar in bars:
            if not isinstance(bar, dict):
                issues.append("bar_shape_invalid:" + symbol)
                break
            try:
                session = date.fromisoformat(bar["session"])
                available = parse_time(bar["available_at"])
                if available.date() != session or available > current:
                    issues.append("bar_availability_after_session:" + symbol)
                    break
                instrument = get_research_instrument(symbol)
                if not calendar.is_session(instrument, bar["session"]):
                    issues.append("bar_not_exchange_session:" + symbol)
                    break
                if available < parse_time(calendar.close(instrument, bar["session"])):
                    issues.append("bar_available_before_session_close:" + symbol)
                    break
                local_dates.append(session)
                fields = ("open", "high", "low", "close", "adjusted_close", "split_ratio")
                if not all(_finite(bar.get(key), positive=True) for key in fields):
                    raise ValueError("nonfinite prices/actions")
                if "dividend_cash_per_share" in bar and bar["dividend_cash_per_share"] != 0:
                    issues.append("legacy_dividend_cash_scalar_unsupported:" + symbol)
                if not bar["low"] <= min(bar["open"], bar["close"]) <= max(bar["open"], bar["close"]) <= bar["high"]:
                    raise ValueError("inconsistent OHLC")
                if bar["split_ratio"] != 1 and abs(bar["split_ratio"] - round(bar["split_ratio"])) > 1e-8:
                    # Fractional reverse-split cash-in-lieu needs a separate ledger contract.
                    raise ValueError("unsupported reverse/fractional split")
            except CalendarUnavailable:
                issues.append("calendar_unavailable:" + symbol)
                break
            except (KeyError, TypeError, ValueError, OverflowError):
                issues.append("bar_semantics_invalid:" + symbol)
                break
        if len(local_dates) != len(bars):
            continue
        if any(right <= left for left, right in zip(local_dates, local_dates[1:])):
            issues.append("sessions_not_strictly_increasing:" + symbol)
        if dates is None:
            dates = local_dates
        elif local_dates != dates:
            issues.append("unaligned_listing_or_calendar_history:" + symbol)
    provenance = bundle.get("provenance", {})
    if not isinstance(provenance, dict):
        provenance = {}
    for field in ("point_in_time_verified", "calendar_verified", "corporate_actions_complete", "survivorship_verified"):
        if provenance.get(field) is not True:
            issues.append(field + "_missing")
    if provenance.get("universe_method") not in {"point_in_time_membership", "predeclared_fixed_universe"}:
        issues.append("universe_selection_unverified")
    if not isinstance(provenance.get("universe_evidence"), str) or not provenance["universe_evidence"].strip():
        issues.append("universe_evidence_missing")
    evidence = provenance.get("source_evidence")
    if not isinstance(evidence, list) or not evidence or any(not isinstance(entry, dict)
        or not isinstance(entry.get("id"), str) or not entry["id"]
        or not isinstance(entry.get("url"), str) or not entry["url"].startswith("https://")
        or not isinstance(entry.get("sha256"), str) or len(entry["sha256"]) != 64
        or any(char not in "0123456789abcdef" for char in entry["sha256"]) for entry in evidence):
        issues.append("source_hash_evidence_missing")
    if provenance.get("data_role") not in {"fixture", "market"}:
        issues.append("history_data_role_unknown")
    if provenance.get("dividend_event_basis") != "pre_ex_entitlement_with_explicit_cash_availability":
        issues.append("dividend_entitlement_event_basis_unverified")
    if provenance.get("dividend_events_complete") is not True:
        issues.append("dividend_event_coverage_unverified")
    issues.extend(_dividend_issues(spec, bundle, current, calendar))
    if provenance.get("execution_price_basis") != "raw_unadjusted_usd":
        issues.append("execution_raw_price_basis_unverified")
    if provenance.get("indicator_price_basis") != "total_return_adjusted":
        issues.append("indicator_adjusted_price_basis_unverified")
    if dates:
        warmup = spec.parameters["trend_sessions"] - 1 if spec.mode == "long_term" else max(199, spec.parameters["breakout_sessions"])
        replay_start = dates[warmup]
        if (dates[-1] - replay_start).days / 365.2425 < 15:
            issues.append("validated_span_less_than_15_years")
        complete_years = range(replay_start.year + (replay_start != date(replay_start.year, 1, 1)), dates[-1].year)
        if not isinstance(expected, dict) or any(not isinstance(year, int) or isinstance(year, bool)
            or not isinstance(count, int) or isinstance(count, bool) or count <= 0 for year, count in expected.items()):
            issues.append("expected_calendar_counts_missing")
            expected = {}
        for year in sorted(set(complete_years) | {2008, 2020, 2022}):
            actual = [session for session in dates if session.year == year and session >= replay_start]
            total = expected.get(year)
            try:
                cursor = date(year, 1, 1)
                actual_expected = 0
                while cursor.year == year:
                    actual_expected += bool(calendar.is_session(get_research_instrument(spec.universe[0]), cursor.isoformat()))
                    cursor += timedelta(days=1)
                if total is not None and total != actual_expected:
                    issues.append("expected_calendar_count_mismatch:" + str(year))
            except CalendarUnavailable:
                issues.append("calendar_unavailable:" + str(year))
            if total is None or not .99 <= len(actual) / total <= 1:
                issues.append("session_coverage_insufficient:" + str(year))
            if year in {2008, 2020, 2022} and {session.month for session in actual} != set(range(1, 13)):
                issues.append("stress_year_months_missing:" + str(year))
    if not isinstance(freeze, dict):
        return issues + ["freeze_manifest_missing"], dates
    for field, actual in (("spec_hash", spec_hash(spec)), ("code_hash", runtime_fingerprint()), ("data_hash", history_hash(bundle))):
        if freeze.get(field) != actual:
            issues.append("freeze_" + field.split("_")[0] + "_mismatch")
    if freeze.get("kind") not in {"current_frozen_replay", "prospective"}:
        issues.append("freeze_kind_unknown")
    if freeze.get("holdout_locked") is not True or freeze.get("holdout_tuned") is not False:
        issues.append("holdout_not_locked_or_tuned")
    try:
        locked = parse_time(freeze["locked_at"])
        train_end = date.fromisoformat(freeze["training_end"])
        oos_start, oos_end = date.fromisoformat(freeze["oos_start"]), date.fromisoformat(freeze["oos_end"])
        if locked > current or not train_end < oos_start <= oos_end or (oos_end - oos_start).days / 365.2425 < 2:
            issues.append("freeze_chronology_or_oos_span_invalid")
        if dates and (train_end < dates[0] or oos_start <= replay_start or oos_end > dates[-1]):
            issues.append("oos_window_outside_validated_history")
        if freeze.get("kind") == "prospective" and locked.date() >= oos_start:
            issues.append("prospective_freeze_must_precede_oos")
    except (KeyError, TypeError, ValueError):
        issues.append("freeze_times_invalid")
    return list(dict.fromkeys(issues)), dates


def validate_history(spec, history_bundle, *, freeze_manifest, expected_sessions,
                     cost_model=None, data_attestation=None, calendar=None, now=None) -> dict:
    """Recompute evidence. Provenance assertions require external audit/support.

    No serialized caller-supplied performance is trusted. A current frozen replay
    is historical holdout replay, not a prospective live test. Fixture admission
    exercises gates but is forbidden at the explicit adoption boundary.
    """
    spec, cost = _spec(spec), _cost(cost_model)
    current = parse_time(now if now is not None else utc_now())
    if data_attestation is not None and not _affirmation(data_attestation, HISTORY_ATTESTATIONS):
        raise ValueError("data_attestation must be an independent explicit user review statement")
    issues, dates = _history_issues(spec, history_bundle, expected_sessions, freeze_manifest, current, calendar)
    if not isinstance(freeze_manifest, dict) or freeze_manifest.get("cost_hash") != cost_hash(cost):
        issues.append("freeze_cost_mismatch")
    report = {"schema_version": SCHEMA_VERSION, "kind": "advisor_strategy_validation",
              "spec": spec.to_dict(), "spec_hash": spec_hash(spec), "code_hash": runtime_fingerprint(),
              "data_hash": history_hash(history_bundle), "freeze": copy.deepcopy(freeze_manifest),
              "expected_sessions": {str(k): v for k, v in expected_sessions.items()} if isinstance(expected_sessions, dict) else {},
              "cost_model": asdict(cost), "sizing_profile": dict(SIZING_PROFILE),
              "cost_hash": cost_hash(cost), "currency": "USD", "initial_nav": 100_000.0, "initial_nav_usd": 100_000.0,
              "excluded_account_overlays": [
                  "personal_account_capital_and_integer_share_minimum_fee_nonlinearity",
                  "existing_personal_positions_fractional_shares_and_actual_entry_lots",
                  "other_mode_allocations_shared_sector_limits_and_lookthrough_overlap",
                  "external_cash_flows_and_actual_broker_nav_history_baselines",
                  "live_pending_orders_cash_reservations_and_actual_settlement_calendar",
                  "actual_broker_fees_spreads_tick_sizes_liquidity_market_impact_taxes_and_fx",
              ],
              "execution_price_basis": "raw_unadjusted_usd", "indicator_price_basis": "total_return_adjusted",
              "data_role": history_bundle.get("provenance", {}).get("data_role") if isinstance(history_bundle, dict) and isinstance(history_bundle.get("provenance"), dict) else None,
              "execution_assumptions": {"execution_timing": "next_session_close", "signals": "previous_completed_session",
                  "tail_signal": "unexecuted", "raw_share_fees": True,
                  "dividends": "pre_ex_raw_share_entitlement_fixed_usd_receivable_to_explicit_available_cash",
                  "dividend_scope": "ordinary_cash_below_25pct_gross_pretax_no_stock_distribution_due_bill_or_default",
                  "dividend_publication": "known_before_exchange_local_midnight_on_ex_session",
                  "fee_rounding": "up_to_usd_cent",
                  "execution_sizing": "frozen_signal_weights_integer_shares_at_execution_close_idealized",
                  "entry_condition": "execution_close_at_or_above_frozen_trigger_and_above_frozen_invalidation",
                  "swing_risk_budget": "existing_fixed_stop_lots_plus_new_entry_round_trip_costs_no_same_card_exit_credit",
                  "drawdown_new_risk_brake": "nav_at_or_below_90pct_nonreset_highwater_blocks_only_new_buys",
                  "sale_proceeds": "receivable_then_fixed_5_published_session_hold_not_statutory_settlement_calendar",
                  "buy_funding": "execution_start_settled_cash_less_total_nav_reserve",
                  "exposure_capacity": "pre_execution_holdings_no_same_card_sales_release",
                  "buy_sizing": "common_basket_scale_post_cost_nav_caps_no_sector_or_pending_orders_in_history",
                  "protective_exits": "daily_close_signal_then_next_close_no_intraday_stop_guarantee",
                  "rebalance_cadence": "completed_nonzero_trades", "terminal_liquidation": False},
              "replay": None, "oos": None, "stress_years": {}, "sensitivity": {}, "benchmarks": {},
              "issues": issues, "admitted": False, "market_validated": False,
              "source_authenticated": False, "data_reviewed": data_attestation is not None,
              "source_authentication": "human_reviewed_local_manifest; not_network_certified" if data_attestation is not None else "local_manifest_declared; not_network_certified",
              "adoption_eligible": False,
              "admission_scope": "coverage_costs_frozen_reproducibility_drawdown_limit_not_profit_or_optimality",
              "disclosures": ["Historical net returns do not ensure future profits.",
                  "Current frozen replay is not prospective forward evidence or proof of an untuned holdout.",
                  "Provenance/calendar/universe assertions and source hashes require independent audit; no external source is fetched here.",
                  "An independent CLI user attestation records review of sources, calendars, corporate actions, universe and frozen holdout; it does not authenticate market data.",
                  "Close-only sensitivity is neither next-open execution nor a guaranteed return lower bound.",
                  "No liquidity/market-impact/tax/FX/borrow model; raw daily marks and an explicit cost model only.",
                  "Mode allocation, integer shares and fees are bound; live broker/manual preflight remains necessary."]}
    report["disclosures"].append("Single-mode simulation keeps other sleeves in cash; no joint mixed-account validation. Sale proceeds are conservatively held five published sessions, not actual historical statutory settlement dates.")
    report["disclosures"].append("Initial NAV is USD 100,000. Whole shares and minimum fees are nonlinear with capital; returns are not validation of a smaller personal account. Live sector, other-mode, broker and flow overlays can restrict trades further.")
    report["disclosures"].append("Ordinary cash entitlements are gross/pre-tax USD amounts per pre-ex raw share. Ex-session rights are separate from payment cash; special 25%+ distributions, stock distributions, due bills, issuer default and unknown payment availability are unsupported. Same-day pre-open announcements are conservatively rejected; no timezone is guessed.")
    if not issues:
        try:
            bars = history_bundle["bars"]
            verified_calendar = calendar if calendar is not None else MarketCalendar()
            clocks = {bar["session"]: verified_calendar.close(get_research_instrument(spec.universe[0]), bar["session"])
                      for bar in bars[spec.universe[0]]} if any(history_bundle["dividend_events"].values()) else None
            replay_arguments = {"dividend_events": history_bundle["dividend_events"], "session_closes": clocks}
            replay, curve = _replay(spec, bars, cost, **replay_arguments)
            same, _ = _replay(spec, bars, cost, timing="same_session_close", **replay_arguments)
            doubled, _ = _replay(spec, bars, cost, cost_multiple=2, **replay_arguments)
            begin, end = date.fromisoformat(freeze_manifest["oos_start"]), date.fromisoformat(freeze_manifest["oos_end"])
            report.update(replay=replay, oos=_window_metrics(curve, replay["trades"], begin, end),
                          stress_years={str(year): _window_metrics(curve, replay["trades"], date(year, 1, 1), date(year, 12, 31)) for year in (2008, 2020, 2022)},
                          sensitivity={"same_session_close": _summary(same), "double_cost": _summary(doubled)})
            basket, basket_curve = _replay(spec, bars, cost, buy_and_hold=True, **replay_arguments)
            report["benchmarks"] = {"buy_and_hold_basket": {**_summary(basket),
                "oos": _window_metrics(basket_curve, basket["trades"], begin, end), "currency": "USD",
                "allocation": "equal_approved_universe_same_mode_and_single_name_caps_cash_dividends_not_auto_reinvested"},
                "cash": {**_metrics([(day, 100_000.) for day, _ in curve]), "currency": "USD", "interest_assumption": "zero"}}
            variants = []
            for key, value in spec.parameters.items():
                step = .25 if key == "atr_stop_multiple" else max(1, round(value * .1))
                bounds = (.5, 5.) if key == "atr_stop_multiple" else (2, 252)
                for candidate in sorted({max(bounds[0], value - step), min(bounds[1], value + step)} - {value}):
                    parameters = {**spec.parameters, key: candidate}
                    variant = TemplateSpec(spec.family, spec.universe, parameters)
                    variant_replay, variant_curve = _replay(variant, bars, cost, **replay_arguments)
                    variants.append({"parameter": key, "value": candidate, "parameters": parameters,
                        "spec_hash": spec_hash(variant), "metrics": _summary(variant_replay),
                        "oos": _window_metrics(variant_curve, variant_replay["trades"], begin, end)})
            report["sensitivity"]["parameters"] = {"method": "one_parameter_at_a_time_fixed_10pct_sessions_or_0.25atr",
                "selection": "none", "oos_tuning": False, "variants": variants,
                "warmup_disclosure": "Full-period starts follow each variant lookback; OOS uses the same frozen window and prior-session valuation baseline."}
            if replay["trade_count"] == 0:
                report["issues"].append("no_completed_trades")
            if report["oos"]["sessions"] < 2:
                report["issues"].append("oos_metrics_missing")
            if replay["max_drawdown"] > .5 or report["oos"]["max_drawdown"] > .5:
                report["issues"].append("maximum_drawdown_exceeds_50_percent")
        except (ValueError, ArithmeticError, KeyError, TypeError) as exc:
            report["issues"].append("replay_failed:" + type(exc).__name__)
    report["admitted"] = not report["issues"]
    report["adoption_eligible"] = report["admitted"] and report["data_role"] == "market" and report["data_reviewed"]
    report["validation_id"] = "validation-" + _hash(report)
    return report


def _verify_validation(validation, spec):
    if not isinstance(validation, dict):
        raise ValueError("malformed validation")
    body = {key: value for key, value in validation.items() if key != "validation_id"}
    if validation.get("validation_id") != "validation-" + _hash(body):
        raise ValueError("validation content digest mismatch")
    if (validation.get("kind") != "advisor_strategy_validation" or validation.get("schema_version") != SCHEMA_VERSION
            or validation.get("spec") != spec.to_dict() or validation.get("spec_hash") != spec_hash(spec)
            or validation.get("code_hash") != runtime_fingerprint() or validation.get("sizing_profile") != SIZING_PROFILE
            or validation.get("admitted") is not True or validation.get("adoption_eligible") is not True
            or validation.get("market_validated") is not False or validation.get("source_authenticated") is not False
            or validation.get("data_reviewed") is not True or validation.get("data_role") != "market"
            or validation.get("source_authentication") != "human_reviewed_local_manifest; not_network_certified"
            or validation.get("issues") != [] or validation.get("replay") is None or validation.get("oos") is None):
        raise ValueError("validation is not current admissible market evidence")
    cost = _cost(validation.get("cost_model"))
    freeze = validation.get("freeze")
    if (validation.get("cost_hash") != cost_hash(cost) or not isinstance(freeze, dict)
            or any(freeze.get(key) != validation.get(key) for key in ("spec_hash", "code_hash", "data_hash", "cost_hash"))
            or freeze.get("kind") not in {"current_frozen_replay", "prospective"}
            or freeze.get("holdout_locked") is not True or freeze.get("holdout_tuned") is not False
            or validation.get("currency") != "USD" or validation.get("initial_nav") != 100_000.0
            or validation.get("initial_nav_usd") != 100_000.0):
        raise ValueError("validation freeze/cost/currency binding mismatch")
    assumptions = validation.get("execution_assumptions")
    sensitivities = validation.get("sensitivity")
    parameters = sensitivities.get("parameters") if isinstance(sensitivities, dict) else None
    if (not isinstance(assumptions, dict) or assumptions.get("execution_timing") != "next_session_close"
            or assumptions.get("rebalance_cadence") != "completed_nonzero_trades"
            or assumptions.get("sale_proceeds") != "receivable_then_fixed_5_published_session_hold_not_statutory_settlement_calendar"
            or assumptions.get("dividends") != "pre_ex_raw_share_entitlement_fixed_usd_receivable_to_explicit_available_cash"
            or not isinstance(validation.get("benchmarks"), dict)
            or set(validation["benchmarks"]) != {"buy_and_hold_basket", "cash"}
            or not isinstance(parameters, dict) or parameters.get("selection") != "none"):
        raise ValueError("validation execution assumptions or reproducibility report mismatch")


def _adoption_identity(spec, validation):
    return {"schema_version": SCHEMA_VERSION, "kind": "advisor_strategy", "spec": spec.to_dict(),
            "validation_id": validation["validation_id"], "code_hash": validation["code_hash"],
            "sizing_profile": dict(SIZING_PROFILE), "cost_model": validation["cost_model"]}


def build_adoption(spec, validation, confirmation: str) -> dict:
    """Explicit boundary. Shipped CLI must recompute validation in this call."""
    spec = _spec(spec)
    if isinstance(validation, dict) and validation.get("data_role") == "fixture":
        raise ValueError("fixture evidence cannot be adopted for user trading")
    _verify_validation(validation, spec)
    if not _affirmation(confirmation, ADOPTION_CONFIRMATIONS):
        raise ValueError("explicit user confirmation is required")
    identity = _adoption_identity(spec, validation)
    adoption = {**identity, "rule_id": "rule-" + _hash(identity)[:16], "sleeve": "advisor",
                "mode": spec.mode, "validation": copy.deepcopy(validation),
                "created_at": iso(utc_now()), "confirmation": confirmation.strip()}
    adoption["digest"] = _hash(adoption)
    return adoption


def verify_adoption(adoption) -> bool:
    """Fail closed for tampering, old calculation code, profile and cost drift."""
    try:
        if not isinstance(adoption, dict):
            return False
        body = {key: value for key, value in adoption.items() if key != "digest"}
        if adoption.get("digest") != _hash(body):
            return False
        spec = _spec(adoption["spec"])
        validation = adoption["validation"]
        _verify_validation(validation, spec)
        identity = _adoption_identity(spec, validation)
        return (all(adoption.get(key) == value for key, value in identity.items())
                and adoption.get("rule_id") == "rule-" + _hash(identity)[:16]
                and adoption.get("sleeve") == "advisor" and adoption.get("mode") == spec.mode
                and _affirmation(adoption.get("confirmation"), ADOPTION_CONFIRMATIONS))
    except (ValueError, TypeError, KeyError, OverflowError):
        return False


def compile_signal(adoption, research_snapshot, mode, now=None) -> dict:
    """Compile a current adopted template into account-free price conditions.

    A missing breakout is wait, never an automatic exit of a previously held lot.
    Swing stop/time exits need actual entry-session and fixed-stop lot evidence
    from the caller; those facts are not reconstructed from a reviewed plan.
    """
    if not verify_adoption(adoption) or mode != adoption.get("mode"):
        raise ValueError("current validated advisor adoption and matching mode required")
    result = evaluate_candidate(adoption["spec"], research_snapshot, now=now)
    result.update(kind="advisor_strategy_signal", rule_id=adoption["rule_id"],
                  validation_id=adoption["validation_id"], code_hash=adoption["code_hash"],
                  cost_model=copy.deepcopy(adoption["cost_model"]), execution_scope="validated_template")
    return result

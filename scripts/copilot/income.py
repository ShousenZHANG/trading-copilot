"""Income ETF events and bounded allocation research, separate from execution.

All monetary outputs are decimal strings. Announced actual amounts, estimates,
future schedules and a broker's credited cash are different facts. A historical
cash-flow maximum is not a total-return optimum or an executable order.
"""
from __future__ import annotations

import calendar
import copy
import hashlib
import itertools
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from .income_sources import ISSUER_URLS, SourceError, exchange_url, fetch_events

SCHEMA_VERSION = 1
SUPPORTED = frozenset(ISSUER_URLS)


def _time(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("income timestamp requires a timezone")
    return value.astimezone(timezone.utc)


def _day(value):
    if isinstance(value, str):
        for form in ("%Y-%m-%d", "%m/%d/%Y"):
            try:
                return datetime.strptime(value.strip(), form).date()  # noqa: DTZ007 - calendar date, never an instant
            except ValueError:
                pass
    raise ValueError("income event date invalid")


def _number(value, label="number", *, minimum=None, maximum=None):
    if value is None or isinstance(value, bool):
        raise ValueError(label + " unknown or invalid")
    result = Decimal(str(value).strip().replace("$", "").replace(",", ""))
    if not result.is_finite() or (minimum is not None and result < minimum) or (maximum is not None and result > maximum):
        raise ValueError(label + " outside valid bounds")
    return result


def _amount(value):
    return format(value, "f")


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()


def _seal(value):
    result = copy.deepcopy(value)
    result.pop("snapshot_id", None)
    result["snapshot_id"] = "income-" + _hash(result)
    return result


def normalize_income_events(symbol, rows, *, source_url, source_family, retrieved_at, as_of,
                            source_role="issuer", payload_sha256=None):
    """Normalize one primary response; date-only publication is not an exact time.

    `actual` means the published amount is an actual announced distribution,
    never that this user owned shares or received money. Historical revisions
    cannot be certified point-in-time by fetching a current page.
    """
    symbol = str(symbol).upper()
    if symbol not in SUPPORTED or source_url not in {ISSUER_URLS[symbol], exchange_url(symbol)}:
        raise ValueError("income source/instrument binding invalid")
    if source_role not in {"issuer", "exchange"} or (source_role == "issuer") != (source_url == ISSUER_URLS[symbol]):
        raise ValueError("income source role invalid")
    expected_family = "Nasdaq" if source_role == "exchange" else "NEOS" if symbol == "QQQI" else "JPM"
    if source_family != expected_family:
        raise ValueError("income source family invalid")
    retrieved, cutoff = _time(retrieved_at), _time(as_of)
    if not isinstance(rows, list) or not 1 <= len(rows) <= 1000:
        raise ValueError("income event rows invalid")
    events = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("income event row invalid")
        declared, ex, record, pay = (_day(row.get(key)) for key in ("declaration_date", "ex_date", "record_date", "pay_date"))
        if not declared <= ex <= record <= pay:
            raise ValueError("income event dates reversed or unsupported")
        if row.get("currency") != "USD":
            raise ValueError("income event currency unsupported")
        kind = row.get("amount_kind")
        if kind not in {"actual", "estimate", "scheduled"}:
            raise ValueError("income amount kind must be explicit")
        amount = None if kind == "scheduled" else _amount(_number(row.get("amount_per_share"), "distribution amount", minimum=0))
        if kind == "scheduled" and row.get("amount_per_share") not in {None, "", "-", "N/A"}:
            raise ValueError("scheduled event cannot contain an actual amount")
        published = _time(row["published_at"]) if row.get("published_at") else None
        if published and (published.date() != declared or published > retrieved):
            raise ValueError("income publication time conflicts with declaration or retrieval")
        # A date-only announcement on the cutoff date is not silently placed
        # at midnight. Today's retrieval proves current availability only when
        # it occurred no later than the requested cutoff.
        known = published <= cutoff if published else declared < cutoff.date() or (declared == cutoff.date() and retrieved <= cutoff)
        identity = {"instrument_id": symbol, "declaration_date": declared.isoformat(), "ex_date": ex.isoformat(),
                        "record_date": record.isoformat(), "pay_date": pay.isoformat()}
        event = dict(**identity, amount_per_share=amount, currency="USD", unit="USD_per_share",
                     amount_kind=kind, payment_status="user_receipt_unknown", published_at=published.isoformat() if published else None,
                     holding_entitlement_status="unknown_requires_actual_pre_ex_holding_evidence",
                     tax_components_status="unknown", return_of_capital_status="unknown_not_inferred_from_19a_estimate",
                     published_date=declared.isoformat(), publication_precision="instant" if published else "date",
                     retrieved_at=retrieved.isoformat(), as_of=cutoff.isoformat(), available_by_cutoff=known,
                     provenance={"source_url": source_url, "source_family": source_family, "source_role": source_role,
                                     "payload_sha256": payload_sha256, "point_in_time_authenticated": False})
        key = ex.isoformat()
        if key in events and events[key] != event:
            raise ValueError("conflicting distribution events for one ex-date")
        events[key] = event
    for event in events.values():
        event["event_id"] = "distribution-" + _hash(event)
    return sorted(events.values(), key=lambda event: event["ex_date"])


def collect_income_snapshot(instrument_ids, *, as_of=None, now=None, transport=None,
                            allow_exchange_fallback=True):
    """Fetch public evidence only. `transport` follows HttpClient.get for fixtures."""
    moment = _time(now)
    cutoff = _time(as_of) if as_of else moment
    if cutoff > moment:
        raise ValueError("income as_of cannot be in the future")
    if not isinstance(instrument_ids, list) or not 1 <= len(instrument_ids) <= 2:
        raise ValueError("income research requires one or two supported ETFs")
    symbols = [str(symbol).strip().upper() for symbol in instrument_ids]
    if len(set(symbols)) != len(symbols) or not set(symbols) <= SUPPORTED:
        raise ValueError("income universe unsupported or duplicated")
    if transport is None:
        from .providers import HttpClient
        transport = HttpClient(cache_dir=None, timeout=15, attempts=1, budget_seconds=40)
    instruments, issues = {}, []
    for symbol in symbols:
        try:
            fetched = fetch_events(symbol, transport, allow_exchange_fallback=allow_exchange_fallback)
            response = fetched["response"]
            retrieved = _time(response["retrieved_at"])
            if now is not None and retrieved > moment:
                raise ValueError("income retrieval is in the future")
            events = normalize_income_events(symbol, fetched["rows"], source_url=fetched["source_url"],
                source_family=fetched["source_family"], source_role=fetched["source_role"],
                retrieved_at=retrieved, as_of=cutoff, payload_sha256=hashlib.sha256(response["body"].encode()).hexdigest())
            instruments[symbol] = {"status": "ready", "events": events, "source_diagnostics": fetched["diagnostics"],
                                       "source_role": fetched["source_role"], "source_url": fetched["source_url"]}
        except (ValueError, TypeError, KeyError, ArithmeticError) as exc:
            code = str(exc) if isinstance(exc, (SourceError, ValueError)) else "income_collection_failed:" + type(exc).__name__
            issues.append(symbol + ":" + code)
            instruments[symbol] = {"status": "blocked", "events": [], "issues": [code],
                "source_diagnostics": exc.diagnostics if isinstance(exc, SourceError) else []}
    created = moment if now is not None else _time()
    return _seal({"schema_version": SCHEMA_VERSION, "created_at": created.isoformat(), "as_of": cutoff.isoformat(),
        "valid_until": (created+timedelta(hours=1)).isoformat(), "status": "blocked" if issues else "ready",
        "complete": not issues, "issues": issues, "instruments": instruments, "execution_scope": "research_only",
        "order_submission": False, "point_in_time_authenticated": False})


def validate_income_snapshot(snapshot, *, now=None, require_complete=True):
    try:
        if not isinstance(snapshot, dict):
            raise TypeError("income snapshot malformed")
        if snapshot.get("schema_version") != SCHEMA_VERSION or snapshot.get("snapshot_id") != _seal(snapshot)["snapshot_id"]:
            raise ValueError("income snapshot integrity invalid")
        moment = _time(now)
        if _time(snapshot["created_at"]) > moment or _time(snapshot["as_of"]) > _time(snapshot["created_at"]) or _time(snapshot["valid_until"]) <= moment:
            raise ValueError("income snapshot expired or future-dated")
        instruments = snapshot.get("instruments")
        if not isinstance(instruments, dict) or not 1 <= len(instruments) <= 2 or not set(instruments) <= SUPPORTED:
            raise ValueError("income snapshot instruments invalid")
        if any(not isinstance(item, dict) or item.get("status") not in {"ready", "blocked"} or not isinstance(item.get("events"), list)
               for item in instruments.values()):
            raise ValueError("income snapshot instrument payload invalid")
        if require_complete and (snapshot.get("status") != "ready" or snapshot.get("complete") is not True):
            raise ValueError("income evidence incomplete")
        return {"valid": True, "complete": snapshot.get("complete") is True, "issues": []}
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return {"valid": False, "issues": [str(exc)]}


def _year_before(day):
    return day.replace(year=day.year-1, day=min(day.day, calendar.monthrange(day.year-1, day.month)[1]))


def _event_metrics(events, cutoff):
    day, start = cutoff.date(), _year_before(cutoff.date())
    actual = sorted((event for event in events if event["amount_kind"] == "actual" and event["available_by_cutoff"] and _day(event["ex_date"]) <= day), key=lambda event: event["ex_date"])
    trailing = [event for event in actual if start < _day(event["ex_date"]) <= day]
    amounts = [_number(event["amount_per_share"]) for event in trailing]
    # Monthly ex-dates can move across month boundaries. A strict one-per-month
    # rule would reject valid holiday shifts; count alone can hide missing data.
    # Bound consecutive gaps, including the closest pre-window observation,
    # and both window edges. This is a heuristic, not source authentication.
    days = [_day(event["ex_date"]) for event in trailing]
    previous = [_day(event["ex_date"]) for event in actual if _day(event["ex_date"]) <= start]
    span = previous[-1:]+days
    gaps = [(right-left).days for left, right in itertools.pairwise(span)]
    first_gap = (days[0]-start).days if days else None
    last_gap = (day-days[-1]).days if days else None
    max_gap = max(gaps) if gaps else None
    continuous = bool(previous and days and max_gap is not None and max_gap <= 45 and first_gap <= 45 and last_gap <= 45)
    complete = continuous and len(trailing) >= 12
    recent = actual[-12:]
    values = [_number(event["amount_per_share"]) for event in recent]
    mean = sum(values, Decimal(0))/len(values) if values else None
    std = (sum((value-mean)**2 for value in values)/len(values)).sqrt() if values else None
    total = sum(amounts, Decimal(0))
    return {"window_start_exclusive": start.isoformat(), "window_end_inclusive": day.isoformat(), "window_basis": "ex_date",
        "ttm_status": "complete_monthly_history_observed" if complete else "incomplete",
        "monthly_continuity": {"method": "maximum_45_day_event_and_window_edge_gap_heuristic", "passed": continuous,
            "maximum_allowed_gap_days": 45, "maximum_event_gap_days": max_gap,
            "window_start_gap_days": first_gap, "window_end_gap_days": last_gap,
            "source_completeness_authenticated": False},
        "ttm_event_count": len(trailing), "ttm_distribution_per_share": _amount(total) if complete else None,
        "observed_window_sum_per_share": _amount(total), "monthly_cashflow_proxy": _amount(total/12) if complete else None,
        "latest_actual": actual[-1] if actual else None, "latest_12_count": len(recent),
        "latest_12_sum_per_share": _amount(sum(values, Decimal(0))),
        "stability": {"mean": _amount(mean) if mean is not None else None, "minimum": _amount(min(values)) if values else None,
            "maximum": _amount(max(values)) if values else None, "population_std": _amount(std) if std is not None else None,
            "coefficient_of_variation": _amount(std/mean) if mean else None, "basis": "latest_up_to_12_actual_announced_amounts"},
        "future_or_estimated_events": [event for event in events if event["amount_kind"] != "actual" or _day(event["ex_date"]) > day],
        "actual_account_cash_received": "unknown", "future_monthly_cashflow": "unknown"}


def _total_return(events, nav, cutoff):
    """Unreinvested one-share economic NAV+entitlement return, never broker P/L."""
    if not isinstance(nav, dict):
        return {"status": "unknown", "reason": "raw_NAV_history_not_supplied"}
    try:
        if nav.get("basis") != "raw_nav" or nav.get("currency") != "USD" or nav.get("splits_verified_absent") is not True or nav.get("distribution_events_complete") is not True:
            raise ValueError("raw NAV/split/distribution coverage unverified")
        start, end = _day(nav["start_date"]), _day(nav["end_date"])
        if not start < end <= cutoff.date() or not nav.get("source_url"):
            raise ValueError("NAV dates/source invalid")
        first, last = _number(nav["start_nav"], minimum=Decimal(".00000001")), _number(nav["end_nav"], minimum=0)
        selected = [event for event in events if start < _day(event["ex_date"]) <= end and event["amount_kind"] == "actual" and event["available_by_cutoff"]]
        rights = sum((_number(event["amount_per_share"]) for event in selected), Decimal(0))
        return {"status": "calculated_from_declared_inputs", "start_date": start.isoformat(), "end_date": end.isoformat(),
            "nav_change_per_share": _amount(last-first), "distribution_entitlements_per_share": _amount(rights),
            "total_return_fraction": _amount((last-first+rights)/first), "reinvested": False, "basis": "raw_NAV_plus_ex_date_entitlements",
            "taxes_and_trading_costs_included": False, "fund_expenses": "already_reflected_in_NAV_not_subtracted_twice",
            "source_url": nav["source_url"], "source_authenticated": False}
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return {"status": "unknown", "reason": str(exc)}


def _tax_rates(scenario, symbols):
    if not isinstance(scenario, dict) or not isinstance(scenario.get("name"), str) or not scenario["name"].strip():
        raise ValueError("withholding scenario requires a name")
    if not isinstance(scenario.get("rates"), dict):
        raise TypeError("withholding scenario requires per-fund rates")
    return {symbol: _number(scenario.get("rates", {}).get(symbol), "withholding rate "+symbol, minimum=0, maximum=1) for symbol in symbols}


def _fx_scenario(fx):
    if not isinstance(fx, dict) or not isinstance(fx.get("name"), str) or not fx["name"].strip() or not re.fullmatch(r"[A-Z]{3}", str(fx.get("base_currency", ""))):
        raise ValueError("FX scenario name/base currency invalid")
    return _number(fx.get("usd_to_base"), "FX scenario", minimum=Decimal(".00000001"))


def analyze_income(snapshot, *, prices=None, nav_history=None, withholding_scenarios=None, fx_scenarios=None, now=None):
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    checked = validate_income_snapshot(snapshot, now=now, require_complete=False)
    status = "blocked" if not checked["valid"] else "ready" if checked["complete"] else "partial"
    result = {"schema_version": 1, "snapshot_id": snapshot.get("snapshot_id"), "status": status,
        "issues": checked["issues"] or list(snapshot.get("issues", [])), "as_of": snapshot.get("as_of"), "instruments": {}, "execution_scope": "research_only", "orders": [],
        "objective_boundary": "distribution_cashflow_is_not_profit_or_optimal_total_return", "withholding_status": "unknown",
        "fx_status": "unknown", "scenario_results": [], "gross_fx_scenarios": [], "disclosures": ["Actual account entitlement/payment, final tax classification and resident-country liability are unknown.",
            "A current primary page is not authenticated point-in-time history; payout proxies do not forecast future distributions."]}
    if not checked["valid"]:
        return result
    cutoff = _time(snapshot["as_of"])
    for symbol, item in snapshot["instruments"].items():
        if item.get("status") != "ready":
            result["instruments"][symbol] = {"status": "blocked", "issues": item.get("issues", [])}
            continue
        metric = _event_metrics(item["events"], cutoff)
        metric["status"] = "ready"
        metric["total_return"] = _total_return(item["events"], (nav_history or {}).get(symbol), cutoff)
        if prices and symbol in prices:
            try:
                price = _number(prices[symbol], "research reference price", minimum=Decimal(".00000001"))
                metric["research_reference_price_usd"] = _amount(price)
                metric["ttm_cash_distribution_to_reference_price"] = _amount(_number(metric["ttm_distribution_per_share"])/price) if metric["ttm_distribution_per_share"] is not None else None
            except (ValueError, ArithmeticError) as exc:
                result["issues"].append(symbol+":"+str(exc))
                result["status"] = "partial"
        result["instruments"][symbol] = metric
    symbols = tuple(symbol for symbol, metric in result["instruments"].items() if metric["status"] == "ready")
    for fx in fx_scenarios or []:
        rate = _fx_scenario(fx)
        result["gross_fx_scenarios"].append({"name": fx["name"], "base_currency": fx["base_currency"], "usd_to_base": _amount(rate),
            "instruments": {symbol: _amount(_number(result["instruments"][symbol]["monthly_cashflow_proxy"])*rate)
                if result["instruments"][symbol]["monthly_cashflow_proxy"] is not None else None for symbol in symbols},
            "basis": "explicit_FX_assumption_not_a_completed_conversion"})
    for scenario in withholding_scenarios or []:
        rates = _tax_rates(scenario, symbols)
        output = {"name": scenario["name"], "rates": {s:_amount(r) for s,r in rates.items()}, "basis": "explicit_assumption_withholding_only_not_final_tax", "instruments": {}}
        for symbol in symbols:
            gross = result["instruments"][symbol]["monthly_cashflow_proxy"]
            net = _number(gross)*(1-rates[symbol]) if gross is not None else None
            output["instruments"][symbol] = {"monthly_net_usd_proxy": _amount(net) if net is not None else None, "fx_scenarios": []}
            for fx in fx_scenarios or []:
                rate = _fx_scenario(fx)
                output["instruments"][symbol]["fx_scenarios"].append({"name": fx["name"], "base_currency": fx["base_currency"],
                    "usd_to_base": _amount(rate), "net_base_proxy": _amount(net*rate) if net is not None else None, "basis": "explicit_FX_assumption_not_a_completed_conversion"})
        result["scenario_results"].append(output)
    if withholding_scenarios:
        result["withholding_status"] = "explicit_scenarios_not_certified_personal_tax"
    if fx_scenarios:
        result["fx_status"] = "explicit_scenarios_not_broker_FX_or_cash"
    return result


def compare_income_allocations(snapshot, *, prices, budget_usd, cost_model, constraints,
                               objective="gross_cashflow", withholding_scenarios=None,
                               fx_scenarios=None, nav_history=None, existing_holdings=None, now=None):
    """Enumerate bounded whole-share research targets, with explicit funding costs.

    budget_usd is a hypothetical allocation value, NOT settled broker cash.
    No target here authorizes a buy, sale, FX conversion, or account transfer.
    """
    result = {"status": "blocked", "execution_scope": "research_only", "orders": [], "issues": [], "candidates": [],
        "objective": objective, "funding_status": "not_verified", "budget_role": "hypothetical_allocation_value_not_spendable_broker_cash",
        "future_cashflow": "unknown", "personal_tax_status": "unknown", "selected_target_requires_user_intent_and_fresh_execution_compiler": True}
    try:
        if not all(isinstance(value, dict) for value in (prices, cost_model, constraints)):
            raise ValueError("income prices/cost/constraints must be objects")
        if existing_holdings is not None and not isinstance(existing_holdings, dict):
            raise TypeError("income existing holdings must be an object")
        bounds = constraints.get("weight_bounds", {})
        if not isinstance(bounds, dict) or any(not isinstance(value, dict) for value in bounds.values()):
            raise TypeError("income weight bounds must contain per-fund objects")
        if not set(bounds) <= SUPPORTED:
            raise ValueError("income weight bounds contain unsupported instruments")
        if objective not in {"gross_cashflow", "net_cashflow", "total_return"}:
            raise ValueError("income objective unsupported")
        research = analyze_income(snapshot, prices=prices, nav_history=nav_history, now=now)
        if research["status"] != "ready" or research["issues"]:
            raise ValueError("income evidence/reference prices incomplete")
        symbols = tuple(sorted(research["instruments"]))
        budget = _number(budget_usd, "research budget", minimum=Decimal(".01"))
        floor = _number(constraints.get("cash_floor_pct"), "cash floor", minimum=0, maximum=1)*budget
        share_fee = _number(cost_model.get("per_share_usd"), "per-share cost", minimum=0)
        minimum = _number(cost_model.get("minimum_usd"), "minimum cost", minimum=0)
        other = _number(cost_model.get("other_cost_bps"), "other costs", minimum=0)
        values = {symbol:_number(prices.get(symbol), "price "+symbol, minimum=Decimal(".00000001")) for symbol in symbols}
        previous = {symbol:_number((existing_holdings or {}).get(symbol, "0"), "existing shares", minimum=0) for symbol in symbols}
        if any(value != value.to_integral_value() for value in previous.values()) or set(existing_holdings or {})-set(symbols):
            raise ValueError("research holdings must be whole supported shares")
        if set(bounds)-set(symbols):
            raise ValueError("income weight bounds must match researched instruments")
        ranges = []
        for symbol in symbols:
            low = _number(bounds.get(symbol, {}).get("min", "0"), "minimum weight", minimum=0, maximum=1)
            high = _number(bounds.get(symbol, {}).get("max", "1"), "maximum weight", minimum=0, maximum=1)
            if low > high:
                raise ValueError("income weight bounds reversed")
            start = int((budget*low/values[symbol]).to_integral_value(rounding="ROUND_CEILING"))
            end = int((budget*high/values[symbol]).to_integral_value(rounding="ROUND_FLOOR"))
            ranges.append(range(start, end+1))
        size = 1
        for possible in ranges:
            size *= len(possible)
        if size > 100_000:
            raise ValueError("income enumeration exceeds 100000 configurations; narrow bounds/budget")
        scenarios = withholding_scenarios if objective == "net_cashflow" else [{"name":"gross_or_total_return", "rates":{s:"0" for s in symbols}}]
        if not scenarios:
            raise ValueError("net cashflow requires explicit per-fund withholding scenarios")
        score_rates = []
        for scenario in scenarios:
            taxes = _tax_rates(scenario, symbols)
            score_rates.append((scenario["name"], taxes))
        income = {}
        for symbol in symbols:
            metric = research["instruments"][symbol]
            if metric["ttm_distribution_per_share"] is None:
                raise ValueError("complete trailing distribution coverage required:"+symbol)
            income[symbol] = _number(metric["monthly_cashflow_proxy"])
            if objective == "total_return" and metric["total_return"]["status"] != "calculated_from_declared_inputs":
                raise ValueError("total-return objective requires comparable verified-basis NAV inputs")
        if objective == "total_return" and len({(research["instruments"][s]["total_return"]["start_date"],research["instruments"][s]["total_return"]["end_date"]) for s in symbols}) != 1:
            raise ValueError("total-return windows must match")
        best = {}
        feasible = 0
        for quantities in itertools.product(*ranges):
            invested = sum((q*values[s] for s,q in zip(symbols,quantities)),Decimal(0))
            fees = sum((max(minimum,abs(Decimal(q)-previous[s])*share_fee)+abs(Decimal(q)-previous[s])*values[s]*other/10000
                        for s,q in zip(symbols,quantities) if Decimal(q) != previous[s]),Decimal(0))
            if invested+fees+floor > budget:
                continue
            feasible += 1
            gross = sum((q*income[s] for s,q in zip(symbols,quantities)),Decimal(0))
            for name,taxes in score_rates:
                net = sum((q*income[s]*(1-taxes[s]) for s,q in zip(symbols,quantities)),Decimal(0))
                score = net if objective == "net_cashflow" else gross
                if objective == "total_return":
                    score = sum((q*values[s]*_number(research["instruments"][s]["total_return"]["total_return_fraction"]) for s,q in zip(symbols,quantities)),Decimal(0))-fees
                key = (score,-fees,-invested)
                if name not in best or key > best[name][0]:
                    best[name] = (key,{"scenario": name, "hypothetical_target_holdings": {s:str(q) for s,q in zip(symbols,quantities)},
                        "invested_usd": _amount(invested), "estimated_reallocation_cost_usd": _amount(fees),
                        "cash_remaining_usd": _amount(budget-invested-fees), "required_cash_floor_usd": _amount(floor),
                        "historical_gross_monthly_cashflow_proxy_usd": _amount(gross),
                        "historical_net_monthly_cashflow_proxy_usd": _amount(net) if objective == "net_cashflow" else None,
                        "score": _amount(score), "taxes_used": {s:_amount(r) for s,r in taxes.items()} if objective == "net_cashflow" else None})
        if not feasible:
            raise ValueError("no allocation meets budget/cost/cash-floor/weight constraints")
        window = {"start": research["instruments"][symbols[0]]["window_start_exclusive"], "end": snapshot["as_of"], "basis": "ex_date_TTM"}
        if objective == "total_return":
            measured = research["instruments"][symbols[0]]["total_return"]
            window = {"start": measured["start_date"], "end": measured["end_date"], "basis": "declared_raw_NAV_plus_entitlements"}
        result.update(status="research_comparison", candidates=[entry[1] for entry in best.values()],
            configurations_enumerated=size, feasible_configurations=feasible, window=window,
            objective_scope="historical_proxy_under_explicit_constraints_not_future_optimum", allocation_budget_usd=_amount(budget),
            cost_scope="explicit_trade_cost_assumptions_excludes_realized_capital_gains_tax_and_unprovided_FX")
        for candidate in result["candidates"]:
            candidate["fx_scenarios"] = []
            for fx in fx_scenarios or []:
                rate = _fx_scenario(fx)
                net = candidate["historical_net_monthly_cashflow_proxy_usd"]
                candidate["fx_scenarios"].append({"name": fx["name"],"base_currency": fx["base_currency"], "usd_to_base": _amount(rate),
                    "gross_monthly_base_proxy": _amount(_number(candidate["historical_gross_monthly_cashflow_proxy_usd"])*rate),
                    "net_monthly_base_proxy": _amount(_number(net)*rate) if net is not None else None,
                    "basis": "explicit_FX_assumption_not_a_completed_conversion"})
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        result.update(status="blocked", candidates=[])
        result["issues"].append(str(exc))
    return result

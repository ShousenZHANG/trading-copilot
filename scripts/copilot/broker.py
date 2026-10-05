"""Bounded, opt-in IBKR read-only queries. No credentials or state are loaded here.

The optional official ``ibapi`` SDK is imported only for an enabled live query.
Tests inject a transport implementing ``collect(settings, symbols, now=...)``.
Its raw response is normalized by the same code used for the socket transport.
Neither an end callback nor a requested market-data tier certifies execution.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

SCHEMA_VERSION = 1
_SECTIONS = ("accounts", "cash", "positions", "orders", "fills", "contracts", "quotes")
_TERMINAL = {"filled", "cancelled", "rejected"}
_ACCOUNT_MONEY = {"NetLiquidation", "TotalCashValue", "CashBalance", "TotalCashBalance",
                  "SettledCash", "AvailableFunds", "BuyingPower"}
_STATUSES = {"PendingSubmit": "submitted", "ApiPending": "submitted",
             "PreSubmitted": "submitted", "Submitted": "submitted",
             "PendingCancel": "cancel_pending", "Cancelled": "cancelled",
             "ApiCancelled": "cancelled", "Filled": "filled", "Rejected": "rejected"}
# Exact broad labels only. This is broker industry attribution, not a GICS
# assertion. Ambiguous consumer categories and unrecognized text remain unknown.
_INDUSTRY_SECTORS = {"Technology": "technology", "Financial": "financials",
                     "Energy": "energy", "Utilities": "utilities",
                     "Basic Materials": "materials", "Healthcare": "health_care",
                     "Health Care": "health_care", "Industrial": "industrials",
                     "Real Estate": "real_estate"}
MAX_ACCOUNT_INSTRUMENTS = 64


class BrokerError(Exception):
    """A fixed public diagnostic, never an upstream message or credential."""


def _silence_sdk_logs():
    # Official SDK INFO callbacks serialize raw account identifiers and payloads.
    # Keep them out of MCP stderr and inherited application logging handlers.
    parent = logging.getLogger("ibapi")
    parent.setLevel(logging.CRITICAL + 1)
    parent.propagate = False
    parent.handlers[:] = [logging.NullHandler()]
    for name, logger in list(logging.root.manager.loggerDict.items()):
        if name.startswith("ibapi.") and isinstance(logger, logging.Logger):
            logger.disabled = True


def _clock(value=None):
    if value is None:
        return datetime.now(timezone.utc)
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError("timezone-aware broker timestamp required")
    return value.astimezone(timezone.utc)


def _money(value, *, nonnegative=False):
    if isinstance(value, bool) or value is None:
        raise ValueError("missing monetary value")
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        raise ValueError("invalid monetary value") from None
    # IBKR's unset double/Decimal sentinel is not a measured balance or fee.
    if not number.is_finite() or abs(number) >= Decimal("1e100"):
        raise ValueError("nonfinite or unset monetary value")
    if nonnegative and number < 0:
        raise ValueError("negative monetary value")
    result = format(number, "f")
    if "." in result:
        result = result.rstrip("0").rstrip(".")
    return "0" if number == 0 else result


def _optional_money(value):
    return None if value is None else _money(value)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _seal(snapshot):
    snapshot["snapshot_id"] = "broker-" + _digest({k: v for k, v in snapshot.items() if k != "snapshot_id"})
    return snapshot


def seal_execution_snapshot(snapshot: dict) -> dict:
    """Seal an isolated transport fixture or trusted local snapshot; no authentication claim."""
    return _seal(copy.deepcopy(snapshot))


def _base(moment):
    return {"schema_version": SCHEMA_VERSION, "provider": "ibkr", "snapshot_id": "",
            "as_of": moment.isoformat(), "valid_until": moment.isoformat(),
            "status": "blocked", "complete": False, "issues": [],
            "account": {}, "account_version": None, "cash": {}, "positions": [],
            "orders": [], "fills": [], "quotes": {}, "fx_rates": {},
            "coverage": {section: {"complete": False} for section in _SECTIONS}}


def _settings(settings):
    if not isinstance(settings, dict):
        raise ValueError("broker settings must be an object")
    result = dict(settings)
    result.setdefault("enabled", False)
    result.setdefault("host", "127.0.0.1")
    result.setdefault("port", 7497)
    result.setdefault("client_id", 71)
    result.setdefault("timeout_seconds", 15)
    result.setdefault("quote_ttl_seconds", 60)
    result.setdefault("account_alias", "ibkr")
    result.setdefault("orders_scope_confirmed", False)
    result.setdefault("market_data_feed", "unknown")
    result.setdefault("regular_hours_only", True)
    for name, low, high in (("port", 1, 65535), ("client_id", 1, 2147483647),
                            ("timeout_seconds", 1, 30), ("quote_ttl_seconds", 1, 120)):
        value = result[name]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            raise ValueError("invalid broker " + name)
    for name in ("enabled", "orders_scope_confirmed", "regular_hours_only"):
        if not isinstance(result[name], bool):
            raise ValueError("invalid broker " + name)
    if not isinstance(result["host"], str) or result["host"] not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("broker host must be loopback")
    if not isinstance(result.get("account_id", ""), str):
        raise ValueError("invalid broker account selection")
    if result["market_data_feed"] not in {"unknown", "consolidated", "non_consolidated"}:
        raise ValueError("invalid broker market_data_feed")
    if "paper" in result and not isinstance(result["paper"], bool):
        raise ValueError("invalid broker paper declaration")
    return result


def _execution_timestamp(value):
    """An operator-local IBKR time is not silently labelled UTC."""
    if not isinstance(value, str):
        return None
    try:
        if re.match(r"\d{4}-\d{2}-\d{2}T", value):
            return _clock(value).isoformat()
        words = value.split()
        if len(words) == 3 and words[-1] in {"UTC", "GMT"}:
            return datetime.strptime(" ".join(words[:2]), "%Y%m%d %H:%M:%S").replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    return None


def _expanded_symbols(raw, selected, symbols):
    """Include account exposure after account ends; never silently truncate it."""
    expanded = list(symbols)
    for section in ("positions", "orders"):
        for row in raw.get(section, []):
            if (row.get("account_id") != selected or row.get("currency") != "USD"
                    or row.get("sec_type") != "STK"):
                continue
            if section == "positions" and Decimal(_money(row.get("quantity"))) == 0:
                continue
            if section == "orders" and _STATUSES.get(row.get("status"), row.get("status")) in _TERMINAL:
                continue
            symbol = str(row.get("instrument_id", "")).strip().upper()
            if not re.fullmatch(r"[A-Z][A-Z0-9.]{0,11}", symbol):
                raise BrokerError("account_instrument_symbol_unsupported")
            if symbol not in expanded:
                expanded.append(symbol)
    if len(expanded) > MAX_ACCOUNT_INSTRUMENTS:
        raise BrokerError("account_quote_universe_limit_exceeded")
    return expanded


def _classification(contract):
    raw = {field: contract.get(field) if isinstance(contract.get(field), str) else None
           for field in ("industry", "category", "subcategory")}
    industry = raw["industry"]
    sector = _INDUSTRY_SECTORS.get(industry)
    return {"broker_industry": raw, "sector": sector,
            "sector_evidence": {"source": "IBKR_contractDetails",
                                "mapping": "exact_broker_industry_label_v1",
                                "status": "known" if sector else "unknown",
                                "con_id": contract.get("con_id"),
                                "industry": industry, "gics_verified": False}}


def _eastern_zone(moment):
    """US DST rules since 2007, avoiding a system tzdb dependency on Windows."""
    year = moment.year
    march = datetime(year, 3, 8, 7, tzinfo=timezone.utc)
    start = march + timedelta(days=(6 - march.weekday()) % 7)
    november = datetime(year, 11, 1, 6, tzinfo=timezone.utc)
    end = november + timedelta(days=(6 - november.weekday()) % 7)
    return timezone(timedelta(hours=-4 if start <= moment < end else -5))


def _regular_hours(contract, moment):
    zone = contract.get("time_zone_id")
    if zone in {"US/Eastern", "America/New_York", "EST5EDT"} and moment.year >= 2007:
        tz = _eastern_zone(moment)
    elif zone in {"UTC", "GMT"}:
        tz = timezone.utc
    else:
        return None
    local = moment.astimezone(tz)
    day = local.strftime("%Y%m%d")
    entries = [part for part in str(contract.get("liquid_hours", "")).split(";") if part.startswith(day + ":")]
    if len(entries) != 1:
        return None
    span = entries[0][9:]
    if span == "CLOSED":
        return False
    try:
        for window in span.split(","):
            begin, end = window.split("-")
            left = day + ":" + begin if ":" not in begin else begin
            right = day + ":" + end if ":" not in end else end
            start = datetime.strptime(left, "%Y%m%d:%H%M").replace(tzinfo=tz)
            finish = datetime.strptime(right, "%Y%m%d:%H%M").replace(tzinfo=tz)
            if start <= local < finish:
                return True
        return False
    except ValueError:
        return None


def _quote(raw, contract, symbol, settings, moment, *, forex=False):
    issues = []
    result = {"instrument_id": symbol, "con_id": contract["con_id"],
              "currency": contract["currency"], "asset_class": contract["asset_class"],
              "bid": None, "ask": None, "bid_size": None, "ask_size": None,
              "actual_data_type": raw.get("actual_data_type"),
              "requested_data_type": 1, "event_time": None,
              "received_at": None, "bid_received_at": None, "ask_received_at": None,
              "tick_size": contract.get("tick_size"), "market_rule": contract.get("market_rule", []),
              "market_rule_id": contract.get("market_rule_id"),
              "quantity_step": "1", "feed_scope": settings["market_data_feed"] if not forex else "forex",
              "feed_scope_source": "user_declared_entitlement" if not forex else "ibkr_forex",
              "regular_hours": None if forex else _regular_hours(contract, moment),
              "session": "unknown", "valid_until": moment.isoformat(), "status": "blocked"}
    if not forex and contract["asset_class"] == "stock":
        result.update(_classification(contract))
    for field in ("bid", "ask", "bid_size", "ask_size"):
        if raw.get(field) is not None:
            result[field] = _money(raw[field], nonnegative=True)
    if result["actual_data_type"] != 1:
        issues.append("quote_not_confirmed_live")
    if not forex and raw.get("halted") not in {False, 0, "0"}:
        # Halted is a generic tick and absent means unknown, not unhindered trading.
        issues.append("halt_status_unknown_or_halted")
    if any(result[field] is None or Decimal(result[field]) <= 0 for field in ("bid", "ask")):
        issues.append("quote_bid_ask_missing")
    elif Decimal(result["bid"]) > Decimal(result["ask"]):
        issues.append("quote_crossed")
    if any(result[field] is None or Decimal(result[field]) <= 0 for field in ("bid_size", "ask_size")):
        issues.append("quote_size_missing")
    stamps = []
    for field in ("bid_received_at", "ask_received_at"):
        if raw.get(field) is None:
            issues.append("quote_timestamp_missing")
            continue
        stamp = _clock(raw[field])
        result[field] = stamp.isoformat()
        stamps.append(stamp)
        if stamp > moment + timedelta(seconds=2):
            issues.append("quote_timestamp_future")
    if len(stamps) == 2:
        expiry = min(stamps) + timedelta(seconds=settings["quote_ttl_seconds"])
        result["received_at"] = max(stamps).isoformat()
        result["valid_until"] = expiry.isoformat()
        if expiry <= moment:
            issues.append("quote_expired")
        if abs((stamps[0] - stamps[1]).total_seconds()) > 15:
            issues.append("quote_collection_window_inconsistent")
    # reqMktData L1 does not supply an exchange timestamp for each bid/ask.
    # Last-trade tickString time is NOT promoted to the BBO observation time.
    if not forex:
        result["session"] = "regular" if result["regular_hours"] is True else "closed" if result["regular_hours"] is False else "unknown"
        if settings["regular_hours_only"] and result["regular_hours"] is not True:
            issues.append("regular_session_unverified_or_closed")
        if result["feed_scope"] == "unknown":
            issues.append("quote_feed_scope_unknown")
    if (result["tick_size"] is None or Decimal(result["tick_size"]) <= 0
            or not result["market_rule"] or any(Decimal(row["increment"]) <= 0 for row in result["market_rule"])):
        issues.append("price_increment_unverified")
    result["issues"] = sorted(set(issues))
    result["status"] = "ready" if not issues else "blocked"
    return result


def _account_observation(row):
    raw_tag = row.get("raw_tag", row["tag"])
    tag = row["tag"].removeprefix("$LEDGER-")
    if raw_tag.removeprefix("$LEDGER-") != tag:
        raise BrokerError("account_value_identity_invalid")
    source = row.get("source", "unknown")
    if source not in {"account_summary", "account_updates", "account_updates_multi", "unknown"}:
        source = "unknown"
    scope = row.get("value_scope")
    if raw_tag.startswith("$LEDGER-"):
        if scope not in {None, "per_currency"}:
            raise BrokerError("account_value_scope_conflict")
        scope = "per_currency"
    elif scope is None:
        scope = "per_currency" if tag in {"CashBalance", "TotalCashBalance"} else "account" if source == "account_summary" else "unknown"
    if scope not in {"per_currency", "account", "unknown"}:
        raise BrokerError("account_value_scope_unknown")
    value = _money(row["value"]) if tag in _ACCOUNT_MONEY else row["value"]
    return dict(row, tag=tag, raw_tag=raw_tag, value=value, source=source, value_scope=scope)


def _account_base_currency(rows, selected, declared=None):
    candidates, nav_currencies, evidence = set(), set(), []
    for raw_row in rows:
        if raw_row.get("account_id") != selected:
            continue
        row = _account_observation(raw_row)
        value = None
        if row["tag"] == "Currency" and row["currency"] == "BASE" and row["value"] != "BASE":
            value = row["value"]
        elif (row["tag"] == "NetLiquidation" and row["value_scope"] != "per_currency"
              and row["source"] == "account_summary" and row["currency"] != "BASE"):
            value = row["currency"]
            nav_currencies.add(value)
        if value is not None:
            if not isinstance(value, str) or not re.fullmatch("[A-Z]{3}", value):
                raise BrokerError("base_currency_invalid")
            candidates.add(value)
            evidence.append({field: row[field] for field in ("tag", "raw_tag", "currency", "source")})
    if len(nav_currencies) > 1:
        raise BrokerError("base_currency_ambiguous")
    if declared is not None:
        if not isinstance(declared, str) or not re.fullmatch("[A-Z]{3}", declared):
            raise BrokerError("base_currency_invalid")
        candidates.add(declared)
        evidence.append({"tag": "base_currency", "raw_tag": "base_currency", "currency": declared, "source": "transport_declaration"})
    if len(candidates) > 1:
        raise BrokerError("base_currency_conflict")
    if not candidates:
        raise BrokerError("base_currency_unknown")
    evidence.sort(key=lambda row: (row["tag"], row["raw_tag"], row["source"], row["currency"]))
    return next(iter(candidates)), {"status": "broker_reported", "sources": evidence}


def _normalize(raw, settings, symbols, moment):
    result = _base(moment)
    accounts = raw.get("accounts", [])
    selected = settings.get("account_id") or (accounts[0] if len(accounts) == 1 else None)
    if not selected or selected not in accounts:
        raise BrokerError("account_selection_missing_or_ambiguous")
    symbols = _expanded_symbols(raw, selected, symbols)
    key = "acct-" + hashlib.sha256(selected.encode()).hexdigest()[:24]
    alias = settings["account_alias"]
    if not isinstance(alias, str) or not alias.strip() or selected in alias:
        alias = "ibkr"
    values, ledger = {}, {}
    for row in raw.get("account_values", []):
        if row.get("account_id") == selected:
            row = _account_observation(row)
            identity = (row["tag"], row["currency"])
            if row["value_scope"] == "per_currency":
                if identity in ledger and ledger[identity]["value"] != row["value"]:
                    raise BrokerError("account_values_changed_during_collection")
                if identity not in ledger or row["raw_tag"].startswith("$LEDGER-"):
                    ledger[identity] = row
            else:
                if identity in values and values[identity] != row["value"]:
                    raise BrokerError("account_values_changed_during_collection")
                values[identity] = row["value"]
    base, base_evidence = _account_base_currency(raw.get("account_values", []), selected, raw.get("base_currency"))
    nav = values.get(("NetLiquidation", base), values.get(("NetLiquidation", "BASE")))
    result["account"] = {"alias": alias, "account_key": key, "base_currency": base,
                          "base_currency_evidence": base_evidence,
                         "nav": _optional_money(nav), "nav_currency": base,
                         "account_type": values.get(("AccountType", base), values.get(("AccountType", "BASE"))),
                         "available_funds": _optional_money(values.get(("AvailableFunds", base), values.get(("AvailableFunds", "BASE")))),
                         "available_funds_basis": "broker_margin_aware_base_currency_not_spendable_cash",
                         "paper": raw.get("paper") if isinstance(raw.get("paper"), bool) else settings.get("paper"),
                         "paper_status": "verified" if isinstance(raw.get("paper"), bool) else "declared" if isinstance(settings.get("paper"), bool) else "unknown"}
    ends = raw.get("complete", {})
    for section in _SECTIONS:
        marker = {"accounts": "managedAccounts_and_accountSummaryEnd", "cash": "accountDownloadEnd",
                  "positions": "positionEnd", "orders": "openOrderEnd", "fills": "execDetailsEnd",
                  "contracts": "contractDetailsEnd", "quotes": "tickSnapshotEnd"}[section]
        result["coverage"][section] = {"complete": ends.get(section) is True,
                                         "request_complete": ends.get(section) is True, "end_marker": marker}
    result["coverage"]["orders"]["scope"] = "account_all_user_confirmed" if settings["orders_scope_confirmed"] else "api_request_complete_manual_order_scope_unverified"
    result["coverage"]["orders"]["scope_source"] = "user_declared_capability" if settings["orders_scope_confirmed"] else "unverified"
    result["coverage"]["fills"].update(history_complete=False, scope="TWS_available_recent_executions_only")
    result["coverage"]["quotes"].update(required_instrument_ids=symbols,
                                         universe_scope="requested_plus_account_USD_STK_exposure")
    if not settings["orders_scope_confirmed"]:
        result["coverage"]["orders"]["complete"] = False
        result["issues"].append("orders_scope_unverified")
    if raw.get("changed_during_collection"):
        result["issues"].append("account_changed_during_collection")
    result["issues"].extend(raw.get("issues", []))
    if result["account"]["nav"] is None or Decimal(result["account"]["nav"]) <= 0:
        result["issues"].append("nav_missing_or_nonpositive")
    if str(values.get(("AccountReady", "BASE"), "")).lower() == "false":
        result["issues"].append("account_not_ready")
    for row in raw.get("positions", []):
        if row.get("account_id") != selected:
            continue
        result["positions"].append({"instrument_id": row["instrument_id"], "account_key": key,
                                    "con_id": int(row["con_id"]), "currency": row["currency"],
                                    "security_type": row.get("sec_type"),
                                    "quantity": _money(row["quantity"]), "avg_cost": _optional_money(row.get("avg_cost"))})
    order_keys = {}
    for row in raw.get("orders", []):
        if row.get("account_id") != selected:
            continue
        status = _STATUSES.get(row.get("status"), row.get("status", "unknown"))
        if status not in set(_STATUSES.values()) | {"partially_filled", "unknown"}:
            status = "unknown"
        quantity = _money(row["total_quantity"], nonnegative=True)
        filled = _optional_money(row.get("filled_quantity"))
        remaining = _money(row.get("remaining_quantity", quantity), nonnegative=True)
        if (row["side"].upper() not in {"BUY", "SELL"} or Decimal(remaining) > Decimal(quantity)
                or filled is not None and (Decimal(filled) < 0 or Decimal(filled) > Decimal(quantity))):
            raise BrokerError("order_values_inconsistent")
        if filled is not None and status not in _TERMINAL and Decimal(filled) + Decimal(remaining) != Decimal(quantity):
            raise BrokerError("order_values_inconsistent")
        if filled is not None and Decimal(filled) > 0 and Decimal(remaining) > 0 and status == "submitted":
            status = "partially_filled"
        order_key = key + ":" + ("perm:" + str(row["perm_id"]) if row.get("perm_id") else "client:" + str(row.get("client_id")) + ":order:" + str(row.get("order_id")))
        if not row.get("perm_id") and not row.get("order_id"):
            raise BrokerError("order_identity_unknown")
        order = {"order_key": order_key, "perm_id": row.get("perm_id"),
                                 "con_id": int(row["con_id"]), "instrument_id": row["instrument_id"],
                                 "side": row["side"].lower(), "total_quantity": quantity,
                                 "filled_quantity": filled, "remaining_quantity": remaining,
                                 "remaining_basis": "broker_status" if "remaining_quantity" in row else "total_quantity_upper_bound",
                                 "limit_price": _optional_money(row.get("limit_price")),
                                 "order_type": row.get("order_type"), "currency": row["currency"], "status": status}
        if order_key in order_keys:
            if order_keys[order_key] != order:
                raise BrokerError("duplicate_order_conflict")
            continue
        order_keys[order_key] = order
        result["orders"].append(order)
    fill_ids = {}
    for row in raw.get("fills", []):
        if row.get("account_id") != selected:
            continue
        fill = {"execution_id": row["execution_id"], "account_key": key,
                                "perm_id": row.get("perm_id"), "con_id": int(row["con_id"]),
                                "instrument_id": row["instrument_id"], "side": row["side"].lower(),
                                "quantity": _money(row["quantity"], nonnegative=True), "price": _money(row["price"], nonnegative=True),
                                "currency": row["currency"], "executed_at": row.get("executed_at"),
                                "time_status": "verified" if row.get("executed_at") else "timezone_unknown",
                                "commission": _optional_money(row.get("commission")),
                                "commission_currency": row.get("commission_currency"),
                                "fee_status": "known" if row.get("commission") is not None else "pending"}
        if fill["execution_id"] in fill_ids:
            if fill_ids[fill["execution_id"]] != fill:
                raise BrokerError("duplicate_execution_conflict")
            continue
        fill_ids[fill["execution_id"]] = fill
        result["fills"].append(fill)
        if fill["commission"] is None or fill["commission_currency"] is None:
            result["issues"].append("execution_fees_pending")
    result["coverage"]["fills"]["executed_at_unknown_count"] = sum(fill["executed_at"] is None for fill in result["fills"])
    # A broker base-currency AvailableFunds value cannot be used as USD cash.
    currencies = {currency for tag, currency in ledger if tag in {"CashBalance", "TotalCashBalance", "SettledCash", "TotalCashValue"} and currency != "BASE"}
    for currency in sorted(currencies):
        gross_row = next((ledger[(tag, currency)] for tag in ("CashBalance", "TotalCashBalance", "TotalCashValue") if (tag, currency) in ledger), None)
        settled_row = ledger.get(("SettledCash", currency))
        gross = gross_row["value"] if gross_row else None
        settled = settled_row["value"] if settled_row else None
        reserved = Decimal(0)
        unknown = not result["coverage"]["orders"]["complete"]
        for order in result["orders"]:
            if order["currency"] != currency or order["status"] in _TERMINAL:
                continue
            if order["status"] == "unknown":
                unknown = True
            if order["side"] == "buy":
                if order["limit_price"] is None or Decimal(order["limit_price"]) <= 0 or order["order_type"] != "LMT":
                    unknown = True
                else:
                    reserved += Decimal(order["remaining_quantity"]) * Decimal(order["limit_price"])
        net = None if settled is None or unknown else _money(max(Decimal(0), Decimal(_money(settled)) - reserved))
        result["cash"][currency] = {"gross": _optional_money(gross), "settled": _optional_money(settled),
                                     "gross_evidence": {field: gross_row[field] for field in ("raw_tag", "source", "value_scope")} if gross_row else None,
                                     "settled_evidence": {field: settled_row[field] for field in ("raw_tag", "source", "value_scope")} if settled_row else None,
                                     "reserved": None if unknown else _money(reserved),
                                     "reservation_basis": "open_buy_limit_orders_excludes_fees" if not unknown else "unknown",
                                     "available": net, "available_basis": "settled_cash_net_open_orders"}
        if gross is None or settled is None or unknown:
            result["issues"].append("cash_or_reservation_unknown:" + currency)
    if "USD" not in result["cash"]:
        result["issues"].append("USD_cash_missing")
    contracts = raw.get("contracts", {})
    from .instruments import ETF_REGISTRY
    for symbol in symbols:
        candidates = contracts.get(symbol, [])
        if len(candidates) != 1:
            result["issues"].append("contract_missing_or_ambiguous:" + symbol)
            continue
        contract = dict(candidates[0])
        if (contract.get("sec_type") != "STK" or contract.get("currency") != "USD"
                or int(contract.get("con_id", 0)) <= 0 or str(contract.get("symbol", "")).replace(" ", ".").upper() != symbol):
            result["issues"].append("contract_identity_mismatch:" + symbol)
            continue
        contract["asset_class"] = "etf" if symbol in ETF_REGISTRY else "stock"
        contract["tick_size"] = _optional_money(contract.get("tick_size"))
        contract["market_rule"] = [{"low_edge": _money(row["low_edge"], nonnegative=True), "increment": _money(row["increment"], nonnegative=True)} for row in contract.get("market_rule", [])]
        q = _quote(raw.get("quotes", {}).get(symbol, {}), contract, symbol, settings, moment)
        result["quotes"][symbol] = q
        result["issues"].extend(issue + ":" + symbol for issue in q["issues"])
        for position in result["positions"]:
            if position["instrument_id"] == symbol and (position["con_id"] != contract["con_id"] or position["currency"] != contract["currency"]):
                result["issues"].append("position_contract_mismatch:" + symbol)
            elif position["instrument_id"] == symbol and contract["asset_class"] == "stock":
                position.update(sector=q["sector"], sector_evidence=copy.deepcopy(q["sector_evidence"]))
        for order in result["orders"]:
            if order["instrument_id"] == symbol and order["con_id"] != contract["con_id"]:
                result["issues"].append("order_contract_mismatch:" + symbol)
    if base != "USD":
        pair = base + ".USD"
        candidates = contracts.get(pair, [])
        if len(candidates) == 1:
            contract = dict(candidates[0], asset_class="forex")
            if contract.get("sec_type") == "CASH" and contract.get("symbol") == base and contract.get("currency") == "USD":
                q = _quote(raw.get("quotes", {}).get(pair, {}), contract, pair, settings, moment, forex=True)
                q["base_currency"], q["quote_currency"] = base, "USD"
                q["rate"] = _money((Decimal(q["bid"]) + Decimal(q["ask"])) / 2) if q["status"] == "ready" else None
                q["purpose"] = "NAV_risk_conversion_only_not_currency_funding"
                result["fx_rates"][pair] = q
        if not result["fx_rates"].get(pair, {}).get("rate"):
            result["issues"].append("NAV_FX_unknown")
    if len(result["quotes"]) != len(symbols):
        result["coverage"]["quotes"]["complete"] = False
    for section in _SECTIONS:
        if not result["coverage"][section]["complete"]:
            result["issues"].append("incomplete:" + section)
    expiries = [_clock(q["valid_until"]) for q in list(result["quotes"].values()) + list(result["fx_rates"].values())]
    result["valid_until"] = min([moment + timedelta(seconds=settings["quote_ttl_seconds"]), *expiries]).isoformat()
    result["issues"] = sorted(set(result["issues"]))
    result["complete"] = not result["issues"]
    result["status"] = "ready" if result["complete"] else "blocked"
    semantic = {name: result[name] for name in ("account", "cash", "positions", "orders", "fills")}
    for name in ("positions", "orders", "fills"):
        semantic[name] = sorted(semantic[name], key=lambda row: json.dumps(row, sort_keys=True))
    result["account_version"] = "account-" + _digest(semantic)
    # Upstream diagnostics never enter the public result, even from a fake transport.
    serialized = json.dumps(result, ensure_ascii=False)
    if selected in serialized:
        raise BrokerError("account_identity_in_public_payload")
    return _seal(result)


def collect_execution_snapshot(settings: dict, instrument_ids: list[str], *, now=None, transport=None) -> dict:
    """Collect one immutable read-only view. Failure is structured, never an empty book."""
    moment = _clock(now)
    result = _base(moment)
    try:
        cfg = _settings(settings)
        if not isinstance(instrument_ids, list) or not 1 <= len(instrument_ids) <= 16:
            raise ValueError("broker requires 1 to 16 instruments")
        symbols = list(dict.fromkeys(str(s).strip().upper() for s in instrument_ids))
        if any(not re.fullmatch(r"[A-Z][A-Z0-9.]{0,11}", s) for s in symbols):
            raise ValueError("broker supports US stock/ETF symbols only")
        if not cfg["enabled"] and transport is None:
            result["issues"] = ["broker_disabled"]
            return _seal(result)
        reader = transport or ReadOnlyIBKRTransport()
        raw = reader.collect(cfg, symbols, now=moment)
        return _normalize(raw, cfg, symbols, _clock() if now is None else moment)
    except BrokerError as exc:
        result["issues"] = [str(exc)]
    except (ValueError, KeyError, TypeError, InvalidOperation, OverflowError):
        result["issues"] = ["broker_settings_or_payload_invalid"]
    except Exception:
        # A socket or SDK exception can carry host paths/account IDs. Do not expose it.
        result["issues"] = ["broker_transport_failed"]
    return _seal(result)


def validate_execution_snapshot(snapshot: dict, *, now=None) -> dict:
    """Validate stored broker provenance/freshness; does not certify unchanged live state."""
    issues = []
    try:
        if not isinstance(snapshot, dict):
            raise TypeError("broker snapshot must be an object")
        moment = _clock(now)
        if snapshot.get("schema_version") != SCHEMA_VERSION or snapshot.get("provider") != "ibkr":
            issues.append("broker_schema_invalid")
        if snapshot.get("snapshot_id") != "broker-" + _digest({k: v for k, v in snapshot.items() if k != "snapshot_id"}):
            issues.append("broker_digest_invalid")
        if _clock(snapshot["as_of"]) > moment + timedelta(seconds=2) or _clock(snapshot["valid_until"]) <= moment:
            issues.append("broker_snapshot_expired_or_future")
        if snapshot.get("status") != "ready" or snapshot.get("complete") is not True:
            issues.append("broker_snapshot_incomplete")
        source_issues = snapshot.get("issues", [])
        if not isinstance(source_issues, list) or any(not isinstance(issue, str) for issue in source_issues):
            raise TypeError("broker issues must be strings")
        issues.extend(source_issues)
    except (ValueError, KeyError, TypeError):
        issues.append("broker_snapshot_malformed")
    return {"status": "blocked" if issues else "ready", "complete": not issues,
            "issues": sorted(set(issues))}


class ReadOnlyIBKRTransport:
    """Official socket SDK behind a query-only facade; no SDK object is returned."""

    def collect(self, settings, symbols, *, now=None):
        _silence_sdk_logs()
        try:
            from ibapi.client import EClient
            from ibapi.wrapper import EWrapper
            from ibapi.contract import Contract
            from ibapi.execution import ExecutionFilter
        except ImportError:
            raise BrokerError("ibapi_sdk_missing") from None
        _silence_sdk_logs()
        reader = _IBCollector(settings, symbols, Contract, ExecutionFilter)

        class Client(EWrapper, EClient):
            def __init__(self):
                EWrapper.__init__(self)
                EClient.__init__(self, self)

            def error(self, *args):
                # Current official SDK supplies errorTime before errorCode.
                code = args[2] if len(args) >= 4 and isinstance(args[2], int) else args[1] if len(args) >= 3 else None
                reader.error(code, req_id=args[0] if args else None)

            def nextValidId(self, orderId): reader.connected.set()
            def managedAccounts(self, accountsList): reader.managed(accountsList)
            def accountSummary(self, reqId, account, tag, value, currency): reader.account_value(account, tag, value, currency, source="account_summary")
            def accountSummaryEnd(self, reqId): reader.done("accounts")
            def updateAccountValue(self, key, val, currency, accountName): reader.account_value(accountName, key, val, currency, source="account_updates")
            def accountDownloadEnd(self, accountName): reader.done("cash")
            def position(self, account, contract, position, avgCost): reader.position(account, contract, position, avgCost)
            def positionEnd(self): reader.done("positions")
            def openOrder(self, orderId, contract, order, orderState): reader.order(orderId, contract, order, orderState)
            def openOrderEnd(self): reader.done("orders")
            def orderStatus(self, orderId, status, filled, remaining, avgFillPrice, permId, parentId, lastFillPrice, clientId, whyHeld, mktCapPrice=0): reader.order_status(orderId, status, filled, remaining, permId)
            def execDetails(self, reqId, contract, execution): reader.fill(contract, execution)
            def execDetailsEnd(self, reqId): reader.done("fills")
            def commissionReport(self, commissionReport): reader.commission(commissionReport)
            def commissionAndFeesReportProtoBuf(self, report): reader.commission_proto(report)
            def commissionAndFeesReport(self, commissionAndFeesReport): reader.commission(commissionAndFeesReport)
            def contractDetails(self, reqId, contractDetails): reader.contract(reqId, contractDetails)
            def contractDetailsEnd(self, reqId): reader.contract_end(reqId)
            def marketRule(self, marketRuleId, priceIncrements): reader.market_rule(marketRuleId, priceIncrements)
            def marketDataType(self, reqId, marketDataType): reader.quote(reqId)["actual_data_type"] = marketDataType
            def tickPrice(self, reqId, tickType, price, attrib): reader.price(reqId, tickType, price)
            def tickSize(self, reqId, tickType, size): reader.size(reqId, tickType, size)
            def tickGeneric(self, reqId, tickType, value):
                if tickType == 49: reader.quote(reqId)["halted"] = value
            def tickSnapshotEnd(self, reqId): reader.quote_end(reqId)
            def connectionClosed(self): reader.error(1100)

            def placeOrder(self, *args, **kwargs): raise BrokerError("order_mutation_forbidden")
            def cancelOrder(self, *args, **kwargs): raise BrokerError("order_mutation_forbidden")
            def reqGlobalCancel(self, *args, **kwargs): raise BrokerError("order_mutation_forbidden")
            def reqOpenOrders(self, *args, **kwargs): raise BrokerError("order_binding_forbidden")
            def reqAutoOpenOrders(self, *args, **kwargs): raise BrokerError("order_binding_forbidden")

        client = Client()
        reader.client = client
        thread = None
        try:
            client.connect(settings["host"], settings["port"], settings["client_id"])
            thread = threading.Thread(target=client.run, daemon=True)
            thread.start()
            reader.wait(reader.connected)
            client.reqManagedAccts()
            reader.wait(reader.managed_end)
            account = settings.get("account_id") or (reader.raw["accounts"][0] if len(reader.raw["accounts"]) == 1 else None)
            if account not in reader.raw["accounts"]:
                raise BrokerError("account_selection_missing_or_ambiguous")
            reader.selected = account
            client.reqAccountSummary(1, "All", "AccountType,NetLiquidation,TotalCashValue,SettledCash,AvailableFunds,BuyingPower,$LEDGER:ALL")
            client.reqAccountUpdates(True, account)
            client.reqPositions()
            client.reqAllOpenOrders()
            filter_ = ExecutionFilter()
            filter_.acctCode = account
            client.reqExecutions(2, filter_)
            for section in ("accounts", "cash", "positions", "orders", "fills"):
                reader.wait(reader.ends[section])
            reader.request_contracts(_expanded_symbols(reader.raw, account, symbols))
            base = reader.base_currency()
            if base and base != "USD": reader.request_contracts([base + ".USD"], forex=True)
            for event in list(reader.contract_ends.values()): reader.wait(event)
            client.reqMarketDataType(1)
            reader.request_quotes()
            for event in list(reader.quote_ends.values()): reader.wait_quote(event)
            reader.raw["complete"]["contracts"] = all(event.is_set() for event in reader.contract_ends.values())
            reader.raw["complete"]["quotes"] = all(event.is_set() for event in reader.quote_ends.values()) and not reader.failed_quotes
            return copy.deepcopy(reader.raw)
        finally:
            # Cancels below stop local data queries, never an order or paid subscription.
            if client.isConnected():
                for request_id in reader.quote_ends: client.cancelMktData(request_id)
                client.cancelAccountSummary(1)
                client.cancelPositions()
                if reader.selected: client.reqAccountUpdates(False, reader.selected)
            client.disconnect()
            if thread: thread.join(timeout=1)


class _IBCollector:
    """Callbacks keep raw IDs local until normalization. Bounded by one deadline."""

    def __init__(self, settings, symbols, contract_type, filter_type):
        self.settings, self.contract_type = settings, contract_type
        self.deadline = time.monotonic() + settings["timeout_seconds"]
        self.client, self.selected = None, None
        self.connected, self.managed_end = threading.Event(), threading.Event()
        self.ends = {section: threading.Event() for section in _SECTIONS}
        self.contract_ends, self.quote_ends, self.request_symbols, self.rule_requests = {}, {}, {}, {}
        self.raw = {"accounts": [], "account_values": [], "positions": [], "orders": [], "fills": [],
                    "contracts": {}, "quotes": {}, "complete": {}, "issues": []}
        self._order_statuses, self._commissions, self._commission_presence = {}, {}, {}
        self._fatal_issue, self.failed_quotes = None, set()
        self._next = 100

    def wait(self, event):
        if not event.wait(max(0, self.deadline - time.monotonic())):
            raise BrokerError("ibkr_query_timeout")
        if self._fatal_issue:
            raise BrokerError(self._fatal_issue)

    def wait_quote(self, event):
        # An unavailable feed must not erase independently completed account reads.
        if not event.wait(max(0, self.deadline - time.monotonic())):
            self.failed_quotes.update(req_id for req_id, item in self.quote_ends.items() if item is event)
            if "ibkr_quote_query_timeout" not in self.raw["issues"]:
                self.raw["issues"].append("ibkr_quote_query_timeout")
        if self._fatal_issue:
            raise BrokerError(self._fatal_issue)

    def error(self, code, *, req_id=None):
        if code in {2104, 2106, 2107, 2108, 2158}:
            return
        if code in {2103, 2105, 2186}:
            issue = {2103: "ibkr_market_data_farm_disconnected",
                     2105: "ibkr_historical_data_farm_disconnected",
                     2186: "ibkr_api_realtime_subscription_required"}[code]
            if issue not in self.raw["issues"]: self.raw["issues"].append(issue)
            return
        issue = "ibkr_not_entitled" if code in {354, 10089, 10090, 10167, 10168} else "ibkr_connection_or_query_error"
        if issue not in self.raw["issues"]: self.raw["issues"].append(issue)
        if req_id in self.quote_ends and code in {354, 10089, 10090, 10167, 10168}:
            self.failed_quotes.add(req_id)
            self.quote_ends[req_id].set()
            return
        self._fatal_issue = issue
        self.connected.set()
        self.managed_end.set()
        for event in list(self.ends.values()) + list(self.contract_ends.values()) + list(self.quote_ends.values()): event.set()

    def managed(self, value):
        self.raw["accounts"] = [account for account in value.split(",") if account]
        self.managed_end.set()

    def done(self, section):
        self.raw["complete"][section] = True
        self.ends[section].set()

    def account_value(self, account, tag, value, currency, *, source="unknown"):
        try:
            row = _account_observation({"account_id": account, "tag": tag, "value": value, "currency": currency, "source": source})
        except (ValueError, BrokerError):
            self.error(None)
            return
        identity = (account, row["raw_tag"], currency, row["source"])
        old = next((r for r in self.raw["account_values"] if (r["account_id"], r.get("raw_tag", r["tag"]), r["currency"], r.get("source", "unknown")) == identity), None)
        if old and old["value"] != row["value"]: self.raw["changed_during_collection"] = True
        if old: self.raw["account_values"].remove(old)
        self.raw["account_values"].append(row)

    def base_currency(self):
        return _account_base_currency(self.raw["account_values"], self.selected)[0]

    @staticmethod
    def identity(contract):
        return {"con_id": contract.conId, "instrument_id": contract.symbol.replace(" ", "."),
                "currency": contract.currency, "sec_type": getattr(contract, "secType", None)}

    def position(self, account, contract, quantity, avg_cost):
        row = dict(self.identity(contract), account_id=account, quantity=str(quantity), avg_cost=avg_cost)
        old = next((r for r in self.raw["positions"] if r["account_id"] == account and r["con_id"] == contract.conId), None)
        if old and old != row: self.raw["changed_during_collection"] = True
        if old: self.raw["positions"].remove(old)
        self.raw["positions"].append(row)

    def order(self, order_id, contract, order, state):
        row = dict(self.identity(contract), account_id=order.account, order_id=order_id,
                   perm_id=order.permId, client_id=order.clientId, side=order.action,
                   total_quantity=str(order.totalQuantity), status=state.status,
                   order_type=order.orderType, limit_price=order.lmtPrice if order.orderType == "LMT" else None)
        row.update(self._order_statuses.get((order_id, order.permId), {}))
        old = next((r for r in self.raw["orders"] if r["order_id"] == order_id and r["perm_id"] == order.permId), None)
        if old and old != row: self.raw["changed_during_collection"] = True
        if old: self.raw["orders"].remove(old)
        self.raw["orders"].append(row)

    def order_status(self, order_id, status, filled, remaining, perm_id):
        row = dict(status=status, filled_quantity=str(filled), remaining_quantity=str(remaining), perm_id=perm_id)
        previous = self._order_statuses.get((order_id, perm_id))
        if previous and previous != row: self.raw["changed_during_collection"] = True
        self._order_statuses[(order_id, perm_id)] = row
        for order in self.raw["orders"]:
            if order["order_id"] == order_id and order["perm_id"] == perm_id: order.update(row)

    def fill(self, contract, execution):
        row = dict(self.identity(contract), account_id=execution.acctNumber,
                   execution_id=execution.execId, perm_id=execution.permId,
                   side="buy" if execution.side == "BOT" else "sell" if execution.side == "SLD" else "unknown",
                   quantity=str(execution.shares), price=execution.price,
                   executed_at=_execution_timestamp(execution.time), execution_time_raw=execution.time)
        row.update(self._commissions.get(execution.execId, {}))
        if self.ends["fills"].is_set(): self.raw["changed_during_collection"] = True
        previous = next((r for r in self.raw["fills"] if r["execution_id"] == execution.execId), None)
        if previous and previous != row: self.raw["changed_during_collection"] = True
        if not previous: self.raw["fills"].append(row)

    def commission_proto(self, report):
        # 1050's decoder substitutes 0.0 when the protobuf field is absent.
        # Preserve HasField before the compatibility callback loses that fact.
        if not report.HasField("execId"):
            self.raw["issues"].append("execution_fee_identity_unknown")
            return
        self._commission_presence[report.execId] = report.HasField("commissionAndFees") and report.HasField("currency")

    def commission(self, report):
        value = getattr(report, "commissionAndFees", getattr(report, "commission", None))
        try: amount = _optional_money(value)
        except ValueError: amount = None
        if (self._commission_presence.get(report.execId) is False
                or not re.fullmatch("[A-Z]{3}", str(report.currency))):
            amount = None
        data = {"commission": amount, "commission_currency": report.currency if amount is not None else None}
        self._commissions[report.execId] = data
        for fill in self.raw["fills"]:
            if fill["execution_id"] == report.execId: fill.update(data)

    def request_contracts(self, symbols, forex=False):
        for symbol in symbols:
            self._next += 1
            req_id = self._next
            contract = self.contract_type()
            contract.symbol = symbol.split(".")[0] if forex else symbol.replace(".", " ")
            contract.secType, contract.exchange, contract.currency = ("CASH", "IDEALPRO", "USD") if forex else ("STK", "SMART", "USD")
            self.request_symbols[req_id] = symbol
            self.contract_ends[req_id] = threading.Event()
            self.raw["contracts"][symbol] = []
            self.client.reqContractDetails(req_id, contract)

    def contract(self, req_id, details):
        symbol = self.request_symbols[req_id]
        contract = details.contract
        exchanges = str(details.validExchanges).split(",")
        rules = str(details.marketRuleIds).split(",")
        rule_id = next((int(rule) for exchange, rule in zip(exchanges, rules) if exchange == contract.exchange and rule.isdigit()), None)
        self.raw["contracts"][symbol].append({"symbol": contract.symbol, "con_id": contract.conId,
            "sec_type": contract.secType, "currency": contract.currency, "exchange": contract.exchange,
            "tick_size": details.minTick, "market_rule_id": rule_id, "market_rule": [],
            "liquid_hours": details.liquidHours, "time_zone_id": details.timeZoneId,
            "industry": getattr(details, "industry", None), "category": getattr(details, "category", None),
            "subcategory": getattr(details, "subcategory", None), "sdk_contract": contract})

    def contract_end(self, req_id): self.contract_ends[req_id].set()

    def market_rule(self, rule_id, increments):
        for contracts in self.raw["contracts"].values():
            for contract in contracts:
                if contract["market_rule_id"] == rule_id:
                    contract["market_rule"] = [{"low_edge": str(row.lowEdge), "increment": str(row.increment)} for row in increments]

    def request_quotes(self):
        for symbol, candidates in self.raw["contracts"].items():
            if len(candidates) != 1: continue
            contract = candidates[0]
            if contract["market_rule_id"] is not None: self.client.reqMarketRule(contract["market_rule_id"])
            self._next += 1
            req_id = self._next
            self.request_symbols[req_id] = symbol
            self.quote_ends[req_id] = threading.Event()
            self.raw["quotes"][symbol] = {}
            # Ordinary one-shot snapshot: never a regulatory paid snapshot or subscription change.
            self.client.reqMktData(req_id, contract.pop("sdk_contract"), "", True, False, [])

    def quote(self, req_id): return self.raw["quotes"].setdefault(self.request_symbols[req_id], {})

    def price(self, req_id, tick_type, price):
        field = {1: "bid", 2: "ask", 66: "bid", 67: "ask"}.get(tick_type)
        if field:
            self.quote(req_id)[field] = price
            self.quote(req_id)[field + "_received_at"] = datetime.now(timezone.utc).isoformat()
            if tick_type in {66, 67} and self.quote(req_id).get("actual_data_type") != 4:
                self.quote(req_id)["actual_data_type"] = 3

    def size(self, req_id, tick_type, size):
        field = {0: "bid_size", 3: "ask_size", 69: "bid_size", 70: "ask_size"}.get(tick_type)
        if field: self.quote(req_id)[field] = str(size)

    def quote_end(self, req_id): self.quote_ends[req_id].set()

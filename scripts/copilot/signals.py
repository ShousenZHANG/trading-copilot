"""Versioned research diagnostics from one saved snapshot, never order sizing.

Price history states describe observed prices, not expected returns. Current
collection/identity gates and the adopted-rule engine retain their authority.
"""
from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Literal, TypedDict

from .data_calendar import iso, parse_time, utc_now
from .instruments import get_instrument, normalize_research_instrument, verify_stock_identity
from .market_data import INDICATOR_FORMULA_VERSION, INDICATOR_MINIMUM_SAMPLES, verify_snapshot

SIGNAL_SCHEMA_VERSION = 1
SIGNAL_RULE_VERSION = "close-sma50-sma200-return20-prior20-breakout-v1"

Trend = Literal["up", "down", "mixed", "unknown"]
Momentum = Literal["positive", "negative", "flat", "unknown"]
SignalStatus = Literal["conditions_met", "watch", "data_insufficient"]


class ResearchCondition(TypedDict):
    status: str
    description: str
    logic: str
    comparisons: list[dict]
    requires_new_snapshot: bool


class ResearchSignal(TypedDict):
    instrument_id: str
    asset_class: str
    context_role: str
    horizon: str
    signal_rule_version: str
    formula_version: str | None
    signal_status: SignalStatus
    direction: str
    trend_state: Trend
    momentum_state: Momentum
    setup: str
    reference_trigger_price: float | None
    reference_invalidation_price: float | None
    reference_basis: str | None
    reference_is_executable: Literal[False]
    reference_level_sources: dict
    trigger_condition: ResearchCondition
    invalidation_condition: ResearchCondition
    counter_evidence: list[dict]
    research_coverage: dict
    missing: list[str]
    evidence_ids: list[str]
    claims: list[dict]
    execution_scope: Literal["research_only"]
    adoption_eligible: Literal[False]


def _finite(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (not positive or value > 0))


def _market_ids(item: dict) -> list[str]:
    supplied = item.get("evidence_ids")
    if not isinstance(supplied, list):
        return []
    sections = item.get("research")
    research_ids = set()
    if isinstance(sections, dict):
        for section in sections.values():
            ids = section.get("evidence_ids") if isinstance(section, dict) else None
            if isinstance(ids, list):
                research_ids.update(eid for eid in ids if isinstance(eid, str))
    return [eid for eid in supplied if isinstance(eid, str) and eid not in research_ids]


def _blockers(snapshot: dict, symbol: str, item: dict, evidence: dict, current) -> list[str]:
    issues = []
    try:
        decision = parse_time(snapshot["decision_at"])
        created = parse_time(snapshot["created_at"])
        expiry = parse_time(snapshot["valid_until"])
    except (KeyError, TypeError, ValueError, AttributeError):
        return ["snapshot_time_invalid"]
    if current >= expiry or created >= expiry:
        issues.append("snapshot_expired")
    if decision > current + timedelta(seconds=5) or decision > created + timedelta(seconds=5) or created > current + timedelta(seconds=5):
        issues.append("snapshot_future_time")
    if snapshot.get("status") == "blocked":
        issues.append("snapshot_blocked")
    if item.get("instrument_id") != symbol:
        issues.append("instrument_identity_mismatch")
    if item.get("asset_class") == "stock":
        try:
            verify_stock_identity(snapshot, symbol)
        except ValueError:
            issues.append("common_stock_identity_unverified")
    else:
        try:
            registered = get_instrument(symbol)
            if any(item.get(key) != registered[key] for key in ("asset_class", "currency", "unit", "price_kind")):
                issues.append("registered_instrument_semantics_mismatch")
        except ValueError:
            issues.append("registered_instrument_identity_unknown")
    metrics = item.get("indicators", {})
    if not isinstance(metrics, dict) or metrics.get("formula_version") != INDICATOR_FORMULA_VERSION:
        issues.append("indicator_formula_version_unsupported")
        metrics = metrics if isinstance(metrics, dict) else {}
    if not isinstance(metrics.get("missing"), list):
        issues.append("indicator_missing_metadata_malformed")
    if metrics.get("basis") not in {"total_return_adjusted", "split_adjusted", "index_points", "unadjusted_benchmark", "single_current_session"}:
        issues.append("indicator_basis_unsupported")
    count = metrics.get("sample_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        issues.append("indicator_sample_count_invalid")
    elif any(metrics.get(key) is not None and count < minimum for key, minimum in INDICATOR_MINIMUM_SAMPLES.items()):
        issues.append("indicator_sample_count_insufficient")
    supplied_ids = item.get("evidence_ids")
    ids = _market_ids(item)
    verification = item.get("verification", {})
    primary_id = verification.get("primary_evidence_id") if isinstance(verification, dict) else None
    if (not isinstance(supplied_ids, list) or not ids or not all(isinstance(eid, str) for eid in supplied_ids)
            or not isinstance(primary_id, str) or primary_id not in ids or primary_id not in evidence):
        return [*issues, "primary_market_evidence_binding_missing"]
    try:
        latest = date.fromisoformat(item["latest_session"])
        if latest != date.fromisoformat(item["expected_session"]) or latest > decision.date():
            issues.append("market_session_cutoff_mismatch")
        for eid in ids:
            record = evidence[eid]
            if record.get("instrument_id") != symbol or record.get("status") != "ok" or record.get("cache_status", "live") != "live":
                issues.append("market_evidence_identity_or_status_invalid")
            retrieved = parse_time(record["retrieved_at"])
            if retrieved > created + timedelta(seconds=5):
                issues.append("market_evidence_future_retrieval")
            if retrieved < decision - timedelta(days=1):
                issues.append("market_evidence_stale_retrieval")
            for field in ("published_at", "available_at", "observed_at"):
                if record.get(field) is not None and parse_time(record[field]) > decision:
                    issues.append("market_evidence_not_available_at_cutoff")
            bars = record.get("bars")
            if not isinstance(bars, list) or not bars:
                issues.append("market_evidence_bars_missing")
                continue
            if any(date.fromisoformat(bar["session"]) > latest for bar in bars):
                issues.append("future_market_bar")
        primary = evidence[primary_id]
        bars = primary.get("bars", [])
        if primary.get("indicator_basis", "split_adjusted") != metrics.get("basis") or metrics.get("sample_count") != len(bars):
            issues.append("indicator_primary_basis_or_sample_mismatch")
        close_key = "adjusted_close" if metrics.get("basis") == "total_return_adjusted" else "close"
        if not bars or bars[-1].get("session") != item.get("latest_session") or bars[-1].get(close_key) != metrics.get("reference_close"):
            issues.append("indicator_reference_close_mismatch")
    except (KeyError, TypeError, ValueError, AttributeError):
        issues.append("market_evidence_malformed")
    return list(dict.fromkeys(issues))


def _signal(symbol: str, item: dict, horizon: str, evidence: dict, blockers: list[str]) -> ResearchSignal:
    metrics = item.get("indicators", {})
    if not isinstance(metrics, dict):
        metrics = {}
    missing_values = metrics.get("missing")
    missing = [str(value) for value in missing_values] if isinstance(missing_values, list) else []
    if item.get("quality_status") != "pass":
        blockers = [*blockers, "market_quality_not_pass"]
    missing.extend(blockers)
    ref, medium, long = (metrics.get(key) for key in ("reference_close", "sma50", "sma200"))
    trend: Trend = "unknown"
    if not blockers and all(_finite(value, positive=True) for value in (ref, medium, long)):
        trend = "up" if ref > medium > long else "down" if ref < medium < long else "mixed"
    else:
        missing.append("trend: valid same-basis reference_close/SMA50/SMA200 required")
    change = metrics.get("return_20_sessions")
    momentum: Momentum = "unknown"
    if not blockers and _finite(change):
        momentum = "positive" if change > 0 else "negative" if change < 0 else "flat"
    else:
        missing.append("momentum: valid return_20_sessions required")
    direction = "unknown" if "unknown" in (trend, momentum) else "neutral"
    if trend == "up" and momentum == "positive":
        direction = "bullish"
    elif trend == "down" and momentum == "negative":
        direction = "bearish"
    status: SignalStatus = "data_insufficient" if direction == "unknown" else "watch"
    trigger_key = "low_previous_20_sessions" if direction == "bearish" else "high_previous_20_sessions"
    trigger_level = metrics.get(trigger_key) if direction in {"bullish", "bearish"} else None
    invalidation_level = medium if direction in {"bullish", "bearish"} else None
    if direction in {"bullish", "bearish"}:
        if not _finite(trigger_level, positive=True):
            status, direction = "data_insufficient", "unknown"
            trigger_level = invalidation_level = None
            missing.append("trigger: valid prior-20-session same-basis close range required")
        elif (ref < trigger_level if direction == "bearish" else ref > trigger_level):
            status = "conditions_met"
    prefix = "/instruments/" + symbol.replace("~", "~0").replace("/", "~1") + "/indicators/"
    operator = "<" if direction == "bearish" else ">"
    comparisons = [
        {"left_path": prefix + "reference_close", "operator": operator, "right_path": prefix + "sma50"},
        {"left_path": prefix + "sma50", "operator": operator, "right_path": prefix + "sma200"},
        {"left_path": prefix + "return_20_sessions", "operator": operator, "right_constant": 0},
        {"left_path": prefix + "reference_close", "operator": operator, "right_path": prefix + trigger_key},
    ]
    verification = item.get("verification")
    primary = verification.get("primary_evidence_id") if isinstance(verification, dict) else None
    ids = _market_ids(item)
    if not isinstance(primary, str) or primary not in ids or primary not in evidence:
        primary = None
    claims = []
    if primary is not None and not blockers:
        for key in ("reference_close", "sma50", "sma200", "rsi14", "atr14_percent",
                    "distance_sma50_percent", "distance_sma200_percent", "return_20_sessions",
                    "high_previous_20_sessions", "low_previous_20_sessions",
                    "average_volume_previous_20_sessions", "volume_ratio_20_sessions"):
            value = metrics.get(key)
            if _finite(value):
                claims.append({"evidence_id": primary, "path": prefix + key, "value": value})
    research = item.get("research", {})
    research = research if isinstance(research, dict) else {}
    coverage = {name: section["status"] if isinstance(section, dict) and isinstance(section.get("status"), str) else "unknown"
                for name, section in research.items()}
    for name in ("news", "filings"):
        coverage.setdefault(name, "unknown" if item.get("asset_class") == "stock" or name == "news" else "not_applicable")
    counter = [{"kind": "limitation", "description": "趋势与动量只描述过去价格，不能证实未来收益", "evidence_ids": list(ids)}]
    if (trend, momentum) in {("up", "negative"), ("down", "positive")}:
        counter.insert(0, {"kind": "observed_conflict", "description": "均线趋势与近期动量方向冲突", "evidence_ids": list(ids)})
    if direction in {"bullish", "bearish"} and status == "watch":
        counter.insert(0, {"kind": "unmet_trigger", "description": "均线与动量同向，但收盘尚未跨越此前交易日收盘区间", "evidence_ids": list(ids)})
    return {
        "instrument_id": symbol, "asset_class": item.get("asset_class", "unknown"),
        "context_role": "market_benchmark" if item.get("asset_class") == "index" else
                        "gold_benchmark" if item.get("asset_class") == "physical_gold" else "instrument",
        "horizon": horizon, "signal_rule_version": SIGNAL_RULE_VERSION,
        "formula_version": metrics.get("formula_version"), "signal_status": status,
        "direction": direction, "trend_state": trend, "momentum_state": momentum,
        "setup": "trend_momentum_prior_close_breakout",
        "reference_trigger_price": trigger_level, "reference_invalidation_price": invalidation_level,
        "reference_basis": metrics.get("basis"), "reference_is_executable": False,
        "reference_level_sources": {
            "trigger": {"evidence_id": primary, "path": prefix + trigger_key,
                        "formula": "min(closes[-21:-1])" if direction == "bearish" else "max(closes[-21:-1])"},
            "invalidation": {"evidence_id": primary, "path": prefix + "sma50", "formula": "mean(closes[-50:])"},
        } if direction in {"bullish", "bearish"} else {},
        "trigger_condition": {"status": "observed" if status == "conditions_met" else "unknown" if status == "data_insufficient" else "waiting",
            "description": "同价基均线与动量同向，且已完成交易日收盘跨越此前收盘区间；后续须以新快照重新确认",
            "logic": "all",
            "comparisons": comparisons if direction in {"bullish", "bearish"} else [], "requires_new_snapshot": True},
        "invalidation_condition": {"status": "not_observed" if status == "conditions_met" else "unknown" if status == "data_insufficient" else "not_applicable",
            "description": "任一方向条件不再满足，或行情、身份、证据有效期失效，即撤销本研究条件",
            "logic": "any",
            "comparisons": [{**entry, "operator": ">=" if operator == "<" else "<="} for entry in comparisons]
                           if direction in {"bullish", "bearish"} else [], "requires_new_snapshot": True},
        "counter_evidence": counter, "research_coverage": coverage,
        "missing": list(dict.fromkeys(missing)), "evidence_ids": list(ids), "claims": claims,
        "execution_scope": "research_only", "adoption_eligible": False,
    }


def analyze(snapshot: dict, instrument_ids: list[str] | None = None, horizon: str = "swing", *, now=None) -> dict:
    """Analyze a sealed snapshot. No data collection, portfolio access or orders.

    ``now`` is injectable for archived/offline fixtures; live callers use UTC.
    Every result remains bound to the snapshot's expiry and research scope.
    """
    if not verify_snapshot(snapshot) or snapshot.get("schema_version") != 1:
        raise ValueError("research analysis requires an intact stored snapshot")
    instruments, records = snapshot.get("instruments"), snapshot.get("evidence")
    if not isinstance(instruments, dict) or not isinstance(records, list):
        raise ValueError("snapshot instruments/evidence are malformed")
    if horizon not in {"daily", "swing", "long_term", "long-term", "accumulation"}:
        raise ValueError("unsupported research horizon")
    horizon = "long_term" if horizon in {"long-term", "accumulation"} else horizon
    if instrument_ids is not None and (not isinstance(instrument_ids, list) or not instrument_ids or len(instrument_ids) > 25):
        raise ValueError("instrument_ids must contain 1 to 25 snapshot symbols")
    selected = list(dict.fromkeys(normalize_research_instrument(symbol) for symbol in instrument_ids)) if instrument_ids else list(instruments)
    if not selected or len(selected) > 25 or any(symbol not in instruments for symbol in selected):
        raise ValueError("research instruments must belong to the stored snapshot")
    current = parse_time(now if now is not None else utc_now())
    evidence = {entry["evidence_id"]: entry for entry in records if isinstance(entry, dict) and isinstance(entry.get("evidence_id"), str)}
    if len(evidence) != len(records) or any(not isinstance(instruments[symbol], dict) for symbol in selected):
        raise ValueError("snapshot evidence identities or instruments are malformed")
    signals = {symbol: _signal(symbol, instruments[symbol], horizon, evidence,
                              _blockers(snapshot, symbol, instruments[symbol], evidence, current)) for symbol in selected}
    statuses = [signal["signal_status"] for signal in signals.values()]
    return {
        "schema_version": SIGNAL_SCHEMA_VERSION, "signal_rule_version": SIGNAL_RULE_VERSION,
        "snapshot_id": snapshot["snapshot_id"], "decision_at": snapshot.get("decision_at"),
        "valid_until": snapshot.get("valid_until"), "analyzed_at": iso(current), "horizon": horizon,
        "status": "data_insufficient" if all(value == "data_insufficient" for value in statuses) else
                  "partial" if any(value == "data_insufficient" for value in statuses) else "available",
        "signals": signals, "execution_scope": "research_only", "adoption_eligible": False,
        "disclosure": "观察状态不是盈利预测；研究水平不是委托限价；精确计划仍需已采用规则与账户校验。",
    }

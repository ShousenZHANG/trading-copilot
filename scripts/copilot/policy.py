"""Deterministic gates for evidence-backed conversational proposals.

This module checks eligibility, not whether an investment thesis predicts returns.
Portfolio completeness and measured inputs come from the trusted application
context, never a model's assertion that its own risk checks passed.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime, timezone

POLICY_VERSION = "1.0"
ACTIONS = {"buy", "hold", "reduce", "sell", "avoid"}
_LIMITS = {
    "single_name": ("post_trade_weight", 0.05, False),
    "sector": ("post_trade_sector_weight", 0.25, False),
    "correlation": ("max_correlation", 0.7, True),
    "liquidity": ("position_adv_fraction", 0.01, False),
    "drawdown": ("drawdown", 0.15, False),
}
_INDICATOR_SAMPLES = {"sma20": 20, "sma50": 50, "sma200": 200, "rsi14": 15, "atr14": 15,
                      "return_20_sessions": 21, "return_252_sessions": 253,
                      "high_252_sessions": 252, "low_252_sessions": 252}

#: Concentration is a different risk for a fund than for a single company. A
#: broad-market ETF already holds hundreds of names, so the 5% ceiling written
#: for individual stocks makes any ETF sleeve unexecutable: 8-12 holdings is
#: 8-12% each and a top-5 momentum rotation is 20%. 25% is conventional, not
#: measured -- see ADR-0007 clause 2.
_SLEEVE_LIMITS = {"etf": {"single_name": 0.25}}

#: Checks that cannot be made informative for a sleeve, with the reason. This is
#: deliberately not a threshold: daily-return correlation among broad equity
#: ETFs is structurally 0.85-0.95, so 0.7 rejects every basket and any cap loose
#: enough to admit one rejects nothing. The informative measure is look-through
#: holdings overlap and no configured source provides fund constituents.
#: ADR-0007 clause 7 records the consequence: beyond single-name and sector
#: concentration the ETF sleeve has no diversification check.
_SLEEVE_EXEMPT = {
    "etf": {"correlation": "equity ETF return correlation is structurally 0.85-0.95, so no "
                           "threshold separates a diversified basket from a concentrated "
                           "one; look-through holdings overlap is the informative measure "
                           "and no configured source provides fund constituents"},
}


def limit_for(name: str, asset_class: str) -> float:
    """The limit for a risk check, per sleeve. Defaults to the stock limit."""
    return _SLEEVE_LIMITS.get(asset_class, {}).get(name, _LIMITS[name][1])


def _time(value: object, name: str) -> datetime:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("timezone required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"{name} must be an ISO datetime with timezone") from exc


def _number(value: object, positive: bool = False) -> bool:
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value) and (not positive or value > 0)


def _strings(value: object, name: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
        raise ValueError(f"{name} must be a list of nonempty strings")
    return list(value)


def _digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def proposal_fingerprint(proposal: dict) -> str:
    """Bind measured post-trade risks to the actual proposed trade inputs."""
    return _digest({key: proposal.get(key) for key in ("instrument_id", "action", "mode", "horizon", "price", "quantity", "target_weight", "stop_loss")})


def _finite_tree(value: object) -> bool:
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, dict):
        return all(_finite_tree(v) for v in value.values())
    if isinstance(value, list):
        return all(_finite_tree(v) for v in value)
    return True


def _pointer(value: object, path: str) -> object:
    """Resolve a strict RFC 6901 pointer without eval or permissive lookup."""
    if not isinstance(path, str) or not path.startswith("/"):
        raise ValueError("claim path must be a JSON Pointer")
    for raw in path.split("/")[1:]:
        if re.search(r"~(?![01])", raw):
            raise ValueError("invalid JSON Pointer escape")
        key = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(value, dict) and key in value:
            value = value[key]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", key) and int(key) < len(value):
            value = value[int(key)]
        else:
            raise ValueError("claim path is absent from stored evidence")
    return value


_NUMERIC_TEXT = re.compile(r"(?<![\d.])(?P<number>[-+−]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)(?:[eE][-+]?\d+)?)\s*(?P<scale>万亿|亿|万|千|百|trillion\b|billion\b|million\b|thousand\b)?(?P<percent>%|％)?(?P<ratio>倍)?", re.I)
_SCALES = {"万亿": 1e12, "亿": 1e8, "万": 1e4, "千": 1e3, "百": 1e2,
           "trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3}
_IDENTITY_OR_DATE = re.compile(
    r"(?<![A-Za-z])(?:RSI|SMA|EMA|ATR|MACD)\s*\d+(?!\d)|\b(?:snap_|ev_|research-|decision-)[A-Za-z0-9_-]+\b"
    r"|\brule-[0-9a-f]{16}\b"
    r"|(?<!\d)(?:19|20)\d{2}(?:[-/]\d{1,2}){2}(?!\d)|(?:19|20)\d{2}年(?:\d{1,2}月(?:\d{1,2}日)?)?"
    r"|\b(?:19|20)\d{2}Q[1-4]\b|\b(?:Nasdaq[- ]?100|S&P\s*500)\b|纳斯达克\s*100"
    r"|\b(?:DFII10|DGS10|DGS2|T10YIE|10-K|10-Q|8-K|20-F|6-K)(?:/A)?\b"
    r"|\d+\s*(?:日|天|周|月)均线", re.I)


def _claims(proposal: dict, snapshot: dict, evidence: dict, market_ids: set[str], ids: list[str]) -> tuple[list[dict], list[str]]:
    """Match structured scalar claims, then check literal numeric prose values.

    This checks source/value identity, not natural-language entailment or whether
    a thesis predicts returns. Dates/indicator identities are labels, not values.
    """
    supplied = proposal.get("claims", [])
    if not isinstance(supplied, list):
        raise ValueError("claims must be a list")
    verified, issues = [], []
    symbol = proposal["instrument_id"]
    instrument_prefix = "/instruments/" + symbol.replace("~", "~0").replace("/", "~1") + "/"
    for claim in supplied:
        if not isinstance(claim, dict) or not {"evidence_id", "path", "value"}.issubset(claim):
            issues.append("claim requires evidence_id, path and exact value")
            continue
        eid, path, expected = claim["evidence_id"], claim["path"], claim["value"]
        rec = evidence.get(eid) if isinstance(eid, str) else None
        if eid not in ids or rec is None:
            issues.append("claim cites an evidence ID absent from the proposal/snapshot")
            continue
        try:
            if isinstance(path, str) and path.startswith("/instruments/"):
                if eid not in market_ids or not (path == instrument_prefix + "price" or path.startswith(instrument_prefix + "indicators/")):
                    raise ValueError("computed claim must belong to this instrument and its market evidence")
                observed = _pointer(snapshot, path)
            else:
                observed = _pointer(rec, path)
            if observed is None or isinstance(observed, (dict, list, bool)) or not isinstance(observed, (int, float, str)):
                raise ValueError("claim must refer to a nonempty scalar fact")
            if isinstance(observed, (int, float)):
                if not _number(observed) or not _number(expected) or observed != expected:
                    raise ValueError("numeric claim does not exactly match stored evidence")
            elif not isinstance(expected, str) or observed != expected:
                raise ValueError("text claim does not exactly match stored evidence")
            verified.append({"evidence_id": eid, "path": path, "value": observed})
        except (ValueError, TypeError) as exc:
            issues.append(str(exc))
    values, percentages = [], []
    for claim in verified:
        value = claim["value"]
        if not _number(value):
            continue
        values.append(value)
        path, rec = claim["path"], evidence[claim["evidence_id"]]
        data = rec.get("data", {})
        unit = data.get("unit") if isinstance(data, dict) else None
        leaf = path.rsplit("/", 1)[-1]
        if path.startswith(instrument_prefix + "indicators/return_") or unit == "fraction":
            percentages.append(value * 100)
        elif leaf.endswith("_percent") or unit == "percent":
            percentages.append(value)
    for text in [*proposal["reasons"], *proposal["conditions"]]:
        for match in _NUMERIC_TEXT.finditer(_IDENTITY_OR_DATE.sub(" ", text)):
            raw = match["number"].replace(",", "").replace("−", "-")
            decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
            scale = _SCALES.get((match["scale"] or "").lower(), 1)
            stated = float(raw) * scale
            tolerance = 1e-10 if "e" in raw.lower() else 0.5 * (10 ** -decimals) * scale + 1e-10
            candidates = percentages if match["percent"] else values
            # Explicit scales and percentage units belong to the claim, too.
            # Literal ratio/multiple claims currently have no typed core source.
            if match["ratio"] or not math.isfinite(stated) or not any(abs(stated - value) <= tolerance for value in candidates):
                issues.append("unsupported numerical fact in reasons/conditions; provide a matching verified claim")
                break
    return verified, list(dict.fromkeys(issues))


def assess_proposal(proposal: dict, snapshot: dict, context: dict | None = None, *,
                    now=None, source: str = "model") -> dict:
    """Validate a model proposal against immutable evidence and trusted context.

    Invalid schema raises ValueError. Unavailable/stale/conflicting data returns
    data_insufficient. Unknown portfolio gates retain only a research direction,
    omit all precise sizing, and explicitly prevent an executable recommendation.
    """
    if not isinstance(proposal, dict) or not isinstance(snapshot, dict):
        raise ValueError("proposal and snapshot must be objects")
    context = {} if context is None else context
    if not isinstance(context, dict):
        raise ValueError("context must be an object")
    instrument_id = proposal.get("instrument_id")
    if not isinstance(instrument_id, str) or not instrument_id:
        raise ValueError("instrument_id is required")
    requested = proposal.get("action")
    if requested not in ACTIONS:
        raise ValueError(f"action must be one of {sorted(ACTIONS)}")
    mode, horizon = proposal.get("mode"), proposal.get("horizon")
    if mode not in {"accumulation", "tactical"} or horizon not in {"daily", "swing", "long_term"}:
        raise ValueError("unsupported mode or horizon")
    reasons = _strings(proposal.get("reasons"), "reasons")
    conditions = _strings(proposal.get("conditions"), "conditions")
    ids = _strings(proposal.get("evidence_ids"), "evidence_ids")
    if not reasons:
        raise ValueError("at least one reason is required")
    if source not in ("model", "engine"):
        raise ValueError(f"source must be 'model' or 'engine', got {source!r}")
    for key in ("price", "quantity", "target_weight", "stop_loss"):
        if key in proposal and not _number(proposal[key], positive=True):
            raise ValueError(f"{key} must be a finite positive number")
    if "target_weight" in proposal and proposal["target_weight"] > 1:
        raise ValueError("target_weight must be a fraction <= 1")
    if snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("snapshot_id"), str):
        raise ValueError("unsupported snapshot schema or missing snapshot_id")
    instruments, records = snapshot.get("instruments"), snapshot.get("evidence")
    if not isinstance(instruments, dict) or not isinstance(records, list):
        raise ValueError("snapshot needs instruments and evidence")
    item = instruments.get(instrument_id)
    if not isinstance(item, dict) or item.get("instrument_id") != instrument_id:
        raise ValueError("instrument is absent from snapshot or has mismatched identity")
    from .instruments import get_instrument
    identity = get_instrument(instrument_id)
    if identity["instrument_id"] != instrument_id:
        raise ValueError("proposal must use the canonical instrument_id")
    # ADR-0004 clause 2: the model has no interface through which it can alter
    # quantity, direction or rule identity. A model-supplied `price` is allowed
    # only where it can be checked: equal to the snapshot's own price for a
    # market instrument, or equal to a captured merchant quote for gold. Before
    # this, a proposal claiming 4242.0 against a snapshot price of 100.0
    # returned buy/actionable and the 4242.0 was written to an immutable table.
    if source == "model":
        for key in ("quantity", "target_weight", "stop_loss"):
            if key in proposal:
                raise ValueError(f"{key} is computed by the engine and must not be supplied "
                                 f"by a proposal; call evaluate_rule instead")
        if "price" in proposal and identity["asset_class"] != "physical_gold":
            if proposal["price"] != item.get("price"):
                raise ValueError(f"price must equal the snapshot price for {instrument_id}; "
                                 f"the proposal says {proposal['price']!r} and the snapshot "
                                 f"says {item.get('price')!r}")
    item_ids = _strings(item.get("evidence_ids"), "instrument.evidence_ids")
    research_sections = item.get("research", {})
    if not isinstance(research_sections, dict) or any(not isinstance(section, dict) for section in research_sections.values()):
        raise ValueError("instrument.research must contain section objects")
    research_ids = {eid for section in research_sections.values() for eid in _strings(section.get("evidence_ids", []), "research.evidence_ids")}
    market_ids = set(item_ids) - research_ids
    created = _time(now if now is not None else datetime.now(timezone.utc), "now")
    snapshot_time = _time(snapshot.get("created_at"), "snapshot.created_at")
    decision_time = _time(snapshot.get("decision_at"), "snapshot.decision_at")
    valid_until = _time(snapshot.get("valid_until"), "snapshot.valid_until")
    blockers: list[str] = []
    warnings: list[str] = []
    for key in ("currency", "unit", "tradable", "price_kind"):
        if item.get(key) != identity[key]:
            blockers.append(f"instrument {key} does not match canonical identity")
    if identity["asset_class"] in {"index", "physical_gold"} and item.get("asset_class") != identity["asset_class"]:
        blockers.append("benchmark instrument class cannot be changed")
    if identity["asset_class"] == "etf" and item.get("asset_class") != "etf":
        blockers.append("registered ETF class cannot be changed")
    # Import lazily so CLI clients need not load adapters until a real snapshot
    # is assessed. A matching digest checks identity; it is not provenance proof.
    from .market_data import verify_snapshot
    if not verify_snapshot(snapshot):
        blockers.append("snapshot integrity verification failed")
    if snapshot_time > created or decision_time > created or created >= valid_until or valid_until <= snapshot_time:
        blockers.append("snapshot expired or timestamp is invalid/future")
    if snapshot.get("status") not in {"ready", "partial"} or item.get("quality_status") != "pass":
        blockers.append("required instrument data has not passed quality checks")
    if item.get("latest_session") is None or item.get("latest_session") != item.get("expected_session"):
        blockers.append("latest completed session is missing or stale")
    else:
        try:
            if date.fromisoformat(item["latest_session"]) > created.date():
                blockers.append("session is in the future")
        except (TypeError, ValueError) as exc:
            raise ValueError("latest_session must be an ISO date") from exc
    if not _number(item.get("price"), positive=True) or not _finite_tree(item.get("indicators", {})):
        blockers.append("price or indicators contain invalid numeric data")
    indicators = item.get("indicators", {})
    if not isinstance(indicators, dict):
        raise ValueError("instrument.indicators must be an object")
    for name, value in indicators.items():
        minimum = _INDICATOR_SAMPLES.get(name.lower())
        count = indicators.get("sample_count")
        if minimum and value is not None and (not _number(value) or not isinstance(count, int) or isinstance(count, bool) or count < minimum):
            blockers.append(f"indicator {name} has insufficient/invalid sample provenance")
    for name in _strings(proposal.get("required_indicators", []), "required_indicators"):
        if not _number(indicators.get(name)):
            blockers.append(f"required indicator {name} is unavailable")
    if not item.get("currency") or not item.get("unit") or not item.get("price_kind") or not item.get("adjustment"):
        blockers.append("price currency/unit/kind/adjustment is unspecified")
    evidence: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("evidence_id"), str):
            raise ValueError("invalid evidence record")
        eid = record["evidence_id"]
        if eid in evidence:
            raise ValueError("duplicate evidence_id")
        evidence[eid] = record
    if not ids or not set(ids).intersection(market_ids):
        blockers.append("proposal has no evidence for this instrument")
    for eid in sorted(set(ids) | market_ids):
        rec = evidence.get(eid)
        if rec is None or rec.get("status") not in {"pass", "ok", "ready"} or not rec.get("source_url"):
            blockers.append(f"evidence {eid} is absent or unverified")
            continue
        if eid in ids and rec.get("critical_evidence_eligible") is False:
            blockers.append(f"evidence {eid} cannot support a critical recommendation claim")
        retrieved = _time(rec.get("retrieved_at"), f"{eid}.retrieved_at")
        if retrieved > created:
            blockers.append(f"evidence {eid} was retrieved in the future")
        if rec.get("observed_at") is not None:
            observed = _time(rec["observed_at"], f"{eid}.observed_at")
            if observed > decision_time or observed > retrieved:
                blockers.append(f"evidence {eid} has a future observation")
            if eid in market_ids and observed.date().isoformat() != item.get("latest_session"):
                blockers.append(f"market evidence {eid} observation does not match the current session")
        else:
            warnings.append(f"{eid}: exact publication/observation timestamp is unknown; keep the source's date-level meaning")
        if eid in market_ids and rec.get("latest_session", item.get("latest_session")) != item.get("latest_session"):
            blockers.append(f"market evidence {eid} session is stale")
        # A cited market record must describe the instrument being assessed.
        # This is stronger than anything the policy did before: the old
        # dynamic-ETF branch bound identity only for symbols the registry had
        # provisionally typed as stock, so a registered symbol such as QQQ was
        # never checked here at all. Providers are deliberately not
        # allow-listed, so a new price source needs no change to this gate.
        # Research records declare none of these fields; only a declared field
        # is compared, and omitting one is not an error.
        if eid in market_ids:
            for field in ("instrument_id", "currency", "unit", "price_kind", "asset_class"):
                if rec.get(field) is not None and rec[field] != identity[field]:
                    blockers.append(f"market evidence {eid} {field} does not match the instrument identity")
    verified_claims, claim_issues = _claims(proposal, snapshot, evidence, market_ids, ids)
    blockers.extend(claim_issues)
    if proposal.get("snapshot_id", snapshot["snapshot_id"]) != snapshot["snapshot_id"]:
        blockers.append("proposal refers to a different snapshot")
    version = context.get("portfolio_version")
    if proposal.get("portfolio_version", version) != version:
        blockers.append("portfolio changed after this proposal")
    if context.get("snapshot_id", snapshot["snapshot_id"]) != snapshot["snapshot_id"]:
        blockers.append("portfolio valuation belongs to a different snapshot")

    checks = {"data": {"status": "fail" if blockers else "pass", "detail": "; ".join(blockers) or "current validated instrument evidence"}}
    is_gold = item.get("asset_class") == "physical_gold"
    if item.get("asset_class") == "index":
        warnings.append("index points are a market view only; use a separately assessed ETF for purchases")
    checks["tradable"] = {"status": "not_applicable" if is_gold else ("pass" if item.get("tradable") is True else "fail"), "detail": "physical product quote required" if is_gold else "index points are research only" if item.get("tradable") is not True else "tradable instrument"}
    # ETF look-through and cross-currency risk need real measured context too.
    complete = (context.get("portfolio_complete") is True and isinstance(version, str) and bool(version)
                and bool(context.get("base_currency")) and context.get("snapshot_id") == snapshot["snapshot_id"]
                and context.get("risk_proposal_fingerprint") == proposal_fingerprint(proposal))
    inputs = context.get("verified_risk_inputs", {}) if complete else {}
    if not isinstance(inputs, dict):
        raise ValueError("verified_risk_inputs must be an object")
    sleeve = identity["asset_class"]
    for name, (field, _default, strict) in _LIMITS.items():
        exempt = _SLEEVE_EXEMPT.get(sleeve, {}).get(name)
        if exempt:
            checks[name] = {"status": "not_applicable", "value": None, "limit": None,
                            "detail": exempt}
            continue
        limit = limit_for(name, sleeve)
        value = inputs.get(field)
        eligible = _number(value) and 0 <= value <= 1
        check = "unknown" if not eligible else "pass" if (value < limit if strict else value <= limit) else "fail"
        checks[name] = {"status": check, "value": value if eligible else None, "limit": limit}
    stop = proposal.get("stop_loss")
    stop_ok = _number(stop, positive=True) and _number(item.get("price"), positive=True) and stop < proposal.get("price", item["price"])
    checks["stop"] = {"status": "not_applicable" if mode == "accumulation" or requested != "buy" else "pass" if stop_ok else "unknown", "detail": "accumulation does not require a tactical stop" if mode == "accumulation" else "stop below entry required for tactical purchase"}
    retail_ok = False
    if is_gold:
        quote = proposal.get("retail_quote")
        if isinstance(quote, dict):
            rec = evidence.get(quote.get("evidence_id"))
            # Exact quoted fields must be present in captured evidence. A prose
            # assertion that the quote is verified is intentionally ignored.
            retail_ok = bool(rec and rec.get("status") in {"pass", "ok"} and rec.get("retail_quote") == quote and quote.get("merchant") and quote.get("product") and quote.get("currency") == "CNY" and quote.get("unit") == "gram" and _number(quote.get("ask_per_fine_gram"), positive=True) and quote.get("evidence_id") in ids)
            if retail_ok:
                try:
                    observed = _time(quote.get("observed_at"), "retail quote observed_at")
                    retail_ok = observed <= created and (created - observed).total_seconds() <= 86400
                except ValueError:
                    retail_ok = False
        checks["retail_quote"] = {"status": "pass" if retail_ok else "unknown", "detail": "merchant/product CNY per fine gram quote required for concrete prices"}
        if not retail_ok:
            warnings.append("SGE is a benchmark, not a retail purchase price; merchant quote is missing/unverified")
    known_risk_fail = any(checks[k]["status"] == "fail" for k in _LIMITS)
    action = "data_insufficient" if blockers else requested
    if not blockers and requested == "buy" and known_risk_fail:
        action = "hold"
        warnings.append("purchase blocked by measured portfolio risk limit")
    executable = not blockers and item.get("asset_class") != "index" and (item.get("tradable") is True or retail_ok) and all(checks[k]["status"] in {"pass", "not_applicable"} for k in [*_LIMITS, "stop"])
    if not complete:
        warnings.append("portfolio completeness/base currency unknown; exact quantity/weight is withheld")
    decision = {
        "schema_version": 1, "policy_version": POLICY_VERSION, "snapshot_id": snapshot["snapshot_id"],
        "instrument_id": instrument_id, "action": action, "requested_action": requested,
        "data_status": "blocked" if blockers else "ready", "reasons": reasons if not blockers else blockers,
        "conditions": conditions if not blockers else [], "risk_checks": checks, "evidence_ids": ids,
        "claims": verified_claims if not blockers else [],
        "reasoning_verification": "evidence_identity_and_literal_numeric_values_only; qualitative interpretation is not machine-fact-checked",
        "created_at": created.isoformat(), "valid_until": valid_until.isoformat(),
        "portfolio_version": version, "mode": mode, "horizon": horizon, "warnings": warnings,
        "execution_scope": "actionable" if executable and action == requested else "research_only",
    }
    if not blockers and item.get("asset_class") != "index" and (not is_gold or retail_ok):
        if "price" in proposal:
            if not is_gold or proposal["price"] == proposal["retail_quote"]["ask_per_fine_gram"]:
                decision["price"] = proposal["price"]
        if executable:
            for key in ("quantity", "target_weight", "stop_loss"):
                if key in proposal:
                    decision[key] = proposal[key]
    # Each assessment timestamp is part of the immutable payload. Reusing an ID
    # while changing created_at would conflict with the append-only ledger.
    decision["decision_id"] = "decision-" + _digest(decision)
    return decision

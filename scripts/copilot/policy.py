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
from decimal import Decimal

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
#:
#: Gold has no entry on purpose. Four of its five limits are exempt below and
#: the one it keeps -- drawdown -- uses the shared default, so an entry here
#: could only hold a number nobody measured. A limit of 1.0 in particular is
#: forbidden: a reader takes any number in this table as a check that ran and
#: passed, and 1.0 would be a check that cannot fail.
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
    # A one-instrument sleeve cannot be measured by any limit that compares a
    # position to its peers. Each of these is not_applicable WITH A REASON --
    # a silent default would look like an unmeasured check, and a limit of 1.0
    # would look like a measured one that passed. drawdown is deliberately
    # absent: it measures the book losing money, which one asset does as well
    # as a basket, so it transfers unchanged at the shared 15%.
    # Keyed "physical_gold", NOT "gold". Both tables are named _SLEEVE_* but are
    # looked up by the instrument's asset_class (see limit_for's own parameter
    # name, and assess_proposal's `identity["asset_class"]`). ETF works only
    # because its sleeve and its asset_class happen to share the spelling "etf";
    # gold's are "gold" and "physical_gold" and the collision does not save it.
    # A first draft keyed this "gold", which silently did nothing: GOLD.CNY fell
    # through to the single-stock defaults -- single_name 0.05 against a
    # position that is 100% of its sleeve by construction -- and every reason
    # written below reached no reader at all.
    "physical_gold": {
        "single_name": "gold is the entire gold sleeve by construction; a single-name cap "
                       "would refuse every purchase it is supposed to size",
        "sector": "physical gold is not a member of any equity sector map",
        "correlation": "a one-instrument sleeve has no second series to correlate against",
        "liquidity": "no SGE volume series is collected, so position-versus-ADV cannot be "
                     "computed; do not read this as ample liquidity",
    },
}


def _is_reported_quote(record: object) -> bool:
    """A user-reported merchant quote, identified by shape rather than trust.

    Every field is required. A record that merely claims provider
    "user_reported" without a well-formed quote underneath gets none of the
    exemptions in `_evidence_blockers` -- the shape IS the credential here,
    because there is no URL to check and no vendor to hold responsible.

    Deliberately narrow: `retailquote.build` is the only thing that produces
    this shape, and it validates merchant, product, the CNY-per-gram band and
    a timezone-aware observation before it does.
    """
    if not isinstance(record, dict) or record.get("provider") != "user_reported":
        return False
    quote = record.get("retail_quote")
    if not isinstance(quote, dict):
        return False
    return (bool(quote.get("merchant")) and bool(quote.get("product"))
            and quote.get("currency") == "CNY" and quote.get("unit") == "gram"
            and quote.get("verification") == "self_reported"
            and _number(quote.get("ask_per_fine_gram"), positive=True)
            and record.get("critical_evidence_eligible") is False)


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
        # A reported merchant quote is citable but never claimable. Citing it
        # is how the retail gate identifies the price a trade will execute at;
        # claiming against it would let a self-reported number verify as a
        # market fact, which is exactly what its critical_evidence_eligible:
        # False is for. The evidence-blocker loop exempts reported quotes from
        # that flag so they CAN be cited, so the flag's real protection has to
        # live here instead. Its retail_quote field is a resolvable dict, so
        # without this a claim on /retail_quote/ask_per_fine_gram verified.
        if _is_reported_quote(rec):
            issues.append(f"claim cites reported quote {eid}; a self-reported price can be "
                          "the price a trade executes at, never evidence that it is correct")
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
        # A user-reported merchant quote is a THIRD kind of evidence, and both
        # checks below were written for the other two. Market data is fetched
        # and so must carry the URL it came from; news is unverifiable prose and
        # so may never back a factual claim. A retail quote is neither: it is a
        # transactable PRICE the user attests to, whose provenance is the
        # merchant, the product and the moment they observed it -- there is no
        # URL because no public source in China publishes a bank's 积存金 ask.
        #
        # Without this branch the gate at the bottom of this function was a
        # catch-22 and gold could never execute: it requires the quote's
        # evidence_id to appear in evidence_ids, and appearing there tripped
        # "absent or unverified" on the missing source_url. Measured before the
        # fix: retail_quote status "pass" alongside data_status "blocked".
        #
        # This does NOT make a reported price citable as a fact. The record
        # keeps critical_evidence_eligible: False, it carries no `data` for a
        # claim path to resolve against, and the retail_quote gate below
        # re-validates merchant, product, currency, unit, positivity, exact
        # equality with the proposal, and a 24-hour freshness window.
        reported = _is_reported_quote(rec)
        if rec is None or rec.get("status") not in {"pass", "ok", "ready"} or (
                not rec.get("source_url") and not reported):
            blockers.append(f"evidence {eid} is absent or unverified")
            continue
        if eid in ids and not reported and rec.get("critical_evidence_eligible") is False:
            blockers.append(f"evidence {eid} cannot support a critical recommendation claim")
        retrieved = _time(rec.get("retrieved_at"), f"{eid}.retrieved_at")
        if retrieved > created:
            blockers.append(f"evidence {eid} was retrieved in the future")
        if rec.get("observed_at") is not None:
            observed = _time(rec["observed_at"], f"{eid}.observed_at")
            # `observed > decision_time` is anachronism protection for FETCHED
            # data: a price stamped after the moment the decision was framed
            # would be information the decision could not have had. A reported
            # merchant quote is the opposite case -- the real sequence is
            # collect the snapshot, open the bank app, then report what it
            # says, so the quote is newer than decision_at every single time.
            # Requiring it to precede decision_at would reject exactly the
            # quotes worth having and admit only stale ones.
            #
            # `observed > retrieved` still applies to it: a quote observed
            # after the moment it was written down is incoherent whoever
            # supplied it. And the retail gate below re-checks freshness far
            # more tightly than this one -- against the decision's own clock,
            # within 24 hours, both directions.
            too_late = observed > retrieved or (observed > decision_time and not reported)
            if too_late:
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


#: How the engine's order side maps onto the five ACTIONS policy accepts. A full
#: exit is a sell; a partial trim is a reduce. There is no "rebalance" action.
def _order_action(side: str, target_shares: int) -> str:
    if side == "buy":
        return "buy"
    if side == "sell":
        return "sell" if target_shares == 0 else "reduce"
    return "hold"


#: market_data.py's collect_snapshot() enforces minimum_history in [260, 400]
#: and defaults it to 260; that value is not exposed as an importable constant
#: there, so the floor of its valid range is mirrored here.
_MIN_HISTORY_BARS = 260


def _primary_evidence(snapshot: dict, symbol: str) -> dict:
    """The market evidence record whose bars the indicators were computed from.

    Selected by the same rule market_data.py's collect_snapshot() uses for
    `primary` (its `primary = max(successes, key=...)` line), so the live
    signal is computed on the series the snapshot's own indicators describe.
    That is a 4-key sort -- bar count clears the collector's own minimum
    history, then no missing sessions, then total-return-adjusted basis, then
    most bars -- not the 3-key version ("no missing sessions, then adjusted
    basis, then most bars") an earlier draft of this docstring described.
    ADR-0007's Task 1 follow-up made the bar-count test outrank the gap test,
    because `missing_sessions` is trivially empty for a one-bar record: after
    that change every symbol can carry a full Yahoo history alongside a
    single-bar Nasdaq record, and picking naively would intersect a 260-bar
    history down to one session or mix an unadjusted close into an adjusted
    series.
    """
    item = snapshot.get("instruments", {}).get(symbol) or {}
    owned = set(item.get("evidence_ids") or [])
    candidates = [record for record in snapshot.get("evidence", [])
                  if record.get("evidence_id") in owned
                  and isinstance(record.get("bars"), list) and record["bars"]
                  and record.get("critical_evidence_eligible") is not False]
    if not candidates:
        raise ValueError(f"{symbol}: no market evidence in this snapshot carries bars; pass the "
                         "stored snapshot rather than the summary view, which strips them")
    return max(candidates, key=lambda r: (
        len(r["bars"]) >= _MIN_HISTORY_BARS,
        not bool(r.get("missing_sessions")),
        r.get("indicator_basis") == "total_return_adjusted",
        len(r["bars"])))


def _bars_matrix(snapshot: dict, universe: tuple) -> tuple[list, list, dict]:
    """Align each symbol's primary bars onto the sessions they all share.

    Intersection, not union: a weight computed from a forward-filled price is one
    nobody could have traded on. Reads `adjusted_close` when the record's basis is
    total-return adjusted, because that is the basis the rule families were
    backtested on; `item["price"]` supplies the limit price separately and the two
    are never mixed (ADR-0006 clause 3).
    """
    from datetime import date as _date
    per_symbol, records = {}, {}
    for symbol in universe:
        record = _primary_evidence(snapshot, symbol)
        records[symbol] = record
        adjusted = record.get("indicator_basis") == "total_return_adjusted"
        series = {}
        for row in record["bars"]:
            stamp = row.get("session")
            close = row.get("adjusted_close") if adjusted else row.get("close")
            if not stamp or isinstance(close, bool) or not isinstance(close, (int, float)):
                continue
            series[_date.fromisoformat(str(stamp)[:10])] = float(close)
        if not series:
            raise ValueError(f"{symbol}: no bar carried both a session and a usable close")
        per_symbol[symbol] = series
    common = set(per_symbol[universe[0]])
    for symbol in universe[1:]:
        common &= set(per_symbol[symbol])
    if not common:
        raise ValueError("the adopted universe shares no common session in this snapshot")
    dates = sorted(common)
    closes = [[per_symbol[symbol][day] for symbol in universe] for day in dates]
    return dates, closes, records


def _build_rule(adoption: dict):
    """Rebuild the rule object from the stored adoption, never from a model payload."""
    from .backtest import rules as rule_families
    family = adoption["family"]
    universe = tuple(adoption["universe"])
    p = {k: float(v) for k, v in (adoption.get("parameters") or {}).items()}
    if family == "fixed_weight_bands":
        targets = adoption.get("targets")
        if not targets:
            raise ValueError("fixed_weight_bands requires stored targets")
        return rule_families.FixedWeightBands(
            {str(k): float(v) for k, v in targets.items()},
            relative_band=p["relative_band"], absolute_band=p["absolute_band"],
            calendar_days=int(p["calendar_days"]))
    if family == "inverse_volatility":
        return rule_families.InverseVolatility(
            universe, lookback_days=int(p["lookback_days"]),
            rebalance_days=int(p["rebalance_days"]))
    if family == "momentum_top_n":
        return rule_families.MomentumTopN(
            universe, top_n=int(p["top_n"]), lookback_days=int(p["lookback_days"]),
            skip_days=int(p["skip_days"]))
    raise ValueError(f"unknown rule family {family!r}")


def _last_rebalance_index(context: dict, adoption: dict, dates: list) -> int | None:
    """Index of the session this rule last rebalanced on, from prior decisions.

    The rule families decide WHETHER to trade in `should_rebalance`, not in
    `weights`: FixedWeightBands.weights returns the stored targets unconditionally
    and all of its behaviour -- the 25% relative band, the 5-point absolute band,
    the calendar leg -- lives in the trigger. Evaluating `weights` alone turns an
    annual rule into a daily one and the turnover the admission gate measured
    stops describing it.
    """
    from datetime import date as _date
    sessions = [r.get("evaluation_session") for r in (context.get("recommendations") or [])
                if r.get("rule_id") == adoption["rule_id"] and r.get("rebalance_due")]
    stamps = sorted({_date.fromisoformat(str(s)[:10]) for s in sessions if s})
    if not stamps:
        return None
    for index in range(len(dates) - 1, -1, -1):
        if dates[index] <= stamps[-1]:
            return index
    return None


def _current_weights(holdings: list, prices: dict, total_value: float) -> dict:
    if total_value <= 0:
        return {}
    weights = {}
    for holding in holdings or []:
        symbol = str(holding.get("instrument_id", "")).upper()
        price, quantity = prices.get(symbol), holding.get("quantity")
        if price is None or isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            continue
        weights[symbol] = weights.get(symbol, 0.0) + float(quantity) * float(price) / total_value
    return weights


def _held_shares(holdings: list) -> dict:
    held = {}
    for holding in holdings or []:
        symbol = str(holding.get("instrument_id", "")).upper()
        quantity = holding.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            continue
        held[symbol] = held.get(symbol, 0.0) + float(quantity)
    return held


def _absent_decision(snapshot: dict, symbol: str, reasons: list, version: str) -> dict:
    """A blocked decision for a universe symbol the snapshot does not carry."""
    return {
        "schema_version": 1, "policy_version": POLICY_VERSION,
        "snapshot_id": snapshot.get("snapshot_id"), "instrument_id": symbol,
        "action": "data_insufficient", "requested_action": "hold",
        "data_status": "blocked", "reasons": list(reasons), "conditions": [],
        "risk_checks": {"data": {"status": "fail",
                                 "detail": f"{symbol} is absent from the snapshot"}},
        "evidence_ids": [], "claims": [],
        "reasoning_verification": "instrument absent from the evaluated snapshot",
        "portfolio_version": version, "mode": "accumulation", "horizon": "long_term",
        "warnings": [f"{symbol} is in the adopted universe but not in this snapshot"],
        "execution_scope": "research_only",
    }


def evaluate_rule(*, adoption: dict, snapshot: dict, context: dict | None = None,
                  investable_cash: float, brake: dict | None = None, now=None) -> dict:
    """Compute orders from an adopted rule. The only producer of live numbers.

    The engine feeds the evidence gate rather than bypassing it: it computes the
    weights, the share deltas and the measured risk inputs, then runs one
    proposal per symbol through assess_proposal with source="engine" and the
    per-proposal context that gate requires -- snapshot_id plus this proposal's
    own fingerprint plus the risk it measured for this trade. That is legitimate
    for the engine and not for a model, because the engine computed the trade and
    the risk from the same holdings and prices, so they genuinely correspond.

    ADR-0007 clause 6: one blocked symbol pauses the whole evaluation. Weights
    are computed across the universe, so one unreliable symbol makes every weight
    unreliable, and a partial basket would be a policy nobody approved.
    """
    from . import brake as brake_module
    from . import riskinputs, sizing
    from .backtest.engine import CostModel
    from .instruments import sector_of

    if not isinstance(adoption, dict) or not adoption.get("rule_id"):
        raise ValueError("adoption must be a stored adoption record with a rule_id")
    sleeve = adoption.get("sleeve")
    if sleeve not in ("etf", "gold"):
        raise ValueError(f"sleeve must be 'etf' or 'gold'; this adoption says "
                         f"{sleeve!r} and no other sleeve has an engine")
    context = dict(context or {})
    rule_id = str(adoption["rule_id"])
    universe = tuple(str(s).upper() for s in adoption.get("universe") or [])
    if not universe:
        raise ValueError("adoption has an empty universe")

    created = _time(now if now is not None else datetime.now(timezone.utc), "now")
    valid_until = _time(snapshot.get("valid_until"), "snapshot.valid_until")
    if created >= valid_until:
        raise ValueError(f"snapshot {snapshot.get('snapshot_id')} expired at "
                         f"{valid_until.isoformat()}; collect a fresh one before sizing")

    brake_record = brake_module.record(
        level=str((brake or {}).get("level", "none")),
        reason=str((brake or {}).get("reason", "")),
        evidence_ids=(brake or {}).get("evidence_ids") or [])

    # Placed AFTER the checks above, not at the very top: the adoption shape,
    # the universe, the snapshot expiry and the brake record are all validated
    # the same way for both sleeves, and a gold branch jumped to before them
    # would be the one order path that skips the expiry check. `brake_record`
    # in particular has to exist first -- _evaluate_gold_rule takes it, and
    # brake_module.record() is where a non-"none" brake with no reason or no
    # evidence id is refused.
    if sleeve == "gold":
        return _evaluate_gold_rule(adoption=adoption, snapshot=snapshot, context=context,
                                   investable_cash=investable_cash, now=now,
                                   brake_record=brake_record)

    instruments = snapshot.get("instruments", {})
    blocked = [s for s in universe
               if s not in instruments or instruments[s].get("quality_status") != "pass"]
    coverage_known = context.get("portfolio_complete") is True
    version = str(context.get("portfolio_version") or "")
    withhold = bool(blocked) or not coverage_known

    plan, measured, rebalance_due = None, {}, None
    if not withhold:
        dates, closes, records = _bars_matrix(snapshot, universe)
        rule = _build_rule(adoption)
        if len(dates) <= rule.warmup_bars:
            raise ValueError(f"{adoption['family']} needs more than {rule.warmup_bars} bars to "
                             f"produce a signal; this snapshot shares {len(dates)}")
        from .backtest.frame import build as build_frame
        frame = build_frame(dates=dates, symbols=list(universe), closes=closes)
        last_index = len(dates) - 1
        prices = {s: instruments[s]["price"] for s in universe}
        held = _held_shares(context.get("holdings"))
        holdings_value = sum(held.get(s, 0.0) * prices[s] for s in held if s in prices)
        total_value = holdings_value + float(investable_cash)
        current = _current_weights(context.get("holdings"), prices, total_value)
        rebalance_due = rule.should_rebalance(
            frame, last_index, current, _last_rebalance_index(context, adoption, dates))
        weights = rule.weights(frame, last_index)
        # `held` is passed unconditionally, not
        # `held if rebalance_due else weights and held` (a draft expression that
        # was reaching for "pass held either way" and reduces to `held` in every
        # case anyway, since `weights` is never empty for a real rule). The loop
        # below zeroes every delta when a rebalance is not due, so plan_orders
        # never needs to see a different holdings view to get that right.
        plan = sizing.plan_orders(
            weights=weights, prices=prices, held_shares=held,
            investable_cash=float(investable_cash),
            cash_floor_pct=float(adoption.get("cash_floor_pct", 0.0)),
            cost_model=CostModel(**adoption["cost_model"]))
        if not rebalance_due:
            for order in plan["orders"]:
                order.update(delta_shares=0, side="hold", notional=0.0, estimated_cost=0.0)
        # journal.get_context returns recommendations NEWEST FIRST ("ORDER BY
        # recorded_at DESC"), and riskinputs.portfolio_drawdown walks FORWARD
        # from a running peak, so reading that list in place measured the curve
        # backwards: 100 -> 90 -> 70 -> 50 came out as 0% rather than 50%, and
        # the 15% limit passed a book that had halved. Drawdown is the only one
        # of the five limits that measures the book LOSING money, so nothing
        # else caught it.
        #
        # Reversing first puts rows that carry no usable timestamp back in
        # chronological order; the stable sort then orders by each row's own
        # created_at, but only when every row has one -- an empty-string key
        # would otherwise drag untimestamped rows to the front of the series.
        prior = [r for r in reversed(context.get("recommendations") or [])
                 if isinstance(r.get("portfolio_total_value"), (int, float))
                 and not isinstance(r.get("portfolio_total_value"), bool)]
        if all(str(r.get("created_at") or "") for r in prior):
            prior.sort(key=lambda r: str(r["created_at"]))
        history = [float(r["portfolio_total_value"]) for r in prior]
        history.append(plan["total_value"])
        sector_values: dict[str, float] = {}
        for order in plan["orders"]:
            sector = sector_of(order["instrument_id"])
            # "diversified" is instruments.py's label for "not a sector", used
            # for every broad fund the static map does not name explicitly, and
            # its own comment there says it "therefore never concentrates".
            # Aggregating unrelated diversified funds into one shared bucket
            # would contradict that and ADR-0007 clause 7's own worked example
            # ("QQQ plus SPY at 25% each will pass"): two diversified ETFs at
            # 25% each combined would read as 50% of one "sector" and fail a
            # 25% sector cap that clause explicitly says should pass. A real
            # sector (technology, financials, ...) still aggregates normally.
            if sector == "diversified":
                continue
            sector_values[sector] = sector_values.get(sector, 0.0) + \
                order["target_shares"] * order["limit_price"]
        for order in plan["orders"]:
            symbol = order["instrument_id"]
            sector = sector_of(symbol)
            own = order["target_shares"] * order["limit_price"]
            other_sector_value = 0.0 if sector == "diversified" else sector_values.get(sector, 0.0) - own
            measured[symbol] = riskinputs.compute(
                symbol=symbol, target_shares=order["target_shares"],
                price=order["limit_price"], total_value=plan["total_value"],
                sector_values={sector: other_sector_value},
                sector_of=sector,
                average_dollar_volume=riskinputs.average_dollar_volume(
                    _primary_evidence(snapshot, symbol)["bars"]),
                value_history=history, delta_shares=order["delta_shares"])

    sized = {o["instrument_id"]: o for o in (plan["orders"] if plan else [])}
    orders = []
    for symbol in universe:
        if blocked:
            reasons = [f"adopted rule {rule_id} paused: {', '.join(blocked)} has no "
                       "validated market data in this snapshot"]
        elif not coverage_known:
            reasons = [f"adopted rule {rule_id} produced a research view only; holdings "
                       "coverage has not been declared"]
        elif rebalance_due is False:
            reasons = [f"adopted rule {rule_id} is within its rebalance band; no trade is due"]
        else:
            reasons = [f"adopted rule {rule_id} produced this order"]
        if symbol not in instruments:
            orders.append({**_absent_decision(snapshot, symbol, reasons, version),
                           "rule_id": rule_id, "brake": dict(brake_record)})
            continue
        order = sized.get(symbol)
        # A rotation's weights carry only the names it chose, and plan_orders
        # covers `set(weights) | set(held)`, so a symbol it passed over and that
        # is not held gets no order at all: no measured risk inputs, "unknown"
        # on every limit, and a research_only decision that -- through the all()
        # below -- dragged the whole basket down with it. That is every real
        # rotation, since top_n < len(universe) is the point of one; correctly
        # sized, actionable orders for the selected names were being withheld
        # because of a symbol with nothing to do. A HELD name the rule dropped
        # is a different thing: it IS in `held`, so sizing emits a sell, that
        # sell must still be executed, and it still vetoes on failure.
        idle = plan is not None and order is None
        if idle:
            reasons = [f"adopted rule {rule_id} did not select {symbol}（未选中）and no "
                       "position is held, so there is nothing to do"]
        applied = None
        item = {"instrument_id": symbol, "action": "hold", "mode": "accumulation",
                "horizon": "long_term", "reasons": reasons, "conditions": [],
                "evidence_ids": [_primary_evidence(snapshot, symbol)["evidence_id"]]}
        if order is not None and order["delta_shares"] != 0:
            traded = abs(order["delta_shares"])
            # The brake only ever reduces EXPOSURE. Halving a sell would leave
            # more exposure than the rule asked for, so a sell is never braked.
            if order["side"] == "buy":
                applied = brake_module.applied(record=brake_record, quantity=traded)
                traded = applied["post_brake_quantity"]
            else:
                applied = {**brake_module.applied(record=brake_record, quantity=traded),
                           "post_brake_quantity": traded}
            applied["applied_to_side"] = order["side"]
            applied["changed"] = traded != abs(order["delta_shares"])
            if traded > 0:
                item["action"] = _order_action(order["side"], order["target_shares"])
                item["quantity"] = traded
                item["price"] = order["limit_price"]
        proposal_context = {**context, "snapshot_id": snapshot["snapshot_id"],
                            "risk_proposal_fingerprint": proposal_fingerprint(item),
                            "verified_risk_inputs": measured.get(symbol, {})}
        decision = assess_proposal(item, snapshot, proposal_context, now=now, source="engine")
        decision["rule_id"] = rule_id
        decision["brake"] = applied or dict(brake_record)
        if idle:
            decision["no_action_required"] = True
        if order is not None and decision["data_status"] == "ready" and "quantity" in decision:
            decision["limit_price"] = order["limit_price"]
            decision["limit_price_basis"] = "split_adjusted_close"
            decision["delta_shares"] = order["delta_shares"]
            decision["target_shares"] = order["target_shares"]
            decision["held_shares"] = order["held_shares"]
            decision["estimated_cost"] = order["estimated_cost"]
            decision["portfolio_total_value"] = plan["total_value"]
            decision["admitted_metrics"] = dict(adoption.get("admission", {}).get("metrics", {}))
        orders.append(decision)

    # Only the orders that ask for something get a vote. The nonempty check is a
    # deliberate guard against all([]) == True rather than a live case: a basket
    # in which EVERY name is idle would need empty weights, and plan_orders
    # rejects those outright ("weights must be a nonempty mapping"), so no rule
    # family can reach it today. It is here so that one which can hold nothing
    # does not read as "actionable" with no order under it. No test covers it,
    # because no fixture can construct it.
    requires_action = [o for o in orders if not o.get("no_action_required")]
    actionable = (not withhold and bool(rebalance_due) and bool(requires_action)
                  and all(o.get("execution_scope") == "actionable" for o in requires_action))
    result = {
        "schema_version": 1, "rule_id": rule_id, "sleeve": "etf",
        "snapshot_id": snapshot.get("snapshot_id"), "orders": orders,
        "blocked_symbols": blocked, "coverage_known": coverage_known,
        "rebalance_due": bool(rebalance_due),
        "execution_scope": "actionable" if actionable else "research_only",
        "brake": brake_record, "waived": bool(adoption.get("waived")),
        "admitted_metrics": dict(adoption.get("admission", {}).get("metrics", {})),
        "evaluation_session": (snapshot.get("instruments", {}).get(universe[0], {})
                               .get("latest_session")),
    }
    if not withhold:
        result["cash_plan"] = plan
    result["evaluation_id"] = "eval-" + _digest(result)[:16]
    return result


def _evaluate_gold_rule(*, adoption, snapshot, context, investable_cash, now, brake_record):
    """The gold sleeve's own evaluation. Money in CNY, position in grams.

    It does NOT call sizing.plan_orders: that requires weights summing to 1.0
    across a multi-symbol universe, computes whole shares, and has no concept
    of an order increment or a daily cap. See goldsizing's docstring.

    It DOES call assess_proposal, exactly as the ETF path does. An earlier
    draft computed `execution_scope` itself, which was a quiet violation of
    CLAUDE.md's "every recommendation ... passes assess_investment_proposal" --
    the gold sleeve would have been the one order path in the system that never
    met the evidence gate. It only became possible to call it once commit
    6332799 let a reported merchant quote be cited; before that the gate was a
    catch-22 and gold was permanently data_insufficient.
    """
    from . import brake as brake_module
    from . import goldsizing
    from .backtest import goldrules

    rule_id = str(adoption["rule_id"])
    # An adoption may carry sleeve "gold" and still describe something else:
    # journal.record_adoption stores whatever build_adoption produced, and
    # nothing downstream re-reads the family. Without these two refusals a
    # fixed_weight_bands adoption mislabelled "gold" would reach
    # assess_proposal as a GOLD.CNY proposal against a snapshot that has no
    # such instrument, and surface as "instrument is absent from snapshot" --
    # a message about the snapshot for a fault in the adoption.
    family = str(adoption.get("family"))
    if family not in goldrules.FAMILIES:
        raise ValueError(f"the gold sleeve has no engine for rule family {family!r}; only "
                         f"{sorted(goldrules.FAMILIES)} accumulate a single metal position")
    universe = [str(s).upper() for s in adoption.get("universe") or []]
    if universe != [goldsizing.SYMBOL]:
        raise ValueError(f"the gold sleeve trades exactly [{goldsizing.SYMBOL!r}], and this "
                         f"adoption names {universe}")

    instrument = snapshot.get("instruments", {}).get(goldsizing.SYMBOL)
    settings = dict(adoption.get("gold") or {})
    blocked = [] if instrument and instrument.get("quality_status") == "pass" else [goldsizing.SYMBOL]

    # A CNY sleeve needs a CNY declaration. CLAUDE.md forbids adding USD and
    # CNY without dated FX, so a USD declaration cannot fund this book even
    # though it is "complete".
    coverage_known = (context.get("portfolio_complete") is True
                      and str(context.get("base_currency")) == "CNY")

    quote_record = next((e for e in snapshot.get("evidence", [])
                         if e.get("retail_quote")
                         and e.get("instrument_id") == goldsizing.SYMBOL), None)

    item = {"instrument_id": goldsizing.SYMBOL, "action": "hold", "mode": "accumulation",
            "horizon": "long_term", "conditions": [],
            # The quote record is deliberately NOT cited here. retailquote.attach
            # keeps it out of instrument["evidence_ids"] (see its comment), so it
            # is not market evidence; it is added below only on a real buy, which
            # is the one case the retail gate needs it cited for.
            "evidence_ids": [e["evidence_id"] for e in snapshot.get("evidence", [])
                             if e.get("instrument_id") == goldsizing.SYMBOL
                             and not e.get("retail_quote")]}
    plan = None

    if blocked:
        item["reasons"] = [f"adopted rule {rule_id} paused: GOLD.CNY has no validated price"]
    elif not coverage_known:
        item["reasons"] = [f"adopted rule {rule_id} produced a research view only; a CNY "
                           "holdings coverage declaration is required to size a gold order"]
    elif quote_record is None:
        item["reasons"] = [f"adopted rule {rule_id} produced a research view only; "
                           "缺少商家报价（上金所基准价不是可成交价）"]
    else:
        quote = quote_record["retail_quote"]
        held_grams = sum(float(h.get("quantity", 0.0)) for h in (context.get("holdings") or [])
                         if h.get("instrument_id") == goldsizing.SYMBOL)
        sized = dict(ask_per_fine_gram=float(quote["ask_per_fine_gram"]),
                     investable_total_cny=float(investable_cash), held_grams=held_grams,
                     min_order_cny=int(settings.get("min_order_cny", 1200)),
                     order_increment_cny=int(settings.get("order_increment_cny", 200)),
                     orders_today=int(context.get("gold_orders_today", 0)),
                     max_orders_per_day=int(settings.get("max_orders_per_day", 10)))
        plan = goldsizing.plan_contribution(
            **sized, contribution_cny=float(settings.get("contribution_cny", 0.0)))

        # The brake only ever reduces, and it halves the MONEY rather than the
        # grams: an increment-respecting half of the contribution is still a
        # buyable order, whereas half the grams is not a figure the merchant
        # sells in. Re-planning rather than scaling the result keeps the
        # minimum and the increment enforced on the braked amount too.
        if plan["action"] == "buy" and brake_record.get("level", "none") != "none":
            halved = brake_module.apply(level=brake_record["level"],
                                        quantity=int(Decimal(plan["amount_cny"])))
            plan = goldsizing.plan_contribution(**sized, contribution_cny=float(halved))

        # plan["reasons"] and plan["refusals"] are deliberately NOT folded into
        # item["reasons"]. _claims re-reads every number appearing in a
        # proposal's reasons and demands a matching verified claim for it, and
        # the sizing prose is made of numbers ("5000 CNY at 962.5 CNY per fine
        # gram"). No such claim can exist: the engine computed the 5000, and the
        # 962.5 comes from a reported merchant quote _claims refuses outright
        # ("citable, never claimable" -- see its own comment). Folding them in
        # made EVERY gold order data_insufficient, blocked by its own
        # explanation. They reach the reader on the decision instead, as
        # sizing_reasons and refusals, which the gold render block prints.
        if plan["action"] == "buy":
            item["reasons"] = [f"adopted rule {rule_id} produced this contribution"]
            item.update(action="buy", price=float(quote["ask_per_fine_gram"]),
                        retail_quote=quote)
            item["evidence_ids"] = item["evidence_ids"] + [quote_record["evidence_id"]]
        else:
            item["reasons"] = [f"adopted rule {rule_id} sized no contribution this cycle"]

    proposal_context = {**context, "snapshot_id": snapshot["snapshot_id"],
                        "risk_proposal_fingerprint": proposal_fingerprint(item),
                        "verified_risk_inputs": _gold_risk_inputs(context, plan)}
    decision = assess_proposal(item, snapshot, proposal_context, now=now, source="engine")
    decision["rule_id"] = rule_id
    decision["sleeve"] = "gold"
    decision["currency"] = "CNY"
    decision["brake"] = dict(brake_record)
    decision["schedule_disclosure"] = adoption.get("schedule_disclosure")
    if plan is not None:
        for field in ("amount_cny", "grams", "held_grams", "target_grams",
                      "ask_per_fine_gram", "refusals"):
            decision[field] = plan[field]
        decision["sizing_reasons"] = list(plan["reasons"])
    # The merchant and product the rendered message names come from here.
    # assess_proposal VALIDATES `retail_quote` on the proposal but copies only
    # price/quantity/target_weight/stop_loss onto the decision, so without this
    # the renderer raised KeyError on the single field that distinguishes a
    # merchant's ask from the exchange benchmark. Gated on the ASSESSED action,
    # not the plan's: a decision the gate downgraded must not carry a quote it
    # was not allowed to act on.
    if decision["action"] == "buy" and item.get("retail_quote"):
        decision["retail_quote"] = item["retail_quote"]

    return {"schema_version": 1, "rule_id": rule_id, "sleeve": "gold",
            "snapshot_id": snapshot.get("snapshot_id"), "orders": [decision],
            "blocked_symbols": blocked, "coverage_known": coverage_known,
            "rebalance_due": bool(plan and plan["action"] == "buy"),
            # Read off the decision rather than recomputed. assess_proposal is
            # the only thing entitled to say a gold order is actionable, and
            # `.get` defaults to research_only so a future assess_proposal that
            # stopped emitting the key would fail closed.
            "execution_scope": decision.get("execution_scope", "research_only"),
            "brake": dict(brake_record), "waived": bool(adoption.get("waived")),
            "schedule_disclosure": adoption.get("schedule_disclosure"),
            "admitted_metrics": dict(adoption.get("admission", {}).get("metrics", {})),
            # service.evaluate stamps this onto every order before recording it,
            # so a gold result without it raises a KeyError out of the facade
            # rather than the engine -- far from the omission.
            "evaluation_session": (instrument or {}).get("latest_session"),
            "evaluation_id": "eval-" + _digest({"rule_id": rule_id, "plan": plan,
                                                "snapshot_id": snapshot.get("snapshot_id")})[:16]}


def _gold_risk_inputs(context, plan) -> dict:
    """Only drawdown is measurable for a one-instrument sleeve.

    The other four limits are exempt for gold (policy._SLEEVE_EXEMPT keyed
    "physical_gold"), so supplying figures for them would be inventing numbers
    nothing measured. Drawdown comes from the same recommendation history the
    ETF path uses, read forwards -- see the comment above `prior` in
    evaluate_rule for why the ordering is load-bearing.
    """
    from . import riskinputs

    prior = [r for r in reversed(context.get("recommendations") or [])
             if isinstance(r.get("portfolio_total_value"), (int, float))
             and not isinstance(r.get("portfolio_total_value"), bool)]
    if all(str(r.get("created_at") or "") for r in prior):
        prior.sort(key=lambda r: str(r["created_at"]))
    history = [float(r["portfolio_total_value"]) for r in prior]
    if plan is not None:
        history.append(float(Decimal(plan["held_grams"]) * Decimal(plan["ask_per_fine_gram"])
                             + Decimal(plan["amount_cny"])))
    # portfolio_drawdown drops non-positive observations itself and then RAISES
    # if nothing positive is left -- rightly, since a book worth 0 has no peak
    # to have fallen from. A refused contribution on an empty position values
    # this book at exactly 0, so the plan's `if not history` guard was not
    # enough: every refusal (below the minimum, daily cap reached, out of
    # budget) with no prior recommendations crashed the whole evaluation out of
    # the facade. Returning {} instead leaves drawdown "unknown", which is what
    # it is, and unknown already forecloses an actionable order.
    if not any(math.isfinite(v) and v > 0 for v in history):
        return {}
    drawdown, samples = riskinputs.portfolio_drawdown(history)
    return {"drawdown": drawdown, "drawdown_sample_count": samples}

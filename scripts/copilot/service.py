"""The shared CLI/MCP interface. Stored evidence, not model payloads, feeds policy."""
from __future__ import annotations

import hashlib
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
# The allow-list of names load_credentials() will read out of .env. This is a
# deliberate boundary: an unknown name in .env is never promoted into the
# process environment. Adding a credential means adding its name here AND
# re-running scripts/sync_runtimes.py, because the generated Codex config
# forwards exactly this tuple.
KEY_NAMES = (
    "FINNHUB_API_KEY", "FRED_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SEC_USER_AGENT",
)


def load_credentials() -> None:
    """Load only the allow-listed provider settings from .env.

    A real process override still wins. An EMPTY one does not: an MCP client
    expanding `${VAR}` against a host environment that never sourced .env
    injects "", and `setdefault` would let that empty string mask the real
    value. Every Finnhub tool in this repository returned HTTP 401 for exactly
    that reason.
    """
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name in KEY_NAMES and value and not os.environ.get(name, "").strip():
            os.environ[name] = value


def secret_values() -> tuple[str, ...]:
    """Every live credential value, for redaction. Single source of truth.

    Callers that scrub text or URLs must use this rather than keeping their own
    list of variable names, which is how research_data._redact drifted from
    KEY_NAMES: SEC_USER_AGENT was loaded and never redacted.
    """
    seen: list[str] = []
    for name in KEY_NAMES:
        value = os.environ.get(name, "").strip()
        if value and value not in seen:
            seen.append(value)
    return tuple(seen)


def database_path(db_path: str | Path | None = None) -> Path:
    return Path(db_path or os.environ.get("COPILOT_DB_PATH") or ROOT / "data/state/copilot.sqlite").resolve()


def strict_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)


def public_response(value):
    from .advisor import public_view
    return public_view(value)


def collect(instrument_ids: list[str], horizon: str = "daily", *, db_path=None,
            decision_at: str | None = None, research: bool = True, research_client=None,
            **dependencies) -> dict:
    from .market_data import collect_snapshot, snapshot_digest
    from .journal import save_snapshot
    load_credentials()
    dependencies.setdefault("cache_dir", database_path(db_path).parent / "provider-cache")
    snapshot = collect_snapshot(instrument_ids, decision_at=decision_at, horizon=horizon, **dependencies)
    if research:
        from .research_data import enrich
        from .providers import HttpClient
        enrich(snapshot, client=research_client or HttpClient(dependencies["cache_dir"], timeout=10, attempts=1, budget_seconds=30))
        for item in snapshot["instruments"].values():
            for section in item.get("research", {}).values():
                item["evidence_ids"].extend(section.get("evidence_ids", []))
        snapshot["created_at"] = datetime.now(timezone.utc).isoformat()
        # News must be checked again on the next conversational decision window.
        from datetime import timedelta
        expiry = datetime.fromisoformat(snapshot["valid_until"].replace("Z", "+00:00"))
        snapshot["valid_until"] = min(expiry, datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
        snapshot["snapshot_id"] = snapshot_digest(snapshot)
    strict_json(snapshot)
    save_snapshot(snapshot, db_path=database_path(db_path))
    return snapshot


def context(instrument_ids: list[str] | None = None, *, db_path=None, as_of=None,
            sleeve: str = "etf", now=None) -> dict:
    """Holdings and audit context. `sleeve` selects whose coverage declaration is read.

    Defaulting to "etf" matches journal.get_context: a declaration is per
    sleeve, and a lookup that ignored the sleeve would let a CNY gold
    declaration mark the USD ETF book complete.
    """
    from .journal import get_context, _clock
    result = get_context(instrument_ids, as_of=as_of, sleeve=sleeve, now=now,
                         db_path=database_path(db_path))
    moment = _clock(now if now is not None else result["as_of"])
    result["recommendations"] = [_recommendation_view(item, moment)
                                 for item in result["recommendations"]]
    return result


def advisor_context(instrument_ids: list[str] | None = None, *, db_path=None, as_of=None,
                    sleeve: str = "etf", now=None, config_path=None) -> dict:
    """Model-facing default; internal sizing continues to use the full local context."""
    from .advisor import context_summary
    from .config import load_config
    result = context_summary(context(instrument_ids, db_path=db_path, as_of=as_of,
                                     sleeve=sleeve, now=now))
    settings = load_config(config_path)
    result["adopted_rules"] = {
        "long_term": settings.advisor.long_term_rule_id or settings.etf.adopted_rule_id,
        "swing": settings.advisor.swing_rule_id,
    }
    result["strategy_templates"] = {}
    for mode, rule_id in result["adopted_rules"].items():
        summary = {"rule_id": rule_id, "status": "not_adopted", "universe": []}
        if rule_id:
            try:
                rule = adoption(rule_id, db_path=db_path)
                spec = rule.get("spec", {}) if rule.get("kind") == "advisor_strategy" else rule
                summary.update(status="stored_adoption_requires_current_validation", kind=rule.get("kind", "legacy_rule"),
                               family=spec.get("family"), universe=spec.get("universe", []))
            except KeyError:
                summary["status"] = "adoption_pointer_unresolved"
        result["strategy_templates"][mode] = summary
    result["broker_enabled"] = settings.ibkr.enabled
    result["execution_readiness"] = "requires_fresh_account_quote_and_adopted_rule"
    return result


def operation_context(operation_id: str, *, db_path=None) -> dict:
    """Bounded local-record lookup for corrections; never returns raw transaction text."""
    from .advisor import operation_summary
    for state in context(db_path=db_path)["operations"]:
        if state["operation_id"] == operation_id:
            return operation_summary(state)
    raise KeyError("operation not found")


def broker_snapshot(instrument_ids: list[str] | None = None, *, config_path=None,
                    db_path=None, now=None, transport=None) -> dict:
    """Persist a read-only broker observation; return only its sanitized projection."""
    from dataclasses import asdict
    from .config import load_config
    from .broker import collect_execution_snapshot
    from .plan_store import save_snapshot
    from .advisor import execution_summary
    settings = load_config(config_path)
    symbols = list(instrument_ids or settings.advisor.research_universe)
    observed = collect_execution_snapshot(asdict(settings.ibkr), symbols, now=now, transport=transport)
    save_snapshot(observed, db_path=database_path(db_path), now=now)
    return execution_summary(observed)


def _manual_policy(settings, mode):
    from dataclasses import asdict
    if mode not in {"long_term", "swing"}:
        raise ValueError("manual plan mode must be long_term or swing")
    rule_id = (settings.advisor.long_term_rule_id or settings.etf.adopted_rule_id
               if mode == "long_term" else settings.advisor.swing_rule_id)
    result = {**asdict(settings.advisor), "mode": mode, "adopted_rule_id": rule_id}
    result["long_term_rule_id" if mode == "long_term" else "swing_rule_id"] = rule_id
    return result


def prepare_trade_plan(research_snapshot_id: str, execution_snapshot_id: str,
                       mode: str = "long_term", *, config_path=None, db_path=None, now=None) -> dict:
    """Local configuration supplies strategy and risk limits; model supplies only stored IDs."""
    from .config import load_config
    from .plan_store import create_plan, read_snapshot
    path = database_path(db_path)
    policy = _manual_policy(load_config(config_path), mode)
    try:
        rule = adoption(policy["adopted_rule_id"], db_path=path) if policy["adopted_rule_id"] else {}
    except KeyError:
        rule = {}  # A dangling local pointer is a blocked prerequisite, not an order.
    plan = create_plan(execution_snapshot=read_snapshot(execution_snapshot_id, db_path=path),
                       research_snapshot=snapshot(research_snapshot_id, db_path=path), adoption=rule,
                       policy=policy, db_path=path, now=now)
    return {"plan": public_response(plan), "message": render_trade_plan(plan)}


def get_trade_plan(plan_id: str, *, config_path=None, db_path=None, now=None) -> dict:
    from .plan_store import read_plan
    plan = read_plan(plan_id, db_path=database_path(db_path), now=now)
    if plan.get("orders"):
        from .config import load_config
        from .trade_plan import policy_binding
        issues = []
        try:
            effective = _manual_policy(load_config(config_path), plan["mode"])
            if policy_binding(effective) != plan.get("policy_binding"):
                issues.append("current local configuration differs from the frozen card")
            current_rule = adoption(plan["rule_id"], db_path=db_path)
            if current_rule.get("kind") == "advisor_strategy":
                from .advisor_strategy import verify_adoption
                if not verify_adoption(current_rule):
                    issues.append("adopted advisor template no longer matches current calculation code")
        except (ValueError, KeyError, OSError) as exc:
            issues.append("current configuration/adoption cannot be verified: " + type(exc).__name__)
        if issues:
            plan["historical_orders"] = plan.pop("orders", [])
            plan.update(orders=[], review_state="needs_recompile", execution_scope="research_only",
                        requires_review=True, requires_revalidation=True)
            plan["issues"] = list(dict.fromkeys(plan.get("issues", []) + issues))
    return {"plan": public_response(plan), "message": render_trade_plan(plan)}


def confirm_plan_review(plan_id: str, statement: str, expected_version: int, outcome: str = "approve",
                        *, config_path=None, db_path=None, now=None) -> dict:
    from .plan_store import review_plan
    if outcome == "approve":
        current = get_trade_plan(plan_id, config_path=config_path, db_path=db_path, now=now)["plan"]
        if current.get("review_state") != "awaiting_review" or current.get("status") != "ready_for_review":
            raise ValueError("only a current ready card can receive a new approval")
    plan = review_plan(plan_id, statement=statement, expected_version=expected_version,
                       outcome=outcome, db_path=database_path(db_path), now=now)
    return {"plan": public_response(plan), "message": render_trade_plan(plan)}


def revalidate_trade_plan(plan_id: str, expected_version: int, *, config_path=None,
                          db_path=None, now=None, transport=None) -> dict:
    """Collect again immediately before manual action; a stale stored quote is not preflight."""
    from .config import load_config
    from .plan_store import read_plan, read_snapshot, revalidate_plan
    path = database_path(db_path)
    existing = read_plan(plan_id, db_path=path, now=now)
    symbols = [order["instrument_id"] for order in existing.get("orders", [])]
    if not symbols or existing.get("review_state") != "reviewed":
        raise ValueError("preflight requires a fresh explicitly reviewed plan")
    frozen_rule = adoption(existing["rule_id"], db_path=path)
    universe = (frozen_rule.get("spec", {}).get("universe", [])
                if frozen_rule.get("kind") == "advisor_strategy" else frozen_rule.get("universe", []))
    symbols = sorted(set(symbols) | set(universe))
    observed = broker_snapshot(symbols, config_path=config_path, db_path=path, now=now, transport=transport)
    policy = _manual_policy(load_config(config_path), existing["mode"])
    plan = revalidate_plan(plan_id, execution_snapshot=read_snapshot(observed["snapshot_id"], db_path=path),
                           policy=policy, expected_version=expected_version, db_path=path, now=now)
    return {"plan": public_response(plan), "message": render_trade_plan(plan)}


def confirm_broker_cash_flow(previous_snapshot_id: str, execution_snapshot_id: str,
                             net_external_flow_usd: str, statement: str, *, db_path=None, now=None) -> dict:
    """An explicit user report distinguishes deposits/withdrawals from trading return."""
    from .plan_store import record_cash_flow
    return public_response(record_cash_flow(execution_snapshot_id, net_external_flow_usd, statement,
                           previous_snapshot_id=previous_snapshot_id,
                           db_path=database_path(db_path), now=now))


def confirm_mode_allocations(execution_snapshot_id: str, allocations: dict, statement: str,
                             expected_account_version: str, expected_version: int | None = None,
                             *, entry_sessions: dict | None = None, entry_plan_ids: dict | None = None,
                             db_path=None, now=None) -> dict:
    """The user assigns held shares to investment modes; never inferred from ticker matches."""
    from .plan_store import record_mode_allocations
    return public_response(record_mode_allocations(execution_snapshot_id, allocations, statement,
                           expected_account_version=expected_account_version, expected_version=expected_version,
                           entry_sessions=entry_sessions, entry_plan_ids=entry_plan_ids,
                           db_path=database_path(db_path), now=now))


def confirm_strategy_execution_state(execution_snapshot_id: str, rule_id: str, mode: str,
                                     statement: str, expected_account_version: str,
                                     last_execution_session: str | None = None,
                                     no_prior_executions: bool = False,
                                     expected_version: int | None = None,
                                     *, config_path=None, db_path=None, now=None) -> dict:
    """Record the user's actual cadence facts, never deduce them from recent fills."""
    from .config import load_config
    from .plan_store import record_strategy_state
    policy = _manual_policy(load_config(config_path), mode)
    if rule_id != policy["adopted_rule_id"] or not rule_id:
        raise ValueError("strategy state must name the currently configured adopted rule and mode")
    return public_response(record_strategy_state(execution_snapshot_id, rule_id, mode, statement,
        expected_account_version=expected_account_version, last_execution_session=last_execution_session,
        no_prior_executions=no_prior_executions, expected_version=expected_version,
        db_path=database_path(db_path), now=now))


def render_trade_plan(plan: dict) -> str:
    labels = {"ready_for_review": "待人工 Review", "blocked": "暂停交易方案", "no_trade": "本次不交易"}
    lines = ["交易方案：" + labels.get(plan.get("status"), "状态待核对") + "；" + str(plan.get("mode", ""))]
    state = plan.get("review_state")
    if state and state not in {"awaiting_review", "reviewed"}:
        lines[0] = "交易方案：暂停复用原交易数字；" + state
    if state:
        lines.append("复核状态：" + state)
    for order in plan.get("orders", []):
        side = "买入" if order["side"] == "buy" else "卖出"
        lines.append(f"{order['instrument_id']}：{side} {order['quantity']} 股，USD 限价 {order['limit_price']}；预计费用 {order['estimated_fees']}")
    if plan.get("issues"):
        lines.append("缺口：" + "；".join(str(issue) for issue in plan["issues"][:4]))
    if plan.get("revalidation"):
        check = plan["revalidation"]
        lines.append("提交前复核：" + str(check.get("status")) +
                     ("；" + "；".join(str(issue) for issue in check.get("issues", [])[:3]) if check.get("issues") else ""))
    if plan.get("valid_until"):
        lines.append("方案有效至：" + str(plan["valid_until"]))
    lines.append("人工下单前刷新复核；未提交任何订单。方案过期不会撤销券商订单。")
    return "\n".join(lines)


def _recommendation_view(decision: dict, moment: datetime) -> dict:
    """Expose read-only historical advice without rewriting immutable records.

    The journal's valuation summaries use original observations even after
    advice expires. The conversation view must not revive old order quantities
    just because no new trade has changed the portfolio version.
    """
    from .data_calendar import parse_time
    from .policy import POLICY_VERSION
    from .ruleset import SCHEMA_VERSION
    view = dict(decision)
    policy_current = decision.get("policy_version") == POLICY_VERSION
    try:
        snapshot_current = (isinstance(decision.get("created_at"), str)
                            and isinstance(decision.get("valid_until"), str)
                            and parse_time(decision["created_at"]) <= moment
                            < parse_time(decision["valid_until"]))
    except (TypeError, ValueError):
        snapshot_current = False
    is_basket = "rule_id" in decision or "evaluation_id" in decision
    adoption_current = not is_basket or decision.get("adoption_schema_version") == SCHEMA_VERSION
    basket_current = not is_basket or decision.get("basket_execution_scope") == "actionable"
    invalidation = []
    for current, reason in (
            (decision.get("portfolio_current") is True, "portfolio_changed"),
            (policy_current, "policy_version_missing_or_changed"),
            (snapshot_current, "evidence_expired_future_or_time_unknown"),
            (adoption_current, "adoption_evidence_version_missing_or_changed"),
            (basket_current, "basket_not_actionable_or_binding_missing")):
        if not current:
            invalidation.append(reason)
    recorded_scope = decision.get("execution_scope", "research_only")
    # Context is an audit read, not a new evaluation of active config, current
    # cash, fees, quotes and account quotas. Even fresh historical advice must
    # not become an order channel through this interface.
    bindings_current = not invalidation
    invalidation.append("historical_recommendation_requires_fresh_evaluation")
    view.update(policy_current=policy_current, snapshot_current=snapshot_current,
                adoption_schema_current=adoption_current, basket_recorded_actionable=basket_current,
                recorded_bindings_current=bindings_current, requires_reevaluation=True,
                view="historical_recommendation", recorded_execution_scope=recorded_scope,
                execution_scope="research_only", current_execution_scope="research_only",
                invalidation_reasons=invalidation)
    if "basket_execution_scope" in view:
        view["recorded_basket_execution_scope"] = view["basket_execution_scope"]
        view["basket_execution_scope"] = "research_only"
    execution_fields = ("quantity", "price", "delta_shares", "target_shares", "target_weight",
                        "limit_price", "limit_price_basis", "estimated_cost", "stop_loss",
                        "amount_cny", "grams", "target_grams")
    historical = {key: view.pop(key) for key in execution_fields if key in view}
    if historical:
        view["historical_order"] = {**historical, "action": decision.get("action"),
                                     "execution_scope": "research_only"}
    return view


def snapshot(snapshot_id: str, *, db_path=None) -> dict:
    from .journal import load_snapshot
    return load_snapshot(snapshot_id, db_path=database_path(db_path))


def snapshot_view(stored: dict, *, full: bool = False) -> dict:
    """Bound conversational payloads while retaining full immutable evidence in SQLite."""
    from .advisor import public_view
    result = public_view(stored)
    if full:
        return result
    for item in result.get("evidence", []):
        if isinstance(item.get("bars"), list):
            rows = item.pop("bars")
            item["bar_count"] = len(rows)
            item["recent_bars"] = rows[-5:]
    result["view"] = "summary; stored snapshot contains full bars; digest applies to stored snapshot"
    return result


def analyze_signals(snapshot_id: str, instrument_ids: list[str] | None = None,
                    horizon: str = "swing", *, db_path=None, now=None) -> dict:
    from .signals import analyze
    stored = snapshot(snapshot_id, db_path=db_path)
    return analyze(stored, instrument_ids=instrument_ids, horizon=horizon, now=now)


def research_scan(instrument_ids: list[str] | None = None, horizon: str = "swing", *,
                  config_path=None, db_path=None, decision_at=None, **dependencies) -> dict:
    """Discover within the configured candidate pool, retaining rejected/missing candidates."""
    from .config import load_config
    settings = load_config(config_path)
    candidates = list(instrument_ids or settings.advisor.research_universe)
    if not candidates or len(candidates) > 24:
        raise ValueError("research scan needs 1 to 24 explicit candidates")
    universe = list(dict.fromkeys([*candidates, settings.advisor.benchmark]))
    stored = collect(universe, horizon, db_path=db_path, decision_at=decision_at,
                     allow_us_stocks=True, **dependencies)
    result = analyze_signals(stored["snapshot_id"], candidates, horizon, db_path=db_path,
                             now=decision_at)
    result["market_benchmark"] = settings.advisor.benchmark
    result["benchmark_observation"] = analyze_signals(
        stored["snapshot_id"], [settings.advisor.benchmark], horizon, db_path=db_path,
        now=decision_at)
    result["candidate_scope"] = "configured_pool; not an exhaustive whole-market search"
    result["research_gaps"] = stored.get("research_issues", [])
    result["context"] = advisor_context(db_path=db_path, config_path=config_path)
    return result


def review(snapshot_id: str, proposal: dict, *, db_path=None, now=None) -> dict:
    from .journal import record_recommendation
    from .policy import assess_proposal
    stored = snapshot(snapshot_id, db_path=db_path)
    # Restore a locally stored quota identity only after the submitted public
    # quote matches its complete sanitized projection. It never enters the
    # model response and cannot be changed by a model-supplied account field.
    submitted = proposal.get("retail_quote")
    if isinstance(submitted, dict):
        from .advisor import public_view
        matches = [record.get("retail_quote") for record in stored.get("evidence", [])
                   if record.get("evidence_id") == submitted.get("evidence_id")]
        if len(matches) == 1 and isinstance(matches[0], dict) and submitted == public_view(matches[0]):
            proposal = copy.deepcopy(proposal)
            proposal["retail_quote"] = copy.deepcopy(matches[0])
    sleeve = "gold" if proposal.get("instrument_id") == "GOLD.CNY" else "etf"
    current = context(db_path=db_path, sleeve=sleeve, now=now)
    decision = assess_proposal(proposal, stored, current, now=now)
    gaps = stored.get("research_issues", [])
    if gaps:
        decision.setdefault("warnings", []).insert(0, "部分财报/新闻/宏观依据不可用；仅解释已取得的证据")
    # The persistence identity includes the final warning payload and creation time.
    decision["decision_id"] = "decision-" + hashlib.sha256(strict_json(decision).encode()).hexdigest()
    strict_json(decision)
    record_recommendation(decision, db_path=database_path(db_path))
    return {"decision": decision, "message": render_decision(decision, stored)}


def record(operation: dict, idempotency_key: str, *, db_path=None, now=None) -> dict:
    from .journal import record_operation
    strict_json(operation)
    result = record_operation(operation, idempotency_key, db_path=database_path(db_path), now=now)
    status = result.get("status")
    labels = {"executed": "已记录成交", "pending": "已记为待补全", "intent": "已记录意向",
              "reversed": "已记录撤销", "pending_duplicate": "疑似重复，待核对",
              "duplicate_linked": "已关联原成交，未重复入账"}
    label = labels.get(status, "记录状态：" + str(status))
    missing = result.get("missing_fields") or []
    result["message"] = label + ("；待补：" + "、".join(missing) if missing else "")
    if result.get("duplicate_candidates"):
        result["message"] += "；发现相似记录，请核对是否另一笔成交"
    return result


def render_decision(decision: dict, stored: dict) -> str:
    """Render only assessed action; never substitute the unapproved requested action."""
    labels = {"buy": "可考虑买入", "hold": "保持观察/持有", "reduce": "考虑减少持有",
              "sell": "考虑卖出", "avoid": "暂不考虑买入", "data_insufficient": "数据不足，暂停具体建议"}
    lines = ["建议：" + labels.get(decision.get("action"), "暂停建议，状态待核对")]
    reasons = decision.get("reasons") or []
    if reasons:
        lines.append("依据：" + "；".join(str(r) for r in reasons[:2]))
    conditions = decision.get("conditions") or []
    if conditions:
        lines.append("再看：" + "；".join(str(r) for r in conditions[:2]))
    instrument = stored.get("instruments", {}).get(decision.get("instrument_id"), {})
    if decision.get("action") != "data_insufficient":
        if instrument.get("asset_class") == "index":
            lines[0] = "指数研究观点：" + labels.get(decision.get("action"), "待核对") + "；指数点位不能直接买卖"
        elif instrument.get("asset_class") == "physical_gold" and decision.get("execution_scope") != "actionable":
            lines[0] = "黄金基准研究：" + labels.get(decision.get("action"), "待核对") + "；具体购买需商家报价"
        elif decision.get("execution_scope") != "actionable":
            lines[0] += "（研究观点，尚未核定具体仓位）"
    lines.append("数据：" + str(instrument.get("latest_session") or "未取得完整交易日") + "；" +
                 str(decision.get("data_status") or instrument.get("quality_status", "unknown")))
    evidence_ids = set(decision.get("evidence_ids") or [])
    links = []
    for item in stored.get("evidence", []):
        url = item.get("source_url") or ""
        if item.get("evidence_id") in evidence_ids and url.startswith("https://"):
            link = f"[{item.get('provider', '来源')}]({url})"
            if link not in links:
                links.append(link)
    if links:
        lines.append("来源：" + "、".join(links[:3]))
    warnings = decision.get("warnings") or []
    if warnings:
        lines.append("缺口：" + "；".join(str(w) for w in warnings[:3]))
    return "\n".join(lines)


def declare_coverage(*, sleeve: str = "etf", base_currency: str = "USD",
                     portfolio_version: str | None = None, db_path=None) -> dict:
    """Declare that a sleeve's recorded holdings are complete.

    Omitting portfolio_version reads the current one, which is the normal path.
    Passing one explicitly lets a caller assert WHICH book it inspected, so a
    declaration written against a stale reading is refused rather than silently
    applied to a book that moved in between.
    """
    from .journal import coverage_history, record_coverage_declaration
    path = database_path(db_path)
    version = portfolio_version or context(db_path=db_path)["portfolio_version"]
    receipt = record_coverage_declaration(sleeve=sleeve, base_currency=base_currency,
                                          portfolio_version=version, db_path=path)
    receipt["history"] = coverage_history(db_path=path)
    receipt["message"] = (
        f"已记录 {sleeve} sleeve 的持仓覆盖声明（基准货币 {base_currency}）。"
        "任何新成交都会让这条声明失效，届时需要重新声明。")
    return receipt


def capture_retail_quote(*, snapshot_id: str, merchant: str, product: str,
                         ask_per_fine_gram: float, observed_at: str,
                         account_id: str | None = None, db_path=None, now=None) -> dict:
    """Attach a user-reported merchant gold quote to a stored snapshot.

    Writes a NEW snapshot rather than editing the stored one: snapshots are
    content-addressed and the journal's triggers forbid an update. The returned
    snapshot_id is the one to evaluate against. Only an explicit user-confirmed
    account_id scopes a daily quota; omitting it keeps all accounts included.
    """
    from .journal import save_snapshot
    from . import retailquote
    path = database_path(db_path)
    stored = snapshot(snapshot_id, db_path=path)
    moment = now or datetime.now(timezone.utc)
    quote = retailquote.build(merchant=merchant, product=product,
                              ask_per_fine_gram=ask_per_fine_gram, observed_at=observed_at,
                              account_id=account_id)
    attached = retailquote.attach(stored, quote, now=moment)
    save_snapshot(attached, db_path=path)
    return {"snapshot_id": attached["snapshot_id"], "quote": quote,
            "supersedes": snapshot_id,
            "disclosure": "商家报价由你本人上报，系统只校验格式与时效，不核实价格真伪"}


def adopt(adoption_inputs: dict, *, db_path=None) -> dict:
    """Record an adoption and tell the user how to make it live.

    Recording is not activation. The engine trades whatever
    config/user.toml's corresponding sleeve.adopted_rule_id points at, and only the user edits
    that file (ADR-0007 clause 8). `adoption_inputs["result"]` must be a real
    `backtest.engine.Result` object, in memory in this process -- never a JSON
    payload, because Result cannot survive a JSON round trip with the binding
    ruleset.build_adoption enforces (rule_name/parameters/universe matching the
    configuration being adopted) intact. `backtest_cli.py --adopt` is the
    supported way to reach this: it runs a real backtest and calls this
    function directly with the in-memory Result it just produced.
    """
    from .journal import record_adoption
    from .ruleset import build_adoption
    record = build_adoption(**adoption_inputs)
    receipt = record_adoption(record, db_path=database_path(db_path))
    receipt["adoption"] = record
    receipt["next_step"] = (f'set {record["sleeve"]}.adopted_rule_id = "{record["rule_id"]}" in '
                            "config/user.toml to make this rule live")
    return receipt


def adoption(rule_id: str, *, db_path=None) -> dict:
    """Read back a stored adoption. Read-only: recording a new one is `adopt`."""
    from .journal import load_adoption
    return load_adoption(rule_id, db_path=database_path(db_path))


def advisor_strategy_fingerprint(inputs: dict) -> dict:
    """Inspect candidate bindings without inventing a historical lock or adopting."""
    from .advisor_strategy import TemplateSpec, spec_hash, history_hash, runtime_fingerprint, cost_hash
    spec = TemplateSpec(**inputs["spec"])
    return {"kind": "advisor_strategy_fingerprint", "spec": spec.to_dict(),
            "spec_hash": spec_hash(spec), "code_hash": runtime_fingerprint(),
            "data_hash": history_hash(inputs["history_bundle"]),
            "cost_hash": cost_hash(inputs.get("cost_model")),
            "source_authenticated": False, "adopted": False,
            "note": "Current hashes do not establish a historical lock, an unseen holdout or authentic market data."}


def validate_advisor_strategy(inputs: dict, *, data_attestation: str | None = None, now=None) -> dict:
    """Explicit offline hypothesis validation; never selects a live configuration pointer."""
    from .advisor_strategy import TemplateSpec, validate_history
    spec = TemplateSpec(**inputs["spec"])
    expected = inputs["expected_sessions"]
    if not isinstance(expected, dict):
        raise ValueError("expected_sessions must map calendar years to session counts")
    calendar_counts = {}
    for year, count in expected.items():
        if isinstance(year, bool) or (not isinstance(year, int) and
                                      not (isinstance(year, str) and year.isdecimal())):
            raise ValueError("expected_sessions years must be calendar integers")
        normalized = int(year)
        if normalized in calendar_counts:
            raise ValueError("duplicate normalized calendar year")
        calendar_counts[normalized] = count
    return validate_history(spec, inputs["history_bundle"], freeze_manifest=inputs["freeze_manifest"],
                            expected_sessions=calendar_counts, cost_model=inputs.get("cost_model"),
                            data_attestation=data_attestation, now=now)


def adopt_advisor_strategy(inputs: dict, confirmation: str, data_attestation: str, *, db_path=None) -> dict:
    """CLI-only explicit adoption of a validated candidate; stock/swing rule pointer stays manual."""
    from .advisor_strategy import TemplateSpec, build_adoption
    from .journal import record_adoption
    validation = validate_advisor_strategy(inputs, data_attestation=data_attestation)
    candidate = build_adoption(TemplateSpec(**inputs["spec"]), validation, confirmation)
    # The explicit statement is local evidence. Re-adopting the identical
    # immutable strategy reuses its committed record rather than overwriting it.
    try:
        previous = adoption(candidate["rule_id"], db_path=db_path)
    except KeyError:
        previous = None
    if previous is not None:
        from .advisor_strategy import verify_adoption
        if not verify_adoption(previous):
            raise ValueError("existing strategy adoption is no longer valid")
        candidate = previous
    receipt = record_adoption(candidate, db_path=database_path(db_path))
    return {"receipt": public_response(receipt), "rule": public_response(candidate),
            "message": "已记录策略采用；当前配置指针需用户明确设置，本操作未提交订单。"}


def _verify_brake_evidence(stored: dict, brake: dict | None) -> None:
    """Every brake evidence id must name a record that exists and is news.

    The brake is the only evidence field in the system with no validation behind
    it, and its input is a headline the model read from an aggregator. An id that
    resolves to nothing would become the stated grounds for zeroing a basket.
    """
    ids = list((brake or {}).get("evidence_ids") or [])
    if not ids:
        return
    records = {r.get("evidence_id"): r for r in stored.get("evidence", [])}
    for eid in ids:
        record = records.get(eid)
        if record is None:
            raise ValueError(f"brake evidence {eid!r} is not in snapshot "
                             f"{stored.get('snapshot_id')}")
        if record.get("critical_evidence_eligible") is not False:
            raise ValueError(f"brake evidence {eid!r} is market evidence, not news; the brake "
                             "reads news and market evidence belongs in evidence_ids")


ORDER_LABELS = {"buy": "买入", "reduce": "减持", "sell": "清仓"}


def _brake_zeroed(order: dict) -> bool:
    """True when the brake took a real order down to nothing."""
    applied = order.get("brake") or {}
    return bool(applied.get("pre_brake_quantity")) and not applied.get("post_brake_quantity")


def _baseline_required(orders: list[dict]) -> bool:
    return any(order.get("drawdown_history_status") in {
        "baseline_required", "valuation_unavailable"} for order in orders)


def render_evaluation(result: dict, stored: dict) -> str:
    """Render only what the engine produced. Never print a heading with nothing under it.

    Everything the engine decided about a symbol must reach this message. An
    earlier version skipped every order without a `quantity`, which is exactly
    what a brake that zeroes a buy produces -- the line simply vanished, and if
    the brake emptied the whole basket the message blamed "风险检查未全部通过"
    when every risk check had in fact passed. A cause the user cannot act on is
    worse than no cause.
    """
    lines = []
    if result.get("sleeve") == "gold":
        order = (result.get("orders") or [{}])[0]
        if order.get("action") == "buy":
            lines.append("按已采纳规则计算的黄金定投：")
            lines.append(f"  {order['instrument_id']} 买入 {order['amount_cny']} 元，"
                         f"约 {order['grams']} 克，按 {order['ask_per_fine_gram']} 元/克")
            lines.append(f"  报价来源：{order['retail_quote']['merchant']}"
                         f"{order['retail_quote']['product']}（你本人上报，系统只校验格式与时效）")
        else:
            lines.append("研究观点，未给出具体克数（" +
                         "；".join(order.get("refusals") or order.get("reasons") or ["原因未记录"]) + "）")
        if _baseline_required([order]):
            lines.append("尚无可比较估值历史；本次已保留估值，更新行情后再评估")
        if (result.get("brake") or {}).get("level", "none") != "none":
            lines.append(result["brake"]["disclosure"])
        # All three disclosures travel with the number, never in a document the
        # reader may not open. The schedule one exists because engine.run
        # cannot exercise a contribution schedule at all (see the measurement
        # above Task 5); the waiver one because gold's evidence is ten years
        # deep and misses 2008.
        if result.get("schedule_disclosure"):
            lines.append(result["schedule_disclosure"])
        if result.get("waived"):
            lines.append("该规则的准入门槛带有书面豁免，见 ADR-0008")
        return "\n".join(lines)
    orders = result.get("orders") or []
    suppressed = [o for o in orders if _brake_zeroed(o)]
    if result.get("execution_scope") != "actionable":
        blocked = result.get("blocked_symbols") or []
        # Named symbols before generic phrasing: "which one" is the only part
        # the user can do anything about.
        failed = [o["instrument_id"] for o in orders
                  if o.get("execution_scope") != "actionable"
                  and not o.get("no_action_required") and o not in suppressed]
        if blocked:
            reason = "数据未通过校验：" + "、".join(blocked)
        elif not result.get("coverage_known"):
            reason = "持仓覆盖未声明"
        elif not result.get("rebalance_due"):
            reason = "规则未触发再平衡"
        elif _baseline_required(orders):
            reason = "尚无可比较估值历史；本次已保留估值，更新行情后再评估"
        elif failed:
            reason = "风险检查未通过：" + "、".join(failed)
        elif suppressed:
            reason = "新闻刹车把本次所有买单归零"
        else:
            reason = "本次没有需要执行的委托"
        lines.append(f"研究观点，未给出具体仓位（{reason}）")
    else:
        # The limit price is rounded for display only -- the journal keeps the
        # engine's value. A close carried through a weight calculation lands on
        # things like 153.92000000000002, and this line is what the user copies
        # into an order ticket.
        priced = [f"  {o['instrument_id']} "
                  f"{ORDER_LABELS.get(o['action'], o['action'])} "
                  f"{o['quantity']} 股，限价 {float(o['limit_price']):.2f}"
                  for o in orders if o.get("quantity")]
        # A `skip` brake empties this list while the basket itself stays
        # actionable, so the heading must not be printed unconditionally.
        lines.append("按已采纳规则计算的委托：" if priced else "本次没有需要下单的标的")
        lines.extend(priced)
    if suppressed:
        lines.append("新闻刹车归零，本次不下单：" + "、".join(
            f"{o['instrument_id']}（原计划 {o['brake']['pre_brake_quantity']} 股）"
            for o in suppressed))
    unfunded = (result.get("cash_plan") or {}).get("unfunded") or []
    if unfunded:
        lines.append("现金不足未下单：" + "、".join(unfunded))
    if (result.get("brake") or {}).get("level", "none") != "none":
        lines.append(result["brake"]["disclosure"])
    if result.get("waived"):
        lines.append("该规则的准入门槛带有书面豁免，见 ADR-0006")
    return "\n".join(lines)


def evaluate(*, snapshot_id: str, sleeve: str = "etf", config_path=None, db_path=None,
            brake: dict | None = None, now=None) -> dict:
    """Run the adopted rule against a stored snapshot and persist every order.

    `sleeve` selects which pointer, which cash figure and whose coverage
    declaration are read. The two sleeves never share any of the three: the ETF
    book is USD and the gold book is CNY, and CLAUDE.md forbids adding them
    without dated FX. A pointer that resolves to an adoption for the OTHER
    sleeve is refused rather than run, because the only thing that would
    otherwise catch it is the currency of the numbers it produced.

    The journal validates and commits the whole basket in one transaction. A
    conflicting portfolio version or a failure on any row rolls back every row.
    """
    from .config import load_config
    from .journal import load_adoption, record_recommendations
    from .policy import evaluate_rule
    from .ruleset import SCHEMA_VERSION
    from .journal import COVERAGE_SLEEVES
    if sleeve not in COVERAGE_SLEEVES:
        raise ValueError(f"sleeve must be one of {COVERAGE_SLEEVES}, got {sleeve!r}")
    settings = load_config(config_path)
    section = settings.gold if sleeve == "gold" else settings.etf
    pointer = section.adopted_rule_id
    investable = (settings.gold.investable_total_cny if sleeve == "gold"
                  else settings.etf.investable_cash_usd)
    if not pointer:
        raise ValueError(f"no rule is adopted; set {sleeve}.adopted_rule_id in config/user.toml "
                         "to a rule id printed by `backtest_cli.py --adopt`")
    path = database_path(db_path)
    adoption_record = load_adoption(pointer, db_path=path)
    if adoption_record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("adopted rule uses an older execution schema; rerun the backtest "
                         "and adopt its result before evaluation")
    if adoption_record.get("sleeve") != sleeve:
        raise ValueError(f"{sleeve}.adopted_rule_id points at {pointer}, whose sleeve is "
                          f"{adoption_record.get('sleeve')!r}; a pointer must name a rule for "
                          "its own sleeve, because the two books are different currencies")
    if sleeve == "etf":
        adopted_floor = adoption_record.get("cash_floor_pct")
        if (isinstance(adopted_floor, bool) or not isinstance(adopted_floor, (int, float))
                or abs(adopted_floor - settings.etf.min_cash_reserve_pct) > 1e-12):
            raise ValueError("configured cash reserve differs from the adopted backtest; "
                             "rerun backtest_cli.py with --cash-floor-pct and adopt that rule")
        if (settings.etf.universe
                and set(settings.etf.universe) != set(adoption_record.get("universe", []))):
            raise ValueError("configured ETF universe differs from the adopted backtest; "
                             "backtest and adopt the configured universe before evaluation")
    stored = snapshot(snapshot_id, db_path=db_path)
    _verify_brake_evidence(stored, brake)
    current = context(db_path=db_path, sleeve=sleeve, now=now)
    if sleeve == "etf":
        current["max_drawdown_pct"] = settings.etf.max_drawdown_pct
    if sleeve == "gold":
        # The [gold] account limits live in config, not in the adoption: they
        # describe the merchant's product (Bank of China 积存金: 1200 CNY
        # minimum, 200 CNY steps, 10 orders a day), not the backtested rule, and
        # they change when the user changes banks rather than when the rule
        # changes. A stored adoption that already carries its own block wins, so
        # an explicitly-recorded contribution is never silently overwritten.
        adoption_record = {**adoption_record,
                           "gold": {"min_order_cny": settings.gold.min_order_cny,
                                    "order_increment_cny": settings.gold.order_increment_cny,
                                    "max_orders_per_day": settings.gold.max_orders_per_day,
                                    "contribution_cny": settings.gold.contribution_cny,
                                    **(adoption_record.get("gold") or {})}}
    result = evaluate_rule(adoption=adoption_record, snapshot=stored, context=current,
                           investable_cash=investable, brake=brake, now=now)
    prepared = []
    for order in result["orders"]:
        if (result["execution_scope"] != "actionable"
                and order.get("execution_scope") == "actionable"
                and order.get("action") in {"buy", "reduce", "sell"}):
            raise ValueError("a non-executable basket cannot persist an actionable transaction")
        order["basket_execution_scope"] = result["execution_scope"]
        order["adoption_schema_version"] = adoption_record["schema_version"]
        order["evaluation_id"] = result["evaluation_id"]
        order["evaluation_session"] = result["evaluation_session"]
        order["rebalance_due"] = result["rebalance_due"]
        order["decision_id"] = "decision-" + hashlib.sha256(
            strict_json(order).encode()).hexdigest()
        for field in ("snapshot_id", "portfolio_version", "instrument_id", "action"):
            if not isinstance(order.get(field), str) or not order[field]:
                raise ValueError(f"order for {order.get('instrument_id')!r} cannot be recorded: "
                                 f"{field} is missing")
        prepared.append(order)
    record_recommendations(prepared, db_path=path)
    result["message"] = render_evaluation(result, stored)
    return result


def capabilities(*, config_path=None) -> dict:
    load_credentials()
    from importlib.util import find_spec
    from .config import load_config
    settings = load_config(config_path)
    return {"schema_version": 1, "credentials_present": {k: bool(os.getenv(k)) for k in KEY_NAMES},
            "state": "local_sqlite", "data_budget": "free_research; optional_broker_entitlement",
            "advisor": {"enabled": settings.advisor.enabled,
                        "stock_scope": "provider_verified_common_stock; funded_cards_require_separate_adoption",
                        "signals": "deterministic_completed_session_research",
                        "manual_plans": "adopted_legacy_ETF_or_current_advisor_template; fresh_broker_prerequisites",
                        "strategy_templates": ["long_term_trend", "swing_breakout"],
                        "swing_scope": "separately_validated_adopted_template; confirmed_mode_lots",
                        "historical_sources_authenticated": False},
            "broker": {"enabled": settings.ibkr.enabled, "sdk_available": find_spec("ibapi") is not None,
                       "connection_tested": False, "order_submission": False,
                       "orders_scope_confirmed": settings.ibkr.orders_scope_confirmed},
            "note": "credential presence is not an entitlement or live data test"}

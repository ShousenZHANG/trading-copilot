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


def context(instrument_ids: list[str] | None = None, *, db_path=None, as_of=None) -> dict:
    from .journal import get_context
    return get_context(instrument_ids, as_of=as_of, db_path=database_path(db_path))


def snapshot(snapshot_id: str, *, db_path=None) -> dict:
    from .journal import load_snapshot
    return load_snapshot(snapshot_id, db_path=database_path(db_path))


def snapshot_view(stored: dict, *, full: bool = False) -> dict:
    """Bound conversational payloads while retaining full immutable evidence in SQLite."""
    if full:
        return stored
    result = copy.deepcopy(stored)
    for item in result.get("evidence", []):
        if isinstance(item.get("bars"), list):
            rows = item.pop("bars")
            item["bar_count"] = len(rows)
            item["recent_bars"] = rows[-5:]
    result["view"] = "summary; stored snapshot contains full bars; digest applies to stored snapshot"
    return result


def review(snapshot_id: str, proposal: dict, *, db_path=None, now=None) -> dict:
    from .journal import record_recommendation
    from .policy import assess_proposal
    stored = snapshot(snapshot_id, db_path=db_path)
    current = context(db_path=db_path)
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
        url = item.get("source_url", "")
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


def adopt(adoption_inputs: dict, *, db_path=None) -> dict:
    """Record an adoption and tell the user how to make it live.

    Recording is not activation. The engine trades whatever
    config/user.toml's etf.adopted_rule_id points at, and only the user edits
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
    receipt["next_step"] = (f'set etf.adopted_rule_id = "{record["rule_id"]}" in '
                            "config/user.toml to make this rule live")
    return receipt


def adoption(rule_id: str, *, db_path=None) -> dict:
    """Read back a stored adoption. Read-only: recording a new one is `adopt`."""
    from .journal import load_adoption
    return load_adoption(rule_id, db_path=database_path(db_path))


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


def evaluate(*, snapshot_id: str, config_path=None, db_path=None,
            brake: dict | None = None, now=None) -> dict:
    """Run the adopted rule against a stored snapshot and persist every order.

    All orders are validated before any is written. record_recommendation opens
    its own transaction per row, so writing as we go would leave half a basket in
    a table with immutability triggers if a later row failed. That guarantee
    covers validation failures (a missing required field) -- it does not make
    the write loop itself atomic across rows, because record_recommendation
    commits one row per call with no cross-row transaction primitive. A conflict
    that lands on the FIRST row leaves nothing committed; a conflict landing on
    a LATER row (a genuine race against a concurrent trade) would still leave
    earlier rows committed. See _test_copilot_service.py's
    evaluate_with_a_failing_row for the demonstration and full explanation.
    """
    from .config import load_config
    from .journal import load_adoption, record_recommendation
    from .policy import evaluate_rule
    settings = load_config(config_path)
    pointer = settings.etf.adopted_rule_id
    if not pointer:
        raise ValueError("no rule is adopted; set etf.adopted_rule_id in config/user.toml to a "
                         "rule id printed by `backtest_cli.py --adopt`")
    path = database_path(db_path)
    adoption_record = load_adoption(pointer, db_path=path)
    stored = snapshot(snapshot_id, db_path=db_path)
    _verify_brake_evidence(stored, brake)
    current = context(db_path=db_path)
    result = evaluate_rule(adoption=adoption_record, snapshot=stored, context=current,
                           investable_cash=settings.etf.investable_cash_usd,
                           brake=brake, now=now)
    prepared = []
    for order in result["orders"]:
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
    for order in prepared:
        record_recommendation(order, db_path=path)
    result["message"] = render_evaluation(result, stored)
    return result


def capabilities() -> dict:
    load_credentials()
    return {"schema_version": 1, "credentials_present": {k: bool(os.getenv(k)) for k in KEY_NAMES},
            "state": "local_sqlite", "data_budget": "free_only",
            "note": "credential presence is not an entitlement or live data test"}

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
KEY_NAMES = (
    "FINNHUB_API_KEY", "FRED_API_KEY", "APCA_API_KEY_ID", "APCA_API_SECRET_KEY",
    "ALPACA_API_KEY", "ALPACA_SECRET_KEY", "SEC_USER_AGENT",
)


def load_credentials() -> None:
    """Load only provider settings; preserve explicit process environment overrides."""
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
        if name in KEY_NAMES and value:
            os.environ.setdefault(name, value)


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


def capabilities() -> dict:
    load_credentials()
    return {"schema_version": 1, "credentials_present": {k: bool(os.getenv(k)) for k in KEY_NAMES},
            "state": "local_sqlite", "data_budget": "free_only",
            "note": "credential presence is not an entitlement or live data test"}

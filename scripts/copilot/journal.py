"""Local append-only operation journal shared by Claude Code and Codex.

All public writes return only after SQLite commits. Monetary values are decimal
strings; unknown fees, account balances, FX and opening holdings remain unknown.
The original conversation statement is retained as evidence, never as SQL/code.
Run the isolated contract tests with ``python scripts/_test_journal.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time as wall_time
import uuid
from contextlib import contextmanager
from datetime import date, datetime, time, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "state" / "copilot.sqlite"


class JournalConflict(ValueError):
    """A retry changed payload, an external trade conflicts, or a version is old."""


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _clock(value: Any = None, *, end_of_day: bool = False) -> datetime:
    if value is None:
        result = datetime.now(timezone.utc)
    elif isinstance(value, datetime):
        result = value
    elif isinstance(value, date):
        result = datetime.combine(value, time.max if end_of_day else time.min, timezone.utc)
    elif isinstance(value, str):
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            result = datetime.combine(date.fromisoformat(value), time.max if end_of_day else time.min, timezone.utc)
        else:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("time must be an ISO date or an ISO timestamp with timezone")
    if result.tzinfo is None:
        raise ValueError("timestamp requires an explicit timezone")
    return result.astimezone(timezone.utc)


def _stamp(value: Any = None, *, end_of_day: bool = False) -> str:
    return _clock(value, end_of_day=end_of_day).isoformat(timespec="microseconds")


def _decimal(value: Any, field: str, *, zero: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a decimal string, never a float")
    try:
        number = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not number.is_finite() or (number < 0 if zero else number <= 0):
        raise ValueError(f"{field} must be {'nonnegative' if zero else 'positive'} and finite")
    if len(number.as_tuple().digits) > 28 or abs(number.as_tuple().exponent) > 12:
        raise ValueError(f"{field} precision exceeds 28 digits / 12 decimal places")
    return _number(number)


def _number(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


_SCHEMA = """
CREATE TABLE IF NOT EXISTS journal_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    operation_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    state TEXT NOT NULL,
    UNIQUE(operation_id, version)
);
CREATE TABLE IF NOT EXISTS operation_states (
    operation_id TEXT PRIMARY KEY,
    version INTEGER NOT NULL,
    state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS journal_requests (
    idempotency_key TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    receipt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS external_trades (
    account_id TEXT NOT NULL,
    external_trade_id TEXT NOT NULL,
    operation_id TEXT NOT NULL REFERENCES operation_states(operation_id),
    PRIMARY KEY(account_id, external_trade_id)
);
CREATE TABLE IF NOT EXISTS holdings (
    holding_key TEXT PRIMARY KEY,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS evidence_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    recorded_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS recommendations (
    decision_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES evidence_snapshots(snapshot_id),
    recorded_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON journal_events
BEGIN SELECT RAISE(ABORT, 'journal events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON journal_events
BEGIN SELECT RAISE(ABORT, 'journal events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_update BEFORE UPDATE ON evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'evidence snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS snapshots_no_delete BEFORE DELETE ON evidence_snapshots
BEGIN SELECT RAISE(ABORT, 'evidence snapshots are immutable'); END;
CREATE TRIGGER IF NOT EXISTS recommendations_no_update BEFORE UPDATE ON recommendations
BEGIN SELECT RAISE(ABORT, 'recommendations are immutable'); END;
CREATE TRIGGER IF NOT EXISTS recommendations_no_delete BEFORE DELETE ON recommendations
BEGIN SELECT RAISE(ABORT, 'recommendations are immutable'); END;
"""


@contextmanager
def _connection(db_path: Any = None):
    path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA foreign_keys=ON")
        # Concurrent first-open WAL conversion can return SQLITE_BUSY immediately
        # even with busy_timeout. Retry only bootstrap locks, with a hard bound.
        for attempt in range(8):
            try:
                if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                break
            except sqlite3.OperationalError as exc:
                if getattr(exc, "sqlite_errorcode", None) not in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED} or attempt == 7:
                    raise
                wall_time.sleep(min(0.02 * 2 ** attempt, 0.25))
        connection.execute("PRAGMA synchronous=FULL")
        connection.executescript(_SCHEMA)
        yield connection
    finally:
        connection.close()


@contextmanager
def _transaction(connection: sqlite3.Connection):
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
        connection.execute("COMMIT")
    except BaseException:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _confirmation(statement: str, side: Any) -> bool:
    """Conservative statement guard, not an unrestricted natural-language parser.

    Ambiguous, attributed, quoted, hypothetical and negated statements require
    clarification. This guard supplements structured intent supplied by the host.
    """
    if not statement or side not in {"buy", "sell"}:
        return False
    if re.search(r"[\"“”‘’「」『』?？]|(?:建议|假如|假设|如果|想要|想买|想卖|打算|计划|准备|尚未|没有|没买|没卖|未买|未卖|未成交|没成交|取消|以为|并未|他说|她说|别人|朋友|听说|引用|举例|模拟|测试)|(?:买了|卖了).*?(?:吗|没|么)(?:[。！!]|$)", statement):
        return False
    if re.search(r"\b(?:if|would|could|should|plan|planning|want|recommend|suggest|hypothetical|example|said|says|told|quote|not|never|haven't|hasn't|didn't|don't|cancelled|canceled|test|pretend)\b", statement, re.I) or re.search(r"(?:^|\s)'[^']*\bI\s+.*\b(?:bought|sold)\b[^']*'", statement, re.I):
        return False
    chinese = r"(?:我\s*)?(?:已经|刚刚|刚|已)?\s*(?:买了|买入了|买好了|购入了|买进了|买入成功|已买入|买入成交|购买完成|买入.*?成交)" if side == "buy" else r"(?:我\s*)?(?:已经|刚刚|刚|已)?\s*(?:卖了|卖出了|售出了|卖出成功|已卖出|卖出成交|卖出.*?成交)"
    english = r"\bI\s+(?:(?:have|just|already)\s+)*(?:bought|purchased)\b" if side == "buy" else r"\bI\s+(?:(?:have|just|already)\s+)*sold\b"
    return bool(re.search(chinese, statement) or re.search(english, statement, re.I))


def _amendment_confirmation(statement: str, event_type: str) -> bool:
    if re.search(r"[\"“”「」]|不要|别撤销|别修改|不修改|不撤销|如果|假如|假设|建议|\b(?:if|would|recommend|suggest|don't|not)\b", statement, re.I):
        return False
    pattern = r"撤销|取消.*记录|删除.*记录|重复|\b(?:reverse|revoke|duplicate|delete\s+.*record|cancel\s+.*record)\b" if event_type == "reverse" else r"更正|修正|改为|改成|写错|记错|修改|填错|应为|错了|正确|\b(?:correct|correction|amend|amendment|mistake|change)\b"
    return bool(re.search(pattern, statement, re.I))


_TRADE_FIELDS = (
    "instrument_id", "side", "quantity", "unit", "price", "currency",
    "occurred_at", "fees", "account_id", "external_trade_id", "merchant",
    "purity", "weight_grams", "price_basis", "product_id",
)


def _normalize(payload: dict, recorded_at: str, *, confirmation: bool = True) -> tuple[dict, str, list[str]]:
    result = dict(payload)
    for field in ("statement", "source_message_id"):
        if not isinstance(result.get(field), str) or not result[field].strip():
            raise ValueError(f"{field} is required and must retain the original message identity/text")
    if result.get("execution_status") not in {"executed", "intent"}:
        raise ValueError("execution_status must be executed or intent")
    missing = [field for field in ("instrument_id", "side", "quantity", "unit", "price", "currency", "occurred_at") if result.get(field) in (None, "")]
    instrument = result.get("instrument_id")
    if instrument is not None:
        if not isinstance(instrument, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,23}", instrument):
            raise ValueError("instrument_id must identify a tradable US share/ETF or GOLD.CNY, not an index/derivative")
    for field, accepted in (("side", {"buy", "sell"}), ("unit", {"share", "gram", "item"}), ("currency", {"USD", "CNY"}), ("price_basis", {"unit", "total"})):
        if result.get(field) is not None and result[field] not in accepted:
            raise ValueError(f"invalid {field}")
    for field in ("quantity", "price", "fees", "purity", "weight_grams"):
        if result.get(field) is not None:
            result[field] = _decimal(result[field], field, zero=field == "fees")
    result.setdefault("fees", None)
    result.setdefault("price_basis", "unit")
    result.setdefault("account_id", None)
    if result.get("purity") and Decimal(result["purity"]) > 1:
        raise ValueError("purity is a fraction in (0, 1], e.g. '0.9999'")
    if result.get("occurred_at"):
        event_clock = _clock(result["occurred_at"])
        if event_clock > _clock(recorded_at):
            raise ValueError("occurred_at is in the future")
        # Preserve date-only precision instead of inventing a fill time.
        if "T" in result["occurred_at"] or " " in result["occurred_at"]:
            result["occurred_at"] = event_clock.isoformat(timespec="microseconds")
    if instrument == "GOLD.CNY":
        if result.get("currency") not in (None, "CNY") or result.get("unit") not in (None, "gram", "item"):
            raise ValueError("GOLD.CNY requires CNY and gram/item units")
        for field in ("merchant", "purity"):
            if result.get(field) in (None, ""):
                missing.append(field)
        if result.get("unit") == "item" and not result.get("weight_grams"):
            missing.append("weight_grams")
    elif instrument:
        if result.get("currency") not in (None, "USD") or result.get("unit") not in (None, "share"):
            raise ValueError("US shares and ETFs require USD/share")
    if result.get("external_trade_id") and not result.get("account_id"):
        missing.append("account_id")
    for field in ("account_id", "external_trade_id", "merchant", "product_id"):
        if result.get(field) is not None and (not isinstance(result[field], str) or not result[field].strip()):
            raise ValueError(f"{field} must be a nonempty string or null")
    if confirmation and result["execution_status"] == "executed" and not _confirmation(result["statement"], result.get("side")):
        missing.append("execution_confirmation")
    status = "intent" if result["execution_status"] == "intent" else ("pending" if missing else "executed")
    return result, status, sorted(set(missing))


def _states(connection: sqlite3.Connection, as_of: Any = None) -> list[dict]:
    if as_of is None:
        rows = connection.execute("SELECT state FROM operation_states ORDER BY operation_id").fetchall()
    else:
        rows = connection.execute("""SELECT e.state FROM journal_events e JOIN (
            SELECT operation_id, MAX(version) AS version FROM journal_events
            WHERE recorded_at <= ? GROUP BY operation_id
        ) last ON e.operation_id=last.operation_id AND e.version=last.version ORDER BY e.operation_id""", (_stamp(as_of, end_of_day=True),)).fetchall()
    return [json.loads(row["state"]) for row in rows]


def _portfolio_version(states: list[dict]) -> str:
    active = [{"operation_id": state["operation_id"], "version": state["version"], "status": state["status"], "operation": {key: state["operation"].get(key) for key in _TRADE_FIELDS}} for state in states if state.get("had_execution")]
    return _hash(sorted(active, key=lambda state: state["operation_id"]))


def _holdings(states: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    active = [state for state in states if state["status"] == "executed"]
    active.sort(key=lambda state: (_stamp(state["operation"]["occurred_at"]), state["first_sequence"]))
    with localcontext() as context:
        context.prec = 50
        for state in active:
            trade = state["operation"]
            identity = {key: trade.get(key) for key in ("instrument_id", "account_id", "currency", "unit")}
            if trade["instrument_id"] == "GOLD.CNY":
                identity.update({key: trade.get(key) for key in ("merchant", "product_id", "purity", "weight_grams")})
            key = _hash(identity)
            bucket = buckets.setdefault(key, dict(identity, holding_key=key, quantity=Decimal(0), gross_cost_basis=Decimal(0), cost_basis_including_fees=Decimal(0), opening_balance_required=False, fees_unknown=False, operation_ids=[]))
            quantity, price = Decimal(trade["quantity"]), Decimal(trade["price"])
            total = price if trade["price_basis"] == "total" else quantity * price
            bucket["operation_ids"].append(state["operation_id"])
            if trade["side"] == "buy":
                bucket["quantity"] += quantity
                if bucket["gross_cost_basis"] is not None:
                    bucket["gross_cost_basis"] += total
                if trade["fees"] is None:
                    bucket["fees_unknown"] = True
                    bucket["cost_basis_including_fees"] = None
                elif bucket["cost_basis_including_fees"] is not None:
                    bucket["cost_basis_including_fees"] += total + Decimal(trade["fees"])
            else:
                prior_quantity = bucket["quantity"]
                bucket["quantity"] -= quantity
                if prior_quantity < quantity:
                    bucket["opening_balance_required"] = True
                    bucket["gross_cost_basis"] = None
                    bucket["cost_basis_including_fees"] = None
                elif prior_quantity > 0:
                    proportion = bucket["quantity"] / prior_quantity
                    for cost in ("gross_cost_basis", "cost_basis_including_fees"):
                        if bucket[cost] is not None:
                            bucket[cost] *= proportion
            if bucket["quantity"] == 0 and not bucket["opening_balance_required"]:
                bucket["gross_cost_basis"] = Decimal(0)
                bucket["cost_basis_including_fees"] = Decimal(0)
                bucket["fees_unknown"] = False
        result = []
        for key in sorted(buckets):
            bucket = buckets[key]
            if bucket["instrument_id"] == "GOLD.CNY":
                weight = Decimal(bucket["weight_grams"]) if bucket["unit"] == "item" else Decimal(1)
                bucket["pure_gold_grams"] = _number(bucket["quantity"] * weight * Decimal(bucket["purity"]))
            for field in ("quantity", "gross_cost_basis", "cost_basis_including_fees"):
                if bucket[field] is not None:
                    bucket[field] = _number(bucket[field])
            bucket["cost_method"] = "weighted_average_observed_trades"
            result.append(bucket)
    return result


def _rebuild_projection(connection: sqlite3.Connection) -> str:
    states = _states(connection)
    connection.execute("DELETE FROM holdings")
    for holding in _holdings(states):
        connection.execute("INSERT INTO holdings(holding_key,payload) VALUES (?,?)", (holding["holding_key"], _json(holding)))
    return _portfolio_version(states)


def _trade_fingerprint(operation: dict) -> dict:
    return {key: operation.get(key) for key in _TRADE_FIELDS if key not in {"external_trade_id"}}


def record_operation(operation: dict, idempotency_key: str, *, db_path: Any = None, now: Any = None) -> dict:
    """Record a statement, completion, correction or reversal atomically.

    Updates use event_type=complete/correct/reverse, operation_id and
    expected_version. Completion/correction accepts a partial field patch; the
    new statement and source_message_id are required. Use a fresh stable request
    key for each actual user message; retry exactly the same key and payload.
    For gold items weight_grams means gross grams *per item*, purity a fraction.
    Suspected duplicate fills are pending_duplicate and excluded from holdings.
    Resolve with duplicate_of=<candidate ID>, or distinct_confirmation=True;
    both require original user text explicitly confirming that interpretation.
    """
    if not isinstance(operation, dict) or not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("operation dict and stable nonempty idempotency_key are required")
    request_hash = _hash(operation)
    recorded_at = _stamp(now)
    event_type = operation.get("event_type", "record")
    if event_type not in {"record", "complete", "correct", "reverse"}:
        raise ValueError("event_type must be record, complete, correct or reverse")
    with _connection(db_path) as connection:
        with _transaction(connection):
            prior_request = connection.execute("SELECT * FROM journal_requests WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if prior_request:
                if prior_request["payload_hash"] != request_hash:
                    raise JournalConflict("idempotency key was already used with a different payload")
                return dict(json.loads(prior_request["receipt"]), replayed=True)
            previous = None
            if event_type != "record":
                if not operation.get("operation_id") or type(operation.get("expected_version")) is not int:
                    raise ValueError("updates require operation_id and integer expected_version")
                row = connection.execute("SELECT state FROM operation_states WHERE operation_id=?", (operation["operation_id"],)).fetchone()
                if row is None:
                    raise KeyError("operation does not exist")
                previous = json.loads(row["state"])
                if operation["expected_version"] != previous["version"]:
                    raise JournalConflict(f"expected version {operation['expected_version']}; current version is {previous['version']}")
                if previous["status"] in {"reversed", "duplicate_linked"}:
                    raise JournalConflict("reversed operations cannot be edited; record a new real operation")
                if event_type == "complete" and previous["status"] not in {"pending", "pending_duplicate", "intent"}:
                    raise JournalConflict("only pending operations or intents can be completed")
            elif operation.get("operation_id") or operation.get("expected_version") is not None:
                raise ValueError("record cannot supply an existing operation_id/version")
            payload = dict(previous["operation"]) if previous else {}
            payload.update({key: value for key, value in operation.items() if key not in {"event_type", "operation_id", "expected_version"}})
            # Every new event must retain the current, actual message evidence.
            if not operation.get("statement") or not operation.get("source_message_id"):
                raise ValueError("each event requires statement and source_message_id")
            if event_type == "reverse" or (event_type == "correct" and previous and previous.get("had_execution")):
                if not _amendment_confirmation(operation["statement"], event_type):
                    raise ValueError("amendment requires the user's explicit correction/reversal instruction")
            if event_type == "reverse":
                status, missing = "reversed", []
            else:
                # Once a real fill was established, adding missing factual details
                # or correcting it need not repeat 'I bought' in every sentence.
                confirmed_before = bool(previous and (previous.get("had_execution") or ("execution_confirmation" not in previous.get("missing_fields", []) and previous["operation"]["execution_status"] == "executed")))
                payload, status, missing = _normalize(payload, recorded_at, confirmation=not confirmed_before)
                if previous and previous.get("had_execution") and status != "executed":
                    raise JournalConflict("a confirmed trade must remain complete; use reverse to cancel its projection")
            operation_id = previous["operation_id"] if previous else "op_" + uuid.uuid4().hex
            version = previous["version"] + 1 if previous else 1
            candidates = list(previous.get("duplicate_candidates", [])) if previous else []
            ext_id, account = payload.get("external_trade_id"), payload.get("account_id")
            if previous and (ext_id != previous["operation"].get("external_trade_id") or account != previous["operation"].get("account_id")) and previous["operation"].get("external_trade_id"):
                raise JournalConflict("external trade/account identity cannot be changed; reverse and record the correct event")
            if ext_id and account:
                external = connection.execute("SELECT operation_id FROM external_trades WHERE account_id=? AND external_trade_id=?", (account, ext_id)).fetchone()
                if external and external["operation_id"] != operation_id:
                    existing = json.loads(connection.execute("SELECT state FROM operation_states WHERE operation_id=?", (external["operation_id"],)).fetchone()[0])
                    if _trade_fingerprint(existing["operation"]) != _trade_fingerprint(payload) or existing["status"] != status:
                        raise JournalConflict("external trade ID already exists with different facts or state")
                    receipt = {"operation_id": existing["operation_id"], "version": existing["version"], "status": existing["status"], "missing_fields": existing["missing_fields"], "portfolio_version": _portfolio_version(_states(connection)), "duplicate_candidates": [], "external_duplicate": True, "replayed": False}
                    connection.execute("INSERT INTO journal_requests VALUES (?,?,?)", (idempotency_key, request_hash, _json(receipt)))
                    return receipt
            if status == "executed" and not (previous and previous.get("had_execution")):
                candidates = []
                for candidate in _states(connection):
                    if candidate["status"] != "executed" or candidate["operation_id"] == operation_id:
                        continue
                    other = candidate["operation"]
                    if ext_id and other.get("external_trade_id") and ext_id != other["external_trade_id"]:
                        continue
                    if _trade_fingerprint(other) == _trade_fingerprint(payload):
                        candidates.append(candidate["operation_id"])
                if candidates:
                    statement = payload["statement"]
                    explicit_distinct = operation.get("distinct_confirmation") is True and bool(re.search(r"另一笔|又一笔|两笔|不同.*成交|不是重复|独立.*成交|第二笔|\b(?:separate|distinct|another|different)\b", statement, re.I))
                    if explicit_distinct and re.search(r"如果|假设|假如|建议|[\"“”「」]|\b(?:if|would|suggest|recommend|not\s+(?:separate|distinct|another|different))\b", statement, re.I):
                        explicit_distinct = False
                    if not explicit_distinct:
                        status, missing = "pending_duplicate", ["duplicate_resolution"]
            if operation.get("duplicate_of") is not None:
                duplicate_of = operation["duplicate_of"]
                if not previous or previous["status"] != "pending_duplicate" or duplicate_of not in previous.get("duplicate_candidates", []):
                    raise JournalConflict("duplicate_of must resolve an existing pending duplicate to one of its candidates")
                statement = operation["statement"]
                if not re.search(r"重复|同一笔|同一条|\b(?:duplicate|same\s+(?:trade|fill|operation))\b", statement, re.I) or re.search(r"不是重复|不是同一|不要|如果|假设|建议|\b(?:not|if|would|suggest|recommend)\b", statement, re.I):
                    raise ValueError("duplicate linking requires the user's explicit same-trade confirmation")
                status, missing = "duplicate_linked", []
            first_sequence = previous["first_sequence"] if previous else connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM journal_events").fetchone()[0]
            state = {"operation_id": operation_id, "version": version, "status": status, "missing_fields": missing, "operation": payload, "duplicate_candidates": candidates, "had_execution": status == "executed" or bool(previous and previous.get("had_execution")), "first_sequence": first_sequence, "recorded_at": recorded_at}
            connection.execute("INSERT INTO journal_events(event_id,operation_id,version,event_type,recorded_at,payload,state) VALUES (?,?,?,?,?,?,?)", ("evt_" + uuid.uuid4().hex, operation_id, version, event_type, recorded_at, _json(operation), _json(state)))
            connection.execute("INSERT INTO operation_states VALUES (?,?,?) ON CONFLICT(operation_id) DO UPDATE SET version=excluded.version,state=excluded.state", (operation_id, version, _json(state)))
            if ext_id and account:
                connection.execute("INSERT OR IGNORE INTO external_trades VALUES (?,?,?)", (account, ext_id, operation_id))
            portfolio_version = _rebuild_projection(connection)
            receipt = {"operation_id": operation_id, "version": version, "status": status, "missing_fields": missing, "portfolio_version": portfolio_version, "duplicate_candidates": candidates, "replayed": False}
            if status == "pending_duplicate":
                receipt["warning"] = "Similar trade already recorded; pending confirmation and excluded from holdings."
            if status == "duplicate_linked":
                receipt["duplicate_of"] = payload["duplicate_of"]
            connection.execute("INSERT INTO journal_requests VALUES (?,?,?)", (idempotency_key, request_hash, _json(receipt)))
        return receipt


def get_context(instrument_ids: Any = None, as_of: Any = None, *, db_path: Any = None) -> dict:
    """Read holdings and audit context; as_of means information known by that UTC time."""
    instruments = {instrument_ids} if isinstance(instrument_ids, str) else set(instrument_ids or [])
    with _connection(db_path) as connection:
        connection.execute("BEGIN")
        try:
            states = _states(connection, as_of)
            portfolio_version = _portfolio_version(states)
            holdings = [item for item in _holdings(states) if not instruments or item["instrument_id"] in instruments]
            matching = [state for state in states if not instruments or state["operation"].get("instrument_id") in instruments]
            query = "SELECT payload FROM recommendations"
            params = ()
            if as_of is not None:
                query += " WHERE recorded_at <= ?"
                params = (_stamp(as_of, end_of_day=True),)
            query += " ORDER BY recorded_at DESC, decision_id"
            decisions = [json.loads(row[0]) for row in connection.execute(query, params).fetchall()]
            decisions = [dict(item, portfolio_current=item.get("portfolio_version") == portfolio_version) for item in decisions if not instruments or item.get("instrument_id") in instruments]
            result = {"portfolio_version": portfolio_version, "portfolio_complete": False, "completeness": "unknown", "base_currency": None, "fx_status": "unknown", "portfolio_value": None, "holdings": holdings, "holdings_by_currency": {currency: [item for item in holdings if item["currency"] == currency] for currency in sorted({item["currency"] for item in holdings})}, "pending_operations": [state for state in matching if state["status"] in {"pending", "pending_duplicate"}], "intents": [state for state in matching if state["status"] == "intent"], "operations": matching, "recommendations": decisions, "as_of": _stamp(as_of, end_of_day=True) if as_of is not None else None}
            connection.execute("COMMIT")
            return result
        except BaseException:
            connection.execute("ROLLBACK")
            raise


def save_snapshot(snapshot: dict, *, db_path: Any = None) -> dict:
    """Persist an immutable snapshot, accepting an upstream ID or deriving one."""
    if not isinstance(snapshot, dict) or not snapshot:
        raise ValueError("snapshot must be a nonempty dict")
    payload = dict(snapshot)
    snapshot_id = payload.get("snapshot_id") or "snap_" + _hash(payload)
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("snapshot_id must be a nonempty string")
    payload["snapshot_id"] = snapshot_id
    serialized = _json(payload)
    with _connection(db_path) as connection:
        with _transaction(connection):
            previous = connection.execute("SELECT payload FROM evidence_snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
            if previous and previous[0] != serialized:
                raise JournalConflict("snapshot_id already contains different evidence")
            connection.execute("INSERT OR IGNORE INTO evidence_snapshots VALUES (?,?,?)", (snapshot_id, _stamp(), serialized))
        return {"snapshot_id": snapshot_id, "recorded": True, "replayed": bool(previous)}


def record_recommendation(decision: dict, *, db_path: Any = None) -> dict:
    """Store a structured policy Decision. This never creates a journal trade."""
    if not isinstance(decision, dict):
        raise ValueError("decision must be a dict")
    for field in ("snapshot_id", "portfolio_version", "instrument_id", "action"):
        if not isinstance(decision.get(field), str) or not decision[field]:
            raise ValueError(f"decision requires {field}")
    payload = dict(decision)
    decision_id = payload.get("decision_id") or "decision_" + _hash(payload)
    if not isinstance(decision_id, str) or not decision_id:
        raise ValueError("decision_id must be a nonempty string")
    payload["decision_id"] = decision_id
    serialized = _json(payload)
    with _connection(db_path) as connection:
        with _transaction(connection):
            previous = connection.execute("SELECT payload FROM recommendations WHERE decision_id=?", (decision_id,)).fetchone()
            if previous and previous[0] != serialized:
                raise JournalConflict("decision_id already contains a different decision")
            if not previous and payload["portfolio_version"] != _portfolio_version(_states(connection)):
                raise JournalConflict("portfolio changed before recommendation commit; reassess against current holdings")
            if not connection.execute("SELECT 1 FROM evidence_snapshots WHERE snapshot_id=?", (payload["snapshot_id"],)).fetchone():
                raise ValueError("save the referenced evidence snapshot before recording a recommendation")
            connection.execute("INSERT OR IGNORE INTO recommendations VALUES (?,?,?,?)", (decision_id, payload["snapshot_id"], _stamp(), serialized))
        return {"decision_id": decision_id, "recorded": True, "replayed": bool(previous)}


def _load(table: str, key: str, value: str, db_path: Any) -> dict:
    with _connection(db_path) as connection:
        row = connection.execute(f"SELECT payload FROM {table} WHERE {key}=?", (value,)).fetchone()
        if row is None:
            raise KeyError(f"{key} does not exist")
        return json.loads(row[0])


def load_snapshot(snapshot_id: str, *, db_path: Any = None) -> dict:
    return _load("evidence_snapshots", "snapshot_id", snapshot_id, db_path)


def load_decision(decision_id: str, *, db_path: Any = None) -> dict:
    return _load("recommendations", "decision_id", decision_id, db_path)


def backup(destination: Any, *, db_path: Any = None) -> dict:
    """Write a consistent SQLite backup, including WAL contents, without overwrite."""
    target = Path(destination)
    source = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
    if target.resolve() == source.resolve():
        raise ValueError("backup destination must differ from the journal")
    target.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation avoids accidentally replacing an older recovery point.
    with target.open("xb"):
        pass
    try:
        with _connection(source) as connection:
            output = sqlite3.connect(target)
            try:
                connection.backup(output)
                integrity = output.execute("PRAGMA integrity_check").fetchone()[0]
                if integrity != "ok":
                    raise sqlite3.DatabaseError("backup integrity check failed")
            finally:
                output.close()
    except BaseException:
        target.unlink(missing_ok=True)
        raise
    return {"backup_path": str(target.resolve()), "recorded": True}


if __name__ == "__main__":
    import runpy
    import sys

    if sys.argv[1:] != ["--self-test"]:
        raise SystemExit("Use the shared copilot CLI, or journal.py --self-test")
    sys.argv = [str(Path(__file__).resolve().parents[1] / "_test_journal.py")]
    runpy.run_path(sys.argv[0], run_name="__main__")

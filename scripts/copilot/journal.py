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
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path
from typing import Any

DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "state" / "copilot.sqlite"
SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), "Asia/Shanghai")


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
CREATE TABLE IF NOT EXISTS coverage_declarations (
    declaration_id TEXT PRIMARY KEY,
    sleeve TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    portfolio_version TEXT NOT NULL,
    declared_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS adopted_rules (
    rule_id TEXT PRIMARY KEY,
    sleeve TEXT NOT NULL,
    adopted_at TEXT NOT NULL,
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
CREATE TRIGGER IF NOT EXISTS coverage_no_update BEFORE UPDATE ON coverage_declarations
BEGIN SELECT RAISE(ABORT, 'coverage declarations are immutable; declare again instead'); END;
CREATE TRIGGER IF NOT EXISTS coverage_no_delete BEFORE DELETE ON coverage_declarations
BEGIN SELECT RAISE(ABORT, 'coverage declarations are immutable; declare again instead'); END;
CREATE TRIGGER IF NOT EXISTS adoptions_no_update BEFORE UPDATE ON adopted_rules
BEGIN SELECT RAISE(ABORT, 'adoptions are immutable; adopt a new rule instead'); END;
CREATE TRIGGER IF NOT EXISTS adoptions_no_delete BEFORE DELETE ON adopted_rules
BEGIN SELECT RAISE(ABORT, 'adoptions are immutable; adopt a new rule instead'); END;
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
    from .plan_store import _affirmation
    return _affirmation(statement, event_type)


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
    active = [{"operation_id": state["operation_id"], "version": state["version"], "status": state["status"],
               "operation": (state["operation"] if state.get("had_opening_balance") else
                             {key: state["operation"].get(key) for key in _TRADE_FIELDS})}
              for state in states if state.get("had_execution") or state.get("had_opening_balance")]
    return _hash(sorted(active, key=lambda state: state["operation_id"]))


def _holdings(states: list[dict]) -> list[dict]:
    buckets: dict[str, dict] = {}
    active = [state for state in states if state["status"] in {"executed", "opening_balance"}]
    active.sort(key=lambda state: (_stamp(state["operation"].get("observed_as_of") if state["status"] == "opening_balance"
                                         else state["operation"]["occurred_at"]), state["first_sequence"]))
    reversed_openings = {state["operation"].get("account_id") for state in states
                         if state.get("had_opening_balance") and state["status"] == "reversed"}
    with localcontext() as context:
        context.prec = 50
        for state in active:
            trade = state["operation"]
            if state["status"] == "opening_balance":
                for position in trade["positions"]:
                    identity = dict(instrument_id=position["instrument_id"], account_id=trade["account_id"],
                                    currency=position["currency"], unit="share")
                    key = _hash(identity)
                    bucket = buckets.setdefault(key, dict(identity, holding_key=key, quantity=Decimal(0),
                        gross_cost_basis=None, cost_basis_including_fees=None, opening_balance_required=False,
                        fees_unknown=True, operation_ids=[], opening_balance_recorded=True,
                        con_id=position["con_id"], broker_average_cost=position.get("broker_average_cost")))
                    bucket["quantity"] += Decimal(position["quantity"])
                    bucket["operation_ids"].append(state["operation_id"])
                continue
            identity = {key: trade.get(key) for key in ("instrument_id", "account_id", "currency", "unit")}
            if trade["instrument_id"] == "GOLD.CNY":
                identity.update({key: trade.get(key) for key in ("merchant", "product_id", "purity", "weight_grams")})
            key = _hash(identity)
            bucket = buckets.setdefault(key, dict(identity, holding_key=key, quantity=Decimal(0), gross_cost_basis=Decimal(0), cost_basis_including_fees=Decimal(0), opening_balance_required=False, fees_unknown=False, operation_ids=[]))
            if trade.get("account_id") in reversed_openings:
                bucket.update(opening_balance_required=True, gross_cost_basis=None, cost_basis_including_fees=None)
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
            bucket["cost_method"] = "unknown_opening_basis" if bucket.get("opening_balance_recorded") else "weighted_average_observed_trades"
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


def _economic_trade_fingerprint(operation: dict) -> dict:
    """A fee supplement is not evidence of a second economic execution.

    External IDs still use the complete facts fingerprint: changing known fees
    there requires a versioned correction, never silently rewriting a fill.
    """
    return {key: value for key, value in _trade_fingerprint(operation).items() if key != "fees"}


def _same_broker_account(account_id: object, account_key: str) -> bool:
    """Match a local legacy broker ID to its existing sanitized broker identity."""
    return isinstance(account_id, str) and (account_id == account_key or
        "acct-" + hashlib.sha256(account_id.encode()).hexdigest()[:24] == account_key)


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
                if previous.get("had_opening_balance"):
                    raise ValueError("opening observations must use record_opening_balance for corrections/reversals")
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
            if payload.get("record_kind") == "opening_balance":
                raise ValueError("opening observations require the dedicated broker-bound interface")
            opening_id = payload.get("opening_balance_id")
            if opening_id is not None:
                existing_fill_binding = bool(previous and previous.get("had_execution") and
                                             previous["operation"].get("opening_balance_id") == opening_id)
                opening = next((s for s in _states(connection) if s["operation_id"] == opening_id
                                and s.get("had_opening_balance") and
                                (s["status"] == "opening_balance" or existing_fill_binding)), None)
                if opening is None:
                    raise ValueError("opening_balance_id must reference a current opening observation")
                account = opening["operation"]["account_id"]
                if payload.get("account_id") is not None and not _same_broker_account(payload["account_id"], account):
                    raise JournalConflict("trade account differs from its opening observation")
                payload["account_id"] = account
            elif payload.get("account_id") is not None:
                for opening in _states(connection):
                    if opening["status"] == "opening_balance" and _same_broker_account(payload["account_id"], opening["operation"]["account_id"]):
                        payload["account_id"] = opening["operation"]["account_id"]
                        break
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
                if status == "executed" and payload.get("instrument_id") != "GOLD.CNY":
                    openings = [s for s in _states(connection) if s["status"] == "opening_balance"]
                    if openings and payload.get("account_id") is None:
                        raise JournalConflict("select opening_balance_id before recording an unbound-account fill")
                    for opening in openings:
                        if opening["operation"]["account_id"] == payload.get("account_id") and _clock(payload["occurred_at"]) <= _clock(opening["operation"]["observed_as_of"]):
                            raise JournalConflict("fill predates or overlaps the opening observation; it is already included in that balance")
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
                    if _economic_trade_fingerprint(other) == _economic_trade_fingerprint(payload):
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
                canonical = next((s for s in _states(connection) if s["operation_id"] == duplicate_of), None)
                if canonical is None or canonical["status"] != "executed":
                    raise JournalConflict("duplicate target must remain a current executed fill")
                # A later report may supply the execution ID before the canonical
                # fill does. Keep events immutable, but bind the unique external
                # identity to the explicitly selected canonical operation.
                if ext_id and account:
                    connection.execute("UPDATE external_trades SET operation_id=? WHERE account_id=? AND external_trade_id=? AND operation_id=?",
                                       (duplicate_of, account, ext_id, operation_id))
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


def _sleeve_of(value: dict) -> str:
    """Classify old records without allowing their currency to select a book."""
    return "gold" if value.get("instrument_id") == "GOLD.CNY" else "etf"


def _gold_execution_context(states: list[dict], moment: Any) -> dict:
    """Current completed fills, counted on the merchant's Shanghai calendar."""
    day = _clock(moment).astimezone(SHANGHAI_TIMEZONE).date()
    completed = [s["operation"] for s in states
                 if s["status"] == "executed" and s["operation"].get("instrument_id") == "GOLD.CNY"]
    counts: dict[tuple, int] = {}
    for trade in completed:
        if _clock(trade["occurred_at"]).astimezone(SHANGHAI_TIMEZONE).date() != day:
            continue
        key = (trade.get("account_id"), trade.get("merchant"))
        counts[key] = counts.get(key, 0) + 1
    buys = [trade for trade in completed if trade["side"] == "buy"]
    latest = max(buys, key=lambda trade: _clock(trade["occurred_at"])) if buys else None
    return {"gold_orders_today": sum(counts.values()),
            "gold_order_day": day.isoformat(), "gold_order_timezone": "Asia/Shanghai",
            "gold_order_counts_today": [{"account_id": account, "merchant": merchant, "count": count}
                                        for (account, merchant), count in sorted(
                                            counts.items(), key=lambda item: str(item[0]))],
            "gold_last_completed_buy_at": latest["occurred_at"] if latest else None}


def _valuation_summaries(recommendations: list[dict], sleeve: str, version: str) -> list[dict]:
    """Bounded high-water summaries, computed before conversational row limits."""
    currency = "CNY" if sleeve == "gold" else "USD"
    segments: dict[tuple, dict] = {}
    rows = list(reversed(recommendations))
    if rows and all(row.get("created_at") for row in rows):
        rows.sort(key=lambda row: str(row["created_at"]))
    for row in rows:
        valuation = row.get("portfolio_valuation")
        if not isinstance(valuation, dict) or valuation.get("holdings_version") != version:
            continue
        if valuation.get("sleeve") != sleeve or valuation.get("currency") != currency:
            continue
        try:
            value = Decimal(str(valuation.get("total_value")))
        except InvalidOperation:
            continue
        if not value.is_finite() or value <= 0:
            continue
        key = (valuation.get("cash_value"), valuation.get("mark_basis"))
        if any(value is not None and not isinstance(value, str) for value in key):
            continue
        segment = segments.setdefault(key, {
            "sleeve": sleeve, "currency": currency, "holdings_version": version,
            "cash_value": key[0], "mark_basis": key[1], "peak_value": value,
            "max_drawdown": Decimal(0), "sample_count": 0, "seen": set()})
        snapshot_id = row.get("snapshot_id")
        if not isinstance(snapshot_id, str) or snapshot_id in segment["seen"]:
            continue
        segment["seen"].add(snapshot_id)
        segment["peak_value"] = max(segment["peak_value"], value)
        segment["max_drawdown"] = max(segment["max_drawdown"],
                                      (segment["peak_value"] - value) / segment["peak_value"])
        segment["sample_count"] += 1
        segment["latest_snapshot_id"] = snapshot_id
    return [{**{key: value for key, value in segment.items()
                if key not in {"seen", "peak_value", "max_drawdown"}},
             "peak_value": _number(segment["peak_value"]),
             "max_drawdown": _number(segment["max_drawdown"])}
            for segment in segments.values()]


def get_context(instrument_ids: Any = None, as_of: Any = None, *, sleeve: str = "etf",
                recommendations_limit: int = 200, db_path: Any = None, now: Any = None) -> dict:
    """Read holdings and audit context; as_of means information known by that UTC time.

    `sleeve` selects which sleeve's coverage declaration to read. Coverage
    declarations are per-sleeve rows in one shared table; a lookup with no
    WHERE on sleeve returns whichever declaration is newest regardless of
    which sleeve it names, so a gold declaration could mark the etf sleeve
    (or any other) complete. Defaults to "etf" because every caller that
    predates the gold sleeve means the ETF book; a gold caller must say so.

    `recommendations_limit` bounds how many recommendation rows are returned,
    newest first; `recommendations_truncated` in the result says whether more
    exist. This still reads every recommendation row from SQLite and filters
    by instrument in Python before slicing to the limit -- a real cost at
    scale -- because limiting in SQL first would return the newest rows
    overall and then filter them away, so asking for one symbol could come
    back empty while its rows sat in the table.
    """
    if sleeve not in COVERAGE_SLEEVES:
        raise ValueError(f"sleeve must be one of {COVERAGE_SLEEVES}, got {sleeve!r}")
    instruments = {instrument_ids} if isinstance(instrument_ids, str) else set(instrument_ids or [])
    with _connection(db_path) as connection:
        connection.execute("BEGIN")
        try:
            states = _states(connection, as_of)
            portfolio_version = _portfolio_version(states)
            holdings = [item for item in _holdings(states) if not instruments or item["instrument_id"] in instruments]
            sleeve_holdings = [item for item in holdings if _sleeve_of(item) == sleeve]
            matching = [state for state in states if not instruments or state["operation"].get("instrument_id") in instruments
                        or (state.get("had_opening_balance") and any(p["instrument_id"] in instruments
                            for p in state["operation"].get("positions", [])))]
            # Newest first, and the LIMIT is applied AFTER the instrument filter:
            # limiting in SQL first would return the newest rows overall and then
            # filter them away, so asking for one symbol could come back empty
            # while its rows sat in the table. An evaluation over a 12-symbol
            # universe writes 12 rows, so an unbounded read grows fast.
            query = "SELECT payload FROM recommendations"
            params: tuple = ()
            if as_of is not None:
                query += " WHERE recorded_at <= ?"
                params = (_stamp(as_of, end_of_day=True),)
            query += " ORDER BY recorded_at DESC, decision_id"
            matched = [json.loads(row[0]) for row in connection.execute(query, params).fetchall()]
            matched = [item for item in matched
                       if _sleeve_of(item) == sleeve
                       and (not instruments or item.get("instrument_id") in instruments)]
            limit = max(1, int(recommendations_limit))
            truncated = len(matched) > limit
            decisions = [dict(item, portfolio_current=item.get("portfolio_version") == portfolio_version)
                         for item in matched[:limit]]
            # Coverage is a user declaration bound to a portfolio_version. Any
            # executed trade changes that version, so a declaration made before
            # it is reported as stale rather than silently honoured.
            declared = connection.execute(
                "SELECT base_currency, portfolio_version FROM coverage_declarations "
                "WHERE sleeve = ? ORDER BY declared_at DESC, declaration_id LIMIT 1",
                (sleeve,)).fetchone()
            if declared and declared[1] == portfolio_version:
                complete, completeness, base_currency = True, "declared", declared[0]
            elif declared:
                complete, completeness, base_currency = False, "stale_declaration", None
            else:
                complete, completeness, base_currency = False, "unknown", None
            result = {"portfolio_version": portfolio_version, "portfolio_complete": complete, "completeness": completeness, "base_currency": base_currency, "fx_status": "unknown", "portfolio_value": None, "holdings": holdings, "holdings_by_currency": {currency: [item for item in holdings if item["currency"] == currency] for currency in sorted({item["currency"] for item in holdings})}, "pending_operations": [state for state in matching if state["status"] in {"pending", "pending_duplicate"}], "intents": [state for state in matching if state["status"] == "intent"], "operations": matching, "recommendations": decisions, "recommendations_truncated": truncated, "as_of": _stamp(as_of, end_of_day=True) if as_of is not None else None}
            sleeve_states = [state for state in states if _sleeve_of(state["operation"]) == sleeve]
            completed = [state["operation"] for state in sleeve_states if state["status"] == "executed"]
            result.update(context_source="journal", sleeve=sleeve, sleeve_holdings=sleeve_holdings,
                          sleeve_portfolio_version=_portfolio_version(sleeve_states),
                          last_completed_execution_at=(max(completed, key=lambda trade: _clock(
                              trade["occurred_at"]))["occurred_at"] if completed else None))
            result["valuation_history"] = _valuation_summaries(
                matched, sleeve, result["sleeve_portfolio_version"])
            result.update(_gold_execution_context(states, now if now is not None else as_of))
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


def _prepare_recommendation(decision: dict) -> tuple[dict, str]:
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
    return payload, _json(payload)


def record_recommendations(decisions: list[dict], *, db_path: Any = None) -> list[dict]:
    """Commit a whole evaluated basket, or leave every new row uncommitted.

    A single BEGIN IMMEDIATE binds every row to the same current portfolio.
    Replays remain allowed, but IDs can never describe different bytes.
    """
    if not isinstance(decisions, list):
        raise ValueError("decisions must be a list")
    prepared = [_prepare_recommendation(decision) for decision in decisions]
    if not prepared:
        return []
    if len({payload["portfolio_version"] for payload, _ in prepared}) != 1:
        raise JournalConflict("a recommendation basket must use one portfolio_version")
    receipts = []
    with _connection(db_path) as connection:
        with _transaction(connection):
            current = _portfolio_version(_states(connection))
            stamp = _stamp()
            for payload, serialized in prepared:
                decision_id = payload["decision_id"]
                previous = connection.execute("SELECT payload FROM recommendations WHERE decision_id=?", (decision_id,)).fetchone()
                if previous and previous[0] != serialized:
                    raise JournalConflict("decision_id already contains a different decision")
                if not previous and payload["portfolio_version"] != current:
                    raise JournalConflict("portfolio changed before recommendation commit; reassess against current holdings")
                if not connection.execute("SELECT 1 FROM evidence_snapshots WHERE snapshot_id=?", (payload["snapshot_id"],)).fetchone():
                    raise ValueError("save the referenced evidence snapshot before recording a recommendation")
                connection.execute("INSERT OR IGNORE INTO recommendations VALUES (?,?,?,?)",
                                   (decision_id, payload["snapshot_id"], stamp, serialized))
                receipts.append({"decision_id": decision_id, "recorded": True, "replayed": bool(previous)})
    return receipts


def record_recommendation(decision: dict, *, db_path: Any = None) -> dict:
    """Single-decision compatibility facade over the atomic basket operation."""
    return record_recommendations([decision], db_path=db_path)[0]


COVERAGE_SLEEVES = ("etf", "gold")
#: One currency per declaration, and the sleeves never share one. A USD ETF
#: book and a CNY gold book have no single total to size against, and CLAUDE.md
#: forbids adding them without dated FX and a declared base currency. Coverage
#: is therefore declared per sleeve and a declaration never crosses: this tuple
#: widens which currency a declaration may name, never which sleeve a
#: declaration satisfies. get_context filters by sleeve for exactly that reason.
COVERAGE_CURRENCIES = ("USD", "CNY")


def record_coverage_declaration(*, sleeve: str, base_currency: str,
                                portfolio_version: str, db_path: Any = None) -> dict:
    """Record the user's assertion that a sleeve's holdings are fully recorded.

    This does not change holdings, so it is not an operation: it states that
    what has already been recorded is everything. It is bound to the
    portfolio_version current at declaration time, and _portfolio_version hashes
    the executed operations, so recording any trade afterwards makes the
    declaration no longer current. A declaration therefore cannot go stale
    silently -- the failure mode is "coverage unknown again", not "sized against
    a book that moved".

    A declaration that does not match the current version is refused rather
    than stored, so the table never holds a claim that was wrong when made.
    """
    if sleeve not in COVERAGE_SLEEVES:
        raise ValueError(f"sleeve must be one of {COVERAGE_SLEEVES}, got {sleeve!r}")
    if base_currency not in COVERAGE_CURRENCIES:
        raise ValueError(f"base_currency must be one of {COVERAGE_CURRENCIES}, "
                         f"got {base_currency!r}")
    if not isinstance(portfolio_version, str) or not portfolio_version:
        raise ValueError("portfolio_version must be a nonempty string")
    with _connection(db_path) as connection:
        with _transaction(connection):
            current = _portfolio_version(_states(connection))
            if portfolio_version != current:
                raise JournalConflict(
                    "portfolio_version does not match the current holdings; read the "
                    "current context and declare against that version")
            declared_at = _stamp()
            declaration_id = "coverage-" + _hash(
                {"sleeve": sleeve, "base_currency": base_currency,
                 "portfolio_version": portfolio_version, "declared_at": declared_at})
            connection.execute("INSERT OR IGNORE INTO coverage_declarations VALUES (?,?,?,?,?)",
                               (declaration_id, sleeve, base_currency, portfolio_version,
                                declared_at))
        return {"declaration_id": declaration_id, "sleeve": sleeve,
                "base_currency": base_currency, "portfolio_version": portfolio_version,
                "declared_at": declared_at, "recorded": True}


def coverage_history(*, db_path: Any = None) -> list[dict]:
    """Every declaration, newest first, each flagged with whether it is current."""
    with _connection(db_path) as connection:
        current = _portfolio_version(_states(connection))
        rows = connection.execute(
            "SELECT declaration_id, sleeve, base_currency, portfolio_version, declared_at "
            "FROM coverage_declarations ORDER BY declared_at DESC, declaration_id").fetchall()
        return [{"declaration_id": row[0], "sleeve": row[1], "base_currency": row[2],
                 "portfolio_version": row[3], "declared_at": row[4],
                 "current": row[3] == current} for row in rows]


_RULE_ID = re.compile(r"^rule-[0-9a-f]{16}$")


def record_adoption(adoption: dict, *, db_path: Any = None) -> dict:
    """Store an immutable record of adopting a backtested rule.

    Adoption is a historical fact, so the row cannot change: superseding a rule
    means adopting a different one, which gets its own content-addressed id.
    Which rule is CURRENTLY live is not stored here -- that is the pointer the
    user edits by hand in config/user.toml (ADR-0007 clause 8), so nothing but an
    explicit user action changes what the engine will trade.
    """
    if not isinstance(adoption, dict):
        raise ValueError("adoption must be a dict")
    rule_id = adoption.get("rule_id")
    if not isinstance(rule_id, str) or not _RULE_ID.match(rule_id):
        raise ValueError("rule_id must look like rule-<16 lowercase hex characters>")
    sleeve = adoption.get("sleeve")
    if not isinstance(sleeve, str) or not sleeve:
        raise ValueError("adoption requires sleeve")
    serialized = _json(dict(adoption))
    with _connection(db_path) as connection:
        with _transaction(connection):
            previous = connection.execute(
                "SELECT payload FROM adopted_rules WHERE rule_id=?", (rule_id,)).fetchone()
            if previous and previous[0] != serialized:
                raise JournalConflict("rule_id already records a different adoption")
            connection.execute("INSERT OR IGNORE INTO adopted_rules VALUES (?,?,?,?)",
                               (rule_id, sleeve, _stamp(), serialized))
        return {"rule_id": rule_id, "recorded": True, "replayed": bool(previous)}


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


def load_adoption(rule_id: str, *, db_path: Any = None) -> dict:
    """Read a stored adoption. KeyError when the pointer does not resolve."""
    return _load("adopted_rules", "rule_id", rule_id, db_path)


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

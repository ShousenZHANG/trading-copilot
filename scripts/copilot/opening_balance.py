"""Broker-bound opening observations, separate from executed buy/sell events.

Only a saved, integrity-valid account/position snapshot supplies quantities.
Unknown historical basis stays unknown. No cash, fill or strategy clock is
created. Corrections that would overlap subsequent fills require reconciliation.
"""
from __future__ import annotations

import json
import re
import uuid

from . import journal
from .broker import validate_execution_snapshot
from .plan_store import _affirmation

MAX_OBSERVATION_AGE_SECONDS = 300


def _positions(snapshot):
    rows, seen = [], set()
    for position in snapshot.get("positions", []):
        quantity = journal._decimal(position.get("quantity"), "opening quantity", zero=True)
        if quantity == "0":
            continue
        symbol = position.get("instrument_id")
        if not isinstance(symbol, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,23}", symbol):
            raise ValueError("opening positions must identify actual US shares/ETFs")
        if symbol in seen:
            raise ValueError("opening snapshot contains duplicate position identity")
        seen.add(symbol)
        contract = position.get("con_id")
        if type(contract) is not int or contract <= 0 or position.get("currency") != "USD":
            raise ValueError("opening observations currently support positive-conId USD shares only")
        if position.get("account_key") != snapshot["account"]["account_key"]:
            raise ValueError("opening position belongs to another account")
        from .instruments import ETF_REGISTRY
        if position.get("security_type") != "STK" and symbol not in ETF_REGISTRY:
            raise ValueError("opening position asset class is unsupported or unverified")
        average = position.get("avg_cost")
        if average is not None:
            average = journal._decimal(average, "broker average cost", zero=True)
        rows.append(dict(instrument_id=symbol, con_id=contract, currency="USD", unit="share",
                         quantity=quantity, broker_average_cost=average,
                         gross_cost_basis=None, cost_basis_including_fees=None,
                         basis_status="unknown", broker_average_cost_is_verified_tax_basis=False))
    return sorted(rows, key=lambda row: row["instrument_id"])


def record_opening_balance(execution_snapshot_id, statement, source_message_id, idempotency_key, *,
                           expected_account_version, expected_portfolio_version, db_path, now=None,
                           event_type="record", operation_id=None, expected_version=None):
    """Append one account's starting holdings; all quantities come from the snapshot.

    Retry the exact same inputs/key to recover the receipt, even after expiry.
    ``correct`` replaces the observation only before any later recorded fill;
    ``reverse`` retires the local opening without claiming a broker sale. A
    post-opening fill may name ``opening_balance_id`` to bind its account
    locally without exposing the underlying account key to the model.
    """
    if db_path is None:
        raise ValueError("an explicit local database path is required")
    if event_type not in {"record", "correct", "reverse"}:
        raise ValueError("opening event_type must be record/correct/reverse")
    if not _affirmation(statement, "allocation") or not re.search(r"期初|当前持仓|现有持仓|opening|current holdings|existing holdings", statement, re.I):
        raise ValueError("explicit user confirmation of opening/current holdings is required")
    if not isinstance(source_message_id, str) or not source_message_id.strip():
        raise ValueError("source_message_id is required")
    if not all(isinstance(value, str) and value.strip() for value in
               (execution_snapshot_id, idempotency_key, expected_account_version, expected_portfolio_version)):
        raise ValueError("snapshot, account/portfolio versions and stable request key are required")
    if event_type != "record" and (not isinstance(operation_id, str) or type(expected_version) is not int or expected_version < 1):
        raise ValueError("opening amendments require operation_id and positive integer expected_version")
    if event_type == "record" and (operation_id is not None or expected_version is not None):
        raise ValueError("new opening observation cannot supply an existing operation identity")
    if event_type != "record" and not journal._amendment_confirmation(statement, event_type):
        raise ValueError("opening amendment requires an explicit correction/reversal instruction")
    request = dict(execution_snapshot_id=execution_snapshot_id, statement=statement,
                   source_message_id=source_message_id, expected_account_version=expected_account_version,
                   expected_portfolio_version=expected_portfolio_version, event_type=event_type,
                   operation_id=operation_id, expected_version=expected_version)
    request_hash = journal._hash(request)
    created = journal._clock(now)
    with journal._connection(db_path) as connection:
        with journal._transaction(connection):
            retry = connection.execute("SELECT * FROM journal_requests WHERE idempotency_key=?", (idempotency_key,)).fetchone()
            if retry:
                if retry["payload_hash"] != request_hash:
                    raise journal.JournalConflict("idempotency key already contains different facts")
                return dict(json.loads(retry["receipt"]), replayed=True)
            if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='advisor_execution_snapshots'").fetchone():
                raise ValueError("save a broker account snapshot before recording its opening holdings")
            source = connection.execute("SELECT payload FROM advisor_execution_snapshots WHERE snapshot_id=?", (execution_snapshot_id,)).fetchone()
            if source is None:
                raise ValueError("saved execution snapshot not found")
            snapshot = json.loads(source["payload"])
            check = validate_execution_snapshot(snapshot, now=created)
            if any(issue in {"broker_digest_invalid", "broker_schema_invalid", "broker_snapshot_malformed"} for issue in check["issues"]):
                raise ValueError("opening snapshot integrity/schema is invalid")
            account = snapshot.get("account", {}).get("account_key")
            if not isinstance(account, str) or not account or snapshot.get("account_version") != expected_account_version:
                raise journal.JournalConflict("opening account binding changed")
            for section in ("accounts", "positions"):
                if snapshot.get("coverage", {}).get(section, {}).get("complete") is not True:
                    raise ValueError("opening account/position coverage is incomplete")
            if "account_changed_during_collection" in snapshot.get("issues", []):
                raise ValueError("opening account changed during observation")
            states = journal._states(connection)
            if journal._portfolio_version(states) != expected_portfolio_version:
                raise journal.JournalConflict("portfolio changed before opening observation commit")
            previous = next((state for state in states if state["operation_id"] == operation_id), None)
            if event_type != "record":
                if previous is None or not previous.get("had_opening_balance") or previous["status"] != "opening_balance":
                    raise journal.JournalConflict("current opening observation not found")
                if previous["version"] != expected_version or previous["operation"]["account_id"] != account:
                    raise journal.JournalConflict("opening version/account changed")
            observed = journal._clock(snapshot["as_of"])
            if event_type != "reverse":
                if observed > created or (created-observed).total_seconds() > MAX_OBSERVATION_AGE_SECONDS:
                    raise ValueError("opening account observation is stale or future-dated")
                latest = connection.execute("SELECT payload FROM advisor_execution_snapshots WHERE account_key=? ORDER BY as_of DESC,rowid DESC LIMIT 1", (account,)).fetchone()
                latest_snapshot = json.loads(latest["payload"])
                if (latest_snapshot.get("account_version") != expected_account_version
                        or any(latest_snapshot.get("coverage", {}).get(section, {}).get("complete") is not True
                               for section in ("accounts", "positions"))
                        or "account_changed_during_collection" in latest_snapshot.get("issues", [])):
                    raise journal.JournalConflict("a newer account observation must be reconciled first")
                fills = [state for state in states if state["status"] == "executed"
                         and state["operation"].get("instrument_id") != "GOLD.CNY"
                         and (state["operation"].get("account_id") is None or
                              journal._same_broker_account(state["operation"]["account_id"], account))]
                if fills:
                    raise journal.JournalConflict("recorded fills overlap this opening; use explicit account reconciliation instead of resetting its start")
                if event_type == "record" and any(state["status"] == "opening_balance" and state["operation"]["account_id"] == account for state in states):
                    raise journal.JournalConflict("this account already has an opening; correct/reverse its existing operation")
            payload = (dict(previous["operation"]) if event_type == "reverse" else
                       dict(record_kind="opening_balance", sleeve="etf", account_id=account,
                            snapshot_id=execution_snapshot_id, observed_as_of=snapshot["as_of"],
                            account_version=expected_account_version, positions=_positions(snapshot)))
            payload.update(statement=statement, source_message_id=source_message_id)
            identity = operation_id or "op_" + uuid.uuid4().hex
            version = previous["version"]+1 if previous else 1
            first_sequence = previous["first_sequence"] if previous else connection.execute("SELECT COALESCE(MAX(sequence),0)+1 FROM journal_events").fetchone()[0]
            status = "reversed" if event_type == "reverse" else "opening_balance"
            stamp = journal._stamp(created)
            state = dict(operation_id=identity, version=version, status=status, missing_fields=[], operation=payload,
                         duplicate_candidates=[], had_execution=False, had_opening_balance=True,
                         first_sequence=first_sequence, recorded_at=stamp)
            connection.execute("INSERT INTO journal_events(event_id,operation_id,version,event_type,recorded_at,payload,state) VALUES (?,?,?,?,?,?,?)",
                               ("evt_"+uuid.uuid4().hex, identity, version, "opening_"+event_type, stamp, journal._json(request), journal._json(state)))
            connection.execute("INSERT INTO operation_states VALUES (?,?,?) ON CONFLICT(operation_id) DO UPDATE SET version=excluded.version,state=excluded.state",
                               (identity, version, journal._json(state)))
            portfolio = journal._rebuild_projection(connection)
            receipt = dict(operation_id=identity, version=version, status=status, portfolio_version=portfolio,
                           snapshot_id=payload["snapshot_id"], positions_count=len(payload["positions"]),
                           observed_as_of=payload["observed_as_of"], committed=True, replayed=False,
                           affects_cash=False, historical_fill=False)
            connection.execute("INSERT INTO journal_requests VALUES (?,?,?)", (idempotency_key, request_hash, journal._json(receipt)))
        return receipt

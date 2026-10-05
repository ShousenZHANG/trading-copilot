"""Append-only, user-reported actual distributions in their original currency.

Receipt evidence is separate from fills, broker settled balances and external
capital flows. Recording income does not grant spending power, update holdings
or advance strategy cadence. Public facades must project these local records;
``get_distributions`` intentionally retains the source evidence for correction.
"""
from __future__ import annotations

import json
import re
import uuid
from decimal import Decimal

from . import journal
from .plan_store import _affirmation

SCHEMA_VERSION = 1
_FIELDS = {'statement', 'source_message_id', 'opening_balance_id', 'instrument_id',
           'payment_date', 'currency', 'net_amount', 'gross_amount', 'withholding_amount',
           'external_payment_id', 'event_type', 'operation_id', 'expected_version'}
_SCHEMA = """
CREATE TABLE IF NOT EXISTS cash_distribution_events (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE, operation_id TEXT NOT NULL,
    version INTEGER NOT NULL, event_type TEXT NOT NULL,
    recorded_at TEXT NOT NULL, payload TEXT NOT NULL, state TEXT NOT NULL,
    UNIQUE(operation_id, version)
);
CREATE TABLE IF NOT EXISTS cash_distribution_states (
    operation_id TEXT PRIMARY KEY, version INTEGER NOT NULL, state TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_distribution_requests (
    idempotency_key TEXT PRIMARY KEY, payload_hash TEXT NOT NULL, receipt TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cash_distribution_external_ids (
    account_key TEXT NOT NULL, external_payment_id TEXT NOT NULL,
    operation_id TEXT NOT NULL REFERENCES cash_distribution_states(operation_id),
    PRIMARY KEY(account_key, external_payment_id)
);
CREATE TRIGGER IF NOT EXISTS cash_distribution_events_no_update
BEFORE UPDATE ON cash_distribution_events
BEGIN SELECT RAISE(ABORT, 'cash distribution events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS cash_distribution_events_no_delete
BEFORE DELETE ON cash_distribution_events
BEGIN SELECT RAISE(ABORT, 'cash distribution events are append-only'); END;
"""


def _arrival(statement):
    if not _affirmation(statement, 'distribution'):
        return False
    if re.search(r'宣布|预计|预期|将于|明天|后天|未来|尚未|还没|没有|未到账|未收到|(?:会|将).{0,12}(?:到账|收到)|\b(?:announced|expected|expect|scheduled|tomorrow|future|pending|not|never|haven.t|hasn.t|didn.t)\b', statement, re.I):
        return False
    return bool(re.search(r'分派|分红|股息|派息|\b(?:distribution|dividend)\b', statement, re.I))


def _normalize(payload, recorded_at):
    for key in ('statement', 'source_message_id', 'opening_balance_id'):
        if not isinstance(payload.get(key), str) or not payload[key].strip():
            raise ValueError(key+' requires the actual user receipt evidence')
    symbol = payload.get('instrument_id')
    if not isinstance(symbol, str) or not re.fullmatch(r'[A-Z][A-Z0-9.-]{0,23}', symbol):
        raise ValueError('distribution must identify a US share/ETF symbol')
    currency = payload.get('currency')
    # No FX conversion is implied. Other currencies need an explicit extension
    # of the public contract, rather than accepting any arbitrary three letters.
    if currency not in {'USD', 'AUD', 'CNY', 'HKD', 'EUR', 'GBP', 'JPY', 'CAD', 'CHF', 'NZD', 'SGD'}:
        raise ValueError('unsupported or unknown distribution currency')
    payment = payload.get('payment_date')
    if not isinstance(payment, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', payment):
        raise ValueError('actual payment_date must be an ISO calendar date')
    day = journal._clock(payment).date()
    if day > journal._clock(recorded_at).astimezone(journal.SHANGHAI_TIMEZONE).date():
        raise ValueError('a future payment date does not prove actual arrival')
    result = dict(payload, net_amount=journal._decimal(payload.get('net_amount'), 'net_amount', zero=True))
    for key in ('gross_amount', 'withholding_amount'):
        result[key] = None if payload.get(key) is None else journal._decimal(payload[key], key, zero=True)
    net = Decimal(result['net_amount'])
    gross = None if result['gross_amount'] is None else Decimal(result['gross_amount'])
    tax = None if result['withholding_amount'] is None else Decimal(result['withholding_amount'])
    if net == 0 and (gross is None or gross <= 0):
        raise ValueError('a zero net receipt requires known positive gross income')
    if gross is not None and (gross < net or (tax is not None and gross < tax)):
        raise ValueError('distribution gross/net/withholding amounts are inconsistent')
    if gross is not None and tax is not None and gross-tax != net:
        raise ValueError('known gross minus withholding must equal actual net receipt')
    external = result.get('external_payment_id')
    if external is not None and (not isinstance(external, str) or not external.strip() or len(external)>128):
        raise ValueError('external_payment_id must be a bounded nonempty string')
    return result


def _facts(payload):
    return {k: payload.get(k) for k in ('account_key', 'instrument_id', 'payment_date', 'currency',
                                       'net_amount', 'gross_amount', 'withholding_amount')}


def _receipt(state, **extra):
    operation = state['operation']
    return dict(operation_id=state['operation_id'], version=state['version'], status=state['status'],
        instrument_id=operation['instrument_id'], payment_date=operation['payment_date'],
        currency=operation['currency'], net_amount=operation['net_amount'],
        gross_amount=operation['gross_amount'], withholding_amount=operation['withholding_amount'],
        committed=True, replayed=False, affects_holdings=False, affects_external_cash_flow=False,
        advances_strategy_cadence=False, **extra)


def record_distribution(payload, idempotency_key, *, db_path, now=None):
    """Record actual income, or append an explicit versioned correction/reversal.

    A known opening observation supplies only the local account binding; it is
    not dividend entitlement or a tax-basis proof. Unknown tax/gross remain null.
    Corrections may supply partial facts. Same-day economic duplicates require
    correction of the canonical receipt; distinct broker payment IDs distinguish
    truly separate payments. Reversal retires a local record, never broker cash.
    """
    if db_path is None:
        raise ValueError('an explicit local cash ledger database path is required')
    if not isinstance(payload, dict) or set(payload)-_FIELDS:
        raise ValueError('distribution requires only typed cash receipt fields')
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError('stable nonempty distribution idempotency_key required')
    event = payload.get('event_type', 'record')
    if event not in {'record', 'correct', 'reverse'}:
        raise ValueError('cash receipt event_type must be record/correct/reverse')
    recorded_at, request_hash = journal._stamp(now), journal._hash(payload)
    with journal._connection(db_path) as connection:
        connection.executescript(_SCHEMA)
        with journal._transaction(connection):
            request = connection.execute('SELECT * FROM cash_distribution_requests WHERE idempotency_key=?', (idempotency_key,)).fetchone()
            if request:
                if request['payload_hash'] != request_hash:
                    raise journal.JournalConflict('cash receipt request key was already used with different facts')
                return dict(json.loads(request['receipt']), replayed=True)
            previous = None
            if event != 'record':
                if not isinstance(payload.get('operation_id'), str) or type(payload.get('expected_version')) is not int:
                    raise ValueError('cash receipt amendment needs operation_id and expected_version')
                row = connection.execute('SELECT state FROM cash_distribution_states WHERE operation_id=?', (payload['operation_id'],)).fetchone()
                if row is None:
                    raise KeyError('cash receipt does not exist')
                previous = json.loads(row['state'])
                if previous['version'] != payload['expected_version'] or previous['status']=='reversed':
                    raise journal.JournalConflict('cash receipt version is stale or already reversed')
                if not journal._amendment_confirmation(payload.get('statement', ''), event):
                    raise ValueError('cash amendment requires explicit user correction/reversal')
            elif payload.get('operation_id') is not None or payload.get('expected_version') is not None:
                raise ValueError('a new cash receipt cannot supply existing operation identity')
            if not isinstance(payload.get('statement'), str) or not isinstance(payload.get('source_message_id'), str) or not payload['source_message_id'].strip():
                raise ValueError('each cash event requires actual statement and source_message_id')
            if event=='record' and not _arrival(payload['statement']):
                raise ValueError('explicit actual distribution arrival required; an announcement is not a receipt')
            operation = dict(previous['operation']) if previous else {}
            operation.update({k:v for k,v in payload.items() if k not in {'event_type','operation_id','expected_version'}})
            operation = _normalize(operation, recorded_at)
            if previous and operation['opening_balance_id'] != previous['operation']['opening_balance_id']:
                raise journal.JournalConflict('receipt account/opening binding cannot change')
            opening = next((s for s in journal._states(connection) if s['operation_id']==operation['opening_balance_id'] and s.get('had_opening_balance')), None)
            if opening is None:
                raise ValueError('cash receipt requires an actual broker-bound opening_balance_id')
            operation['account_key'] = opening['operation']['account_id']
            if previous and previous['operation'].get('external_payment_id') and operation.get('external_payment_id')!=previous['operation']['external_payment_id']:
                raise journal.JournalConflict('external payment identity cannot be changed')
            operation_id = previous['operation_id'] if previous else 'cash_' + uuid.uuid4().hex
            external = operation.get('external_payment_id')
            if external:
                row = connection.execute('SELECT operation_id FROM cash_distribution_external_ids WHERE account_key=? AND external_payment_id=?', (operation['account_key'], external)).fetchone()
                if row and row['operation_id']!=operation_id:
                    other = json.loads(connection.execute('SELECT state FROM cash_distribution_states WHERE operation_id=?', (row['operation_id'],)).fetchone()[0])
                    if event!='record' or other['status']!='received' or _facts(other['operation'])!=_facts(operation):
                        raise journal.JournalConflict('external payment ID already exists with different facts/state')
                    receipt = _receipt(other, external_duplicate=True)
                    connection.execute('INSERT INTO cash_distribution_requests VALUES (?,?,?)', (idempotency_key, request_hash, journal._json(receipt)))
                    return receipt
            if event != 'reverse':
                economic = {k:v for k,v in _facts(operation).items() if k not in {'gross_amount','withholding_amount'}}
                for row in connection.execute('SELECT state FROM cash_distribution_states'):
                    other = json.loads(row['state'])
                    if other['operation_id'] == operation_id:
                        continue
                    other_id = other['operation'].get('external_payment_id')
                    if external and other_id and external!=other_id:
                        continue
                    other_economic = {k:v for k,v in _facts(other['operation']).items() if k not in {'gross_amount','withholding_amount'}}
                    if other['status']=='received' and economic==other_economic:
                        raise journal.JournalConflict('same economic receipt already recorded; correct operation '+other['operation_id']+' instead of adding income')
            state = dict(schema_version=SCHEMA_VERSION, record_kind='cash_distribution', operation_id=operation_id,
                version=previous['version']+1 if previous else 1, status='reversed' if event=='reverse' else 'received',
                recorded_at=recorded_at, operation=operation)
            connection.execute('INSERT INTO cash_distribution_events(event_id,operation_id,version,event_type,recorded_at,payload,state) VALUES (?,?,?,?,?,?,?)',
                ('cash_evt_'+uuid.uuid4().hex, operation_id, state['version'], event, recorded_at, journal._json(payload), journal._json(state)))
            connection.execute('INSERT INTO cash_distribution_states VALUES (?,?,?) ON CONFLICT(operation_id) DO UPDATE SET version=excluded.version,state=excluded.state',
                               (operation_id, state['version'], journal._json(state)))
            if external:
                connection.execute('INSERT OR IGNORE INTO cash_distribution_external_ids VALUES (?,?,?)', (operation['account_key'], external, operation_id))
            receipt = _receipt(state)
            connection.execute('INSERT INTO cash_distribution_requests VALUES (?,?,?)', (idempotency_key, request_hash, journal._json(receipt)))
        return receipt


def get_distributions(*, db_path, as_of=None):
    """Return local full receipt states, including reversed rows, at a known time.

    This is an audit/correction seam, not an automatically spendable cash book.
    Hosts must omit source text/message/account identifiers from model summaries.
    """
    if db_path is None:
        raise ValueError('an explicit local cash ledger database path is required')
    with journal._connection(db_path) as connection:
        connection.executescript(_SCHEMA)
        if as_of is None:
            return [json.loads(r['state']) for r in connection.execute('SELECT state FROM cash_distribution_states ORDER BY operation_id')]
        cutoff = journal._stamp(as_of, end_of_day=True)
        rows = connection.execute('SELECT state FROM cash_distribution_events WHERE recorded_at<=? ORDER BY sequence', (cutoff,))
        latest = {}
        for row in rows:
            state = json.loads(row['state'])
            latest[state['operation_id']] = state
        return list(latest.values())

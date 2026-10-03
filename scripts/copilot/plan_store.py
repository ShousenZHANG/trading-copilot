"""Local, versioned manual plans and explicit Review events.

All paths are supplied by the caller. These tables are separate from actual
operations: Review cannot submit an order or mutate the investment journal.
"""
from __future__ import annotations

import copy
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .trade_plan import (SCHEMA_VERSION, POLICY_VERSION, ACTIVE_ORDER_STATUSES,
                        canonical, digest, timestamp, decimal, amount, compile_plan, policy_binding)


class PlanConflict(ValueError):
    """An immutable identity or optimistic version no longer matches."""


def _affirmation(statement, purpose):
    """Conservative explicit-language guard, matching journal reporting policy.

    This is a bounded parser: ambiguous text needs a new clear user statement.
    A boolean/outcome argument alone cannot certify the user's approval.
    """
    if not isinstance(statement, str) or not statement.strip():
        return False
    if re.search(r'["“”‘’「」『』?？]|如果|假如|假设|可能|建议|他说|她说|朋友|别人|引用|举例|模拟|测试', statement):
        return False
    if re.search(r'\b(?:if|would|could|should|hypothetical|example|said|says|told|quote|test|pretend)\b', statement, re.I):
        return False
    if re.search(r'(?:计划|打算|准备|想要).{0,6}(?:确认|批准|同意|通过|review|复核|核对|分配|放弃|取消)|\b(?:plan|planning|want|intend)\b.{0,20}\b(?:approve|confirm|agree|review|cancel|assign)\b', statement, re.I):
        return False
    positive = {
        'approve': r'确认|同意|批准|通过|\b(?:approve(?:d)?|confirm(?:ed)?|agree(?:d)?|accept(?:ed)?)\b',
        'reject': r'拒绝|不批准|不同意|\b(?:reject|decline)\b',
        'cancel': r'放弃|取消.*(?:计划|复核)|\b(?:cancel|abandon)\b',
        'cash_flow': r'确认|\bconfirm(?:ed)?\b',
        'allocation': r'确认|分配|\b(?:confirm(?:ed)?|allocat(?:e|ed)|assign(?:ed)?)\b',
    }[purpose]
    if purpose!='reject' and re.search(r'(?:不|未|没|尚未|还没|并未|不能|拒绝)\s*(?:想|愿意|打算|准备|已|完成)?\s*(?:确认|同意|批准|通过|review|复核|核对|分配|放弃|取消)', statement, re.I):
        return False
    if re.search(r"\b(?:not|never|haven't|hasn't|didn't|don't|won't|cannot|can't)\b(?:\W+\w+){0,3}\W+(?:approve(?:d)?|agree(?:d)?|confirm(?:ed)?|review(?:ed)?|allocat(?:e|ed)|assign(?:ed)?|cancel(?:led|ed)?|abandon(?:ed)?)\b", statement, re.I):
        return False
    return bool(re.search(positive, statement, re.I))


@contextmanager
def _transaction(db_path):
    if db_path is None:
        raise ValueError('an explicit local plan database path is required')
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript('''
        CREATE TABLE IF NOT EXISTS advisor_execution_snapshots (
          snapshot_id TEXT PRIMARY KEY, account_key TEXT,
          as_of TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS advisor_manual_plans (
          plan_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL,
          version INTEGER NOT NULL, review_state TEXT NOT NULL,
          payload TEXT NOT NULL, inputs TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS advisor_manual_reviews (
          event_id TEXT PRIMARY KEY, plan_id TEXT NOT NULL, version INTEGER NOT NULL,
          created_at TEXT NOT NULL, payload TEXT NOT NULL,
          UNIQUE(plan_id,version));
        CREATE TABLE IF NOT EXISTS advisor_risk_observations (
          snapshot_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
          as_of TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS advisor_cash_flow_confirmations (
          interval_id TEXT PRIMARY KEY, previous_snapshot_id TEXT NOT NULL,
          snapshot_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS advisor_mode_allocations (
          operation_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
          positions_version TEXT NOT NULL, version INTEGER NOT NULL,
          created_at TEXT NOT NULL, payload TEXT NOT NULL,
          UNIQUE(account_key,version));
        CREATE TABLE IF NOT EXISTS advisor_strategy_states (
          operation_id TEXT PRIMARY KEY, account_key TEXT NOT NULL,
          rule_id TEXT NOT NULL, mode TEXT NOT NULL, version INTEGER NOT NULL,
          created_at TEXT NOT NULL, payload TEXT NOT NULL,
          UNIQUE(account_key,rule_id,mode,version));
        ''')
        connection.execute('BEGIN IMMEDIATE')
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _now(now):
    return timestamp(now or datetime.now(timezone.utc), 'now')


def _save_snapshot(connection, snapshot):
    snapshot_id = snapshot.get('snapshot_id')
    key = snapshot.get('account', {}).get('account_key')
    if not snapshot_id or (not key and (snapshot.get('status')!='blocked' or snapshot.get('complete') is not False)):
        raise ValueError('execution snapshot identity is incomplete')
    payload = canonical(snapshot)
    previous = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if previous:
        if previous['payload'] != payload:
            raise PlanConflict('execution snapshot identity is immutable')
        return
    connection.execute('INSERT INTO advisor_execution_snapshots VALUES (?,?,?,?)',
                       (snapshot_id, key, timestamp(snapshot['as_of']).isoformat(), payload))


def save_snapshot(snapshot, *, db_path, now=None):
    """Persist an integrity-valid snapshot, including blocked diagnostics."""
    from .broker import validate_execution_snapshot
    check = validate_execution_snapshot(snapshot, now=_now(now))
    if any(issue in {'broker_digest_invalid', 'broker_schema_invalid', 'broker_snapshot_malformed'} for issue in check['issues']):
        raise ValueError('; '.join(check['issues']))
    with _transaction(db_path) as connection:
        _save_snapshot(connection, snapshot)
    return {'snapshot_id': snapshot['snapshot_id'], 'committed': True, **check}


def read_snapshot(snapshot_id, *, db_path):
    with _transaction(db_path) as connection:
        row = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE snapshot_id=?', (snapshot_id,)).fetchone()
        if not row:
            raise ValueError('execution snapshot not found')
        return json.loads(row['payload'])


def _positions_version(snapshot):
    positions = sorted((dict(instrument_id=p['instrument_id'], con_id=p['con_id'], currency=p['currency'], avg_cost=p.get('avg_cost'),
                             quantity=amount(decimal(p['quantity'], minimum=0))) for p in snapshot.get('positions', [])),
                       key=lambda row: (row['instrument_id'], row['con_id']))
    fills = sorted((dict(execution_id=f.get('execution_id'), con_id=f.get('con_id'),
        instrument_id=f.get('instrument_id'), side=f.get('side'), quantity=f.get('quantity'),
        executed_at=f.get('executed_at')) for f in snapshot.get('fills', [])), key=canonical)
    return digest({'account_key': snapshot.get('account', {}).get('account_key'), 'positions': positions, 'ownership_events': fills}, 'positions-')


def record_mode_allocations(execution_snapshot_id, allocations, confirmation, *,
                            expected_account_version, db_path, expected_version=None, now=None,
                            entry_sessions=None, entry_plan_ids=None):
    """Append explicit ownership of existing shares; never infer it from fills."""
    if not _affirmation(confirmation, 'allocation'):
        raise ValueError('explicit user mode-allocation confirmation is required')
    if not isinstance(allocations, dict):
        raise ValueError('mode allocations must be a symbol mapping')
    now = _now(now)
    with _transaction(db_path) as connection:
        row = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE snapshot_id=?', (execution_snapshot_id,)).fetchone()
        if not row:
            raise ValueError('save the execution snapshot before assigning modes')
        snapshot = json.loads(row['payload'])
        from .broker import validate_execution_snapshot
        if not validate_execution_snapshot(snapshot, now=now)['complete']:
            raise ValueError('mode allocation needs a current complete account snapshot')
        key = snapshot['account']['account_key']
        if snapshot['account_version'] != expected_account_version:
            raise PlanConflict('account version changed before mode allocation')
        latest = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE account_key=? ORDER BY as_of DESC,rowid DESC LIMIT 1', (key,)).fetchone()
        if json.loads(latest['payload'])['account_version'] != expected_account_version:
            raise PlanConflict('a newer account binding must be reconciled before assigning modes')
        positions = {p['instrument_id']: decimal(p['quantity'], minimum=0) for p in snapshot['positions']}
        clean = {}
        for symbol, split in allocations.items():
            if symbol not in positions or not isinstance(split, dict) or set(split)-{'long_term','swing'}:
                raise ValueError('allocation names must be held instruments and the two supported modes')
            counts = {mode: decimal(split.get(mode, '0'), f'{symbol} {mode} allocation', minimum=0) for mode in ('long_term','swing')}
            if sum(counts.values())>positions[symbol]:
                raise ValueError(f'{symbol}: mode allocations exceed physical shares')
            clean[symbol] = {mode: amount(qty) for mode, qty in counts.items()}
        entries = {}
        if entry_sessions is not None:
            if not isinstance(entry_sessions, dict):
                raise ValueError('entry sessions must be a symbol/mode session mapping')
            for symbol, modes in entry_sessions.items():
                if symbol not in clean or not isinstance(modes, dict) or set(modes)-{'long_term','swing'}:
                    raise ValueError('entry sessions must reference allocated instruments/modes')
                entries[symbol] = {}
                for mode, session in modes.items():
                    if not isinstance(session, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', session):
                        raise ValueError('actual entry session must be an ISO date, not a Review timestamp')
                    day = date.fromisoformat(session)
                    if day>timestamp(snapshot['as_of']).date() or decimal(clean[symbol][mode])<=0:
                        raise ValueError('entry session is future-dated or has no assigned actual shares')
                    entries[symbol][mode] = day.isoformat()
        lots = {}
        if entry_plan_ids is not None:
            if not isinstance(entry_plan_ids, dict):
                raise ValueError('entry plan references must be a symbol/mode mapping')
            for symbol, modes in entry_plan_ids.items():
                if symbol not in clean or not isinstance(modes, dict) or set(modes)-{'swing'}:
                    raise ValueError('fixed entry references must belong to assigned swing shares')
                for mode, plan_id in modes.items():
                    if decimal(clean[symbol][mode])<=0 or not entries.get(symbol, {}).get(mode):
                        raise ValueError('a fixed swing entry needs assigned actual shares and an actual entry session')
                    plan_row = _row(connection, plan_id)
                    plan = json.loads(plan_row['payload'])
                    approved = connection.execute('SELECT payload FROM advisor_manual_reviews WHERE plan_id=? ORDER BY version',(plan_id,)).fetchall()
                    approvals = [json.loads(event['payload']) for event in approved if json.loads(event['payload']).get('outcome')=='approve']
                    if not approvals:
                        raise ValueError('fixed actual-entry protection requires an explicitly approved Review of that entry plan')
                    if not any(timestamp(event['created_at'])<=timestamp(snapshot['as_of']) for event in approvals):
                        raise ValueError('entry-plan Review must precede the actual holdings broker observation')
                    if plan_row['review_state'] in {'rejected','cancelled','superseded'}:
                        raise ValueError('a rejected or abandoned Review cannot prove the actual entry plan')
                    orders = [o for o in plan['orders'] if o['instrument_id']==symbol and o['side']=='buy' and o.get('mode')==mode]
                    if plan.get('account_key')!=key or len(orders)!=1 or not orders[0].get('protective_reference'):
                        raise ValueError('entry reference must be the same-account frozen deterministic swing buy plan')
                    order = orders[0]
                    units = timestamp(plan['created_at']).date().isoformat()
                    if entries[symbol][mode]!=units or order.get('con_id') != next(p['con_id'] for p in snapshot['positions'] if p['instrument_id']==symbol):
                        raise ValueError('actual entry must match the reviewed DAY plan session and unchanged contract identity')
                    lots.setdefault(symbol, {})[mode] = dict(entry_plan_id=plan_id, rule_id=plan['rule_id'],
                        protective_reference=order['protective_reference'], price_units_session=units,
                        planned_quantity=order['quantity'], protective_order_acknowledged=False,
                        reference_source='immutable_deterministic_entry_buy_plan')
        old = connection.execute('SELECT version FROM advisor_mode_allocations WHERE account_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
        version = old['version'] if old else 0
        if expected_version is not None and (isinstance(expected_version, bool) or expected_version!=version):
            raise PlanConflict('mode-allocation version changed')
        result = dict(execution_snapshot_id=execution_snapshot_id, account_key=key,
            positions_version=_positions_version(snapshot), account_version=expected_account_version,
            allocations=clean, entry_sessions=entries, entry_lots=lots, confirmation=confirmation.strip(), version=version+1,
            created_at=now.isoformat(), committed=True)
        result['operation_id'] = digest(result, 'mode-allocation-')
        connection.execute('INSERT INTO advisor_mode_allocations VALUES (?,?,?,?,?,?)',
            (result['operation_id'], key, result['positions_version'], result['version'], now.isoformat(), canonical(result)))
        return result


def _mode_context(connection, snapshot):
    key = snapshot.get('account', {}).get('account_key')
    if not key:
        return {'status': 'unknown', 'allocations': {}}
    row = connection.execute('SELECT payload FROM advisor_mode_allocations WHERE account_key=? ORDER BY version DESC LIMIT 1', (key,)).fetchone()
    if row:
        result = json.loads(row['payload'])
        if result['positions_version']==_positions_version(snapshot):
            return dict(status='confirmed', allocations=result['allocations'], entry_sessions=result.get('entry_sessions', {}),
                        entry_lots=result.get('entry_lots', {}), operation_id=result['operation_id'], version=result['version'])
    return {'status': 'unknown', 'allocations': {}, 'previously_confirmed': bool(row)}


def _strategy_activity_version(snapshot):
    orders = [{k:o.get(k) for k in ('order_key','instrument_id','con_id','side','total_quantity','filled_quantity','remaining_quantity','status')}
              for o in snapshot.get('orders', []) if o.get('status') in ACTIVE_ORDER_STATUSES]
    return digest({'ownership':_positions_version(snapshot),'orders':sorted(orders,key=canonical)},'strategy-activity-')


def record_strategy_state(execution_snapshot_id, rule_id, mode, confirmation, *, expected_account_version,
                          last_execution_session=None, no_prior_executions=False,
                          expected_version=None, db_path, now=None):
    """Record actual strategy history or a prospective start, never infer fills.

    User confirmation is necessary because TWS's recent-execution window does
    not supply all historical mode/rule ownership. New observed activity makes
    this receipt unknown until the user reconciles it again.
    """
    if not _affirmation(confirmation, 'allocation') or mode not in {'long_term','swing'}:
        raise ValueError('explicit user strategy execution-state confirmation and valid mode required')
    if not isinstance(rule_id,str) or not re.fullmatch(r'rule-[0-9a-f]{16}',rule_id):
        raise ValueError('current adopted rule identity required')
    if not isinstance(no_prior_executions,bool) or (last_execution_session is None)==(no_prior_executions is False):
        raise ValueError('confirm either no prior strategy executions or the last actual completed session')
    if no_prior_executions:
        if not re.search(r'(?:首次|初次|first|initial).*(?:无|没有|未|no |never).*(?:成交|执行|execut)',confirmation,re.I):
            raise ValueError('prospective activation requires explicit first-start and no-prior-executions statement')
    else:
        if not re.search(r'实际.*(?:成交|执行)|已成交|完成成交|actual.*(?:execut|fill)|completed.*(?:execut|trade|fill)',confirmation,re.I):
            raise ValueError('last strategy session must be an explicitly reported actual completed execution')
        if not isinstance(last_execution_session,str) or date.fromisoformat(last_execution_session).isoformat()!=last_execution_session:
            raise ValueError('last completed execution session must be an ISO date')
    now = _now(now)
    with _transaction(db_path) as connection:
        row = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE snapshot_id=?',(execution_snapshot_id,)).fetchone()
        if not row:
            raise ValueError('save the complete execution snapshot before confirming strategy state')
        snapshot = json.loads(row['payload'])
        from .broker import validate_execution_snapshot
        if not validate_execution_snapshot(snapshot,now=now)['complete']:
            raise ValueError('strategy state requires a current complete execution snapshot')
        key = snapshot['account']['account_key']
        latest = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE account_key=? ORDER BY as_of DESC,rowid DESC LIMIT 1',(key,)).fetchone()
        if snapshot['account_version']!=expected_account_version or json.loads(latest['payload'])['account_version']!=expected_account_version:
            raise PlanConflict('account binding changed before strategy state confirmation')
        if last_execution_session and last_execution_session>timestamp(snapshot['as_of']).date().isoformat():
            raise ValueError('actual execution session is future-dated')
        old = connection.execute('SELECT version,payload FROM advisor_strategy_states WHERE account_key=? AND rule_id=? AND mode=? ORDER BY version DESC LIMIT 1',(key,rule_id,mode)).fetchone()
        version = old['version'] if old else 0
        if old:
            previous = json.loads(old['payload']).get('last_execution_session')
            if previous and (no_prior_executions or last_execution_session<previous):
                raise PlanConflict('a recorded completed strategy execution cannot be erased by a new first-start/older-session claim')
        if expected_version is not None and (isinstance(expected_version,bool) or expected_version!=version):
            raise PlanConflict('strategy state version changed')
        result = dict(execution_snapshot_id=execution_snapshot_id,account_key=key,account_version=expected_account_version,
            activity_version=_strategy_activity_version(snapshot),rule_id=rule_id,mode=mode,last_execution_session=last_execution_session,
            no_prior_executions=no_prior_executions,version=version+1,confirmation=confirmation.strip(),
            scope='explicit_user_strategy_history_at_snapshot',created_at=now.isoformat(),committed=True)
        result['operation_id'] = digest(result,'strategy-state-')
        connection.execute('INSERT INTO advisor_strategy_states VALUES (?,?,?,?,?,?,?)',
            (result['operation_id'],key,rule_id,mode,result['version'],now.isoformat(),canonical(result)))
        return result


def _strategy_context(connection, snapshot, rule_id, mode):
    row = connection.execute('SELECT payload FROM advisor_strategy_states WHERE account_key=? AND rule_id=? AND mode=? ORDER BY version DESC LIMIT 1',
        (snapshot.get('account',{}).get('account_key'),rule_id,mode)).fetchone()
    if row:
        state = json.loads(row['payload'])
        if state['activity_version']==_strategy_activity_version(snapshot):
            return {k:v for k,v in dict(state,status='confirmed').items() if k not in {'confirmation','account_key'}}
    return dict(status='unknown',rule_id=rule_id,mode=mode,previously_confirmed=bool(row))


def _nav_usd(snapshot, now):
    nav = decimal(snapshot['account'].get('nav'), 'account NAV', minimum=decimal('.01'))
    currency = snapshot['account'].get('nav_currency')
    if currency != 'USD':
        fx = snapshot.get('fx_rates', {}).get(f'{currency}.USD', {})
        if fx.get('status') not in {'ready', 'ok'} or fx.get('actual_data_type') != 1 or now >= timestamp(fx.get('valid_until')):
            raise ValueError('NAV FX coverage is unknown')
        nav *= decimal(fx.get('rate'), 'NAV FX', minimum=decimal('.00000001'))
    return nav


def _risk_context(connection, snapshot, now):
    from .broker import validate_execution_snapshot
    unknown = dict(status='unknown', value=None, account_version=snapshot.get('account_version'),
        scope='prospective_since_baseline', historical_drawdown='unknown',
        issues=['cash-flow coverage or trusted NAV baseline unknown'])
    if not validate_execution_snapshot(snapshot, now=now)['complete']:
        return unknown
    try:
        nav = _nav_usd(snapshot, now)
    except (ValueError, KeyError, TypeError):
        return unknown
    snapshot_id = snapshot['snapshot_id']
    key = snapshot['account']['account_key']
    existing = connection.execute('SELECT payload FROM advisor_risk_observations WHERE snapshot_id=?', (snapshot_id,)).fetchone()
    if existing:
        return json.loads(existing['payload'])
    previous = connection.execute('SELECT payload FROM advisor_risk_observations WHERE account_key=? ORDER BY as_of DESC, rowid DESC LIMIT 1', (key,)).fetchone()
    stamp = timestamp(snapshot['as_of'])
    if previous:
        old = json.loads(previous['payload'])
        if stamp <= timestamp(old['as_of']):
            return dict(unknown, issues=['risk observations must advance the account cutoff'])
        confirmed = connection.execute('SELECT payload FROM advisor_cash_flow_confirmations WHERE snapshot_id=? AND previous_snapshot_id=?',
            (snapshot_id, old['snapshot_id'])).fetchone()
        unchanged = snapshot['account_version'] == old['account_version']
        if not confirmed and not unchanged:
            return dict(unknown, previous_snapshot_id=old['snapshot_id'])
        # Exactly the same native-currency account/cash/positions/order binding
        # cannot introduce a hidden net flow. A fresh FX rate may still change
        # the USD NAV and its risk measurement.
        flow = decimal(json.loads(confirmed['payload'])['net_external_flow_usd']) if confirmed else decimal('0')
        peak = decimal(old['high_water_nav_usd'])+flow
        if peak <= 0:
            return dict(unknown, issues=['cash flow exhausted the measurable risk baseline'])
        peak = max(peak, nav)
        value = max(decimal('0'), (peak-nav)/peak)
        baseline_id = old['baseline_snapshot_id']
        previous_id = old['snapshot_id']
    else:
        peak, value = nav, decimal('0')
        baseline_id, previous_id = snapshot_id, None
    result = dict(status='known', value=amount(value), account_version=snapshot['account_version'],
        scope='prospective_since_baseline', historical_drawdown='unknown',
        baseline_snapshot_id=baseline_id, previous_snapshot_id=previous_id, snapshot_id=snapshot_id,
        as_of=stamp.isoformat(), nav_usd=amount(nav), high_water_nav_usd=amount(peak),
        flow_adjustment='confirmed_interval_net_flow_shifts_high_water', issues=[])
    connection.execute('INSERT INTO advisor_risk_observations VALUES (?,?,?,?)', (snapshot_id, key, stamp.isoformat(), canonical(result)))
    return result


def record_cash_flow(execution_snapshot_id, net_external_flow_usd, confirmation, *,
                     previous_snapshot_id, db_path, now=None):
    """Confirm one exact interval, including an explicit zero; never infer flow.

    A correction needs a new observation/confirmation, rather than overwriting a
    risk observation. The net flow shifts the observed high water; this is a
    prospective risk measure, not a historical or time-weighted return claim.
    """
    if not _affirmation(confirmation, 'cash_flow'):
        raise ValueError('explicit user cash-flow confirmation is required')
    flow = decimal(net_external_flow_usd, 'net external flow USD')
    created = _now(now)
    with _transaction(db_path) as connection:
        rows = [connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE snapshot_id=?', (identity,)).fetchone()
                for identity in (previous_snapshot_id, execution_snapshot_id)]
        if not all(rows):
            raise ValueError('both cash-flow interval snapshots must be saved first')
        before, after = [json.loads(row['payload']) for row in rows]
        if before['account']['account_key'] != after['account']['account_key']:
            raise ValueError('cash-flow interval must bind one physical account')
        if timestamp(before['as_of']) >= timestamp(after['as_of']) or timestamp(after['as_of']) > created:
            raise ValueError('cash-flow interval is reversed or future-dated')
        interval_id = digest({'previous': previous_snapshot_id, 'current': execution_snapshot_id}, 'flow-')
        payload = dict(interval_id=interval_id, previous_snapshot_id=previous_snapshot_id,
            execution_snapshot_id=execution_snapshot_id, net_external_flow_usd=amount(flow),
            confirmation=confirmation.strip(), created_at=created.isoformat(), committed=True)
        previous = connection.execute('SELECT payload FROM advisor_cash_flow_confirmations WHERE snapshot_id=?', (execution_snapshot_id,)).fetchone()
        if previous:
            old = json.loads(previous['payload'])
            if old['previous_snapshot_id'] != previous_snapshot_id or old['net_external_flow_usd'] != amount(flow) or old['confirmation'] != confirmation.strip():
                raise PlanConflict('cash-flow interval confirmation is immutable')
            return old
        latest = connection.execute('SELECT snapshot_id FROM advisor_risk_observations WHERE account_key=? ORDER BY as_of DESC,rowid DESC LIMIT 1',
                                   (after['account']['account_key'],)).fetchone()
        if not latest or latest['snapshot_id'] != previous_snapshot_id:
            raise PlanConflict('cash-flow interval must start at the latest trusted risk observation')
        if connection.execute('SELECT 1 FROM advisor_risk_observations WHERE snapshot_id=?', (execution_snapshot_id,)).fetchone():
            raise PlanConflict('a trusted risk observation cannot be retroactively given a different flow')
        connection.execute('INSERT INTO advisor_cash_flow_confirmations VALUES (?,?,?,?)',
            (interval_id, previous_snapshot_id, execution_snapshot_id, canonical(payload)))
        return payload


def create_plan(*, execution_snapshot, research_snapshot, adoption, policy, db_path, now=None):
    """Freeze all inputs and the compiler result in one committed transaction."""
    now = _now(now)
    from .broker import validate_execution_snapshot
    checked = validate_execution_snapshot(execution_snapshot, now=now)
    if any(issue in {'broker_digest_invalid', 'broker_schema_invalid', 'broker_snapshot_malformed'} for issue in checked['issues']):
        raise ValueError('; '.join(checked['issues']))
    with _transaction(db_path) as connection:
        _save_snapshot(connection, execution_snapshot)
        local_policy = copy.deepcopy(policy)
        # The public facade cannot grant itself a drawdown number. It is always
        # rebuilt from stored, integrity-valid observations and confirmed flows.
        local_policy['drawdown'] = _risk_context(connection, execution_snapshot, now)
        local_policy['mode_allocations'] = _mode_context(connection, execution_snapshot)
        local_policy['strategy_state'] = _strategy_context(connection, execution_snapshot, adoption.get('rule_id'), policy.get('mode'))
        plan = compile_plan(execution_snapshot=execution_snapshot, research_snapshot=research_snapshot,
                            adoption=adoption, policy=local_policy, now=now)
        inputs = dict(execution_snapshot=execution_snapshot, research_snapshot=research_snapshot,
                      adoption=adoption, policy=local_policy)
        current_rows = connection.execute('SELECT * FROM advisor_manual_plans WHERE review_state IN (?,?)',
                                          ('awaiting_review', 'reviewed')).fetchall()
        locked = False
        for row in current_rows:
            prior = json.loads(row['payload'])
            if prior.get('account_key') != plan.get('account_key') or prior['plan_id'] == plan['plan_id']:
                continue
            if row['review_state']=='reviewed':
                expired = prior.get('valid_until') and now >= timestamp(prior['valid_until'])
                symbols = {o['instrument_id'] for o in prior.get('orders', [])}
                orders_known = execution_snapshot.get('coverage', {}).get('orders', {}).get('complete') is True
                active = [o for o in execution_snapshot.get('orders', []) if o.get('instrument_id') in symbols and o.get('status') in ACTIVE_ORDER_STATUSES]
                newer = expired and timestamp(execution_snapshot['as_of'])>timestamp(prior['valid_until'])
                if checked['complete'] and orders_known and newer and not active:
                    _event(connection, row, 'needs_recompile', now, {'issues': ['post-expiry complete broker observation retired the local Review']})
                else:
                    locked = True
            elif not prior.get('valid_until') or now >= timestamp(prior['valid_until']):
                continue
            elif checked['complete'] and prior.get('account_version') != plan['account_version']:
                _event(connection, row, 'needs_recompile', now, {'issues': ['new account snapshot invalidated the prior binding']})
            elif plan['status']=='ready_for_review' and row['review_state']=='awaiting_review':
                _event(connection, row, 'superseded', now, {'replacement_plan_id': plan['plan_id']})
        if locked and plan['status']=='ready_for_review':
            plan.update(status='blocked', execution_scope='research_only', orders=[])
            plan['issues'].append('a reviewed manual plan already binds this physical account; revalidate or explicitly abandon it')
            plan['plan_id'] = digest({k: v for k, v in plan.items() if k!='plan_id'})
        old = connection.execute('SELECT payload FROM advisor_manual_plans WHERE plan_id=?', (plan['plan_id'],)).fetchone()
        if old and old['payload'] != canonical(plan):
            raise PlanConflict('compiled plan identity is immutable')
        if not old:
            connection.execute('INSERT INTO advisor_manual_plans VALUES (?,?,?,?,?,?)',
                (plan['plan_id'], SCHEMA_VERSION, 1, 'awaiting_review', canonical(plan), canonical(inputs)))
        return _view(connection, plan['plan_id'], now)


def _row(connection, plan_id):
    row = connection.execute('SELECT * FROM advisor_manual_plans WHERE plan_id=?', (plan_id,)).fetchone()
    if not row:
        raise ValueError('manual plan not found')
    return row


def _view(connection, plan_id, now):
    row = _row(connection, plan_id)
    result = json.loads(row['payload'])
    state = row['review_state']
    if result.get('schema_version') != SCHEMA_VERSION or result.get('policy_version') != POLICY_VERSION:
        state = 'needs_recompile'
    quote_expired = bool(result.get('valid_until') and now >= timestamp(result['valid_until']))
    if quote_expired and state in {'awaiting_review', 'reviewed'}:
        state = 'expired'
    if state in {'awaiting_review', 'reviewed'} and result.get('account_key'):
        latest = connection.execute('SELECT payload FROM advisor_execution_snapshots WHERE account_key=? ORDER BY as_of DESC,rowid DESC LIMIT 1',
                                    (result['account_key'],)).fetchone()
        if latest:
            snapshot = json.loads(latest['payload'])
            if snapshot.get('account_version') != result['account_version'] or snapshot.get('status')!='ready':
                state = 'needs_recompile'
            elif result.get('mode_allocation_binding') and digest(_mode_context(connection, snapshot), 'mode-binding-') != result['mode_allocation_binding']:
                state = 'needs_recompile'
            elif result.get('strategy_state_binding') and digest(_strategy_context(connection, snapshot, result['rule_id'], result['mode']), 'strategy-state-') != result['strategy_state_binding']:
                state = 'needs_recompile'
    result.update(version=row['version'], review_state=state, committed=True, order_submitted=False,
                  requires_review=state!='reviewed', requires_revalidation=True, quote_expired=quote_expired)
    if state not in {'reviewed', 'awaiting_review'}:
        result['historical_orders'] = result.pop('orders', [])
        result['orders'] = []
        result['execution_scope'] = 'research_only'
    result['review_events'] = [json.loads(event['payload']) for event in connection.execute(
        'SELECT payload FROM advisor_manual_reviews WHERE plan_id=? ORDER BY version', (plan_id,))]
    return result


def read_plan(plan_id, *, db_path, now=None):
    with _transaction(db_path) as connection:
        return _view(connection, plan_id, _now(now))


def _event(connection, row, state, now, payload):
    version = row['version']+1
    event = dict(plan_id=row['plan_id'], version=version, state=state, created_at=now.isoformat(), **payload)
    event['event_id'] = digest(event, 'review-')
    connection.execute('INSERT INTO advisor_manual_reviews VALUES (?,?,?,?,?)',
        (event['event_id'], row['plan_id'], version, now.isoformat(), canonical(event)))
    connection.execute('UPDATE advisor_manual_plans SET version=?,review_state=? WHERE plan_id=?',
                       (version, state, row['plan_id']))


def review_plan(plan_id, *, statement, expected_version, db_path, now=None, outcome='approve'):
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version<1:
        raise ValueError('expected_version must be a positive integer')
    if outcome not in {'approve', 'reject', 'cancel'}:
        raise ValueError('Review outcome must be approve/reject/cancel')
    if not _affirmation(statement, outcome):
        raise ValueError('explicit affirmative user Review/outcome statement is required; quoted/conditional/negated text is insufficient')
    now = _now(now)
    with _transaction(db_path) as connection:
        row = _row(connection, plan_id)
        if row['version'] != expected_version:
            raise PlanConflict('manual plan version changed; reload before Review')
        current = _view(connection, plan_id, now)
        if outcome=='approve':
            if current['status'] != 'ready_for_review' or current['review_state'] != 'awaiting_review':
                raise ValueError('only a fresh awaiting-review plan can be reviewed')
            state = 'reviewed'
        else:
            if row['review_state'] in {'cancelled', 'rejected', 'superseded'}:
                raise ValueError('a retired local plan cannot be retired again')
            state = 'rejected' if outcome=='reject' else 'cancelled'
        _event(connection, row, state, now, {'statement': statement.strip(), 'outcome': outcome,
                                          'orders_submitted': False, 'broker_order_cancelled': False})
        return _view(connection, plan_id, now)


def revalidate_plan(plan_id, *, execution_snapshot, policy, expected_version, db_path, now=None):
    """Observe broker state; a prior Review never certifies current execution."""
    now = _now(now)
    if isinstance(expected_version, bool) or not isinstance(expected_version, int) or expected_version<1:
        raise ValueError('expected_version must be a positive integer')
    from .broker import validate_execution_snapshot
    with _transaction(db_path) as connection:
        row = _row(connection, plan_id)
        if row['version'] != expected_version:
            raise PlanConflict('manual plan version changed; reload before revalidation')
        plan = json.loads(row['payload'])
        inputs = json.loads(row['inputs'])
        check = validate_execution_snapshot(execution_snapshot, now=now)
        state, issues = row['review_state'], []
        if not check['complete']:
            state, issues = 'needs_recompile', check['issues']
        elif execution_snapshot['account']['account_key'] != plan['account_key']:
            state, issues = 'needs_recompile', ['physical account changed']
        else:
            _save_snapshot(connection, execution_snapshot)
            planned_symbols = {o['instrument_id'] for o in plan['orders']}
            active = [o for o in execution_snapshot.get('orders', []) if o.get('instrument_id') in planned_symbols and o.get('status') in ACTIVE_ORDER_STATUSES]
            if any(o.get('status') == 'cancel_pending' for o in active):
                state, issues = 'cancel_pending', ['cancellation has not been acknowledged; shares/cash remain reserved']
            elif any(decimal(o.get('filled_quantity'), minimum=0)>0 for o in active):
                state, issues = 'partially_filled', ['partial fill requires reconciliation and a new funded plan']
            elif active:
                state, issues = 'working', ['an open order already binds the planned instrument']
            elif execution_snapshot.get('account_version') != plan['account_version']:
                state, issues = 'needs_recompile', ['cash/positions/orders/fills or account value changed']
            elif not plan.get('valid_until') or now >= timestamp(plan['valid_until']):
                state, issues = 'expired', ['frozen execution quote expired']
            else:
                local_policy = copy.deepcopy(policy)
                local_policy['drawdown'] = _risk_context(connection, execution_snapshot, now)
                local_policy['mode_allocations'] = _mode_context(connection, execution_snapshot)
                local_policy['strategy_state'] = _strategy_context(connection, execution_snapshot, plan['rule_id'], plan['mode'])
                if policy_binding(local_policy) != plan['policy_binding']:
                    state, issues = 'needs_recompile', ['confirmed policy changed']
                else:
                    fresh = compile_plan(execution_snapshot=execution_snapshot, research_snapshot=inputs['research_snapshot'],
                        adoption=inputs['adoption'], policy=local_policy, now=now)
                    fields = ('instrument_id', 'side', 'quantity', 'limit_price', 'estimated_fees')
                    comparable = lambda orders: [{k: o[k] for k in fields} for o in orders]
                    if fresh['status'] != 'ready_for_review' or comparable(fresh['orders']) != comparable(plan['orders']):
                        state, issues = 'needs_recompile', ['current quote/risk/strategy no longer produces the reviewed orders']+fresh['issues']
        _event(connection, row, state, now, {'execution_snapshot_id': execution_snapshot.get('snapshot_id'), 'issues': issues})
        result = _view(connection, plan_id, now)
        result['revalidation'] = {'status': 'pass' if state in {'reviewed', 'awaiting_review'} else 'blocked', 'issues': issues}
        if 'fresh' in locals():
            result['revalidation']['current_risk'] = fresh.get('risk')
        return result

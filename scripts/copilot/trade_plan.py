"""Compile adopted research signals into a frozen, manual-only limit plan.

This module never submits orders, changes positions, or accepts model supplied
prices/quantities. A completed-session signal and a broker execution quote have
different jobs. Cash from an unfilled sale is never buying power here.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR

SCHEMA_VERSION = 1
POLICY_VERSION = 'manual-plan-1.1'
ACTIVE_ORDER_STATUSES = frozenset({'submitted', 'partially_filled', 'cancel_pending', 'working', 'pending_submit'})
TERMINAL_ORDER_STATUSES = frozenset({'filled', 'cancelled', 'rejected', 'inactive'})


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def digest(value, prefix='plan-'):
    return prefix + hashlib.sha256(canonical(value).encode()).hexdigest()


def policy_binding(policy):
    """Configuration identity; measured account state is rechecked separately."""
    return digest({k: v for k, v in policy.items() if k not in {'drawdown', 'mode_allocations', 'strategy_state'}}, 'policy-')


def timestamp(value, label='timestamp'):
    if isinstance(value, datetime):
        result = value
    elif isinstance(value, str):
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    else:
        raise ValueError(f'{label}: timezone-aware timestamp required')
    if result.tzinfo is None:
        raise ValueError(f'{label}: timezone-aware timestamp required')
    return result.astimezone(timezone.utc)


def decimal(value, label='value', *, minimum=None):
    if isinstance(value, bool) or value is None:
        raise ValueError(f'{label}: known decimal value required')
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f'{label}: invalid decimal value') from exc
    if not result.is_finite() or (minimum is not None and result < minimum):
        raise ValueError(f'{label}: finite value >= {minimum} required')
    return result


def amount(value):
    return format(value.normalize(), 'f') if value else '0'


def _pct(policy, field):
    value = decimal(policy.get(field), field, minimum=0)
    if value > 1:
        raise ValueError(f'{field}: fraction must be <= 1')
    return value


def _fee(quantity, price, policy):
    if quantity <= 0:
        return Decimal(0)
    commission = max(decimal(policy.get('min_commission_usd'), 'minimum commission', minimum=0),
                     quantity * decimal(policy.get('commission_per_share_usd'), 'per-share commission', minimum=0))
    other = quantity * price * decimal(policy.get('other_cost_bps'), 'other cost bps', minimum=0) / 10000
    return (commission + other).quantize(Decimal('.01'), rounding=ROUND_CEILING)


def _tick_price(quote, price, rounding):
    bands = sorted((decimal(b['low_edge'], 'tick low edge', minimum=0),
                    decimal(b['increment'], 'tick increment', minimum=Decimal('.00000001')))
                   for b in quote['market_rule'])
    for _ in range(len(bands)+2):
        eligible = [row for row in bands if row[0] <= price]
        if not eligible:
            raise ValueError('tick schedule does not cover price')
        edge, increment = eligible[-1]
        rounded = edge + ((price-edge)/increment).to_integral_value(rounding=rounding)*increment
        if rounded>0 and [row for row in bands if row[0]<=rounded][-1]==(edge, increment):
            return rounded
        price = rounded
    raise ValueError('unstable/nonpositive tick price')


def _quote(symbol, snapshot, policy, now):
    quote = snapshot.get('quotes', {}).get(symbol)
    if not isinstance(quote, dict):
        raise ValueError(f'{symbol}: execution quote missing')
    if quote.get('currency') != 'USD' or quote.get('actual_data_type') != 1:
        raise ValueError(f'{symbol}: live USD market data required; frozen/delayed quotes cannot price a plan')
    if quote.get('status') not in {'ready', 'ok'}:
        raise ValueError(f'{symbol}: execution quote not ready')
    received = timestamp(quote.get('received_at'), f'{symbol} quote received_at')
    ttl = decimal(policy.get('quote_ttl_seconds'), 'quote TTL', minimum=1)
    if received > now or Decimal(str((now-received).total_seconds())) > ttl or now >= timestamp(quote.get('valid_until')):
        raise ValueError(f'{symbol}: execution quote expired or future-dated')
    if policy.get('regular_hours_only', True) and (quote.get('regular_hours') is not True or quote.get('session') != 'regular'):
        raise ValueError(f'{symbol}: verified regular session required')
    if decimal(quote.get('quantity_step'), f'{symbol} quantity step', minimum=1) != 1:
        raise ValueError(f'{symbol}: only whole-share orders are supported')
    bid = decimal(quote.get('bid'), f'{symbol} bid', minimum=Decimal('.00000001'))
    ask = decimal(quote.get('ask'), f'{symbol} ask', minimum=Decimal('.00000001'))
    if ask < bid:
        raise ValueError(f'{symbol}: crossed quote')
    mid = (bid + ask) / 2
    if (ask-bid)/mid > _pct(policy, 'max_spread_pct'):
        raise ValueError(f'{symbol}: spread exceeds confirmed limit')
    if not quote.get('con_id') or not quote.get('market_rule'):
        raise ValueError(f'{symbol}: verified contract and tick rule required')
    return quote, mid, _tick_price(quote, ask, ROUND_CEILING), _tick_price(quote, bid, ROUND_FLOOR)


def _weights(adoption, research, policy, now):
    from .instruments import ETF_REGISTRY, DEFENSIVE_ETFS
    from .market_data import verify_snapshot
    from .ruleset import SCHEMA_VERSION as ADOPTION_SCHEMA, rule_id
    from .policy import _build_rule, _bars_matrix
    from .backtest.frame import build
    if adoption.get('kind')=='advisor_strategy':
        from .advisor_strategy import compile_signal
        mode = policy.get('mode')
        if mode not in {'long_term','swing'} or policy.get(mode+'_rule_id') != adoption.get('rule_id'):
            raise ValueError('active mode rule does not match the stored advisor adoption')
        signal = compile_signal(adoption, research, mode, now=now)
        if any(item.get('direction')=='unknown' for item in signal['symbols'].values()):
            raise ValueError('adopted template has incomplete current research conditions')
        weights = {symbol: decimal(item['target_account_weight'], 'account target weight', minimum=0)
                   for symbol, item in signal['symbols'].items()}
        return weights, None, None, signal['evaluation_session'], signal
    if adoption.get('schema_version') != ADOPTION_SCHEMA or not adoption.get('admission', {}).get('admitted'):
        raise ValueError('strategy must have a current admitted adoption')
    if policy.get('mode') != 'long_term' or adoption.get('sleeve') != 'etf':
        raise ValueError('no validated adopted swing/individual-stock template is available; research only')
    if policy.get('long_term_rule_id') != adoption.get('rule_id'):
        raise ValueError('active long-term rule does not match the stored adoption')
    if adoption.get('integer_shares') is not True:
        raise ValueError('a fractional-share backtest cannot authorize this whole-share plan')
    expected = rule_id(family=adoption['family'], parameters=adoption['parameters'],
        universe=adoption['universe'], targets=adoption.get('targets'), cost_model=adoption['cost_model'],
        cash_floor_pct=adoption['cash_floor_pct'], integer_shares=adoption['integer_shares'], admission=adoption['admission'])
    if adoption.get('rule_id') != expected:
        raise ValueError('adoption identity does not bind its targets and execution assumptions')
    universe = tuple(adoption.get('universe') or [])
    if not universe or not set(universe) <= ETF_REGISTRY-DEFENSIVE_ETFS:
        raise ValueError('only the currently admitted equity ETF universe can produce orders')
    if not verify_snapshot(research) or research.get('status') != 'ready':
        raise ValueError('research snapshot integrity or readiness failed')
    if now >= timestamp(research.get('valid_until')) or timestamp(research.get('created_at')) > now:
        raise ValueError('research snapshot expired or future-dated')
    for symbol in universe:
        item = research.get('instruments', {}).get(symbol, {})
        if item.get('quality_status') != 'pass' or item.get('latest_session') != item.get('expected_session'):
            raise ValueError(f'{symbol}: validated latest published research session required')
    dates, closes, records = _bars_matrix(research, universe)
    if dates[-1] > now.date():
        raise ValueError('research bars include a future session')
    if any(r.get('indicator_basis') != 'total_return_adjusted' or r.get('missing_sessions') for r in records.values()):
        raise ValueError('adopted rule requires complete total-return research bars')
    rule = _build_rule(adoption)
    if len(dates) <= rule.warmup_bars:
        raise ValueError('insufficient published history for the adopted signal')
    frame = build(dates=dates, symbols=universe, closes=closes)
    weights = {s: decimal(w, f'{s} target', minimum=0) for s, w in rule.weights(frame, len(dates)-1).items()}
    if not set(weights) <= set(universe) or abs(sum(weights.values())-1) > Decimal('.000000001'):
        raise ValueError('computed rule weights are invalid')
    return weights, rule, frame, dates[-1].isoformat(), None


def _session_age(session, bars, minimum, *, symbol=None):
    """Published bars prove elapsed sessions, including an old-window lower bound."""
    if not isinstance(session, str) or date.fromisoformat(session).isoformat()!=session:
        raise ValueError('actual completed execution session must be an ISO session date')
    dates = [bar['session'] for bar in bars]
    if not dates or session>dates[-1]:
        raise ValueError('actual execution/entry is later than published history; cadence unknown')
    if session<dates[0]:
        from .data_calendar import MarketCalendar
        from .instruments import get_research_instrument
        if not symbol or not MarketCalendar().is_session(get_research_instrument(symbol), session):
            raise ValueError('actual execution/entry is not a verified exchange session')
        if len(dates)>=minimum:
            return len(dates)
        raise ValueError('insufficient published history after actual execution/entry')
    if session not in dates:
        raise ValueError('actual execution/entry is not a verified published session')
    return len(dates)-1-dates.index(session)


def _swing_lot(symbol, quantity, mode_allocation, adoption, research):
    """Use a frozen entry reference; corporate actions never silently become zero."""
    from .policy import _primary_evidence
    record = _primary_evidence(research, symbol)
    bars = record['bars']
    entry = mode_allocation.get('entry_sessions', {}).get(symbol, {}).get('swing')
    maximum = int(adoption['spec']['parameters']['max_holding_sessions'])
    age = None
    issues = []
    try:
        age = _session_age(entry, bars, maximum, symbol=symbol)
    except (ValueError, TypeError):
        issues.append('actual swing entry session/holding age unknown')
    lot = mode_allocation.get('entry_lots', {}).get(symbol, {}).get('swing', {})
    stop, factor = None, Decimal(1)
    try:
        units = lot['price_units_session']
        if lot['rule_id']!=adoption['rule_id'] or units<bars[0]['session'] or units>bars[-1]['session']:
            raise ValueError('fixed entry reference history or rule binding unavailable')
        after = [bar for bar in bars if bar['session']>units]
        for bar in after:
            split = decimal(bar.get('splits'), 'verified split event', minimum=0)
            if split:
                factor *= split
        stop = decimal(lot['protective_reference'], 'fixed actual-entry protective reference', minimum=Decimal('.00000001'))/factor
        if quantity>decimal(lot['planned_quantity'], 'entry planned quantity', minimum=0)*factor:
            raise ValueError('current swing shares exceed the confirmed fixed entry lot')
    except (ValueError, KeyError, TypeError, ArithmeticError):
        stop = None
        issues.append('fixed actual-entry protection or intervening split evidence unknown')
    close = decimal(bars[-1]['close'], 'published raw close', minimum=Decimal('.00000001'))
    exit_reason = ('actual_holding_session_limit' if age is not None and age>=maximum else
                   'fixed_actual_entry_protective_reference' if stop is not None and close<=stop else None)
    return dict(age=age, protective=stop, split_factor=factor, exit_reason=exit_reason, issues=issues)


def compile_plan(*, execution_snapshot, research_snapshot, adoption, policy, now=None):
    """Return a frozen review card or structured refusal, never a broker order.

    Only locally loaded, content-addressed adopted rules may supply targets.
    ``policy`` is confirmed local configuration, not a model proposal.
    """
    if adoption.get('kind') == 'user_directed':
        from .manual_intent import compile_directed_plan
        return compile_directed_plan(execution_snapshot=execution_snapshot,
            research_snapshot=research_snapshot, adoption=adoption, policy=policy, now=now)
    now = timestamp(now or datetime.now(timezone.utc), 'now')
    snapshot = execution_snapshot
    result = dict(schema_version=SCHEMA_VERSION, policy_version=POLICY_VERSION,
        created_at=now.isoformat(), status='blocked', execution_scope='research_only',
        requires_review=True, requires_revalidation=True, issues=[], orders=[],
        execution_snapshot_id=snapshot.get('snapshot_id'), account_version=snapshot.get('account_version'),
        account_key=snapshot.get('account', {}).get('account_key'), mode=policy.get('mode'),
        research_snapshot_id=research_snapshot.get('snapshot_id'), rule_id=adoption.get('rule_id'),
        adoption_schema_version=adoption.get('schema_version'), research_price_role='completed_session_signal_only',
        policy_binding=policy_binding(policy))
    try:
        if policy.get('enabled') is not True:
            raise ValueError('advisor must be explicitly enabled in local configuration')
        if policy.get('fee_model_confirmed') is not True:
            raise ValueError('broker fee model is unconfirmed')
        if policy.get('allow_borrowing') is not False:
            raise ValueError('borrowing/margin funding is not implemented')
        from .broker import validate_execution_snapshot
        validated = validate_execution_snapshot(snapshot, now=now)
        if not validated['complete']:
            raise ValueError('; '.join(validated['issues']))
        for section in ('accounts', 'cash', 'positions', 'orders', 'fills', 'quotes'):
            if snapshot.get('coverage', {}).get(section, {}).get('complete') is not True:
                raise ValueError(f'execution {section} coverage is incomplete')
        if snapshot.get('schema_version') != 1 or snapshot.get('status') != 'ready' or snapshot.get('complete') is not True:
            raise ValueError('execution account snapshot is incomplete or blocked')
        if not snapshot.get('account_version') or not snapshot.get('account', {}).get('account_key'):
            raise ValueError('account identity and semantic version are required')
        if now >= timestamp(snapshot.get('valid_until')) or timestamp(snapshot.get('as_of')) > now:
            raise ValueError('execution snapshot expired or future-dated')
        weights, rule, frame, signal_session, signal = _weights(adoption, research_snapshot, policy, now)
        universe = tuple(adoption['spec']['universe'] if signal else adoption['universe'])
        profile = signal['sizing_profile'] if signal else {}
        mode_cap = min(_pct(policy, policy['mode']+'_nav_cap_pct'), decimal(profile.get(policy['mode']+'_nav_cap_pct', '1')))
        nav = decimal(snapshot['account'].get('nav'), 'account NAV', minimum=Decimal('.01'))
        currency = snapshot['account'].get('nav_currency')
        if currency != 'USD':
            fx = snapshot.get('fx_rates', {}).get(f'{currency}.USD', {})
            if fx.get('actual_data_type') != 1 or fx.get('status') not in {'ready', 'ok'} or now >= timestamp(fx.get('valid_until')):
                raise ValueError('fresh live FX is required for non-USD NAV risk budgets')
            if timestamp(fx.get('received_at')) > now:
                raise ValueError('FX observation is in the future')
            nav *= decimal(fx.get('rate'), 'NAV FX rate', minimum=Decimal('.00000001'))
        cash = snapshot.get('cash', {}).get('USD', {})
        settled = decimal(cash.get('settled'), 'settled USD cash', minimum=0)
        reserved = decimal(cash.get('reserved'), 'reserved USD cash', minimum=0)
        if cash.get('reservation_basis') != 'open_buy_limit_orders_excludes_fees' or reserved > settled:
            raise ValueError('cash reservation semantics are unknown or overcommitted')
        floor = nav * max(decimal(adoption.get('cash_floor_pct', '0'), 'adopted cash floor', minimum=0),
                          decimal(profile.get('cash_floor_pct', '0')), _pct(policy, 'cash_floor_pct'))
        open_fee = Decimal(0)
        active_by_symbol = {}
        principal = Decimal(0)
        order_keys = set()
        for order in snapshot.get('orders', []):
            if order.get('order_key') in order_keys:
                raise ValueError('duplicate open-order identity')
            order_keys.add(order.get('order_key'))
            status = order.get('status')
            if status in TERMINAL_ORDER_STATUSES:
                continue
            if status not in ACTIVE_ORDER_STATUSES:
                raise ValueError('unknown order state; funds and shares may still be reserved')
            symbol = order.get('instrument_id')
            active_by_symbol.setdefault(symbol, []).append(order)
            remaining = decimal(order.get('remaining_quantity'), 'remaining open quantity', minimum=0)
            if order.get('side') == 'buy':
                if order.get('currency') != 'USD':
                    raise ValueError('non-USD open-buy funding is unsupported')
                price = decimal(order.get('limit_price'), 'open buy limit', minimum=Decimal('.00000001'))
                principal += remaining*price
                open_fee += _fee(remaining, price, policy)
        if principal != reserved:
            raise ValueError('reserved cash does not match the complete open-buy order book')
        budget = max(Decimal(0), settled-reserved-open_fee-floor)
        held = {}
        from .instruments import ETF_REGISTRY, sector_of
        sectors = {}
        contracts = {}
        for position in snapshot.get('positions', []):
            if position.get('currency') != 'USD' or position.get('account_key') != result['account_key']:
                raise ValueError('position currency/account does not match the supported account book')
            symbol = position.get('instrument_id')
            if symbol in held:
                raise ValueError('duplicate position identity')
            held[symbol] = decimal(position.get('quantity'), f'{symbol} held quantity', minimum=0)
            contracts[symbol] = position.get('con_id')
            sectors[symbol] = sector_of(symbol) if symbol in ETF_REGISTRY else position.get('sector')
        marks, prices = {}, {}
        for symbol in set(held) | set(universe):
            q, mid, buy_price, sell_price = _quote(symbol, snapshot, policy, now)
            if symbol in contracts and contracts[symbol] != q.get('con_id'):
                raise ValueError(f'{symbol}: held contract does not match the execution quote contract')
            expected_asset = signal['symbols'][symbol]['asset_class'] if signal and symbol in universe else 'etf'
            if symbol in universe and q.get('asset_class') != expected_asset:
                raise ValueError(f'{symbol}: adopted instrument quote has the wrong asset class')
            if signal and symbol in universe:
                cost = signal['cost_model']
                if decimal(policy['commission_per_share_usd'])>decimal(cost['per_share_usd']) or decimal(policy['min_commission_usd'])>decimal(cost['minimum_usd']):
                    raise ValueError('confirmed broker commissions exceed the adopted validation cost profile')
                observed_half_spread = (decimal(q['ask'])-decimal(q['bid']))/(2*mid)*10000
                if observed_half_spread+decimal(policy['other_cost_bps'])>decimal(cost['spread_bps'])/2:
                    raise ValueError(f'{symbol}: live spread/other costs exceed the adopted validation profile')
            marks[symbol], prices[symbol] = mid, (q, buy_price, sell_price)
            sectors[symbol] = sectors.get(symbol) or (sector_of(symbol) if symbol in ETF_REGISTRY else q.get('sector'))
        sector_values = {}
        pending_values = {}
        sector_unknown = []
        for symbol, quantity in held.items():
            sector = sectors[symbol]
            if not sector:
                sector_unknown.append(f'{symbol}: sector exposure unknown; new risk paused')
            elif sector != 'diversified':
                sector_values[sector] = sector_values.get(sector, Decimal(0))+quantity*marks[symbol]
        for symbol, active in active_by_symbol.items():
            for order in active:
                if order.get('side') == 'buy':
                    value = decimal(order['remaining_quantity'])*decimal(order['limit_price'])
                    pending_values[symbol] = pending_values.get(symbol, Decimal(0))+value
                    sector = sectors.get(symbol) or (sector_of(symbol) if symbol in ETF_REGISTRY else None)
                    if not sector:
                        sector_unknown.append(f'{symbol}: sector exposure of open buy unknown; new risk paused')
                    elif sector != 'diversified':
                        sector_values[sector] = sector_values.get(sector, Decimal(0))+value
        mode_allocation = policy.get('mode_allocations') or {'status': 'unknown', 'allocations': {}}
        allocations = (mode_allocation.get('allocations') or {}) if mode_allocation.get('status')=='confirmed' else {}
        mode_held = {}
        for symbol, physical in held.items():
            split = allocations.get(symbol, {}) if mode_allocation.get('status')=='confirmed' else {}
            if split:
                owned = {m: decimal(split.get(m, '0'), f'{symbol} {m} ownership', minimum=0) for m in ('long_term','swing')}
                if sum(owned.values())>physical:
                    raise ValueError(f'{symbol}: logical mode holdings exceed physical shares')
                mode_held[symbol] = owned[policy['mode']]
            else:
                # Legacy adopted ETF book is explicitly managed by its long-
                # term rule. Individual stocks and an explicitly split book
                # require the separate local ownership confirmation.
                mode_held[symbol] = physical if (not signal and mode_allocation.get('status')!='confirmed' and not mode_allocation.get('previously_confirmed') and policy['mode']=='long_term' and symbol in universe) else Decimal(0)
        current = {s: float(mode_held.get(s, Decimal(0))*marks[s]/nav) for s in held if s in universe}
        # Broker fills are actual completions; never advance cadence on review.
        from .policy import _last_rebalance_index
        completed = [f.get('executed_at') for f in snapshot.get('fills', [])
                     if f.get('instrument_id') in universe and decimal(f.get('quantity'), 'fill quantity', minimum=0)>0]
        if any(timestamp(t)>now for t in completed if t):
            raise ValueError('completed fill is future-dated')
        history_known = snapshot.get('coverage', {}).get('fills', {}).get('history_complete') is True
        cadence_issues = []
        if signal:
            from .policy import _primary_evidence
            strategy_state = policy.get('strategy_state') or {}
            result['strategy_state_binding'] = digest(strategy_state, 'strategy-state-')
            if strategy_state.get('status')!='confirmed' or strategy_state.get('rule_id')!=adoption['rule_id'] or strategy_state.get('mode')!=policy['mode']:
                due = False
                result['cadence_status'] = 'unknown_new_entries_paused'
                cadence_issues.append('template cadence unknown: explicitly confirm prospective first activation or last actual completed strategy session')
            elif strategy_state.get('last_execution_session') is None and strategy_state.get('no_prior_executions') is True:
                due = True
                result['cadence_status'] = 'explicit_user_confirmed_first_strategy_activation'
            else:
                intervals = [int(item['cadence_sessions']) for item in signal['symbols'].values()]
                try:
                    due = all(_session_age(strategy_state.get('last_execution_session'), _primary_evidence(research_snapshot, symbol)['bars'], interval, symbol=symbol)>=interval
                              for symbol, interval in zip(signal['symbols'], intervals))
                    result['cadence_status'] = 'user_confirmed_actual_execution_session'
                except (ValueError, TypeError) as exc:
                    due = False
                    result['cadence_status'] = 'unknown_new_entries_paused'
                    cadence_issues.append('template cadence unknown: '+str(exc))
            result['issues'].extend(cadence_issues)
        elif not history_known:
            if adoption['family'] != 'fixed_weight_bands':
                raise ValueError('cadence unknown: recent broker fills do not prove the last strategy execution')
            # Fixed bands have an independent current-weight trigger. Never
            # reinterpret an empty recent execution response as bootstrap or a
            # lapsed calendar interval when its history is incomplete.
            due = rule.should_rebalance(frame, len(frame.dates)-1, current, len(frame.dates)-1,
                                        cash_floor_pct=float(adoption['cash_floor_pct']))
            result['cadence_status'] = 'current_weight_drift_only_calendar_history_unknown'
        else:
            last = max(completed, key=lambda t: timestamp(t)) if completed else None
            cadence = _last_rebalance_index({'context_source': 'journal', 'last_completed_execution_at': last}, adoption, list(frame.dates), now=now)
            due = rule.should_rebalance(frame, len(frame.dates)-1, current, cadence,
                                       cash_floor_pct=float(adoption['cash_floor_pct']))
            result['cadence_status'] = 'complete_execution_history'
        result.update(signal_session=signal_session, rebalance_due=due,
                      targets={s: amount(w) for s, w in weights.items()}, preserved_positions=sorted(set(held)-set(universe)))
        result['mode_allocation_status'] = mode_allocation.get('status', 'unknown')
        result['mode_allocations'] = copy.deepcopy(mode_allocation.get('allocations') or {})
        result['mode_allocation_binding'] = digest(mode_allocation, 'mode-binding-')
        drawdown = policy.get('drawdown') or {}
        dd_known = drawdown.get('status') == 'known' and drawdown.get('account_version') == result['account_version']
        dd = decimal(drawdown.get('value'), 'drawdown', minimum=0) if dd_known else None
        dd_limit = min(_pct(policy, 'max_drawdown_pct'), decimal(profile.get('max_drawdown_nav_pct','1')))
        buy_reasons = list(sector_unknown)+cadence_issues
        if dd is None:
            buy_reasons.append('drawdown baseline/flow coverage unknown; new risk paused')
        elif dd >= dd_limit:
            buy_reasons.append('confirmed drawdown limit reached; new risk paused')
        swing_loss_cap = nav*min(_pct(policy, 'max_swing_loss_pct'), decimal(profile.get('max_swing_loss_nav_pct', '1')))
        swing_lots = {}
        existing_swing_loss = Decimal(0)
        if signal and policy['mode']=='swing':
            for symbol, quantity in mode_held.items():
                if quantity<=0:
                    continue
                if symbol not in universe:
                    buy_reasons.append('existing swing lot outside this validated universe has unknown protection; new risk paused')
                    continue
                lot = _swing_lot(symbol, quantity, mode_allocation, adoption, research_snapshot)
                swing_lots[symbol] = lot
                if lot['issues']:
                    buy_reasons.append(symbol+': '+'; '.join(lot['issues']))
                if lot['protective'] is not None:
                    existing_swing_loss += max(Decimal(0), marks[symbol]-lot['protective'])*quantity+_fee(quantity, lot['protective'], policy)
            if any(quantity>sum(decimal(v) for v in allocations.get(symbol, {}).values()) for symbol,quantity in held.items()):
                buy_reasons.append('existing unassigned shares have unknown swing protection; new risk paused')
        invested_target = min(nav-floor, nav*mode_cap)
        outside_value = sum(q*marks[s] for s, q in held.items() if s not in universe)
        outside_value += sum((q-mode_held.get(s, Decimal(0)))*marks[s] for s, q in held.items() if s in universe)
        invested_target = max(Decimal(0), invested_target-outside_value)
        account_exposure = sum(quantity*marks[s] for s, quantity in held.items())+sum(pending_values.values())
        if signal:
            # Unknown ownership counts in this mode conservatively; the other
            # confirmed mode never shares its allocated stocks or cash twice.
            account_exposure = sum((mode_held.get(s, Decimal(0)) + quantity-sum(decimal(v) for v in allocations.get(s, {}).values()))*marks[s]
                                   for s, quantity in held.items())+sum(pending_values.values())
            outside_value = sum((mode_held.get(s, Decimal(0)) + quantity-sum(decimal(v) for v in allocations.get(s, {}).values()))*marks[s]
                                for s, quantity in held.items() if s not in universe)
            invested_target = max(Decimal(0), min(nav-floor, nav*mode_cap)-outside_value)
        buy_cost = sell_proceeds = sell_fees = Decimal(0)
        for symbol in sorted(universe):
            q, buy_price, sell_price = prices[symbol]
            existing = held.get(symbol, Decimal(0))
            mode_existing = mode_held.get(symbol, Decimal(0))
            desired_value = nav*weights.get(symbol, Decimal(0)) if signal else invested_target*weights.get(symbol, Decimal(0))
            condition = signal['symbols'][symbol] if signal else None
            exit_reason = None
            if condition:
                lot = swing_lots.get(symbol)
                if lot and lot['exit_reason']:
                    exit_reason = lot['exit_reason']
                    desired_value = Decimal(0)
                elif condition.get('technical_exit'):
                    exit_reason = 'validated_completed_session_technical_exit'
                    desired_value = Decimal(0)
                elif condition['direction'] in {'wait','hold'}:
                    continue
                elif condition['direction']=='exit':
                    desired_value = Decimal(0)
            delta_value = desired_value-mode_existing*marks[symbol]
            protective_exit = bool(signal and policy['mode']=='swing' and exit_reason and mode_existing>0)
            if (not due and not protective_exit) or delta_value == 0:
                continue
            side = 'buy' if delta_value>0 else 'sell'
            price = buy_price if side=='buy' else sell_price
            quantity = min(abs(delta_value)/price, nav*_pct(policy, 'max_trade_notional_pct')/price).to_integral_value(rounding=ROUND_FLOOR)
            if side=='sell':
                quantity = min(quantity, mode_existing.to_integral_value(rounding=ROUND_FLOOR))
            reasons = list(buy_reasons) if side=='buy' else []
            if condition and side=='buy' and policy['mode']=='swing' and mode_existing>0:
                reasons.append('adding to an existing swing lot needs separate actual-entry protection; no blended/trailing stop inferred')
            protective = None
            if condition and side=='buy':
                trigger = decimal(condition.get('reference_trigger_raw'), 'validated entry trigger', minimum=Decimal('.00000001'))
                invalidation = decimal(condition.get('reference_invalidation_raw'), 'validated invalidation', minimum=Decimal('.00000001'))
                if price<trigger or price<=invalidation:
                    reasons.append('live execution price no longer satisfies the frozen entry conditions')
                if policy['mode']=='swing':
                    protective = _tick_price(q, decimal(condition.get('protective_reference_raw'), 'validated protective reference', minimum=Decimal('.00000001')), ROUND_FLOOR)
                    if protective>=price:
                        reasons.append('protective reference must be below the real entry limit')
                    else:
                        def loss(qty):
                            return qty*(price-protective)+_fee(qty, price, policy)+_fee(qty, protective, policy)
                        low, high = 0, int(quantity)
                        while low<high:
                            middle = (low+high+1)//2
                            if loss(Decimal(middle))+existing_swing_loss<=swing_loss_cap:
                                low = middle
                            else:
                                high = middle-1
                        quantity = Decimal(low)
            if active_by_symbol.get(symbol):
                reasons.append('an active/partial/cancel-pending order already binds this instrument; reconcile before another plan')
            if side=='buy':
                # Every buy must be safe even if none of the proposed sells fill.
                name_limit = min(_pct(policy, 'max_single_name_pct'), decimal(profile.get('max_stock_nav_pct' if q['asset_class']=='stock' else 'max_etf_nav_pct', '.25')))
                name_room = max(Decimal(0), nav*name_limit-existing*marks[symbol]-pending_values.get(symbol, Decimal(0)))
                quantity = min(quantity, (name_room/price).to_integral_value(rounding=ROUND_FLOOR))
                mode_room = max(Decimal(0), nav*mode_cap-account_exposure)
                quantity = min(quantity, (mode_room/price).to_integral_value(rounding=ROUND_FLOOR))
                sector = sectors[symbol]
                if sector != 'diversified':
                    sector_room = max(Decimal(0), nav*_pct(policy, 'max_sector_pct')-sector_values.get(sector, Decimal(0)))
                    quantity = min(quantity, (sector_room/price).to_integral_value(rounding=ROUND_FLOOR))
                    if quantity<=0:
                        reasons.append('sector limit leaves no funded risk capacity')
            if reasons or quantity<=0:
                result['issues'].append(f'{symbol}: '+('; '.join(reasons) if reasons else 'no whole share fits the funded risk limits'))
                continue
            fee = _fee(quantity, price, policy)
            notional = quantity*price
            fee_limit = min(decimal(policy.get('max_fee_pct', '.01'), 'max fee fraction', minimum=0),
                            decimal(profile.get('max_oneway_fee_pct','1')))
            if side=='buy' and fee>notional*fee_limit:
                result['issues'].append(f'{symbol}: estimated buy fees exceed max_fee_pct; no trade')
                continue
            if side=='sell' and fee>=notional:
                result['issues'].append(f'{symbol}: reducing sell has zero or negative net proceeds; borrowing is not allowed')
                continue
            if side=='sell' and fee>notional*fee_limit:
                result['issues'].append(f'{symbol}: reducing sell exceeds fee preference; positive net proceeds require explicit Review')
            order = dict(instrument_id=symbol, con_id=q['con_id'], side=side,
                quantity=amount(quantity), limit_price=amount(price), currency='USD', order_type='LMT', tif='DAY',
                session='regular' if policy.get('regular_hours_only') else q.get('session'),
                limit_price_basis='live_raw_ask_tick_ceiling' if side=='buy' else 'live_raw_bid_tick_floor',
                notional=amount(notional), estimated_fees=amount(fee),
                held_quantity=amount(existing), resulting_quantity=amount(existing+quantity if side=='buy' else existing-quantity),
                mode_held_quantity=amount(mode_existing), resulting_mode_quantity=amount(mode_existing+quantity if side=='buy' else mode_existing-quantity),
                target_weight=amount(weights.get(symbol, Decimal(0))), quote_received_at=q['received_at'],
                execution_scope='manual_review', mode=policy['mode'], asset_class=q['asset_class'])
            if condition:
                order.update(reference_trigger=amount(decimal(condition['reference_trigger_raw'])),
                    reference_invalidation=amount(decimal(condition['reference_invalidation_raw'])),
                    reference_is_executable=False, signal_session=condition['evaluation_session'],
                    evidence_ids=list(condition['evidence_ids']), trigger_basis='adopted_completed_session_condition_checked_against_live_quote')
                if exit_reason:
                    order['exit_reason'] = exit_reason
                    if symbol in swing_lots:
                        order['actual_holding_sessions'] = swing_lots[symbol]['age']
                        if swing_lots[symbol]['protective'] is not None:
                            order['fixed_entry_protective_reference'] = amount(swing_lots[symbol]['protective'])
                if protective is not None:
                    order.update(protective_reference=amount(protective), protective_policy='fixed_at_actual_entry_adjusted_for_verified_splits',
                        protective_order_acknowledged=False,
                        planned_loss_usd=amount(quantity*(price-protective)+_fee(quantity, price, policy)+_fee(quantity, protective, policy)),
                        planned_loss_is_guaranteed=False)
            order['order_id'] = digest({'snapshot': result['execution_snapshot_id'], 'rule_id': result['rule_id'], **order}, 'planned-order-')
            result['orders'].append(order)
            if side=='buy':
                buy_cost += notional+fee
            else:
                sell_proceeds += notional
                sell_fees += fee
        # Cash and shared exposure constrain the whole buy basket. A common
        # scale preserves the rule's relative requested quantities; sequential
        # cash consumption would give alphabetical symbols arbitrary priority.
        buys = [o for o in result['orders'] if o['side']=='buy']
        def funded(scale):
            quantities = [(o, (decimal(o['quantity'])*scale).to_integral_value(rounding=ROUND_FLOOR)) for o in buys]
            notional = sum(quantity*decimal(o['limit_price']) for o, quantity in quantities)
            fees = sum(_fee(quantity, decimal(o['limit_price']), policy) for o, quantity in quantities)
            post_nav = nav-fees-sell_fees-open_fee
            if post_nav<=0 or notional+fees>budget or account_exposure+notional>post_nav*mode_cap:
                return False, quantities
            sector_additions = {}
            for order, quantity in quantities:
                symbol = order['instrument_id']
                added = quantity*decimal(order['limit_price'])
                name_limit = min(_pct(policy, 'max_single_name_pct'), decimal(profile.get('max_stock_nav_pct' if order['asset_class']=='stock' else 'max_etf_nav_pct', '.25')))
                if quantity and held.get(symbol, Decimal(0))*marks[symbol]+pending_values.get(symbol, Decimal(0))+added>post_nav*name_limit:
                    return False, quantities
                sector = sectors[symbol]
                if sector!='diversified':
                    sector_additions[sector] = sector_additions.get(sector, Decimal(0))+added
            if any(sector_values.get(s, Decimal(0))+added>post_nav*_pct(policy, 'max_sector_pct') for s, added in sector_additions.items() if added):
                return False, quantities
            if signal and policy['mode']=='swing':
                planned_loss = sum(quantity*(decimal(order['limit_price'])-decimal(order['protective_reference']))+
                    _fee(quantity, decimal(order['limit_price']), policy)+_fee(quantity, decimal(order['protective_reference']), policy)
                    for order, quantity in quantities)
                if planned_loss+existing_swing_loss>swing_loss_cap:
                    return False, quantities
            return True, quantities
        ok, funded_quantities = funded(Decimal(1))
        if not ok:
            low, high = Decimal(0), Decimal(1)
            for _ in range(64):
                mid = (low+high)/2
                if funded(mid)[0]:
                    low = mid
                else:
                    high = mid
            _, funded_quantities = funded(low)
        buy_cost = Decimal(0)
        for order, quantity in funded_quantities:
            if quantity<=0:
                result['orders'].remove(order)
                result['issues'].append(order['instrument_id']+': no whole share fits the jointly funded basket')
                continue
            price = decimal(order['limit_price'])
            fee = _fee(quantity, price, policy)
            if fee>quantity*price*min(decimal(policy.get('max_fee_pct', '.01')), decimal(profile.get('max_oneway_fee_pct','1'))):
                result['orders'].remove(order)
                result['issues'].append(order['instrument_id']+': funded whole-share size exceeds max_fee_pct; no trade')
                continue
            order.update(quantity=amount(quantity), notional=amount(quantity*price), estimated_fees=amount(fee),
                resulting_quantity=amount(decimal(order['held_quantity'])+quantity),
                resulting_mode_quantity=amount(decimal(order['mode_held_quantity'])+quantity))
            if order.get('protective_reference') is not None:
                protective = decimal(order['protective_reference'])
                order['planned_loss_usd'] = amount(quantity*(price-protective)+fee+_fee(quantity, protective, policy))
            order['order_id'] = digest({k: v for k, v in order.items() if k!='order_id'}, 'planned-order-')
            buy_cost += quantity*price+fee
        result['cash_plan'] = dict(currency='USD', settled=amount(settled), reserved=amount(reserved),
            open_order_fee_buffer=amount(open_fee), required_cash_floor=amount(floor), buy_cost=amount(buy_cost),
            cash_after_buys=amount(settled-reserved-open_fee-buy_cost),
            hypothetical_sell_proceeds=amount(sell_proceeds), sell_fees=amount(sell_fees),
            unfilled_sales_fund_buys=False)
        result['risk'] = dict(nav_usd=amount(nav), drawdown=amount(dd) if dd is not None else None,
            drawdown_status='known' if dd_known else 'unknown', drawdown_scope=drawdown.get('scope', 'unknown'),
            historical_drawdown=drawdown.get('historical_drawdown', 'unknown'), new_risk_constraints=list(buy_reasons))
        result['risk']['max_drawdown_pct'] = amount(dd_limit)
        if signal:
            result['strategy_validation_id'] = signal['validation_id']
            result['validated_sizing_profile'] = copy.deepcopy(profile)
            result['risk'].update(mode_exposure_before=amount(account_exposure), mode_nav_cap=amount(mode_cap),
                max_planned_swing_loss_usd=amount(swing_loss_cap),
                existing_swing_reference_loss_usd=amount(existing_swing_loss),
                new_planned_swing_loss_usd=amount(sum(decimal(o.get('planned_loss_usd','0')) for o in result['orders'])),
                protective_reference_is_broker_order=False, planned_loss_is_guaranteed=False)
        expiries = [timestamp(snapshot['valid_until']), timestamp(research_snapshot['valid_until'])]
        expiries += [timestamp(snapshot['quotes'][o['instrument_id']]['valid_until']) for o in result['orders']]
        result['valid_until'] = min(expiries).isoformat()
        result['status'] = 'ready_for_review' if result['orders'] else 'no_trade'
        result['execution_scope'] = 'manual_review' if result['orders'] else 'research_only'
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        result['issues'].append(str(exc))
        result['orders'] = []
    result['plan_id'] = digest(result)
    return result

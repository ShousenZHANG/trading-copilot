"""User-directed ETF arithmetic, separate from historically admitted strategies.

A sealed, explicitly confirmed intent is not alpha, a standing mandate or a
broker order. Prices are computed from live raw quotes, never accepted here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_FLOOR

from .trade_plan import (SCHEMA_VERSION, POLICY_VERSION, ACTIVE_ORDER_STATUSES,
    TERMINAL_ORDER_STATUSES, amount, canonical, decimal, digest, timestamp,
    policy_binding, _pct, _quote, _fee)

RISK_FIELDS = ('cash_floor_pct', 'max_single_name_pct', 'max_trade_notional_pct',
               'long_term_nav_cap_pct')


def normalize_intent(value):
    from .instruments import ETF_REGISTRY
    if not isinstance(value, dict) or set(value) != {'kind', 'targets', 'allowed_sells', 'risk_limits'}:
        raise ValueError('intent requires exactly kind, targets, allowed_sells and risk_limits')
    if value['kind'] not in {'buy_budget_usd', 'target_shares'}:
        raise ValueError('intent kind must be buy_budget_usd or target_shares')
    targets = value['targets']
    if not isinstance(targets, dict) or not 1 <= len(targets) <= 8 or not set(targets) <= ETF_REGISTRY:
        raise ValueError('one-off targets require 1 to 8 registered US ETFs')
    clean = {}
    for symbol, raw in targets.items():
        number = decimal(raw, symbol+' target', minimum=0)
        if number > Decimal('100000000') or (value['kind']=='target_shares' and number != number.to_integral_value()):
            raise ValueError('bounded whole target shares or bounded USD budget required')
        clean[symbol] = amount(number)
    sells = value['allowed_sells']
    if not isinstance(sells, list) or len(sells)!=len(set(sells)) or not set(sells) <= set(targets):
        raise ValueError('allowed_sells must explicitly name a subset of target symbols')
    if sells and value['kind']=='buy_budget_usd':
        raise ValueError('a buy-budget intent cannot authorize sales')
    limits = value['risk_limits']
    if not isinstance(limits, dict) or set(limits)!=set(RISK_FIELDS):
        raise ValueError('all four one-off cash/concentration/notional/exposure limits need explicit confirmation')
    limits = {field: amount(_pct(limits, field)) for field in RISK_FIELDS}
    if decimal(limits['cash_floor_pct'])+decimal(limits['long_term_nav_cap_pct'])>1:
        raise ValueError('cash floor and invested exposure cap cannot exceed NAV together')
    return dict(kind=value['kind'], targets=clean, allowed_sells=sorted(sells), risk_limits=limits)


def verify_intent(record):
    return (isinstance(record, dict) and record.get('kind')=='user_directed' and
            record.get('rule_id')==digest({k:v for k,v in record.items() if k!='rule_id'}, 'intent-'))


def effective_policy(base, record):
    if not verify_intent(record):
        raise ValueError('stored one-off intent integrity failed')
    result = dict(base)
    result.update(record['intent']['risk_limits'])
    result.update(mode='long_term', plan_kind='user_directed', intent_id=record['rule_id'])
    # Original limits still bind the card: a subsequent config edit invalidates it.
    result['base_policy_binding'] = policy_binding(base)
    return result


def record_intent(execution_snapshot_id, research_snapshot_id, intent, statement, *,
                  expected_account_version, db_path, now=None):
    from .plan_store import _affirmation, _transaction, read_snapshot
    from .journal import load_snapshot
    from .broker import validate_execution_snapshot
    if not _affirmation(statement, 'approve'):
        raise ValueError('an explicit user confirmation of this one-off allocation is required')
    moment = timestamp(now or datetime.now(timezone.utc))
    execution = read_snapshot(execution_snapshot_id, db_path=db_path)
    checked = validate_execution_snapshot(execution, now=moment)
    if not checked['complete'] or execution.get('account_version')!=expected_account_version:
        raise ValueError('one-off intent requires a fresh complete matching account snapshot')
    research = load_snapshot(research_snapshot_id, db_path=db_path)
    clean = normalize_intent(intent)
    _research(research, clean['targets'], moment)
    record = dict(kind='user_directed', schema_version=1, intent=clean,
        execution_snapshot_id=execution_snapshot_id, research_snapshot_id=research_snapshot_id,
        account_key=execution['account']['account_key'], account_version=expected_account_version,
        statement=statement.strip(), created_at=moment.isoformat(),
        valid_until=min(timestamp(execution['valid_until']), timestamp(research['valid_until'])).isoformat(),
        strategy_validation='not_claimed_user_directed', universe=sorted(clean['targets']))
    record['rule_id'] = digest(record, 'intent-')
    with _transaction(db_path) as connection:
        connection.execute('INSERT OR IGNORE INTO advisor_manual_intents VALUES (?,?)',
                           (record['rule_id'], canonical(record)))
    return dict(intent_id=record['rule_id'], committed=True, valid_until=record['valid_until'],
                intent=clean, scope='one_off_manual_calculation', order_submitted=False)


def read_intent(intent_id, *, db_path):
    import json
    from .plan_store import _transaction
    with _transaction(db_path) as connection:
        row = connection.execute('SELECT payload FROM advisor_manual_intents WHERE intent_id=?', (intent_id,)).fetchone()
    if not row:
        raise ValueError('confirmed one-off intent not found')
    result = json.loads(row['payload'])
    if not verify_intent(result):
        raise ValueError('one-off intent integrity failed')
    return result


def _research(research, symbols, now):
    from .market_data import verify_snapshot
    if not verify_snapshot(research) or research.get('status')!='ready':
        raise ValueError('fresh verified market research required for one-off calculations')
    if not timestamp(research['created_at']) <= now < timestamp(research['valid_until']):
        raise ValueError('one-off research is expired or future-dated')
    for symbol in symbols:
        item = research.get('instruments', {}).get(symbol, {})
        if item.get('quality_status')!='pass' or item.get('latest_session')!=item.get('expected_session'):
            raise ValueError(symbol+': latest complete validated research session required')


def compile_directed_plan(*, execution_snapshot, research_snapshot, adoption, policy, now=None):
    from .broker import validate_execution_snapshot
    from .plan_store import _nav_usd
    now = timestamp(now or datetime.now(timezone.utc))
    snap = execution_snapshot
    result = dict(schema_version=SCHEMA_VERSION, policy_version=POLICY_VERSION,
        plan_kind='user_directed', strategy_validation='not_claimed_user_directed',
        created_at=now.isoformat(), status='blocked', execution_scope='research_only',
        requires_review=True, requires_revalidation=True, issues=[], orders=[], mode='long_term',
        execution_snapshot_id=snap.get('snapshot_id'), account_version=snap.get('account_version'),
        account_key=snap.get('account', {}).get('account_key'), rule_id=adoption.get('rule_id'),
        research_snapshot_id=research_snapshot.get('snapshot_id'), policy_binding=policy_binding(policy),
        research_price_role='context_only_not_validated_strategy', price_is_optimal=False)
    try:
        if not verify_intent(adoption):
            raise ValueError('confirmed intent integrity failed')
        intent = normalize_intent(adoption['intent'])
        if (adoption['account_key']!=result['account_key'] or adoption['account_version']!=result['account_version']
                or adoption['research_snapshot_id']!=result['research_snapshot_id']):
            raise ValueError('account or research changed since the one-off intent was confirmed')
        if not timestamp(adoption['created_at']) <= now < timestamp(adoption['valid_until']):
            raise ValueError('one-off intent expired or future-dated')
        if policy.get('enabled') is not True or policy.get('allow_borrowing') is not False:
            raise ValueError('enabled advisor and no-borrowing policy required')
        if policy.get('fee_model_confirmed') is not True:
            raise ValueError('broker fee model is unconfirmed')
        if any(str(policy.get(k))!=v for k,v in intent['risk_limits'].items()):
            raise ValueError('one-off risk limits must match the explicit stored confirmation')
        validated = validate_execution_snapshot(snap, now=now)
        if not validated['complete']:
            raise ValueError('; '.join(validated['issues']))
        for name in ('accounts','cash','positions','orders','fills','quotes'):
            if snap.get('coverage',{}).get(name,{}).get('complete') is not True:
                raise ValueError(name+' coverage is incomplete')
        _research(research_snapshot, intent['targets'], now)
        nav = _nav_usd(snap, now)
        cash = snap.get('cash',{}).get('USD',{})
        settled = decimal(cash.get('settled'), 'settled USD cash', minimum=0)
        reserved = decimal(cash.get('reserved'), 'USD reservations', minimum=0)
        if cash.get('reservation_basis')!='open_buy_limit_orders_excludes_fees' or reserved>settled:
            raise ValueError('verified settled USD reservation semantics required')
        held, quotes, marks = {}, {}, {}
        modes=policy.get('mode_allocations') or {}
        for symbol in intent['targets']:
            if (modes.get('previously_confirmed') and modes.get('status')!='confirmed') or decimal(modes.get('allocations',{}).get(symbol,{}).get('swing','0'),minimum=0)>0:
                raise ValueError('one-off long-term intent cannot alter shares assigned to swing; reconcile explicit mode ownership first')
        from .instruments import ETF_REGISTRY
        for p in snap.get('positions',[]):
            symbol = p['instrument_id']
            if p['currency']!='USD' or p['account_key']!=result['account_key'] or symbol in held:
                raise ValueError('unique same-account USD positions required')
            held[symbol] = decimal(p['quantity'], 'held shares', minimum=0)
        for symbol in set(held)|set(intent['targets']):
            quote, mark, buy, sell = _quote(symbol, snap, policy, now)
            if symbol in intent['targets'] and (symbol not in ETF_REGISTRY or quote.get('asset_class')!='etf'):
                raise ValueError('one-off route currently supports registered US ETFs only')
            if any(p['instrument_id']==symbol and p['con_id']!=quote['con_id'] for p in snap['positions']):
                raise ValueError('position and raw quote contract identity mismatch')
            quotes[symbol], marks[symbol] = (quote,buy,sell), mark
        principal = fees_reserved = Decimal(0)
        active_symbols, order_keys = set(), set()
        for order in snap['orders']:
            if not order.get('order_key') or order['order_key'] in order_keys:
                raise ValueError('unique order identity required')
            order_keys.add(order['order_key'])
            if order['status'] in TERMINAL_ORDER_STATUSES:
                continue
            if order['status'] not in ACTIVE_ORDER_STATUSES:
                raise ValueError('unknown pending order state')
            active_symbols.add(order['instrument_id'])
            if order['side']=='buy':
                if order['currency']!='USD' or order.get('order_type','LMT')!='LMT':
                    raise ValueError('non-USD or unknown pending funding is unsupported')
                remaining = decimal(order['remaining_quantity'], minimum=0)
                price = decimal(order['limit_price'], minimum=Decimal('.00000001'))
                principal += remaining*price
                fees_reserved += _fee(remaining,price,policy)
        if principal!=reserved:
            raise ValueError('complete pending order book does not match reserved cash')
        if set(intent['targets']) & active_symbols:
            raise ValueError('target instrument has an active/partial/cancel-pending order; reconcile first')
        floor = nav*_pct(policy,'cash_floor_pct')
        budget = max(Decimal(0),settled-reserved-fees_reserved-floor)
        exposure = sum(q*marks[s] for s,q in held.items())+principal
        drawdown = policy.get('drawdown',{})
        dd = (decimal(drawdown['value'],minimum=0) if drawdown.get('status')=='known'
              and drawdown.get('account_version')==result['account_version'] else None)
        candidates = []
        for symbol,target in sorted(intent['targets'].items()):
            current = held.get(symbol,Decimal(0))
            quote,buy,sell = quotes[symbol]
            target = decimal(target)
            side = 'buy' if intent['kind']=='buy_budget_usd' or target>current else 'sell'
            if intent['kind']=='target_shares' and target==current:
                continue
            if side=='sell' and symbol not in intent['allowed_sells']:
                raise ValueError(symbol+': sale was not explicitly authorized in this intent')
            price = buy if side=='buy' else sell
            qty = (target/price if intent['kind']=='buy_budget_usd' else abs(target-current)).to_integral_value(rounding=ROUND_FLOOR)
            qty = min(qty,(nav*_pct(policy,'max_trade_notional_pct')/price).to_integral_value(rounding=ROUND_FLOOR))
            if side=='buy':
                if dd is None or dd>=_pct(policy,'max_drawdown_pct'):
                    result['issues'].append(symbol+': drawdown/flow coverage unknown or risk pause reached')
                    continue
                qty = min(qty,(max(Decimal(0),nav*_pct(policy,'max_single_name_pct')-current*marks[symbol])/price).to_integral_value(rounding=ROUND_FLOOR))
                if intent['kind']=='buy_budget_usd':
                    while qty>0 and qty*price+_fee(qty,price,policy)>target:
                        qty-=1
            else:
                qty = min(qty,current.to_integral_value(rounding=ROUND_FLOOR))
            if qty<=0:
                result['issues'].append(symbol+': no whole share fits the confirmed budget/limits')
                continue
            fee = _fee(qty,price,policy)
            if fee>=qty*price or fee>qty*price*decimal(policy.get('max_fee_pct','.01'),minimum=0):
                result['issues'].append(symbol+': fees exceed confirmed preference')
                continue
            candidates.append(dict(instrument_id=symbol,con_id=quote['con_id'],side=side,
                quantity=amount(qty),limit_price=amount(price),estimated_fees=amount(fee),
                currency='USD',order_type='LMT',tif='DAY',session='regular',asset_class='etf',mode='long_term',
                held_quantity=amount(current),resulting_quantity=amount(current+qty if side=='buy' else current-qty),
                limit_price_basis='live_raw_ask_tick_ceiling' if side=='buy' else 'live_raw_bid_tick_floor',
                quote_received_at=quote['received_at'],execution_scope='manual_review',notional=amount(qty*price)))
        # Keep the confirmed relative buy targets when settled cash is insufficient.
        # Unfilled sales are never credited to buying power or risk headroom.
        buys = [o for o in candidates if o['side']=='buy']
        sell_fees = sum(decimal(o['estimated_fees']) for o in candidates if o['side']=='sell')
        def fitted(scale):
            quantities = [(o,(decimal(o['quantity'])*scale).to_integral_value(rounding=ROUND_FLOOR)) for o in buys]
            notional = sum(q*decimal(o['limit_price']) for o,q in quantities)
            fees = sum(_fee(q,decimal(o['limit_price']),policy) for o,q in quantities)
            post_nav = nav-fees-sell_fees-fees_reserved
            return (notional+fees<=budget and (notional==0 or exposure+notional<=post_nav*_pct(policy,'long_term_nav_cap_pct'))
                    and all(q==0 or held.get(o['instrument_id'],0)*marks[o['instrument_id']]+q*decimal(o['limit_price'])
                            <=post_nav*_pct(policy,'max_single_name_pct') for o,q in quantities)), quantities
        low,high = Decimal(0),Decimal(1)
        if fitted(high)[0]:
            low=high
        else:
            for _ in range(80):
                mid=(low+high)/2
                if fitted(mid)[0]: low=mid
                else: high=mid
        for order,qty in fitted(low)[1]:
            price=decimal(order['limit_price'])
            fee=_fee(qty,price,policy)
            if not qty or fee>qty*price*decimal(policy.get('max_fee_pct','.01')):
                candidates.remove(order)
                result['issues'].append(order['instrument_id']+': no affordable whole-share buy after fees and risk constraints')
            else:
                order.update(quantity=amount(qty),estimated_fees=amount(fee),notional=amount(qty*price),
                             resulting_quantity=amount(decimal(order['held_quantity'])+qty))
        for order in candidates:
            order['order_id']=digest({'intent_id':adoption['rule_id'],**order},'planned-order-')
        cost=sum(decimal(o['notional'])+decimal(o['estimated_fees']) for o in candidates if o['side']=='buy')
        result.update(orders=candidates, status='ready_for_review' if candidates else 'no_trade',
            execution_scope='manual_review' if candidates else 'research_only',
            preserved_positions=sorted(set(held)-set(intent['targets'])),
            confirmed_intent=intent,
            valid_until=min(timestamp(adoption['valid_until']),timestamp(snap['valid_until']),
                            *[timestamp(q[0]['valid_until']) for q in quotes.values()]).isoformat(),
            cash_plan=dict(currency='USD',settled=amount(settled),reserved=amount(reserved),
                open_order_fee_buffer=amount(fees_reserved),required_cash_floor=amount(floor),
                buy_cost=amount(cost),cash_after_buys=amount(settled-reserved-fees_reserved-cost),
                unfilled_sales_fund_buys=False),
            risk=dict(nav_usd=amount(nav),drawdown=amount(dd) if dd is not None else None,
                      scope='prospective_account_risk_no_alpha_claim',**intent['risk_limits']))
    except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
        result['issues'].append(str(exc))
        result['orders']=[]
    result['plan_id']=digest(result)
    return result

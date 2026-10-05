"""Read-only, multi-blocker diagnostics. Availability is not execution readiness."""
from __future__ import annotations

from datetime import datetime, timezone
from .trade_plan import decimal, timestamp, _quote


def diagnose(settings, *, execution=None, research=None, rule=None, symbols=None,
             route='strategy', now=None, sdk_available=False):
    from dataclasses import asdict
    from .broker import validate_execution_snapshot
    from .market_data import verify_snapshot
    now=timestamp(now or datetime.now(timezone.utc))
    if route not in {'strategy','user_directed'}:
        raise ValueError('route must be strategy or user_directed')
    checks=[]
    def add(name,status,detail,next_action):
        checks.append(dict(check=name,status=status,detail=detail,next_action=next_action if status!='ready' else None))
    def flag(name,ok,detail,action):
        add(name,'ready' if ok else 'blocked',detail,action)
    flag('advisor_enabled',settings.advisor.enabled,'local advisor setting','Enable advisor after reviewing local configuration')
    flag('broker_enabled',settings.ibkr.enabled,'read-only connection setting','Enable IBKR read-only local integration')
    flag('broker_sdk',sdk_available,'official ibapi import availability','Install the official SDK in the selected runtime')
    flag('fee_model',settings.advisor.fee_model_confirmed,'explicit fee model confirmation','Confirm actual broker commission and cost model')
    flag('borrowing_disabled',settings.advisor.allow_borrowing is False,'cash funding only','Disable borrowing for this workflow')
    if route=='strategy':
        if not rule:
            add('strategy_adoption','blocked','No resolvable adopted rule','Validate and explicitly adopt a supported strategy, or explicitly choose the one-off route')
        elif rule.get('kind')=='advisor_strategy':
            from .advisor_strategy import verify_adoption
            flag('strategy_adoption',verify_adoption(rule),'stored template integrity','Recompute historical validation and review adoption')
        else:
            from .ruleset import SCHEMA_VERSION
            flag('strategy_adoption',rule.get('schema_version')==SCHEMA_VERSION and rule.get('admission',{}).get('admitted') is True,
                 'stored legacy admission','Recompute history/cost/out-of-sample admission')
    else:
        add('one_off_intent','unknown','A fresh explicitly confirmed snapshot-bound intent is required; no alpha validation claimed',
            'Review targets, allowed sales, settled USD budget and all one-off limits; confirm the intent')
    if execution is None:
        for name in ('broker_connection','account_coverage','settled_usd','quotes','nav_fx','cash_flow_drawdown'):
            add(name,'unknown','No stored observation supplied; no connection attempted','Collect a fresh read-only broker snapshot')
    else:
        validity=validate_execution_snapshot(execution,now=now)
        flag('broker_snapshot',validity['complete'],'; '.join(validity['issues']) or 'integrity and timestamp valid',
             'Refresh broker observation and resolve all reported issues')
        for name in ('accounts','cash','positions','orders','fills','quotes'):
            flag('coverage_'+name,execution.get('coverage',{}).get(name,{}).get('complete') is True,
                 name+' observation completion','Wait for complete broker callbacks and refresh')
        cash=execution.get('cash',{}).get('USD',{})
        try:
            settled=decimal(cash.get('settled'),minimum=0)
            reserved=decimal(cash.get('reserved'),minimum=0)
            known=cash.get('reservation_basis')=='open_buy_limit_orders_excludes_fees' and reserved<=settled
            flag('settled_usd',known,'settled USD known; funds still subject to fees, cash floor and account risk',
                 'Reconcile pending orders and USD settlement; AUD NAV is not USD buying power')
            flag('new_buy_funding',settled>reserved,'settled USD exceeds reservations',
                 'Wait for actual USD settlement or manually convert funds, then refresh')
        except (ValueError,ArithmeticError):
            add('settled_usd','blocked','Settled USD/reservations are unknown','Obtain actual currency ledger; never infer USD cash from NAV')
        from .plan_store import _nav_usd
        try:
            _nav_usd(execution,now)
            add('nav_fx','ready','NAV has a current USD risk conversion',None)
        except (ValueError,KeyError,TypeError,ArithmeticError):
            add('nav_fx','blocked','USD NAV or live NAV FX unknown','Refresh actual NAV and live FX; conversion quote does not fund purchases')
        for symbol in sorted(set(symbols or execution.get('quotes',{}))):
            try:
                _quote(symbol,execution,asdict(settings.advisor),now)
                add('quote_'+symbol,'ready','fresh raw live regular-session bid/ask and tick rule',None)
            except (ValueError,KeyError,TypeError,ArithmeticError) as exc:
                add('quote_'+symbol,'blocked',str(exc),'Refresh entitled live data in regular trading hours')
        add('cash_flow_drawdown','unknown','Compiler checks persisted baseline and explicit external cash-flow intervals',
            'Inspect compiler risk result; confirm only actual deposits/withdrawals when asked')
    if research is None:
        add('research','unknown','No research snapshot supplied','Collect current validated market evidence')
    else:
        try:
            ok=verify_snapshot(research) and research.get('status')=='ready' and timestamp(research['created_at'])<=now<timestamp(research['valid_until'])
        except (ValueError,TypeError,KeyError):
            ok=False
        flag('research',ok,'saved research integrity, readiness and freshness','Collect a new completed-session evidence snapshot')
    add('income_evidence','unknown','Distribution coverage is reported by the separate income report',
        'Collect issuer/exchange distribution events and inspect TTM coverage before income comparisons')
    blockers=[c['check'] for c in checks if c['status']=='blocked']
    unknown=[c['check'] for c in checks if c['status']=='unknown']
    for item in checks:
        item['stage']={'one_off_intent':'human_confirmation','cash_flow_drawdown':'plan_compiler',
                       'income_evidence':'optional_income_research'}.get(item['check'],'evidence_preparation')
    return dict(status='blocked' if blockers else 'needs_evidence' if unknown else 'ready',route=route,
        checked_at=now.isoformat(),checks=checks,blockers=blockers,unknown=unknown,
        status_scope='diagnostic_including_pending_human_and_compiler_stages_not_an_execution_gate',
        order_authorization=False,broker_orders_supported=False)

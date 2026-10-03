"""Manual plan public contracts, offline and without personal account state."""
import copy
import unittest
from functools import lru_cache
from datetime import datetime, timezone

from _test_policy import engine_fixture, bands_adoption, seal
from copilot.ruleset import rule_id
from copilot.broker import seal_execution_snapshot
from copilot.trade_plan import compile_plan

NOW = datetime(2026, 9, 6, 2, tzinfo=timezone.utc)


def adoption():
    value = bands_adoption()
    value['rule_id'] = rule_id(family=value['family'], parameters=value['parameters'],
        universe=value['universe'], targets=value['targets'], cost_model=value['cost_model'],
        cash_floor_pct=value['cash_floor_pct'], integer_shares=True, admission=value['admission'])
    return value


def policy(**changes):
    value = dict(enabled=True, mode='long_term', long_term_rule_id=adoption()['rule_id'],
        swing_rule_id='', cash_floor_pct=.05, swing_nav_cap_pct=.10,
        max_swing_loss_pct=.005, max_drawdown_pct=.10, max_single_name_pct=.25,
        max_sector_pct=.50, max_trade_notional_pct=.10, long_term_nav_cap_pct=.90,
        quote_ttl_seconds=60, regular_hours_only=True, allow_borrowing=False,
        fee_model_confirmed=True, commission_per_share_usd=0,
        min_commission_usd=1, other_cost_bps=0, max_spread_pct=.005,
        drawdown={'status': 'known', 'value': '.01', 'account_version': 'acct-v1'})
    value.update(changes)
    return value


def execution(**changes):
    value = dict(schema_version=1, provider='ibkr', snapshot_id='fixture-execution', account_version='acct-v1',
        as_of='2026-09-06T01:59:55+00:00', valid_until='2026-09-06T02:00:55+00:00',
        status='ready', complete=True, issues=[],
        account=dict(alias='local', account_key='account-hash', base_currency='USD',
            nav='10000', nav_currency='USD', account_type='cash', paper=True, paper_status='declared'),
        cash={'USD': dict(gross='10000', settled='10000', reserved='0', available='10000',
            reservation_basis='open_buy_limit_orders_excludes_fees', available_basis='settled_cash_net_open_orders')},
        positions=[], orders=[], fills=[], fx_rates={},
        coverage={k: {'complete': True} for k in ('accounts', 'cash', 'positions', 'orders', 'fills', 'quotes')},
        quotes={s: dict(con_id=n, currency='USD', bid='99.99', ask='100.001',
            bid_size='100', ask_size='100', actual_data_type=1, feed_scope='consolidated',
            received_at='2026-09-06T01:59:55+00:00', event_time=None,
            valid_until='2026-09-06T02:00:55+00:00', tick_size='.01',
            market_rule_id=26, market_rule=[dict(low_edge='0', increment='.01')],
            regular_hours=True, session='regular', status='ready', asset_class='etf', quantity_step='1')
            for s, n in [('QQQ', 1), ('SPY', 2)]})
    value.update(changes)
    return seal_execution_snapshot(value)


@lru_cache(maxsize=4)
def template_adoption(family='long_term_trend', symbol='AAPL'):
    """Synthetic provenance claims test a contract, never real investment evidence."""
    from _test_advisor_strategy import adopted_fixture
    return adopted_fixture(family=family, symbol=symbol)


def template_research(symbol='AAPL'):
    from _test_market_data import snapshot, FixtureProvider
    return snapshot([FixtureProvider(asset_class='stock' if symbol=='AAPL' else 'etf'),
        FixtureProvider('nasdaq', 'Nasdaq US market data', asset_class='stock' if symbol=='AAPL' else 'etf',
            security_type='common_stock', security_type_source_value='COMMON STOCK', exchange='NASDAQ-GS',
            source_url='https://api.nasdaq.com/api/quote/'+symbol+'/historical',
            identity_retrieved_at='2026-09-06T12:00:00Z',
            identity_source_url='https://api.nasdaq.com/api/quote/'+symbol+'/info?assetclass=stocks')], [symbol], allow_us_stocks=True)


def template_execution(symbol='AAPL'):
    snap = execution()
    snap.update(as_of='2026-09-06T11:59:55+00:00', valid_until='2026-09-06T12:00:55+00:00')
    snap['quotes'] = {symbol: dict(snap['quotes']['QQQ'], asset_class='stock' if symbol=='AAPL' else 'etf',
        bid='127.99', ask='128', sector='technology', sector_evidence={'source':'ibkr_contract_industry','value':'Technology'},
        received_at='2026-09-06T11:59:55+00:00', valid_until='2026-09-06T12:00:55+00:00')}
    return seal_execution_snapshot(snap)


def template_policy(adopt, **changes):
    mode = adopt['mode']
    value = policy(mode=mode, **{mode+'_rule_id': adopt['rule_id']},
        strategy_state={'status':'confirmed', 'rule_id':adopt['rule_id'], 'mode':mode,
                        'no_prior_executions':True, 'last_execution_session':None})
    value.update(changes)
    return value


class CompileManualPlan(unittest.TestCase):
    def run_plan(self, **changes):
        args = dict(execution_snapshot=execution(), research_snapshot=engine_fixture(),
                    adoption=adoption(), policy=policy(), now=NOW)
        args.update(changes)
        args['execution_snapshot'] = seal_execution_snapshot(args['execution_snapshot'])
        return compile_plan(**args)

    def test_raw_ask_tick_fee_and_per_trade_cap_produce_manual_review_card(self):
        result = self.run_plan()
        self.assertEqual(result['status'], 'ready_for_review', result['issues'])
        self.assertEqual(result['execution_scope'], 'manual_review')
        self.assertTrue(result['requires_review'])
        self.assertEqual([(o['instrument_id'], o['side'], o['quantity'], o['limit_price'])
                         for o in result['orders']], [('QQQ', 'buy', '9', '100.01'), ('SPY', 'buy', '9', '100.01')])
        self.assertEqual(result['cash_plan']['buy_cost'], '1802.18')
        self.assertEqual(result['cash_plan']['cash_after_buys'], '8197.82')
        self.assertEqual(result['cash_plan']['required_cash_floor'], '6000')
        self.assertEqual(result['research_price_role'], 'completed_session_signal_only')
        self.assertEqual(result['orders'][0]['limit_price_basis'], 'live_raw_ask_tick_ceiling')

    def test_unfilled_sale_does_not_fund_buy_and_omitted_holding_is_preserved(self):
        snap = execution()
        snap['cash']['USD'].update(gross='6000', settled='6000', available='6000')
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                 currency='USD', quantity='40', avg_cost='100'),
                             dict(instrument_id='VTI', account_key='account-hash', con_id=3,
                                 currency='USD', quantity='1.5', avg_cost='100')]
        snap['quotes']['VTI'] = dict(snap['quotes']['SPY'], con_id=3)
        result = self.run_plan(execution_snapshot=snap)
        self.assertEqual([(o['instrument_id'], o['side']) for o in result['orders']], [('QQQ', 'sell')])
        self.assertEqual(result['preserved_positions'], ['VTI'])
        self.assertEqual(result['cash_plan']['buy_cost'], '0')
        self.assertFalse(result['cash_plan']['unfilled_sales_fund_buys'])

    def test_sector_risk_counts_existing_and_open_buy_exposure_without_sell_funding(self):
        snap = execution()
        snap['positions'] = [dict(instrument_id='AAPL', account_key='account-hash', con_id=3,
                                 currency='USD', quantity='40', avg_cost='100', sector='technology')]
        snap['quotes']['AAPL'] = dict(snap['quotes']['SPY'], con_id=3, asset_class='stock', sector='technology')
        adopt = adoption()
        adopt.update(universe=['XLK', 'SPY'], targets={'XLK': .5, 'SPY': .5}, cash_floor_pct=.05)
        adopt['rule_id'] = rule_id(family=adopt['family'], parameters=adopt['parameters'], universe=adopt['universe'],
            targets=adopt['targets'], cost_model=adopt['cost_model'], cash_floor_pct=adopt['cash_floor_pct'],
            integer_shares=True, admission=adopt['admission'])
        research = engine_fixture(symbols=('XLK', 'SPY'))
        snap['quotes']['XLK'] = dict(snap['quotes']['QQQ'], con_id=4)
        result = self.run_plan(execution_snapshot=snap, research_snapshot=research, adoption=adopt,
            policy=policy(long_term_rule_id=adopt['rule_id'], max_sector_pct=.4))
        self.assertFalse(any(o['instrument_id']=='XLK' and o['side']=='buy' for o in result['orders']))
        self.assertIn('sector', ' '.join(result['issues']))

    def test_tampered_snapshot_cannot_authorize_a_manual_order(self):
        snap = execution()
        snap['cash']['USD']['settled'] = '999999'
        result = compile_plan(execution_snapshot=snap, research_snapshot=engine_fixture(),
            adoption=adoption(), policy=policy(), now=NOW)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['orders'], [])
        self.assertIn('digest', ' '.join(result['issues']))

    def test_recent_empty_fills_cannot_prove_clock_strategy_has_never_traded(self):
        adopt = adoption()
        adopt.update(family='inverse_volatility', targets=None,
                     parameters={'lookback_days': 63., 'rebalance_days': 21.})
        adopt['rule_id'] = rule_id(family=adopt['family'], parameters=adopt['parameters'], universe=adopt['universe'],
            targets=adopt['targets'], cost_model=adopt['cost_model'], cash_floor_pct=adopt['cash_floor_pct'],
            integer_shares=True, admission=adopt['admission'])
        snap = execution()
        snap['coverage']['fills'].update(history_complete=False, scope='TWS_available_recent_executions_only')
        result = self.run_plan(execution_snapshot=snap, adoption=adopt, policy=policy(long_term_rule_id=adopt['rule_id']))
        self.assertEqual(result['orders'], [])
        self.assertIn('cadence', ' '.join(result['issues']))

    def test_position_contract_identity_must_match_quote(self):
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=999,
                                 currency='USD', quantity='1', avg_cost='100')]
        result = self.run_plan(execution_snapshot=snap)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('contract', ' '.join(result['issues']))

    def test_limited_cash_scales_the_buy_basket_without_alphabetical_priority(self):
        snap = execution()
        snap['cash']['USD'].update(gross='7000', settled='7000', available='7000')
        result = self.run_plan(execution_snapshot=snap)
        self.assertEqual([(o['instrument_id'], o['quantity']) for o in result['orders']], [('QQQ', '4'), ('SPY', '4')])
        self.assertEqual(result['cash_plan']['buy_cost'], '802.08')
        self.assertEqual(result['cash_plan']['cash_after_buys'], '6197.92')

    def test_long_term_sale_preserves_swing_shares_in_same_instrument(self):
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                 currency='USD', quantity='10.5', avg_cost='100')]
        adopt = adoption()
        adopt['targets'] = {'QQQ': 0., 'SPY': 1.}
        adopt['rule_id'] = rule_id(family=adopt['family'], parameters=adopt['parameters'], universe=adopt['universe'],
            targets=adopt['targets'], cost_model=adopt['cost_model'], cash_floor_pct=adopt['cash_floor_pct'],
            integer_shares=True, admission=adopt['admission'])
        result = self.run_plan(execution_snapshot=snap, adoption=adopt, policy=policy(long_term_rule_id=adopt['rule_id'],
            mode_allocations={'status': 'confirmed', 'allocations': {'QQQ': {'long_term': '8', 'swing': '2.5'}}}))
        sale = next(o for o in result['orders'] if o['instrument_id']=='QQQ')
        self.assertEqual(sale['quantity'], '8')
        self.assertEqual(sale['resulting_quantity'], '2.5')
        self.assertEqual(sale['resulting_mode_quantity'], '0')

    def test_open_buy_principal_and_fee_are_reserved_once_not_twice(self):
        snap = execution()
        snap['orders'] = [dict(order_key='VTI-working', instrument_id='VTI', con_id=3, side='buy',
            total_quantity='8', filled_quantity='0', remaining_quantity='8', limit_price='100',
            currency='USD', status='submitted')]
        snap['cash']['USD'].update(reserved='800', available='9200')
        result = self.run_plan(execution_snapshot=snap)
        self.assertEqual(result['cash_plan']['reserved'], '800')
        self.assertEqual(result['cash_plan']['open_order_fee_buffer'], '1')
        self.assertEqual(result['cash_plan']['cash_after_buys'], '7396.82')
        self.assertEqual(result['cash_plan']['buy_cost'], '1802.18')

    def test_native_cash_and_fx_nav_have_different_roles(self):
        snap = execution()
        snap['account'].update(base_currency='AUD', nav='15000', nav_currency='AUD')
        snap['cash']['USD'].update(gross='6000', settled='6000', available='6000')
        snap['fx_rates'] = {'AUD.USD': dict(rate='.6666666666666666666666666667', bid='.66', ask='.67',
            received_at='2026-09-06T01:59:55+00:00', valid_until='2026-09-06T02:00:55+00:00',
            actual_data_type=1, status='ready')}
        result = self.run_plan(execution_snapshot=snap)
        self.assertEqual(result['risk']['nav_usd'], '10000')
        self.assertEqual(result['orders'], [])
        self.assertEqual(result['cash_plan']['buy_cost'], '0')
        self.assertEqual(result['cash_plan']['required_cash_floor'], '6000')

    def test_unknown_drawdown_stops_new_risk_but_allows_funded_reduction(self):
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                 currency='USD', quantity='40', avg_cost='100')]
        result = self.run_plan(execution_snapshot=snap, policy=policy(drawdown=None))
        self.assertTrue(result['orders'])
        self.assertTrue(all(o['side']=='sell' for o in result['orders']))
        self.assertEqual(result['risk']['drawdown_status'], 'unknown')

    def test_delayed_expired_and_unconfirmed_fees_withhold_order_numbers(self):
        delayed = execution()
        delayed['quotes']['QQQ']['actual_data_type'] = 3
        expired = execution()
        expired['quotes']['QQQ']['valid_until'] = NOW.isoformat()
        for snap, p in [(delayed, policy()), (expired, policy()), (execution(), policy(fee_model_confirmed=False))]:
            with self.subTest(snapshot=snap['snapshot_id'], fees=p['fee_model_confirmed']):
                result = self.run_plan(execution_snapshot=snap, policy=p)
                self.assertEqual(result['execution_scope'], 'research_only')
                self.assertEqual(result['orders'], [])

    def test_known_but_excessive_fees_do_not_turn_into_a_trade(self):
        result = self.run_plan(policy=policy(min_commission_usd=1000, max_fee_pct=.01))
        self.assertEqual(result['orders'], [])
        self.assertIn('fees', ' '.join(result['issues']))
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                 currency='USD', quantity='40', avg_cost='100')]
        reducing = self.run_plan(execution_snapshot=snap, policy=policy(min_commission_usd=1000, drawdown=None))
        self.assertEqual(reducing['orders'], [])
        self.assertIn('negative net', ' '.join(reducing['issues']))

    def test_validated_and_explicitly_adopted_stock_template_can_produce_a_funded_card(self):
        from _test_market_data import NOW as STRATEGY_NOW
        adopt = template_adoption()
        result = self.run_plan(execution_snapshot=template_execution(), research_snapshot=template_research(),
            adoption=adopt, policy=template_policy(adopt), now=STRATEGY_NOW)
        self.assertEqual(result['status'], 'ready_for_review', result['issues'])
        self.assertEqual([(o['instrument_id'],o['side'],o['quantity'],o['limit_price']) for o in result['orders']],
                         [('AAPL','buy','3','128')])
        self.assertEqual(result['orders'][0]['asset_class'], 'stock')
        self.assertLessEqual(float(result['orders'][0]['notional']), 500)

    def test_adopted_swing_entry_has_funded_quantity_and_explicit_reference_risk(self):
        from _test_market_data import NOW as STRATEGY_NOW
        adopt = template_adoption('swing_breakout', 'QQQ')
        result = self.run_plan(execution_snapshot=template_execution('QQQ'), research_snapshot=template_research('QQQ'),
            adoption=adopt, policy=template_policy(adopt), now=STRATEGY_NOW)
        self.assertEqual(result['status'], 'ready_for_review', result['issues'])
        order = result['orders'][0]
        self.assertEqual(order['quantity'], '7')
        self.assertEqual(order['protective_reference'], '123.9')
        self.assertEqual(order['planned_loss_usd'], '30.7')
        self.assertFalse(order['protective_order_acknowledged'])
        self.assertLessEqual(float(order['planned_loss_usd']), 50)

    def test_template_requires_explicit_start_or_actual_execution_cadence(self):
        from _test_market_data import NOW as at
        adopt = template_adoption('long_term_trend', 'AAPL')
        args = dict(execution_snapshot=template_execution('AAPL'), research_snapshot=template_research('AAPL'), adoption=adopt, now=at)
        unknown = self.run_plan(**args, policy=policy(long_term_rule_id=adopt['rule_id']))
        self.assertFalse(unknown['orders'])
        self.assertIn('cadence', ' '.join(unknown['issues']))
        for session, expected in [('2026-09-03', False), ('2026-07-01', True)]:
            cfg = template_policy(adopt, strategy_state={'status':'confirmed', 'rule_id':adopt['rule_id'],
                'mode':'long_term', 'no_prior_executions':False, 'last_execution_session':session})
            result = self.run_plan(**args, policy=cfg)
            self.assertEqual(bool(result['orders']), expected, result['issues'])

    def test_exact_drawdown_threshold_pauses_buy_but_unknown_sector_does_not_block_sell(self):
        paused = self.run_plan(policy=policy(drawdown={'status':'known','value':'.1','account_version':'acct-v1'}))
        self.assertFalse(paused['orders'])
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,currency='USD',quantity='40',avg_cost='100'),
                             dict(instrument_id='AAPL',account_key='account-hash',con_id=3,currency='USD',quantity='1',avg_cost='100')]
        snap['quotes']['AAPL'] = dict(snap['quotes']['QQQ'],con_id=3,asset_class='stock',sector=None)
        reduced = self.run_plan(execution_snapshot=snap, policy=policy(drawdown=None))
        self.assertEqual([(o['instrument_id'],o['side']) for o in reduced['orders']], [('QQQ','sell')], reduced['issues'])

    def test_swing_budget_does_not_subtract_confirmed_other_mode_holdings(self):
        from _test_market_data import NOW as at
        adopt = template_adoption('swing_breakout','QQQ')
        snap = template_execution('QQQ')
        snap['positions']=[dict(instrument_id='SPY',account_key='account-hash',con_id=2,currency='USD',quantity='80',avg_cost='100')]
        snap['quotes']['SPY']=dict(snap['quotes']['QQQ'],con_id=2,bid='99.99',ask='100')
        cfg = template_policy(adopt, mode_allocations={'status':'confirmed','allocations':{'SPY':{'long_term':'80','swing':'0'}}})
        result=self.run_plan(execution_snapshot=snap,research_snapshot=template_research('QQQ'),adoption=adopt,policy=cfg,now=at)
        self.assertEqual(result['status'],'ready_for_review',result['issues'])
        self.assertEqual(result['orders'][0]['quantity'],'7')
        self.assertEqual(result['risk']['mode_exposure_before'],'0')

    def test_swing_actual_entry_age_and_fixed_entry_stop_are_consumed(self):
        from _test_market_data import NOW as at
        adopt=template_adoption('swing_breakout','QQQ')
        snap=template_execution('QQQ')
        snap['positions']=[dict(instrument_id='QQQ',account_key='account-hash',con_id=1,currency='USD',quantity='5',avg_cost='125')]
        owned={'status':'confirmed','allocations':{'QQQ':{'long_term':'2','swing':'3'}},
            'entry_sessions':{'QQQ':{'swing':'2026-07-01'}}}
        recent_state={'status':'confirmed','rule_id':adopt['rule_id'],'mode':'swing',
                      'no_prior_executions':False,'last_execution_session':'2026-09-04'}
        result=self.run_plan(execution_snapshot=snap,research_snapshot=template_research('QQQ'),adoption=adopt,
            policy=template_policy(adopt,mode_allocations=owned,strategy_state=recent_state),now=at)
        self.assertEqual([(o['side'],o['quantity']) for o in result['orders']],[('sell','3')],result['issues'])
        self.assertEqual(result['orders'][0]['exit_reason'],'actual_holding_session_limit')
        self.assertEqual(result['orders'][0]['resulting_quantity'],'2')
        recent=copy.deepcopy(owned)
        recent['entry_sessions']['QQQ']['swing']='2026-09-03'
        recent['entry_lots']={'QQQ':{'swing':{'rule_id':adopt['rule_id'],'entry_plan_id':'immutable-entry',
            'protective_reference':'128.2','price_units_session':'2026-09-03','planned_quantity':'3'}}}
        # Explicit split-zero evidence, not absent corporate actions inferred as zero.
        research=template_research('QQQ')
        for entry in research['evidence']:
            if 'bars' in entry:
                for bar in entry['bars']: bar['splits']=0
        research=seal(research)
        stopped=self.run_plan(execution_snapshot=snap,research_snapshot=research,adoption=adopt,
            policy=template_policy(adopt,mode_allocations=recent,strategy_state=recent_state),now=at)
        self.assertEqual([(o['side'],o['quantity']) for o in stopped['orders']],[('sell','3')],stopped['issues'])
        self.assertEqual(stopped['orders'][0]['exit_reason'],'fixed_actual_entry_protective_reference')
        self.assertFalse(stopped['rebalance_due'])
        unknown=self.run_plan(execution_snapshot=snap,research_snapshot=research,adoption=adopt,
            policy=template_policy(adopt,mode_allocations=recent,strategy_state={'status':'unknown'}),now=at)
        self.assertEqual([(o['side'],o['quantity']) for o in unknown['orders']],[('sell','3')],unknown['issues'])

    def test_fixed_entry_protection_requires_split_coverage_and_scales_share_units(self):
        from _test_market_data import NOW as at
        adopt=template_adoption('swing_breakout','QQQ')
        snap=template_execution('QQQ')
        snap['positions']=[dict(instrument_id='QQQ',account_key='account-hash',con_id=1,currency='USD',quantity='6',avg_cost='125')]
        owned={'status':'confirmed','allocations':{'QQQ':{'swing':'6','long_term':'0'}},
            'entry_sessions':{'QQQ':{'swing':'2026-09-03'}},
            'entry_lots':{'QQQ':{'swing':{'rule_id':adopt['rule_id'],'entry_plan_id':'reviewed-entry',
                'protective_reference':'256','price_units_session':'2026-09-03','planned_quantity':'3'}}}}
        args=dict(execution_snapshot=snap,adoption=adopt,policy=template_policy(adopt,mode_allocations=owned),now=at)
        unknown=self.run_plan(**args,research_snapshot=template_research('QQQ'))
        self.assertFalse(unknown['orders'])
        known=template_research('QQQ')
        for entry in known['evidence']:
            if 'bars' in entry:
                for bar in entry['bars']:
                    bar['splits']=2 if bar['session']=='2026-09-04' else 0
        result=self.run_plan(**args,research_snapshot=seal(known))
        self.assertEqual([(o['side'],o['quantity']) for o in result['orders']],[('sell','6')],result['issues'])
        self.assertEqual(result['orders'][0]['fixed_entry_protective_reference'],'128')

    def test_template_cost_drift_and_stale_mode_history_never_grant_orders(self):
        from _test_market_data import NOW as at
        adopt=template_adoption('long_term_trend','AAPL')
        base=dict(execution_snapshot=template_execution('AAPL'),research_snapshot=template_research('AAPL'),adoption=adopt,now=at)
        for changes in ({'min_commission_usd':2}, {'other_cost_bps':2},
                        {'strategy_state':{'status':'unknown','rule_id':adopt['rule_id'],'mode':'long_term'}},
                        {'strategy_state':{'status':'confirmed','rule_id':adopt['rule_id'],'mode':'long_term',
                                           'no_prior_executions':False,'last_execution_session':'2026-09-08'}}):
            with self.subTest(changes=changes):
                result=self.run_plan(**base,policy=template_policy(adopt,**changes))
                self.assertFalse(result['orders'])
                self.assertTrue(result['issues'])

    def test_local_drawdown_configuration_cannot_widen_the_adopted_template_limit(self):
        from _test_market_data import NOW as at
        adopt=template_adoption('long_term_trend','AAPL')
        result=self.run_plan(execution_snapshot=template_execution('AAPL'),research_snapshot=template_research('AAPL'),
            adoption=adopt,policy=template_policy(adopt,max_drawdown_pct='.20',
                drawdown={'status':'known','value':'.10','account_version':'acct-v1'}),now=at)
        self.assertFalse(result['orders'])
        self.assertIn('drawdown limit', ' '.join(result['issues']))


if __name__ == '__main__':
    unittest.main()

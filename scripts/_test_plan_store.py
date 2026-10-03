"""Persistent manual-review workflow, isolated databases only."""
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from datetime import timedelta

from _test_trade_plan import NOW, execution, adoption, policy
from _test_policy import engine_fixture
from copilot.plan_store import (create_plan, read_plan, review_plan, save_snapshot,
                               record_cash_flow, revalidate_plan, PlanConflict, record_mode_allocations)
from copilot.broker import seal_execution_snapshot


class ManualPlanStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name)/'plans.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def create(self, **changes):
        args = dict(execution_snapshot=execution(), research_snapshot=engine_fixture(),
                    adoption=adoption(), policy=policy(drawdown=None), now=NOW, db_path=self.db)
        args.update(changes)
        return create_plan(**args)

    def test_first_snapshot_establishes_prospective_baseline_and_review_is_not_a_fill(self):
        created = self.create()
        self.assertEqual(created['risk']['drawdown_scope'], 'prospective_since_baseline')
        self.assertEqual(created['risk']['historical_drawdown'], 'unknown')
        self.assertEqual(created['risk']['drawdown'], '0')
        self.assertEqual(created['version'], 1)
        reviewed = review_plan(created['plan_id'], statement='我已核对账户、报价、费用和限额，确认这个人工计划',
                               expected_version=1, db_path=self.db, now=NOW)
        self.assertEqual(reviewed['review_state'], 'reviewed')
        self.assertEqual(reviewed['version'], 2)
        self.assertFalse(reviewed['order_submitted'])
        self.assertEqual(reviewed['execution_scope'], 'manual_review')
        loaded = read_plan(created['plan_id'], db_path=self.db, now=NOW)
        self.assertEqual(loaded['review_state'], 'reviewed')
        self.assertEqual(loaded['orders'], created['orders'])
        expired = read_plan(created['plan_id'], db_path=self.db, now=NOW+timedelta(minutes=1))
        self.assertEqual(expired['review_state'], 'expired')
        self.assertEqual(expired['orders'], [])
        self.assertTrue(expired['historical_orders'])

    def test_unknown_interval_blocks_buys_until_exact_cash_flow_confirmation(self):
        initial = self.create()
        second = execution()
        second.update(as_of='2026-09-06T02:00:01+00:00', account_version='acct-v2')
        second['account']['nav'] = '20000'
        second['cash']['USD'].update(gross='20000', settled='20000', available='20000')
        second = seal_execution_snapshot(second)
        later = NOW+timedelta(seconds=2)
        unknown = self.create(execution_snapshot=second, now=later)
        self.assertEqual(unknown['risk']['drawdown_status'], 'unknown')
        self.assertFalse(any(o['side']=='buy' for o in unknown['orders']))
        receipt = record_cash_flow(second['snapshot_id'], '10000', '确认本区间净入金一万美元',
            previous_snapshot_id=initial['execution_snapshot_id'], db_path=self.db, now=later)
        self.assertTrue(receipt['committed'])
        funded = self.create(execution_snapshot=second, now=later)
        self.assertEqual(funded['risk']['drawdown'], '0')
        self.assertEqual(funded['status'], 'ready_for_review')
        with self.assertRaises(PlanConflict):
            record_cash_flow(second['snapshot_id'], '0', '确认修改为零',
                previous_snapshot_id=initial['execution_snapshot_id'], db_path=self.db, now=later)

    def test_partial_and_cancel_pending_orders_retire_reviewed_quantity(self):
        for status, expected in [('partially_filled', 'partially_filled'), ('cancel_pending', 'cancel_pending'),
                                 ('submitted', 'working')]:
            with self.subTest(status=status):
                created = self.create()
                viewed = read_plan(created['plan_id'], db_path=self.db, now=NOW)
                snapshot = execution()
                snapshot['orders'] = [dict(order_key='open-1', con_id=1, instrument_id='QQQ', side='buy',
                    total_quantity='9', filled_quantity='1' if status=='partially_filled' else '0',
                    remaining_quantity='8' if status=='partially_filled' else '9', limit_price='100.01',
                    currency='USD', status=status)]
                snapshot['account_version'] = 'changed-'+status
                snapshot = seal_execution_snapshot(snapshot)
                result = revalidate_plan(created['plan_id'], execution_snapshot=snapshot, policy=policy(drawdown=None),
                    expected_version=viewed['version'], db_path=self.db, now=NOW)
                self.assertEqual(result['review_state'], expected)
                self.assertEqual(result['orders'], [])
                self.assertEqual(result['execution_scope'], 'research_only')
                with self.assertRaises(PlanConflict):
                    review_plan(created['plan_id'], statement='确认', expected_version=viewed['version'], db_path=self.db, now=NOW)

    def test_quote_refresh_with_unchanged_account_retains_trusted_baseline(self):
        initial = self.create()
        next_snapshot = execution()
        next_snapshot['as_of'] = '2026-09-06T02:00:01+00:00'
        next_snapshot = seal_execution_snapshot(next_snapshot)
        result = self.create(execution_snapshot=next_snapshot, now=NOW+timedelta(seconds=2))
        self.assertEqual(result['risk']['drawdown_status'], 'known')
        self.assertEqual(result['risk']['drawdown'], '0')
        self.assertEqual(result['risk']['historical_drawdown'], 'unknown')

    def test_interval_confirmation_retry_is_idempotent_after_risk_observation(self):
        first = self.create()
        second = execution()
        second.update(as_of='2026-09-06T02:00:01+00:00', account_version='v2')
        second = seal_execution_snapshot(second)
        save_snapshot(second, db_path=self.db, now=NOW+timedelta(seconds=2))
        args = dict(previous_snapshot_id=first['execution_snapshot_id'], db_path=self.db, now=NOW+timedelta(seconds=2))
        before = record_cash_flow(second['snapshot_id'], '0', '明确确认区间无外部入出金', **args)
        self.create(execution_snapshot=second, now=NOW+timedelta(seconds=2))
        after = record_cash_flow(second['snapshot_id'], '0', '明确确认区间无外部入出金', **args)
        self.assertEqual(before, after)

    def test_new_card_supersedes_unreviewed_card_but_reviewed_card_locks_same_account(self):
        first = self.create()
        second = self.create(now=NOW+timedelta(seconds=1))
        old = read_plan(first['plan_id'], db_path=self.db, now=NOW+timedelta(seconds=1))
        self.assertEqual(old['review_state'], 'superseded')
        self.assertEqual(old['orders'], [])
        reviewed = review_plan(second['plan_id'], statement='确认人工Review', expected_version=1, db_path=self.db, now=NOW+timedelta(seconds=1))
        third = self.create(now=NOW+timedelta(seconds=2))
        self.assertEqual(third['status'], 'blocked')
        self.assertEqual(third['orders'], [])
        self.assertIn('reviewed', ' '.join(third['issues']))
        abandoned = review_plan(second['plan_id'], statement='明确放弃本地计划，未提交券商',
            outcome='cancel', expected_version=reviewed['version'], db_path=self.db, now=NOW+timedelta(seconds=2))
        self.assertEqual(abandoned['review_state'], 'cancelled')
        self.assertEqual(abandoned['orders'], [])
        self.assertFalse(abandoned['order_submitted'])
        fourth = self.create(now=NOW+timedelta(seconds=3))
        self.assertEqual(fourth['status'], 'ready_for_review')

    def test_explicit_review_statement_and_version_are_required(self):
        created = self.create()
        with self.assertRaises(ValueError):
            review_plan(created['plan_id'], statement='', expected_version=1, db_path=self.db, now=NOW)
        with self.assertRaises(ValueError):
            review_plan(created['plan_id'], statement='确认', expected_version=True, db_path=self.db, now=NOW)
        self.assertEqual(read_plan(created['plan_id'], db_path=self.db, now=NOW)['version'], 1)

    def test_newly_saved_changed_account_invalidates_old_card_without_explicit_revalidate(self):
        created = self.create()
        changed = execution()
        changed.update(as_of='2026-09-06T02:00:01+00:00', account_version='changed-account')
        changed['cash']['USD'].update(settled='6000', gross='6000', available='6000')
        changed = seal_execution_snapshot(changed)
        save_snapshot(changed, db_path=self.db, now=NOW+timedelta(seconds=2))
        result = read_plan(created['plan_id'], db_path=self.db, now=NOW+timedelta(seconds=2))
        self.assertEqual(result['review_state'], 'needs_recompile')
        self.assertEqual(result['orders'], [])
        with self.assertRaises(ValueError):
            review_plan(created['plan_id'], statement='同意', expected_version=1, db_path=self.db, now=NOW+timedelta(seconds=2))

    def test_cancel_pending_is_still_visible_after_quote_expiry(self):
        created = self.create()
        snap = execution()
        snap['orders'] = [dict(order_key='open', instrument_id='QQQ', side='buy',
            total_quantity='9', filled_quantity='0', remaining_quantity='9',
            limit_price='100.01', currency='USD', status='cancel_pending')]
        snap['account_version'] = 'pending'
        snap = seal_execution_snapshot(snap)
        revalidate_plan(created['plan_id'], execution_snapshot=snap, policy=policy(drawdown=None),
                        expected_version=1, db_path=self.db, now=NOW)
        result = read_plan(created['plan_id'], db_path=self.db, now=NOW+timedelta(minutes=1))
        self.assertEqual(result['review_state'], 'cancel_pending')
        self.assertTrue(result['quote_expired'])
        self.assertEqual(result['orders'], [])

    def test_mode_allocations_cannot_double_own_the_same_shares_and_are_bound_to_positions(self):
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                 currency='USD', quantity='10.5', avg_cost='100')]
        snap = seal_execution_snapshot(snap)
        save_snapshot(snap, db_path=self.db, now=NOW)
        with self.assertRaises(ValueError):
            record_mode_allocations(snap['snapshot_id'], {'QQQ': {'long_term': '8', 'swing': '3'}},
                '分配模式', expected_account_version=snap['account_version'], db_path=self.db, now=NOW)
        receipt = record_mode_allocations(snap['snapshot_id'], {'QQQ': {'long_term': '8', 'swing': '2.5'}},
            '明确分配现有股数', expected_account_version=snap['account_version'], expected_version=0, db_path=self.db, now=NOW)
        self.assertTrue(receipt['committed'])
        self.assertEqual(receipt['version'], 1)
        plan = self.create(execution_snapshot=snap)
        self.assertEqual(plan['mode_allocation_status'], 'confirmed')
        self.assertEqual(plan['mode_allocations']['QQQ']['long_term'], '8')
        with self.assertRaises(PlanConflict):
            record_mode_allocations(snap['snapshot_id'], {'QQQ': {'long_term': '8', 'swing': '2.5'}},
                '并发分配', expected_account_version=snap['account_version'], expected_version=0, db_path=self.db, now=NOW)

    def test_fresh_unchanged_broker_state_revalidates_review_without_fabricating_a_fill(self):
        plan = self.create()
        reviewed = review_plan(plan['plan_id'], statement='确认通过Review', expected_version=1, db_path=self.db, now=NOW)
        result = revalidate_plan(plan['plan_id'], execution_snapshot=execution(), policy=policy(drawdown=None),
            expected_version=reviewed['version'], db_path=self.db, now=NOW+timedelta(seconds=1))
        self.assertEqual(result['revalidation']['status'], 'pass', result['revalidation'])
        self.assertEqual(result['review_state'], 'reviewed')
        self.assertFalse(result['order_submitted'])

    def test_simultaneous_reviews_commit_only_one_version(self):
        created = self.create()
        def approve(_):
            try:
                return review_plan(created['plan_id'], statement='明确确认Review', expected_version=1,
                                   db_path=self.db, now=NOW)['version']
            except PlanConflict:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(approve, range(2)))
        self.assertCountEqual(outcomes, [2, 'conflict'])
        view = read_plan(created['plan_id'], db_path=self.db, now=NOW)
        self.assertEqual(len(view['review_events']), 1)

    def test_sealed_connection_failure_is_saved_without_inventing_account_or_risk(self):
        from copilot.broker import collect_execution_snapshot
        failed = collect_execution_snapshot({}, ['SPY'], now=NOW)
        result = self.create(execution_snapshot=failed)
        self.assertEqual(result['status'], 'blocked')
        self.assertIsNone(result['account_key'])
        self.assertEqual(result['orders'], [])

    def test_fx_only_refresh_changes_usd_risk_without_inventing_external_cash_flow(self):
        first = execution()
        first['account'].update(base_currency='AUD', nav_currency='AUD', nav='15000')
        first['fx_rates'] = {'AUD.USD': dict(rate='.66', received_at='2026-09-06T01:59:55+00:00',
            valid_until='2026-09-06T02:00:55+00:00', actual_data_type=1, status='ready')}
        first = seal_execution_snapshot(first)
        self.create(execution_snapshot=first)
        second = execution()
        second['account'] = dict(first['account'])
        second['as_of'] = '2026-09-06T02:00:01+00:00'
        second['fx_rates'] = {'AUD.USD': dict(first['fx_rates']['AUD.USD'], rate='.64')}
        second = seal_execution_snapshot(second)
        result = self.create(execution_snapshot=second, now=NOW+timedelta(seconds=2))
        self.assertEqual(result['risk']['drawdown_status'], 'known')
        self.assertAlmostEqual(float(result['risk']['drawdown']), 300/9900)

    def test_same_net_quantity_roundtrip_requires_new_mode_ownership_confirmation(self):
        snap = execution()
        snap['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                 currency='USD', quantity='10', avg_cost='100')]
        snap = seal_execution_snapshot(snap)
        save_snapshot(snap, db_path=self.db, now=NOW)
        record_mode_allocations(snap['snapshot_id'], {'QQQ': {'long_term': '8', 'swing': '2'}},
            '确认归属', expected_account_version=snap['account_version'], db_path=self.db, now=NOW)
        changed = dict(snap, as_of='2026-09-06T02:00:01+00:00', account_version='new-roundtrip', fills=[
            dict(execution_id='sell8', instrument_id='QQQ', side='sell', quantity='8', executed_at='2026-09-06T02:00:00+00:00'),
            dict(execution_id='buy8', instrument_id='QQQ', side='buy', quantity='8', executed_at='2026-09-06T02:00:01+00:00')])
        changed = seal_execution_snapshot(changed)
        result = self.create(execution_snapshot=changed, now=NOW+timedelta(seconds=2))
        self.assertEqual(result['mode_allocation_status'], 'unknown')

    def test_expired_review_does_not_unlock_cash_using_a_pre_expiry_broker_observation(self):
        first = self.create()
        review_plan(first['plan_id'], statement='确认通过Review，尚未提交', expected_version=1, db_path=self.db, now=NOW)
        cached = execution()
        cached.update(as_of='2026-09-06T02:00:10+00:00', valid_until='2026-09-06T02:01:10+00:00')
        for quote in cached['quotes'].values():
            quote.update(received_at='2026-09-06T02:00:10+00:00', valid_until='2026-09-06T02:01:10+00:00')
        cached = seal_execution_snapshot(cached)
        result = self.create(execution_snapshot=cached, now=NOW+timedelta(seconds=56))
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('reviewed', ' '.join(result['issues']))

    def test_first_snapshot_can_create_an_explicit_nested_local_database(self):
        from copilot.broker import collect_execution_snapshot
        nested = Path(self.tmp.name)/'new'/'state'/'plans.sqlite'
        save_snapshot(collect_execution_snapshot({}, ['SPY'], now=NOW), db_path=nested, now=NOW)
        self.assertTrue(nested.exists())

    def test_negated_quoted_or_conditional_language_cannot_approve_a_review(self):
        plan = self.create()
        for statement in ('我不批准这张卡', '还没Review，如果合适再同意', '朋友说“我批准这张卡”',
                          'If it looks good I approve', 'I have not reviewed this plan', '"I approve"', 'fixture'):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                review_plan(plan['plan_id'], statement=statement, expected_version=1, db_path=self.db, now=NOW)
        self.assertEqual(read_plan(plan['plan_id'], db_path=self.db, now=NOW)['version'], 1)
        approved = review_plan(plan['plan_id'], statement='I have reviewed and approve this manual plan',
                               expected_version=1, db_path=self.db, now=NOW)
        self.assertEqual(approved['review_state'], 'reviewed')

    def test_strategy_activation_and_completed_session_are_explicit_versioned_facts(self):
        from copilot.plan_store import record_strategy_state
        from _test_trade_plan import template_execution, template_research, template_adoption
        from _test_market_data import NOW as at
        adopt=template_adoption('long_term_trend','AAPL')
        snap=template_execution('AAPL')
        save_snapshot(snap,db_path=self.db,now=at)
        cfg=policy(long_term_rule_id=adopt['rule_id'])
        args=dict(execution_snapshot=snap,research_snapshot=template_research('AAPL'),adoption=adopt,policy=cfg,now=at)
        self.assertFalse(self.create(**args)['orders'])
        with self.assertRaises(ValueError):
            record_strategy_state(snap['snapshot_id'],adopt['rule_id'],'long_term','确认',
                expected_account_version=snap['account_version'],no_prior_executions=True,db_path=self.db,now=at)
        receipt=record_strategy_state(snap['snapshot_id'],adopt['rule_id'],'long_term','确认该策略模式首次启动，此前没有实际成交',
            expected_account_version=snap['account_version'],no_prior_executions=True,expected_version=0,db_path=self.db,now=at)
        self.assertTrue(receipt['committed'])
        self.assertEqual(self.create(**args)['status'],'ready_for_review')
        latest=record_strategy_state(snap['snapshot_id'],adopt['rule_id'],'long_term','确认该策略最后实际成交日期为2026-09-03',
            expected_account_version=snap['account_version'],last_execution_session='2026-09-03',expected_version=1,db_path=self.db,now=at)
        self.assertEqual(latest['version'],2)
        self.assertEqual(self.create(**args)['status'],'no_trade')
        changed=dict(snap,account_version='new-ownership',fills=[dict(execution_id='new',instrument_id='AAPL',side='buy',quantity='1',executed_at='2026-09-06T11:59:58Z')])
        changed=seal_execution_snapshot(changed)
        self.assertFalse(self.create(**dict(args,execution_snapshot=changed))['orders'])

    def test_fixed_swing_entry_reference_comes_from_frozen_buy_plan(self):
        from copilot.plan_store import record_strategy_state
        from _test_trade_plan import template_execution, template_research, template_adoption
        from _test_market_data import NOW as at
        adopt=template_adoption('swing_breakout','QQQ')
        snap=template_execution('QQQ')
        save_snapshot(snap,db_path=self.db,now=at)
        record_strategy_state(snap['snapshot_id'],adopt['rule_id'],'swing','确认该模式首次启动，此前没有实际成交',
            expected_account_version=snap['account_version'],no_prior_executions=True,db_path=self.db,now=at)
        card=self.create(execution_snapshot=snap,research_snapshot=template_research('QQQ'),adoption=adopt,
                         policy=policy(mode='swing',swing_rule_id=adopt['rule_id']),now=at)
        review_plan(card['plan_id'],statement='我已核对该卡并明确同意',expected_version=1,db_path=self.db,now=at)
        held=template_execution('QQQ')
        held.update(account_version='actual-entry',positions=[dict(instrument_id='QQQ',account_key='account-hash',con_id=1,currency='USD',quantity='3',avg_cost='128')])
        held=seal_execution_snapshot(held)
        save_snapshot(held,db_path=self.db,now=at)
        with self.assertRaisesRegex(ValueError,'Review.*observation'):
            record_mode_allocations(held['snapshot_id'],{'QQQ':{'swing':'3'}},'明确确认实际入场持仓归属',
                expected_account_version=held['account_version'],entry_sessions={'QQQ':{'swing':'2026-09-06'}},
                entry_plan_ids={'QQQ':{'swing':card['plan_id']}},db_path=self.db,now=at)
        held=seal_execution_snapshot(dict(held,as_of=(at+timedelta(seconds=1)).isoformat()))
        observed=at+timedelta(seconds=2)
        save_snapshot(held,db_path=self.db,now=observed)
        receipt=record_mode_allocations(held['snapshot_id'],{'QQQ':{'swing':'3'}},'明确确认实际入场持仓归属',
            expected_account_version=held['account_version'],entry_sessions={'QQQ':{'swing':'2026-09-06'}},
            entry_plan_ids={'QQQ':{'swing':card['plan_id']}},db_path=self.db,now=observed)
        self.assertEqual(receipt['entry_lots']['QQQ']['swing']['protective_reference'],card['orders'][0]['protective_reference'])
        self.assertEqual(receipt['entry_lots']['QQQ']['swing']['planned_quantity'],'7')
        with self.assertRaises(ValueError):
            record_mode_allocations(held['snapshot_id'],{'QQQ':{'swing':'3'}},'明确确认',
                expected_account_version=held['account_version'],entry_plan_ids={'QQQ':{'swing':'missing'}},db_path=self.db,now=observed)

    def test_unreviewed_entry_plan_cannot_become_actual_fixed_lot_protection(self):
        from copilot.plan_store import record_strategy_state
        from _test_trade_plan import template_execution, template_research, template_adoption
        from _test_market_data import NOW as at
        adopt=template_adoption('swing_breakout','QQQ')
        snap=template_execution('QQQ')
        save_snapshot(snap,db_path=self.db,now=at)
        record_strategy_state(snap['snapshot_id'],adopt['rule_id'],'swing','确认该模式首次启动，此前没有实际成交',
            expected_account_version=snap['account_version'],no_prior_executions=True,db_path=self.db,now=at)
        card=self.create(execution_snapshot=snap,research_snapshot=template_research('QQQ'),adoption=adopt,
                         policy=policy(mode='swing',swing_rule_id=adopt['rule_id']),now=at)
        held=template_execution('QQQ')
        held.update(account_version='actual-entry',positions=[dict(instrument_id='QQQ',account_key='account-hash',con_id=1,currency='USD',quantity='3',avg_cost='128')])
        held=seal_execution_snapshot(held)
        save_snapshot(held,db_path=self.db,now=at)
        with self.assertRaisesRegex(ValueError,'Review'):
            record_mode_allocations(held['snapshot_id'],{'QQQ':{'swing':'3'}},'明确确认实际入场持仓归属',
                expected_account_version=held['account_version'],entry_sessions={'QQQ':{'swing':'2026-09-06'}},
                entry_plan_ids={'QQQ':{'swing':card['plan_id']}},db_path=self.db,now=at)


if __name__ == '__main__':
    unittest.main()

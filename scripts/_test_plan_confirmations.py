"""Actual local confirmation seams; all account/strategy data are fixtures."""
import tempfile
import unittest
from pathlib import Path
from datetime import timedelta

from copilot.plan_store import (create_plan, review_plan, read_plan, save_snapshot,
                               record_cash_flow, record_mode_allocations, record_strategy_state)
from copilot.broker import seal_execution_snapshot
from _test_trade_plan import NOW, execution, adoption, policy
from _test_policy import engine_fixture


class ExplicitConfirmations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'isolated.sqlite'

    def card(self):
        return create_plan(execution_snapshot=execution(), research_snapshot=engine_fixture(),
                           adoption=adoption(), policy=policy(), db_path=self.db, now=NOW)

    def test_denied_conditional_quoted_and_requested_review_never_grants_approval(self):
        card = self.card()
        statements = ['I do not accept this plan', 'I refuse to approve this plan', '我不予批准这张交易卡',
                      '我暂不同意这个计划', 'I have not yet accepted this plan', 'Please confirm this plan',
                      '我会审核后再确认', 'I confirm this plan unless the market changes',
                      "'I approve this plan'", 'The user approves this plan', 'I disagree but confirm this plan',
                      'My broker approved this plan', '券商已经确认这张卡']
        statements += ['I will approve this plan', '我稍后会确认这张卡']
        for statement in statements:
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                review_plan(card['plan_id'], statement=statement, expected_version=1, db_path=self.db, now=NOW)
            self.assertEqual(read_plan(card['plan_id'], db_path=self.db, now=NOW)['version'], 1)
        reviewed = review_plan(card['plan_id'], statement='我确认本次调仓方案', expected_version=1,
                               db_path=self.db, now=NOW)
        self.assertEqual(reviewed['review_state'], 'reviewed')

    def test_refusal_does_not_confirm_cash_flow_modes_or_first_activation(self):
        initial = self.card()
        after = execution()
        after.update(as_of=(NOW + timedelta(seconds=1)).isoformat(), account_version='fixture-v2')
        after['positions'] = [dict(instrument_id='QQQ', account_key='account-hash', con_id=1,
                                  currency='USD', quantity='10', avg_cost='100')]
        after = seal_execution_snapshot(after)
        at = NOW + timedelta(seconds=2)
        save_snapshot(after, db_path=self.db, now=at)
        with self.assertRaises(ValueError):
            record_cash_flow(after['snapshot_id'], '0', 'I refuse to confirm zero external flows',
                             previous_snapshot_id=initial['execution_snapshot_id'], db_path=self.db, now=at)
        with self.assertRaises(ValueError):
            record_mode_allocations(after['snapshot_id'], {'QQQ': {'long_term': '10'}},
                'I refuse to confirm these allocations', expected_account_version=after['account_version'],
                db_path=self.db, now=at)
        with self.assertRaises(ValueError):
            record_strategy_state(after['snapshot_id'], adoption()['rule_id'], 'long_term',
                'I refuse to confirm first activation with no prior executions',
                expected_account_version=after['account_version'], no_prior_executions=True, db_path=self.db, now=at)
        receipt = record_cash_flow(after['snapshot_id'], '0', '明确确认区间无外部入出金',
                                  previous_snapshot_id=initial['execution_snapshot_id'], db_path=self.db, now=at)
        self.assertTrue(receipt['committed'])
        first = record_strategy_state(after['snapshot_id'], adoption()['rule_id'], 'long_term',
            'I confirm first activation with no prior executions', expected_account_version=after['account_version'],
            no_prior_executions=True, db_path=self.db, now=at)
        self.assertTrue(first['committed'])

    def test_explicit_rejection_cancellation_and_one_off_affirmation_are_supported(self):
        card = self.card()
        reviewed = review_plan(card['plan_id'], statement='I confirm this one-off allocation', expected_version=1,
                               db_path=self.db, now=NOW)
        cancelled = review_plan(card['plan_id'], statement='I abandon this plan', expected_version=reviewed['version'],
                                outcome='cancel', db_path=self.db, now=NOW)
        self.assertEqual(cancelled['review_state'], 'cancelled')
        self.db = Path(self.temp.name) / 'rejection.sqlite'
        card = self.card()
        for statement in ['我不拒绝这张卡', 'I do not decline this plan', 'I refuse to reject this plan']:
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                review_plan(card['plan_id'], statement=statement, expected_version=1,
                            outcome='reject', db_path=self.db, now=NOW)
        rejected = review_plan(card['plan_id'], statement='我不批准这张交易卡', expected_version=card['version'],
                               outcome='reject', db_path=self.db, now=NOW)
        self.assertEqual(rejected['review_state'], 'rejected')

    def test_original_refusal_of_approval_can_be_recorded_as_rejection_only(self):
        for index, statement in enumerate(['I refuse to approve this plan', '我不予批准这张交易卡',
                                            'I do not approve this plan']):
            self.db = Path(self.temp.name) / ('original-rejection-'+str(index)+'.sqlite')
            card = self.card()
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                review_plan(card['plan_id'], statement=statement, expected_version=1,
                            outcome='approve', db_path=self.db, now=NOW)
            rejected = review_plan(card['plan_id'], statement=statement, expected_version=1,
                outcome='reject', db_path=self.db, now=NOW)
            self.assertEqual(rejected['review_state'], 'rejected')
            self.assertEqual(rejected['version'], 2)
            self.assertEqual(rejected['review_events'][-1]['statement'], statement)
            self.assertFalse(rejected['order_submitted'])


if __name__ == '__main__':
    unittest.main()

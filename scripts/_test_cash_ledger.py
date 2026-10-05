"""Actual distributions are local cash receipts, never fills or external deposits."""
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import timedelta
from pathlib import Path

from _test_trade_plan import execution, NOW
from copilot import journal
from copilot.broker import seal_execution_snapshot
from copilot.plan_store import save_snapshot
from copilot.opening_balance import record_opening_balance
from copilot.cash_ledger import record_distribution, get_distributions


class Distributions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'new' / 'isolated.sqlite'
        observed = execution()
        observed['positions'] = [dict(instrument_id='QQQI', account_key='account-hash', con_id=101,
            security_type='STK', currency='USD', quantity='10', avg_cost='50')]
        observed = seal_execution_snapshot(observed)
        save_snapshot(observed, db_path=self.db, now=NOW)
        self.opened = record_opening_balance(observed['snapshot_id'],
            '我明确确认这些现有持仓作为期初持仓记录', 'opening-message', 'opening-key',
            expected_account_version=observed['account_version'],
            expected_portfolio_version=journal.get_context(db_path=self.db)['portfolio_version'],
            db_path=self.db, now=NOW)

    def payload(self, **changes):
        value = dict(statement='我确认QQQI分派已实际到账，净额10美元。', source_message_id='distribution-message',
            opening_balance_id=self.opened['operation_id'], instrument_id='QQQI', payment_date=NOW.date().isoformat(),
            currency='USD', net_amount='10', gross_amount=None, withholding_amount=None)
        value.update(changes)
        return value

    def record(self, value=None, key='distribution-key', **changes):
        return record_distribution(value or self.payload(), key, db_path=self.db, now=changes.get('now', NOW))

    def test_actual_arrival_keeps_unknown_tax_and_does_not_change_shares_cashflow_or_cadence(self):
        before = journal.get_context(db_path=self.db)
        receipt = self.record()
        self.assertTrue(receipt['committed'])
        self.assertFalse(receipt['affects_holdings'])
        self.assertFalse(receipt['affects_external_cash_flow'])
        self.assertFalse(receipt['advances_strategy_cadence'])
        stored = get_distributions(db_path=self.db)[0]
        self.assertIsNone(stored['operation']['gross_amount'])
        self.assertIsNone(stored['operation']['withholding_amount'])
        self.assertEqual(stored['operation']['net_amount'], '10')
        after = journal.get_context(db_path=self.db)
        self.assertEqual(before['portfolio_version'], after['portfolio_version'])
        self.assertEqual(before['holdings'], after['holdings'])
        self.assertIsNone(after['last_completed_execution_at'])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM advisor_cash_flow_confirmations').fetchone()[0], 0)

    def test_announced_future_negated_conditional_attributed_and_quoted_receipts_rejected(self):
        texts = ['基金宣布将分派10美元', '我确认QQQI明天会到账10美元', '我确认QQQI分派没有实际到账',
            '如果QQQI实际到账我就确认', '他说我已收到QQQI分派', '“我已收到QQQI分派”',
            'I have not received the distribution', 'I will confirm the distribution has been received',
            'I confirm the dividend will be credited tomorrow', 'My broker confirmed the distribution was received',
            'I might have received the dividend', 'I received a distribution?']
        for i, text in enumerate(texts):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.record(self.payload(statement=text), 'rejected-'+str(i))
        self.assertEqual(get_distributions(db_path=self.db), [])
        self.assertEqual(self.record(self.payload(statement='I have actually received the QQQI distribution.'), 'actual')['status'], 'received')

    def test_exact_retry_concurrent_commit_and_conflicting_key(self):
        value = self.payload()
        with ThreadPoolExecutor(max_workers=8) as pool:
            receipts = list(pool.map(lambda _: self.record(value), range(16)))
        self.assertEqual(len({r['operation_id'] for r in receipts}), 1)
        self.assertEqual(sum(not r['replayed'] for r in receipts), 1)
        self.assertEqual(len(get_distributions(db_path=self.db)), 1)
        with self.assertRaises(journal.JournalConflict):
            self.record(self.payload(net_amount='11'))

    def test_external_id_duplicates_and_supplement_are_not_new_income(self):
        first = self.record(self.payload(external_payment_id='payment-1'))
        duplicate = self.record(self.payload(external_payment_id='payment-1', source_message_id='repeated'), 'new-message')
        self.assertEqual(first['operation_id'], duplicate['operation_id'])
        self.assertTrue(duplicate['external_duplicate'])
        with self.assertRaises(journal.JournalConflict):
            self.record(self.payload(external_payment_id='payment-1', net_amount='11'), 'conflicting-payment')
        with self.assertRaises(journal.JournalConflict):
            self.record(self.payload(gross_amount='12', withholding_amount='2', source_message_id='tax-details'), 'tax-details')
        other = self.record(self.payload(external_payment_id='payment-2', source_message_id='other-payment'), 'other-payment')
        self.assertNotEqual(first['operation_id'], other['operation_id'])
        self.assertEqual(len(get_distributions(db_path=self.db)), 2)

    def test_versioned_correction_and_reversal_retain_immutable_source(self):
        first = self.record()
        amend = dict(event_type='correct', operation_id=first['operation_id'], expected_version=1,
            statement='更正这笔已实际到账分派的税额', source_message_id='tax-correction',
            gross_amount='12', withholding_amount='2')
        changed = self.record(amend, 'correction', now=NOW+timedelta(seconds=1))
        self.assertEqual(changed['version'], 2)
        self.assertEqual(get_distributions(db_path=self.db)[0]['operation']['gross_amount'], '12')
        with self.assertRaises(journal.JournalConflict):
            self.record(amend, 'stale-correction')
        self.assertIsNone(get_distributions(db_path=self.db, as_of=NOW)[0]['operation']['gross_amount'])
        reverse = dict(event_type='reverse', operation_id=first['operation_id'], expected_version=2,
            statement='撤销这条重复的分派到账记录', source_message_id='reverse-message')
        self.assertEqual(self.record(reverse, 'reverse', now=NOW+timedelta(seconds=2))['status'], 'reversed')
        rows = get_distributions(db_path=self.db)
        self.assertEqual(rows[0]['status'], 'reversed')
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM cash_distribution_events').fetchone()[0], 3)
            original = conn.execute('SELECT payload FROM cash_distribution_events ORDER BY sequence LIMIT 1').fetchone()[0]
            self.assertIn('distribution-message', original)
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute('DELETE FROM cash_distribution_events')
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute("UPDATE cash_distribution_events SET payload='{}'")

    def test_amounts_dates_and_opening_identity_fail_closed(self):
        for bad in [dict(gross_amount='12', withholding_amount='1'), dict(net_amount=10), dict(net_amount='NaN'),
                    dict(withholding_amount='-1'), dict(currency='XXX'), dict(payment_date=(NOW+timedelta(days=2)).date().isoformat()),
                    dict(opening_balance_id='missing'), dict(net_amount='0'), dict(instrument_id='INVALID SYMBOL')]:
            with self.subTest(bad=bad), self.assertRaises((ValueError, KeyError)):
                self.record(self.payload(**bad), 'bad-'+str(bad))
        self.assertEqual(get_distributions(db_path=self.db), [])
        zero = self.record(self.payload(net_amount='0', gross_amount='10', withholding_amount='10'), 'fully-withheld')
        self.assertEqual(zero['status'], 'received')

    def test_correction_cannot_rebind_account_or_claim_future_receipt(self):
        first = self.record()
        for patch in [dict(opening_balance_id='different'), dict(payment_date=(NOW+timedelta(days=2)).date().isoformat()),
                      dict(statement='如果更正这笔已到账分派')]:
            amend = dict(event_type='correct', operation_id=first['operation_id'], expected_version=1,
                statement='更正这笔已实际到账分派', source_message_id='invalid-amendment', **{k:v for k,v in patch.items() if k!='statement'})
            if 'statement' in patch:
                amend['statement'] = patch['statement']
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                self.record(amend, 'amend-'+str(patch))
        self.assertEqual(get_distributions(db_path=self.db)[0]['version'], 1)

    def test_correction_cannot_silently_create_an_economic_duplicate(self):
        first = self.record()
        second = self.record(self.payload(net_amount='11', source_message_id='second-receipt'), 'second-receipt')
        with self.assertRaises(journal.JournalConflict):
            self.record(dict(event_type='correct', operation_id=second['operation_id'], expected_version=1,
                statement='更正实际到账分派净额为10美元', source_message_id='same-net-correction', net_amount='10'), 'same-net-correction')
        self.assertEqual({row['operation']['net_amount'] for row in get_distributions(db_path=self.db)}, {'10', '11'})
        self.assertNotEqual(first['operation_id'], second['operation_id'])


if __name__ == '__main__':
    unittest.main()

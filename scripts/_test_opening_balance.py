"""Opening observations are not historical fills; use disposable account snapshots."""
import tempfile
import hashlib
import unittest
from pathlib import Path
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from copilot import journal
from copilot.broker import seal_execution_snapshot
from copilot.plan_store import save_snapshot
from copilot.opening_balance import record_opening_balance
from _test_trade_plan import execution, NOW


class OpeningBalances(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / 'isolated.sqlite'
        self.observed = execution()
        self.observed['positions'] = [dict(instrument_id='QQQI', account_key='account-hash', con_id=101,
            security_type='STK', currency='USD', quantity='10', avg_cost='50')]
        self.observed = seal_execution_snapshot(self.observed)
        save_snapshot(self.observed, db_path=self.db, now=NOW)

    def record(self, **changes):
        args = dict(execution_snapshot_id=self.observed['snapshot_id'], statement='我明确确认这些现有持仓作为期初持仓记录',
            source_message_id='opening-user-message', idempotency_key='opening-request',
            expected_account_version=self.observed['account_version'],
            expected_portfolio_version=journal.get_context(db_path=self.db)['portfolio_version'], db_path=self.db, now=NOW)
        args.update(changes)
        return record_opening_balance(**args)

    def test_initial_observation_is_unknown_basis_without_fictitious_trade_or_cadence(self):
        receipt = self.record()
        self.assertEqual(receipt['status'], 'opening_balance')
        self.assertTrue(receipt['committed'])
        context = journal.get_context(db_path=self.db)
        self.assertEqual(context['holdings'][0]['quantity'], '10')
        self.assertIsNone(context['holdings'][0]['gross_cost_basis'])
        self.assertIsNone(context['holdings'][0]['cost_basis_including_fees'])
        self.assertIsNone(context['last_completed_execution_at'])
        operation = context['operations'][0]['operation']
        self.assertNotIn('occurred_at', operation)
        self.assertNotIn('side', operation)
        self.assertEqual(operation['observed_as_of'], self.observed['as_of'])
        self.assertFalse(context['portfolio_complete'])

    def fill(self, receipt, **changes):
        operation = dict(statement='I already bought 2 QQQI shares.', source_message_id='actual-fill-message',
            execution_status='executed', instrument_id='QQQI', side='buy', quantity='2', unit='share',
            price='51', currency='USD', occurred_at=(NOW+timedelta(seconds=1)).isoformat(), fees='1',
            opening_balance_id=receipt['operation_id'])
        operation.update(changes)
        return journal.record_operation(operation, 'actual-fill-'+operation['source_message_id'],
                                        db_path=self.db, now=NOW+timedelta(seconds=2))

    def test_later_actual_fill_uses_public_opening_id_and_updates_cadence_only_then(self):
        opened = self.record()
        executed = self.fill(opened)
        self.assertEqual(executed['status'], 'executed')
        context = journal.get_context(['QQQI'], db_path=self.db)
        self.assertEqual(context['holdings'][0]['quantity'], '12')
        self.assertIsNone(context['holdings'][0]['gross_cost_basis'])
        self.assertIsNotNone(context['last_completed_execution_at'])
        self.assertEqual(len(context['operations']), 2)

    def test_retired_opening_does_not_prevent_existing_fill_fee_correction(self):
        opened = self.record()
        executed = self.fill(opened, fees=None)
        self.record(event_type='reverse', operation_id=opened['operation_id'], expected_version=1,
            idempotency_key='retire-opening', statement='我明确确认撤销这条现有持仓期初记录',
            source_message_id='retire-message')
        corrected = journal.record_operation(dict(event_type='correct', operation_id=executed['operation_id'],
            expected_version=1, statement='更正实际成交手续费为1美元', source_message_id='late-fee', fees='1'),
            'late-fee', db_path=self.db, now=NOW+timedelta(seconds=3))
        self.assertEqual(corrected['status'], 'executed')
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '2')
        with self.assertRaises(ValueError):
            self.fill(opened, source_message_id='new-after-retirement')

    def test_already_included_and_unbound_account_fills_cannot_double_opening(self):
        opened = self.record()
        with self.assertRaises(journal.JournalConflict):
            self.fill(opened, occurred_at='2026-09-04')
        with self.assertRaises(journal.JournalConflict):
            self.fill(opened, opening_balance_id=None)
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '10')

    def test_exact_retry_after_expiry_and_new_key_duplicate_start_are_distinct(self):
        initial = journal.get_context(db_path=self.db)['portfolio_version']
        opened = self.record(expected_portfolio_version=initial)
        retry = self.record(expected_portfolio_version=initial, now=NOW+timedelta(hours=1))
        self.assertTrue(retry['replayed'])
        self.assertEqual(retry['operation_id'], opened['operation_id'])
        with self.assertRaises(journal.JournalConflict):
            self.record(idempotency_key='another-opening')
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '10')

    def test_correction_replaces_instead_of_adds_then_reversal_removes_observation(self):
        opened = self.record()
        updated = dict(self.observed)
        updated['positions'] = [dict(self.observed['positions'][0], quantity='12')]
        updated.update(as_of=(NOW+timedelta(seconds=1)).isoformat(), account_version='fixture-v2')
        self.observed = seal_execution_snapshot(updated)
        save_snapshot(self.observed, db_path=self.db, now=NOW+timedelta(seconds=2))
        corrected = self.record(event_type='correct', operation_id=opened['operation_id'], expected_version=1,
            statement='我确认更正期初持仓，按新观察记录数量', source_message_id='opening-correction',
            idempotency_key='opening-correction', now=NOW+timedelta(seconds=2))
        self.assertEqual(corrected['version'], 2)
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '12')
        with self.assertRaises(journal.JournalConflict):
            self.record(event_type='correct', operation_id=opened['operation_id'], expected_version=1,
                statement='我确认更正期初持仓', source_message_id='stale', idempotency_key='stale', now=NOW+timedelta(seconds=2))
        reversed_ = self.record(event_type='reverse', operation_id=opened['operation_id'], expected_version=2,
            statement='我确认撤销期初持仓记录', source_message_id='opening-reversal', idempotency_key='opening-reversal',
            now=NOW+timedelta(hours=1))
        self.assertEqual(reversed_['status'], 'reversed')
        context = journal.get_context(db_path=self.db)
        self.assertEqual(context['holdings'], [])
        self.assertIsNone(context['last_completed_execution_at'])

    def test_current_snapshot_cannot_reset_opening_after_recorded_fill(self):
        opened = self.record()
        self.fill(opened)
        with self.assertRaises(journal.JournalConflict):
            self.record(event_type='correct', operation_id=opened['operation_id'], expected_version=1,
                statement='我确认更正期初持仓', source_message_id='unsafe-reset', idempotency_key='unsafe-reset')
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '12')
        self.record(event_type='reverse', operation_id=opened['operation_id'], expected_version=1,
            statement='我确认撤销期初持仓记录', source_message_id='reversal', idempotency_key='reversal')
        holding = journal.get_context(db_path=self.db)['holdings'][0]
        self.assertEqual(holding['quantity'], '2')
        self.assertTrue(holding['opening_balance_required'])

    def test_quote_subscription_gaps_do_not_require_inventing_opening_prices(self):
        source = dict(self.observed)
        source.update(status='blocked', complete=False, issues=['quote_subscription_unavailable'], valid_until=NOW.isoformat())
        source['coverage'] = {**source['coverage'], 'quotes': {'complete': False}}
        source['quotes'] = {}
        self.observed = seal_execution_snapshot(source)
        save_snapshot(self.observed, db_path=self.db, now=NOW)
        receipt = self.record()
        self.assertEqual(receipt['status'], 'opening_balance')
        self.assertIsNone(journal.get_context(db_path=self.db)['holdings'][0]['gross_cost_basis'])

    def test_denials_and_missing_account_coverage_do_not_mutate_holdings(self):
        for statement in ['I refuse to confirm opening holdings', '如果我确认期初持仓', '请确认现有持仓']:
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                self.record(statement=statement)
        source = dict(self.observed)
        source['coverage'] = {**source['coverage'], 'positions': {'complete': False}}
        self.observed = seal_execution_snapshot(source)
        save_snapshot(self.observed, db_path=self.db, now=NOW)
        with self.assertRaises(ValueError):
            self.record()
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'], [])

    def test_account_and_portfolio_versions_and_newer_snapshot_are_checked(self):
        with self.assertRaises(journal.JournalConflict):
            self.record(expected_account_version='different-account-version')
        with self.assertRaises(journal.JournalConflict):
            self.record(expected_portfolio_version='different-portfolio-version')
        newer = dict(self.observed)
        newer.update(as_of=(NOW+timedelta(seconds=1)).isoformat(), account_version='newer-version')
        newer = seal_execution_snapshot(newer)
        save_snapshot(newer, db_path=self.db, now=NOW+timedelta(seconds=2))
        with self.assertRaises(journal.JournalConflict):
            self.record(now=NOW+timedelta(seconds=2))

    def test_existing_account_fill_and_expired_observation_cannot_become_new_start(self):
        with self.assertRaises(ValueError):
            self.record(now=NOW+timedelta(minutes=6))
        journal.record_operation(dict(statement='I already bought 2 QQQI.', source_message_id='older-fill',
            execution_status='executed', instrument_id='QQQI', side='buy', quantity='2', unit='share',
            price='50', currency='USD', occurred_at='2026-09-04', account_id='account-hash'),
            'older-fill', db_path=self.db, now=NOW)
        with self.assertRaises(journal.JournalConflict):
            self.record()
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '2')

    def test_legacy_raw_account_identity_cannot_duplicate_the_same_broker_account(self):
        raw_account = 'isolated-broker-identity'
        key = 'acct-' + hashlib.sha256(raw_account.encode()).hexdigest()[:24]
        source = dict(self.observed)
        source['account'] = {**source['account'], 'account_key': key}
        source['positions'] = [dict(source['positions'][0], account_key=key)]
        self.observed = seal_execution_snapshot(source)
        save_snapshot(self.observed, db_path=self.db, now=NOW)
        journal.record_operation(dict(statement='I already bought 2 QQQI.', source_message_id='legacy-fill',
            execution_status='executed', instrument_id='QQQI', side='buy', quantity='2', unit='share',
            price='50', currency='USD', occurred_at='2026-09-04', account_id=raw_account),
            'legacy-fill', db_path=self.db, now=NOW)
        with self.assertRaises(journal.JournalConflict):
            self.record()

    def test_newer_unknown_account_observation_cannot_reuse_older_quantity_binding(self):
        newer = dict(self.observed)
        newer.update(as_of=(NOW+timedelta(seconds=1)).isoformat(), status='blocked', complete=False,
                     issues=['incomplete:positions'])
        newer['coverage'] = {**newer['coverage'], 'positions': {'complete': False}}
        newer = seal_execution_snapshot(newer)
        save_snapshot(newer, db_path=self.db, now=NOW+timedelta(seconds=2))
        with self.assertRaises(journal.JournalConflict):
            self.record(now=NOW+timedelta(seconds=2))

    def test_concurrent_identical_requests_commit_one_opening(self):
        initial = journal.get_context(db_path=self.db)['portfolio_version']
        with ThreadPoolExecutor(max_workers=4) as executor:
            receipts = list(executor.map(lambda _: self.record(expected_portfolio_version=initial), range(8)))
        self.assertEqual(len({r['operation_id'] for r in receipts}), 1)
        self.assertEqual(sum(not r['replayed'] for r in receipts), 1)
        self.assertEqual(journal.get_context(db_path=self.db)['holdings'][0]['quantity'], '10')


if __name__ == '__main__':
    unittest.main()

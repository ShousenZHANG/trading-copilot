"""Offline transaction/runtime tests; every database lives in TemporaryDirectory.

Run ``python scripts/_test_journal.py`` or ``python scripts/copilot/journal.py
--self-test``. No actual account, trade, or legacy trading_memory is modified.
"""

from __future__ import annotations

import json
import sqlite3
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot import journal


class JournalTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.db = Path(self.directory.name) / "private" / "copilot.sqlite"
        self.now = "2026-09-06T12:00:00Z"

    def operation(self, **overrides):
        result = {"statement": "我已经买了 10 股 QQQ，成交价 500 美元，9月4日成交。", "execution_status": "executed", "instrument_id": "QQQ", "side": "buy", "quantity": "10", "unit": "share", "price": "500", "currency": "USD", "occurred_at": "2026-09-04", "source_message_id": "thread-a-message-1", "account_id": "broker-a", "fees": None}
        result.update(overrides)
        return result

    def record(self, operation=None, key="retry-key", **kwargs):
        return journal.record_operation(operation or self.operation(), key, db_path=self.db, now=kwargs.pop("now", self.now), **kwargs)

    def context(self, **kwargs):
        return journal.get_context(db_path=self.db, **kwargs)

    def test_executed_receipt_decimal_holdings_and_unknown_fees(self):
        receipt = self.record(self.operation(quantity="0.1", price="0.2"))
        holding = self.context()["holdings"][0]
        self.assertEqual(receipt["status"], "executed")
        self.assertEqual(holding["gross_cost_basis"], "0.02")
        self.assertIsNone(holding["cost_basis_including_fees"])
        self.assertTrue(holding["fees_unknown"])
        self.assertFalse(self.context()["portfolio_complete"])

    def test_lost_response_retry_and_payload_mismatch(self):
        first = self.record()
        replay = self.record()
        self.assertTrue(replay["replayed"])
        self.assertEqual(first["operation_id"], replay["operation_id"])
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")
        with self.assertRaises(journal.JournalConflict):
            self.record(self.operation(quantity="11"))

    def test_concurrent_requests_commit_once(self):
        with ThreadPoolExecutor(max_workers=8) as executor:
            receipts = list(executor.map(lambda _: self.record(), range(24)))
        self.assertEqual(len({item["operation_id"] for item in receipts}), 1)
        self.assertEqual(sum(not item["replayed"] for item in receipts), 1)
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_events").fetchone()[0], 1)

    def test_pending_completion_atomic_and_versioned(self):
        initial = self.context()["portfolio_version"]
        pending = self.record(self.operation(quantity=None))
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(initial, pending["portfolio_version"])
        self.assertEqual(self.context()["holdings"], [])
        completed = self.record({"event_type": "complete", "operation_id": pending["operation_id"], "expected_version": 1, "statement": "补充数量：10 股。", "source_message_id": "thread-a-message-2", "quantity": "10"}, "complete-key")
        self.assertEqual(completed["status"], "executed")
        self.assertEqual(completed["version"], 2)
        self.assertNotEqual(initial, completed["portfolio_version"])
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_events").fetchone()[0], 2)

    def test_intent_is_not_fill_and_needs_execution_confirmation(self):
        intent = self.record(self.operation(statement="我想买 QQQ", execution_status="intent"))
        self.assertEqual(intent["status"], "intent")
        self.assertEqual(self.context()["holdings"], [])
        pending = self.record({"event_type": "complete", "operation_id": intent["operation_id"], "expected_version": 1, "statement": "数量是10", "source_message_id": "m2", "execution_status": "executed"}, "to-pending")
        self.assertEqual(pending["status"], "pending")
        self.assertIn("execution_confirmation", pending["missing_fields"])
        executed = self.record({"event_type": "complete", "operation_id": intent["operation_id"], "expected_version": 2, "statement": "我已经买了 QQQ。", "source_message_id": "m3"}, "to-fill")
        self.assertEqual(executed["status"], "executed")

    def test_negated_quoted_hypothetical_attributed_and_side_mismatch_pending(self):
        statements = ["我没有买了 QQQ", "建议：我已经买了 QQQ", "如果我已经买了 QQQ", "他说他已经买了 QQQ", "“我已经买了 QQQ”", "I did not buy QQQ", "If I bought QQQ", "I would have bought QQQ", "I sold QQQ", "我已经卖了 QQQ", "我以为买了，但订单未成交", "我买了 QQQ 吗？", "我取消了买入成交", "'I bought 10 QQQ'", "Someone told me I bought QQQ"]
        for index, statement in enumerate(statements):
            with self.subTest(statement=statement):
                result = self.record(self.operation(statement=statement), f"ambiguous-{index}")
                self.assertEqual(result["status"], "pending")
                self.assertIn("execution_confirmation", result["missing_fields"])
        self.assertEqual(self.context()["holdings"], [])

    def test_english_confirmed_execution(self):
        self.assertEqual(self.record(self.operation(statement="I have just bought 10 shares of QQQ."))["status"], "executed")

    def test_correction_conflict_append_only_and_reversal(self):
        receipt = self.record()
        update = {"event_type": "correct", "operation_id": receipt["operation_id"], "expected_version": 1, "statement": "更正价格：501 美元", "source_message_id": "m2", "price": "501"}
        corrected = self.record(update, "correction")
        self.assertNotEqual(receipt["portfolio_version"], corrected["portfolio_version"])
        self.assertEqual(self.context()["holdings"][0]["gross_cost_basis"], "5010")
        with self.assertRaises(journal.JournalConflict):
            self.record(update, "stale-correction")
        reversed_ = self.record({"event_type": "reverse", "operation_id": receipt["operation_id"], "expected_version": 2, "statement": "撤销这条记录，重复了。", "source_message_id": "m3"}, "reversal")
        self.assertEqual(reversed_["status"], "reversed")
        self.assertEqual(self.context()["holdings"], [])
        self.assertNotEqual(journal._hash([]), reversed_["portfolio_version"])
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_events").fetchone()[0], 3)
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("DELETE FROM journal_events")
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute("UPDATE journal_events SET state='{}'")

    def test_pending_cannot_remove_confirmed_holding(self):
        receipt = self.record()
        with self.assertRaises(journal.JournalConflict):
            self.record({"event_type": "correct", "operation_id": receipt["operation_id"], "expected_version": 1, "statement": "更正", "source_message_id": "m2", "quantity": None}, "bad-correction")
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")

    def test_negated_reversal_and_hypothetical_correction_cannot_change_holding(self):
        receipt = self.record()
        for event_type, statement in [("reverse", "不要撤销这笔记录"), ("correct", "如果更正价格为600"), ("reverse", "建议撤销这条记录")]:
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                self.record({"event_type": event_type, "operation_id": receipt["operation_id"], "expected_version": 1, "statement": statement, "source_message_id": "amend-msg", "price": "600"}, event_type + statement)
        self.assertEqual(self.context()["holdings"][0]["gross_cost_basis"], "5000")

    def test_same_content_distinct_actual_trade_ids_both_count(self):
        self.record(self.operation(external_trade_id="fill-1"), "request-1")
        second = self.record(self.operation(external_trade_id="fill-2", source_message_id="thread-b-m1"), "request-2")
        self.assertEqual(second["duplicate_candidates"], [])
        self.assertEqual(self.context()["holdings"][0]["quantity"], "20")

    def test_same_content_without_trade_id_pending_duplicate_until_distinguished(self):
        first = self.record()
        second = self.record(self.operation(source_message_id="thread-b-m1"), "separate-message")
        self.assertEqual(second["duplicate_candidates"], [first["operation_id"]])
        self.assertEqual(second["status"], "pending_duplicate")
        self.assertEqual(second["portfolio_version"], first["portfolio_version"])
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")
        resolved = self.record({"event_type": "complete", "operation_id": second["operation_id"], "expected_version": 1, "statement": "这是另一笔成交，确实又买了10股。", "source_message_id": "distinct-msg", "distinct_confirmation": True}, "distinct")
        self.assertEqual(resolved["status"], "executed")
        self.assertEqual(self.context()["holdings"][0]["quantity"], "20")

    def test_pending_duplicate_linked_without_second_holding(self):
        first = self.record()
        second = self.record(self.operation(source_message_id="other-thread"), "repeat")
        linked = self.record({"event_type": "complete", "operation_id": second["operation_id"], "expected_version": 1, "statement": "这两条记录是同一笔成交，重复了。", "source_message_id": "link-msg", "duplicate_of": first["operation_id"]}, "link")
        self.assertEqual(linked["status"], "duplicate_linked")
        self.assertEqual(linked["duplicate_of"], first["operation_id"])
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")
        self.assertEqual(self.context()["pending_operations"], [])

    def test_duplicate_distinct_flag_alone_cannot_forge_confirmation(self):
        self.record()
        second = self.record(self.operation(source_message_id="other-thread"), "repeat")
        unresolved = self.record({"event_type": "complete", "operation_id": second["operation_id"], "expected_version": 1, "statement": "我已经买了 QQQ。", "source_message_id": "unconfirmed-msg", "distinct_confirmation": True}, "unconfirmed")
        self.assertEqual(unresolved["status"], "pending_duplicate")
        self.assertEqual(self.context()["holdings"][0]["quantity"], "10")

    def test_same_external_trade_id_unique_within_account(self):
        first = self.record(self.operation(external_trade_id="fill-1"))
        replay = self.record(self.operation(external_trade_id="fill-1", source_message_id="thread-b-m1"), "new-request")
        self.assertEqual(first["operation_id"], replay["operation_id"])
        self.assertTrue(replay["external_duplicate"])
        with self.assertRaises(journal.JournalConflict):
            self.record(self.operation(external_trade_id="fill-1", price="502"), "conflicting-fill")
        self.record(self.operation(external_trade_id="fill-1", account_id="broker-b"), "other-account")
        self.assertEqual(sum(int(item["quantity"]) for item in self.context()["holdings"]), 20)

    def test_gold_items_cny_and_usd_never_mix(self):
        self.record()
        gold = self.operation(statement="我已经买了两枚金币。", instrument_id="GOLD.CNY", quantity="2", unit="item", price="60000", price_basis="total", currency="CNY", fees="100", merchant="银行", purity="0.9999", weight_grams="31.1034768", account_id=None)
        self.record(gold, "gold")
        context = self.context()
        self.assertEqual(set(context["holdings_by_currency"]), {"USD", "CNY"})
        holding = context["holdings_by_currency"]["CNY"][0]
        self.assertEqual(holding["gross_cost_basis"], "60000")
        self.assertEqual(holding["cost_basis_including_fees"], "60100")
        self.assertEqual(holding["pure_gold_grams"], "62.20073290464")
        self.assertIsNone(context["portfolio_value"])
        self.assertIsNone(context["base_currency"])

    def test_gold_requires_weight_purity_merchant_and_correct_units(self):
        pending = self.record(self.operation(statement="我已经买了金币", instrument_id="GOLD.CNY", unit="item", currency="CNY"))
        self.assertTrue({"weight_grams", "purity", "merchant"}.issubset(pending["missing_fields"]))
        with self.assertRaises(ValueError):
            self.record(self.operation(instrument_id="GOLD.CNY"), "bad-units")

    def test_sell_without_opening_balance_retains_unknown_cost(self):
        self.record(self.operation(statement="我已经卖了 QQQ", side="sell"))
        holding = self.context()["holdings"][0]
        self.assertEqual(holding["quantity"], "-10")
        self.assertTrue(holding["opening_balance_required"])
        self.assertIsNone(holding["gross_cost_basis"])

    def test_sell_cost_basis_is_weighted_average(self):
        self.record(self.operation(fees="10"))
        self.record(self.operation(statement="我已经卖了4股QQQ", side="sell", quantity="4", price="600", fees="3"), "sell")
        holding = self.context()["holdings"][0]
        self.assertEqual(holding["quantity"], "6")
        self.assertEqual(holding["gross_cost_basis"], "3000")
        self.assertEqual(holding["cost_basis_including_fees"], "3006")

    def test_as_of_preserves_known_at_time_not_later_corrections(self):
        receipt = self.record(now="2026-09-05T12:00:00Z")
        self.record({"event_type": "correct", "operation_id": receipt["operation_id"], "expected_version": 1, "statement": "价格更正为 600", "source_message_id": "m2", "price": "600"}, "correction")
        self.assertEqual(self.context(as_of="2026-09-05")["holdings"][0]["gross_cost_basis"], "5000")
        self.assertEqual(self.context(as_of="2026-09-04")["holdings"], [])
        self.assertEqual(self.context()["holdings"][0]["gross_cost_basis"], "6000")

    def test_invalid_numerics_dates_and_instruments_never_record(self):
        for field, value in [("quantity", 1.1), ("quantity", "NaN"), ("price", "Infinity"), ("price", "-1"), ("quantity", "0"), ("fees", "-1"), ("occurred_at", "2027-01-01"), ("occurred_at", "2026-09-04T12:00:00"), ("instrument_id", "^NDX"), ("purity", "99.99")]:
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.record(self.operation(**{field: value}), f"invalid-{field}-{value}")
        self.assertEqual(self.context()["holdings"], [])

    def test_projection_error_rolls_back_event_request_and_holding(self):
        self.context()
        with patch.object(journal, "_rebuild_projection", side_effect=sqlite3.OperationalError("disk is full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.record()
        with closing(sqlite3.connect(self.db)) as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_events").fetchone()[0], 0)
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM journal_requests").fetchone()[0], 0)
        self.assertEqual(self.record()["status"], "executed")

    def test_commit_failure_never_returns_success(self):
        class CommitFailure(sqlite3.Connection):
            def execute(self, sql, *args, **kwargs):
                if sql == "COMMIT":
                    raise sqlite3.OperationalError("simulated disk I/O failure at commit")
                return super().execute(sql, *args, **kwargs)
        real_connect = sqlite3.connect
        with patch.object(journal.sqlite3, "connect", side_effect=lambda *args, **kwargs: real_connect(*args, factory=CommitFailure, **kwargs)):
            with self.assertRaises(sqlite3.OperationalError):
                self.record()
        self.assertEqual(self.context()["holdings"], [])

    def test_recommendation_snapshot_links_and_immutability(self):
        snapshot = {"snapshot_id": "snapshot-1", "instrument_id": "QQQ", "prices": ["500"]}
        saved = journal.save_snapshot(snapshot, db_path=self.db)
        self.assertTrue(saved["recorded"])
        self.assertEqual(journal.load_snapshot("snapshot-1", db_path=self.db), snapshot)
        with self.assertRaises(journal.JournalConflict):
            journal.save_snapshot(dict(snapshot, prices=["600"]), db_path=self.db)
        decision = {"decision_id": "decision-1", "instrument_id": "QQQ", "snapshot_id": "snapshot-1", "portfolio_version": self.context()["portfolio_version"], "action": "reduce", "rating_scale": "advisor", "rating": "Reduce"}
        journal.record_recommendation(decision, db_path=self.db)
        self.assertEqual(journal.load_decision("decision-1", db_path=self.db), decision)
        self.assertEqual(self.context()["holdings"], [])
        self.assertTrue(self.context()["recommendations"][0]["portfolio_current"])
        self.record()
        self.assertFalse(self.context()["recommendations"][0]["portfolio_current"])
        with self.assertRaises(journal.JournalConflict):
            journal.record_recommendation(dict(decision, action="buy"), db_path=self.db)
        with self.assertRaises(ValueError):
            journal.record_recommendation(dict(decision, decision_id="missing-snapshot", snapshot_id="missing"), db_path=self.db)

    def test_backup_roundtrip_and_no_overwrite(self):
        self.record()
        destination = Path(self.directory.name) / "backup.sqlite"
        journal.backup(destination, db_path=self.db)
        self.assertEqual(journal.get_context(db_path=destination), self.context())
        with self.assertRaises(FileExistsError):
            journal.backup(destination, db_path=self.db)
        with self.assertRaises(ValueError):
            journal.backup(self.db, db_path=self.db)

    def test_recommendation_rejects_portfolio_changed_before_commit(self):
        journal.save_snapshot({"snapshot_id": "s1", "instrument_id": "QQQ"}, db_path=self.db)
        proposal = {"snapshot_id": "s1", "instrument_id": "QQQ", "portfolio_version": self.context()["portfolio_version"], "action": "hold", "decision_id": "stale-context"}
        # Another runtime commits after service.context, before decision insertion.
        self.record()
        with self.assertRaises(journal.JournalConflict):
            journal.record_recommendation(proposal, db_path=self.db)
        self.assertEqual(self.context()["recommendations"], [])

    def test_context_filter_keeps_full_portfolio_version(self):
        self.record()
        self.record(self.operation(instrument_id="AAPL"), "apple")
        context = self.context(instrument_ids=["QQQ"])
        self.assertEqual(len(context["holdings"]), 1)
        self.assertEqual(context["portfolio_version"], self.context()["portfolio_version"])


if __name__ == "__main__":
    unittest.main(verbosity=2)

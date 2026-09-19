"""Shared facade tests: isolated databases, no live network or personal operations."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copilot import journal, service
from _test_policy import NOW, fixture, proposal, seal

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "copilot.sqlite"

    def test_collect_persists_exact_snapshot(self):
        value = fixture()
        with patch("copilot.market_data.collect_snapshot", return_value=value), patch.object(service, "load_credentials"):
            collected = service.collect(["QQQ"], db_path=self.db, research=False)
        self.assertEqual(service.snapshot(collected["snapshot_id"], db_path=self.db), value)
        reviewed = service.review(value["snapshot_id"], proposal(), db_path=self.db, now=NOW)
        self.assertEqual(reviewed["decision"]["execution_scope"], "research_only")
        self.assertNotIn("quantity", reviewed["decision"])
        self.assertEqual(service.context(db_path=self.db)["holdings"], [])

    def test_unapproved_action_never_leaks_into_rendered_action(self):
        value = fixture()
        value["instruments"]["QQQ"]["quality_status"] = "unknown"
        seal(value)
        journal.save_snapshot(value, db_path=self.db)
        reviewed = service.review(value["snapshot_id"], proposal(), db_path=self.db, now=NOW)
        self.assertEqual(reviewed["decision"]["action"], "data_insufficient")
        self.assertIn("暂停具体建议", reviewed["message"])
        self.assertNotIn("可考虑买入", reviewed["message"])

    def test_unknown_snapshot_and_forged_ids_rejected(self):
        with self.assertRaises(KeyError):
            service.review("invented", proposal(), db_path=self.db)
        value = fixture()
        journal.save_snapshot(value, db_path=self.db)
        p = proposal()
        p["evidence_ids"] = ["invented"]
        self.assertEqual(service.review(value["snapshot_id"], p, db_path=self.db, now=NOW)["decision"]["action"], "data_insufficient")

    def test_summary_preserves_storage_and_identity(self):
        value = fixture()
        value["evidence"][0]["bars"] = [{"close": i} for i in range(260)]
        original = copy.deepcopy(value)
        view = service.snapshot_view(value)
        self.assertEqual(value, original)
        self.assertEqual(view["snapshot_id"], value["snapshot_id"])
        self.assertEqual(view["evidence"][0]["bar_count"], 260)
        self.assertEqual(len(view["evidence"][0]["recent_bars"]), 5)
        self.assertEqual(service.snapshot_view(value, full=True), original)

    def test_cli_restart_and_pending_receipt(self):
        args = [sys.executable, str(service.ROOT / "scripts/copilot_cli.py"), "--db", str(self.db)]
        op = {"statement": "I already bought QQQ, quantity unknown.",
              "execution_status": "executed", "instrument_id": "QQQ", "side": "buy", "source_message_id": "fixture-pending"}
        saved = subprocess.run(args + ["record", "--idempotency-key", "fixture-only"], input=json.dumps(op),
                               text=True, capture_output=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(saved.stdout)["status"], "pending")
        read = subprocess.run(args + ["context"], text=True, capture_output=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(read.stdout)["holdings"], [])
        self.assertEqual(len(json.loads(read.stdout)["pending_operations"]), 1)

    def test_concurrent_holdings_change_prevents_recommendation_commit(self):
        value = fixture()
        journal.save_snapshot(value, db_path=self.db)
        original = journal.record_recommendation
        def changed(decision, **kwargs):
            journal.record_operation({"statement": "I already bought QQQ.", "instrument_id": "QQQ",
                "execution_status": "executed", "quantity": "1", "price": "100", "currency": "USD",
                "unit": "share", "side": "buy", "occurred_at": "2026-09-04", "source_message_id": "fixture-racer"}, "racer", db_path=self.db)
            return original(decision, **kwargs)
        with patch("copilot.journal.record_recommendation", side_effect=changed):
            with self.assertRaises(journal.JournalConflict):
                service.review(value["snapshot_id"], proposal(), db_path=self.db, now=NOW)
        self.assertEqual(service.context(db_path=self.db)["recommendations"], [])

    def test_gold_and_index_views_keep_instrument_scope(self):
        for symbol, marker in [("^NDX", "指数研究观点"), ("GOLD.CNY", "黄金基准研究")]:
            value = fixture(symbol)
            journal.save_snapshot(value, db_path=self.db)
            reviewed = service.review(value["snapshot_id"], proposal(symbol), db_path=self.db, now=NOW)
            self.assertIn(marker, reviewed["message"])

    def test_gold_missing_research_not_hidden_by_other_warnings(self):
        value = fixture("GOLD.CNY")
        value["research_issues"] = [{"provider": "fred", "status": "not_configured"}]
        seal(value)
        journal.save_snapshot(value, db_path=self.db)
        reviewed = service.review(value["snapshot_id"], proposal("GOLD.CNY"), db_path=self.db, now=NOW)
        self.assertIn("宏观依据不可用", reviewed["message"])

if __name__ == "__main__":
    unittest.main()

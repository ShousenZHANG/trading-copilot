"""Privacy and advisor-config acceptance through isolated shared interfaces."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from copilot import advisor, config, journal, service
from _test_config import VALID


class AdvisorPrivacy(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "fixture.sqlite"
        self.config = Path(self.temp.name) / "absent.toml"

    def fill(self, key, account, currency="USD", symbol="QQQ", unit="share", **extra):
        return service.record({"instrument_id": symbol, "currency": currency, "unit": unit,
                               "side": "buy", "quantity": "2", "price": "10", "fees": "1",
                               "occurred_at": "2026-09-01", "statement": "我已经买入了；fixture私密原话",
                               "source_message_id": "fixture-secret-source", "account_id": account,
                               "external_trade_id": "fixture-external-" + key,
                               "execution_status": "executed", **extra}, key, db_path=self.db)

    def test_model_context_aggregates_accounts_without_disclosing_identifiers(self):
        self.fill("first", "fixture-secret-account-a")
        self.fill("second", "fixture-secret-account-b")
        internal = service.context(db_path=self.db)
        view = service.advisor_context(db_path=self.db, config_path=self.config)
        self.assertEqual(view["holdings"][0]["quantity"], "4")
        self.assertEqual(view["holdings"][0]["cost_basis_including_fees"], "42")
        self.assertTrue(view["multiple_accounts"])
        self.assertNotIn("operations", view)
        encoded = json.dumps(view, ensure_ascii=False)
        for private in ("fixture-secret", "fixture-external", "fixture私密原话", "holding_key"):
            self.assertNotIn(private, encoded)
        self.assertEqual(len(internal["operations"]), 2)
        self.assertEqual(service.context(db_path=self.db), internal)

    def test_currency_and_gold_item_weights_are_not_mixed(self):
        self.fill("usd", "fixture-a")
        for weight in ("10", "20"):
            self.fill("gold" + weight, "fixture-gold", "CNY", "GOLD.CNY", "item",
                      merchant="fixture-bank", purity="0.9999", weight_grams=weight)
        view = service.advisor_context(db_path=self.db, config_path=self.config)
        self.assertEqual(set(view["holdings_by_currency"]), {"USD", "CNY"})
        self.assertEqual(len(view["holdings_by_currency"]["CNY"]), 2)
        self.assertNotIn("fixture-gold", json.dumps(view))

    def test_pending_corrections_have_ids_and_fields_but_no_original_text(self):
        receipt = service.record({"statement": "我已经买了 QQQ，fixture私密原话",
                                  "execution_status": "executed", "instrument_id": "QQQ",
                                  "side": "buy", "source_message_id": "fixture-secret-message"},
                                 "pending", db_path=self.db)
        state = service.operation_context(receipt["operation_id"], db_path=self.db)
        self.assertEqual(state["version"], 1)
        self.assertIn("quantity", state["missing_fields"])
        self.assertNotIn("statement", state["operation"])
        with self.assertRaises(KeyError):
            service.operation_context("missing", db_path=self.db)

    def test_cli_uses_the_same_sanitized_projection(self):
        self.fill("cli", "fixture-secret-account")
        run = subprocess.run([sys.executable, str(service.ROOT / "scripts/copilot_cli.py"),
                              "--db", str(self.db), "--config-path", str(self.config), "context"],
                             capture_output=True, text=True, encoding="utf-8", check=True)
        view = json.loads(run.stdout)
        self.assertEqual(view["view"], "sanitized_advisor_context")
        self.assertNotIn("fixture-secret", run.stdout)

    def test_all_public_snapshot_views_strip_account_ids_without_mutation(self):
        value = {"snapshot_id": "fixture", "retail_quote": {"account_id": "fixture-secret-account",
                 "ask_per_fine_gram": 600}, "evidence": []}
        original = copy.deepcopy(value)
        for full in (False, True):
            self.assertNotIn("fixture-secret", json.dumps(service.snapshot_view(value, full=full)))
        self.assertEqual(value, original)


class AdvisorConfiguration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "user.toml"

    def load(self, extra):
        self.path.write_text(VALID + extra, encoding="utf-8")
        return config.load_config(self.path)

    def test_old_configs_keep_broker_disabled_and_research_available(self):
        cfg = self.load("")
        self.assertFalse(cfg.ibkr.enabled)
        self.assertTrue(cfg.advisor.enabled)
        self.assertFalse(cfg.advisor.fee_model_confirmed)
        self.assertEqual(cfg.advisor.swing_rule_id, "")

    def test_valid_additive_config(self):
        cfg = self.load('\n[advisor]\nresearch_universe=["AAPL","QQQ"]\n[ibkr]\nclient_id=72\n')
        self.assertEqual(cfg.advisor.research_universe, ("AAPL", "QQQ"))
        self.assertEqual(cfg.ibkr.client_id, 72)

    def test_invalid_permissions_types_and_universe_are_rejected(self):
        for text in ('[ibkr]\nclient_id=0', '[ibkr]\nhost="example.com"',
                     '[ibkr]\nenabled="true"', '[ibkr]\nport=inf',
                     '[advisor]\nallow_borrowing=true', '[advisor]\nmax_drawdown_pct=nan',
                     '[advisor]\nresearch_universe=["QQQ","qqq"]', '[advisor]\nunknown=1'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                self.load("\n" + text + "\n")


if __name__ == "__main__":
    unittest.main()

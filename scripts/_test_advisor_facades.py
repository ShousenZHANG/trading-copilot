"""Research and manual-plan facade acceptance, using only disposable local state."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copilot import journal, service
from copilot.policy import assess_proposal
from _test_policy import NOW, fixture, proposal, seal, quoted_gold
from _test_trade_plan import execution


class AdvisorFacades(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "isolated.sqlite"
        self.config = Path(self.temp.name) / "absent.toml"

    def test_disabled_broker_to_persisted_blocked_plan_round_trip(self):
        research = fixture()
        journal.save_snapshot(research, db_path=self.db)
        observed = service.broker_snapshot(["QQQ"], config_path=self.config, db_path=self.db, now=NOW)
        self.assertEqual(observed["issues"], ["broker_disabled"])
        result = service.prepare_trade_plan(research["snapshot_id"], observed["snapshot_id"],
            config_path=self.config, db_path=self.db, now=NOW)
        plan = result["plan"]
        self.assertEqual(plan["status"], "blocked")
        self.assertEqual(plan["orders"], [])
        self.assertTrue(plan["committed"])
        self.assertFalse(plan["order_submitted"])
        self.assertEqual(service.get_trade_plan(plan["plan_id"], db_path=self.db, now=NOW)["plan"], plan)
        with self.assertRaises(ValueError):
            service.confirm_plan_review(plan["plan_id"], "approve", plan["version"], db_path=self.db, now=NOW)
        with self.assertRaises(ValueError):
            service.prepare_trade_plan(research["snapshot_id"], observed["snapshot_id"], "intraday",
                config_path=self.config, db_path=self.db, now=NOW)

    def test_public_gold_quote_restores_private_quota_binding_only_when_exact(self):
        stored, record, p = quoted_gold(account_id="fixture-private-gold")
        journal.save_snapshot(stored, db_path=self.db)
        public = service.public_response(p)
        self.assertNotIn("account_id", public["retail_quote"])
        seen = []
        def capture(proposed, saved, context, **kwargs):
            seen.append(copy.deepcopy(proposed))
            return assess_proposal(proposed, saved, context, **kwargs)
        with patch("copilot.policy.assess_proposal", side_effect=capture):
            result = service.review(stored["snapshot_id"], public, db_path=self.db, now=NOW)
        self.assertEqual(seen[0]["retail_quote"]["account_id"], "fixture-private-gold")
        self.assertEqual(result["decision"]["risk_checks"]["retail_quote"]["status"], "pass")
        self.assertNotIn("fixture-private", json.dumps(service.public_response(result)))
        public["retail_quote"]["ask_per_fine_gram"] += 1
        result = service.review(stored["snapshot_id"], public, db_path=self.db, now=NOW)
        self.assertEqual(result["decision"]["risk_checks"]["retail_quote"]["status"], "unknown")

    def test_volume_multiple_needs_typed_verified_volume_claim(self):
        stored = fixture()
        item = stored["instruments"]["QQQ"]
        item["indicators"]["volume_ratio_20_sessions"] = 1.5
        item["verification"] = {"primary_evidence_id": "market"}
        seal(stored)
        p = proposal()
        p["reasons"] = ["成交量为此前均量的1.5倍"]
        p["claims"] = [{"evidence_id": "market", "path": "/instruments/QQQ/indicators/volume_ratio_20_sessions", "value": 1.5}]
        self.assertEqual(assess_proposal(p, stored, now=NOW)["action"], "buy")
        p["claims"][0] = {"evidence_id": "market", "path": "/instruments/QQQ/price", "value": 100.0}
        p["reasons"] = ["成交量为此前均量的100倍"]
        self.assertEqual(assess_proposal(p, stored, now=NOW)["action"], "data_insufficient")

    def test_public_broker_facts_keep_fx_and_coverage_but_no_linkage(self):
        from copilot.advisor import execution_summary
        observed = execution()
        observed["fx_rates"] = {"AUD.USD": {"rate": ".7", "actual_data_type": 1}}
        observed["coverage"]["orders"]["scope_source"] = "user_confirmed"
        observed["orders"] = [{"instrument_id": "QQQ", "order_key": "fixture-private-order",
            "perm_id": 123, "side": "buy", "remaining_quantity": "2", "limit_price": "100", "status": "working"}]
        result = execution_summary(observed)
        self.assertEqual(result["account_version"], observed["account_version"])
        self.assertIn("AUD.USD", result["fx_rates"])
        self.assertEqual(result["coverage"]["orders"]["scope_source"], "user_confirmed")
        self.assertNotIn("account_key", result["account"])
        self.assertNotIn("fixture-private", json.dumps(result))

    def test_public_receipts_remove_raw_confirmations_but_preserve_claim_pointers(self):
        value = {"confirmation": "fixture-private-account and statement", "path": "C:/fixture-private/file",
                 "claims": [{"evidence_id": "ev-market", "path": "/instruments/QQQ/price", "value": 100}]}
        public = service.public_response(value)
        self.assertNotIn("confirmation", public)
        self.assertNotIn("path", public)
        self.assertEqual(public["claims"][0]["path"], "/instruments/QQQ/price")

    def test_legacy_rule_pointer_is_normalized_for_manual_compiler(self):
        from copilot.config import Config, EtfConfig, GoldConfig, NotifyConfig
        settings = Config(False, "fixture", EtfConfig(adopted_rule_id="rule-0123456789abcdef"), GoldConfig(), NotifyConfig())
        policy = service._manual_policy(settings, "long_term")
        self.assertEqual(policy["long_term_rule_id"], "rule-0123456789abcdef")
        self.assertEqual(policy["adopted_rule_id"], policy["long_term_rule_id"])

    def test_confirmed_stock_thesis_is_assessed_without_widening_etf_adoption(self):
        from _test_market_data import FixtureProvider, snapshot, NOW as market_now
        from copilot.market_data import snapshot_digest
        sources = [FixtureProvider(asset_class="stock"),
                   FixtureProvider("nasdaq", "Nasdaq US market data", asset_class="stock",
                       security_type="common_stock", security_type_source_value="Common Stock",
                       exchange="NASDAQ-GS", identity_retrieved_at=market_now.isoformat(),
                       identity_source_url="https://api.nasdaq.com/api/quote/AAPL/info?assetclass=stocks",
                       source_url="https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks")]
        stored = snapshot(sources, ["AAPL"], allow_us_stocks=True)
        p = proposal("AAPL")
        p["evidence_ids"] = stored["instruments"]["AAPL"]["evidence_ids"]
        result = assess_proposal(p, stored, now=market_now)
        self.assertEqual(result["action"], "buy", result["reasons"])
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertNotIn("quantity", result)
        bad = copy.deepcopy(stored)
        bad["instruments"]["AAPL"]["identity_status"] = "unconfirmed"
        bad["snapshot_id"] = snapshot_digest(bad)
        self.assertEqual(assess_proposal(p, bad, now=market_now)["action"], "data_insufficient")

    def test_mode_assignment_receipt_preserves_only_necessary_public_facts(self):
        from copilot.plan_store import save_snapshot
        from copilot.broker import seal_execution_snapshot
        observed = execution()
        observed["positions"] = [{"instrument_id": "QQQ", "account_key": "account-hash", "con_id": 1,
            "currency": "USD", "quantity": "10", "avg_cost": "100"}]
        observed = seal_execution_snapshot(observed)
        save_snapshot(observed, db_path=self.db, now=NOW)
        result = service.confirm_mode_allocations(observed["snapshot_id"], {"QQQ": {"long_term": "8", "swing": "2"}},
            "我确认这些持仓用途，fixture-private原话", observed["account_version"], db_path=self.db, now=NOW)
        self.assertTrue(result["committed"])
        self.assertNotIn("fixture-private", json.dumps(result, ensure_ascii=False))
        self.assertNotIn("account_key", result)
        with self.assertRaises(ValueError):
            service.confirm_mode_allocations(observed["snapshot_id"], {"QQQ": {"long_term": "11", "swing": "0"}},
                "我确认", observed["account_version"], db_path=self.db, now=NOW)

    def test_first_broker_call_creates_nested_state_and_backup_keeps_plans(self):
        nested = Path(self.temp.name) / "new" / "state" / "first.sqlite"
        observed = service.broker_snapshot(["QQQ"], config_path=self.config, db_path=nested, now=NOW)
        research = fixture()
        journal.save_snapshot(research, db_path=nested)
        result = service.prepare_trade_plan(research["snapshot_id"], observed["snapshot_id"],
            config_path=self.config, db_path=nested, now=NOW)
        destination = Path(self.temp.name) / "backup.sqlite"
        journal.backup(destination, db_path=nested)
        original = service.get_trade_plan(result["plan"]["plan_id"], db_path=nested, now=NOW)
        restored = service.get_trade_plan(result["plan"]["plan_id"], db_path=destination, now=NOW)
        self.assertEqual(restored, original)

    def test_candidate_fingerprints_do_not_authenticate_or_disclose_history(self):
        inputs = {"spec": {"family": "swing_breakout", "universe": ["AAPL"]},
                  "history_bundle": {"private_source_note": "fixture-private"}}
        result = service.advisor_strategy_fingerprint(inputs)
        self.assertFalse(result["source_authenticated"])
        self.assertFalse(result["adopted"])
        self.assertNotIn("fixture-private", json.dumps(result))
        self.assertEqual(result, service.advisor_strategy_fingerprint(inputs))
        for key in ("code_hash", "data_hash", "spec_hash", "cost_hash"):
            self.assertEqual(len(result[key]), 64)

    def test_fake_source_flags_and_serialized_results_cannot_adopt_via_facade(self):
        from _test_advisor_strategy import history_fixture, frozen_fixture
        from copilot.advisor_strategy import TemplateSpec
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture(data_role="market")
        inputs = {"spec": spec.to_dict(), "history_bundle": bundle,
                  "freeze_manifest": frozen_fixture(spec, bundle),
                  "expected_sessions": {str(year): count for year, count in counts.items()},
                  "validation": {"admitted": True, "market_validated": True},
                  "data_attestation": "self-asserted input cannot grant authority"}
        result = service.validate_advisor_strategy(inputs, now=NOW)
        self.assertFalse(result["source_authenticated"])
        self.assertFalse(result["adoption_eligible"])
        self.assertFalse(result["data_reviewed"])
        with self.assertRaises(ValueError):
            service.adopt_advisor_strategy(inputs, "我明确采用此顾问策略",
                "我已审核历史数据来源、交易日历、分红拆股、时点标的与冻结样本外区间", db_path=self.db)
        self.assertEqual(service.context(db_path=self.db)["holdings"], [])

    def test_strategy_state_uses_current_pointer_and_only_public_receipt(self):
        from _test_advisor_strategy import adopted_fixture
        from copilot.config import Config, EtfConfig, GoldConfig, NotifyConfig, AdvisorConfig
        from copilot.plan_store import save_snapshot
        adopted = adopted_fixture()
        journal.record_adoption(adopted, db_path=self.db)
        observed = execution()
        save_snapshot(observed, db_path=self.db, now=NOW)
        settings = Config(False, "fixture", EtfConfig(), GoldConfig(), NotifyConfig(),
                          AdvisorConfig(long_term_rule_id=adopted["rule_id"]))
        with patch("copilot.config.load_config", return_value=settings):
            result = service.confirm_strategy_execution_state(observed["snapshot_id"],
                adopted["rule_id"], "long_term", "我确认该策略模式首次启动，此前没有实际成交",
                observed["account_version"], no_prior_executions=True, db_path=self.db, now=NOW)
            self.assertTrue(result["committed"])
            self.assertNotIn("confirmation", result)
            self.assertNotIn("account_key", result)
            with self.assertRaises(ValueError):
                service.confirm_strategy_execution_state(observed["snapshot_id"], "rule-0000000000000000",
                    "long_term", "我确认", observed["account_version"], no_prior_executions=True,
                    db_path=self.db, now=NOW)

    def test_config_change_hides_order_figures_and_blocks_new_review(self):
        from dataclasses import replace
        from _test_trade_plan import adoption
        from _test_policy import engine_fixture
        from copilot.config import Config, EtfConfig, GoldConfig, NotifyConfig, AdvisorConfig
        from copilot.plan_store import create_plan
        adopted = adoption()
        journal.record_adoption(adopted, db_path=self.db)
        settings = Config(False, "fixture", EtfConfig(adopted_rule_id=adopted["rule_id"]),
                          GoldConfig(), NotifyConfig(), AdvisorConfig(fee_model_confirmed=True, min_commission_usd=1))
        card = create_plan(execution_snapshot=execution(), research_snapshot=engine_fixture(),
            adoption=adopted, policy=service._manual_policy(settings, "long_term"), db_path=self.db, now=NOW)
        self.assertTrue(card["orders"])
        with patch("copilot.config.load_config", return_value=settings):
            current = service.get_trade_plan(card["plan_id"], config_path=self.config, db_path=self.db, now=NOW)["plan"]
            self.assertEqual(current["orders"], card["orders"])
        changed = replace(settings, advisor=replace(settings.advisor, enabled=False))
        with patch("copilot.config.load_config", return_value=changed):
            stale = service.get_trade_plan(card["plan_id"], config_path=self.config, db_path=self.db, now=NOW)["plan"]
            self.assertEqual(stale["orders"], [])
            self.assertEqual(stale["historical_orders"], card["orders"])
            self.assertEqual(stale["review_state"], "needs_recompile")
            with self.assertRaises(ValueError):
                service.confirm_plan_review(card["plan_id"], "我确认批准此方案", card["version"],
                    config_path=self.config, db_path=self.db, now=NOW)


if __name__ == "__main__":
    unittest.main()

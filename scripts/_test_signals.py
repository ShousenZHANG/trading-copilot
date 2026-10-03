"""Offline, pure-snapshot research signals; no account, network or personal DB."""
from __future__ import annotations

import copy
import sys
import unittest
from datetime import timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_market_data import FixtureProvider, NOW, history, snapshot
from copilot.signals import analyze
from copilot.market_data import snapshot_digest
from copilot.instruments import ETF_REGISTRY


class SignalContracts(unittest.TestCase):
    def ready(self):
        return snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP")])

    def test_uptrend_has_verifiable_conditions_and_never_becomes_an_order(self):
        stored = self.ready()
        original = copy.deepcopy(stored)
        result = analyze(stored, now=NOW)
        signal = result["signals"]["QQQ"]
        self.assertEqual(signal["trend_state"], "up")
        self.assertEqual(signal["momentum_state"], "positive")
        self.assertEqual(signal["direction"], "bullish")
        self.assertEqual(signal["signal_status"], "conditions_met")
        self.assertEqual(signal["execution_scope"], "research_only")
        self.assertFalse(signal["adoption_eligible"])
        self.assertTrue(signal["trigger_condition"]["requires_new_snapshot"])
        self.assertTrue(signal["invalidation_condition"]["requires_new_snapshot"])
        self.assertTrue(signal["claims"])
        self.assertEqual(signal["reference_trigger_price"], 127.8)
        self.assertAlmostEqual(signal["reference_invalidation_price"], 125.45)
        self.assertEqual(signal["reference_basis"], "total_return_adjusted")
        self.assertFalse(signal["reference_is_executable"])
        self.assertTrue(any(claim["path"].endswith("/high_previous_20_sessions") for claim in signal["claims"]))
        self.assertTrue(result["signal_rule_version"])
        for field in ("quantity", "price", "limit_price", "stop_loss", "target_shares", "target_weight"):
            self.assertNotIn(field, signal)
        self.assertEqual(stored, original)
        self.assertEqual(analyze(stored, now=NOW), result)

    def test_expiry_future_evidence_and_unknown_quality_never_form_conditions(self):
        stored = self.ready()
        expired = analyze(stored, now=NOW + timedelta(days=1))
        self.assertEqual(expired["signals"]["QQQ"]["signal_status"], "data_insufficient")
        self.assertIn("snapshot_expired", expired["signals"]["QQQ"]["missing"])
        for change in ("future_publication", "future_bar", "cross_instrument", "missing_binding"):
            altered = self.ready()
            item = altered["instruments"]["QQQ"]
            if change == "future_publication":
                altered["evidence"][0]["published_at"] = (NOW + timedelta(days=1)).isoformat()
            elif change == "future_bar":
                altered["evidence"][0]["bars"][-1]["session"] = "2026-09-07"
            elif change == "cross_instrument":
                altered["evidence"][0]["instrument_id"] = "SPY"
            else:
                item["verification"].pop("primary_evidence_id")
            altered["snapshot_id"] = snapshot_digest(altered)
            with self.subTest(change=change):
                result = analyze(altered, now=NOW)["signals"]["QQQ"]
                self.assertEqual(result["signal_status"], "data_insufficient")
                self.assertEqual(result["direction"], "unknown")
                self.assertFalse(result["claims"])
        unknown = analyze(snapshot(), now=NOW)["signals"]["QQQ"]
        self.assertEqual(unknown["trend_state"], "unknown")

    def test_bearish_and_conflicting_states_are_observations_not_sell_orders(self):
        declining = history()
        for index, bar in enumerate(declining):
            close = 150 - index / 10
            bar.update(close=close, adjusted_close=close, open=close, high=close + 1, low=close - 1)
        stored = snapshot([FixtureProvider(bars=declining), FixtureProvider("alpaca", "Alpaca SIP", bars=declining)])
        bearish = analyze(stored, horizon="long_term", now=NOW)["signals"]["QQQ"]
        self.assertEqual(bearish["direction"], "bearish")
        self.assertEqual(bearish["trigger_condition"]["comparisons"][0]["operator"], "<")
        self.assertAlmostEqual(bearish["reference_trigger_price"], 122.2)
        self.assertEqual(bearish["execution_scope"], "research_only")
        self.assertNotIn("action", bearish)
        conflict = self.ready()
        conflict["instruments"]["QQQ"]["indicators"]["return_20_sessions"] = -0.01
        conflict["snapshot_id"] = snapshot_digest(conflict)
        signal = analyze(conflict, now=NOW)["signals"]["QQQ"]
        self.assertEqual(signal["signal_status"], "watch")
        self.assertEqual(signal["direction"], "neutral")
        self.assertEqual(signal["counter_evidence"][0]["kind"], "observed_conflict")

    def test_inputs_and_digest_are_validated_before_analysis(self):
        stored = self.ready()
        stored["instruments"]["QQQ"]["price"] += 1
        with self.assertRaisesRegex(ValueError, "intact"):
            analyze(stored, now=NOW)
        for ids in ([], "QQQ", ["AAPL"]):
            with self.subTest(ids=ids), self.assertRaises(ValueError):
                analyze(self.ready(), ids, now=NOW)
        with self.assertRaises(ValueError):
            analyze(self.ready(), horizon="intraday", now=NOW)

    def test_fabricated_long_window_metrics_cannot_use_a_short_primary_history(self):
        stored = self.ready()
        for record in stored["evidence"]:
            record["bars"] = record["bars"][-20:]
        stored["instruments"]["QQQ"]["indicators"]["sample_count"] = 20
        stored["snapshot_id"] = snapshot_digest(stored)
        signal = analyze(stored, now=NOW)["signals"]["QQQ"]
        self.assertEqual(signal["signal_status"], "data_insufficient")
        self.assertFalse(signal["claims"])

    def test_malformed_optional_metadata_fails_closed_without_a_runtime_error(self):
        for field in ("missing", "verification", "evidence_ids"):
            stored = self.ready()
            item = stored["instruments"]["QQQ"]
            if field == "missing":
                item["indicators"][field] = None
            else:
                item[field] = None
            stored["snapshot_id"] = snapshot_digest(stored)
            with self.subTest(field=field):
                signal = analyze(stored, now=NOW)["signals"]["QQQ"]
                self.assertEqual(signal["signal_status"], "data_insufficient")

    def test_enriched_research_failures_do_not_replace_market_quality(self):
        stored = self.ready()
        item = stored["instruments"]["QQQ"]
        for provider, kind in (("finnhub", "news"), ("fred", "DGS10")):
            eid = "ev_research_" + kind
            stored["evidence"].append({"evidence_id": eid, "provider": provider,
                "status": "unavailable", "critical_evidence_eligible": False, "data": {}})
            item["evidence_ids"].append(eid)
            item.setdefault("research", {})[kind] = {"status": "unavailable", "evidence_ids": [eid]}
        stored["snapshot_id"] = snapshot_digest(stored)
        signal = analyze(stored, now=NOW)["signals"]["QQQ"]
        self.assertEqual(signal["signal_status"], "conditions_met")
        self.assertEqual(signal["research_coverage"]["news"], "unavailable")
        self.assertTrue(signal["claims"])


    def test_adjusted_reference_levels_are_not_executable_raw_quotes(self):
        adjusted = history()
        for bar in adjusted:
            for field in ("open", "high", "low", "close"):
                bar[field] *= 2
        stored = snapshot([FixtureProvider(bars=adjusted), FixtureProvider("alpaca", "Alpaca SIP", bars=adjusted)])
        signal = analyze(stored, now=NOW)["signals"]["QQQ"]
        self.assertEqual(stored["instruments"]["QQQ"]["price"], 255.8)
        self.assertEqual(signal["reference_trigger_price"], 127.8)
        self.assertEqual(signal["reference_basis"], "total_return_adjusted")
        self.assertFalse(signal["reference_is_executable"])
        self.assertNotIn("expected_return", signal)
        self.assertNotIn("win_probability", signal)

    def test_stock_signal_requires_the_confirmed_identity_record(self):
        sources = [FixtureProvider(asset_class="stock"), FixtureProvider(
            "nasdaq", "Nasdaq US market data", asset_class="stock", security_type="common_stock",
            security_type_source_value="Common Stock", exchange="NASDAQ-GS",
            source_url="https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks",
            identity_source_url="https://api.nasdaq.com/api/quote/AAPL/info?assetclass=stocks",
            identity_retrieved_at=NOW.isoformat())]
        stored = snapshot(sources, ["AAPL"], allow_us_stocks=True)
        signal = analyze(stored, now=NOW)["signals"]["AAPL"]
        self.assertEqual(signal["signal_status"], "conditions_met")
        self.assertFalse(signal["adoption_eligible"])
        stored["evidence"][1]["security_type_source_value"] = "ADR"
        stored["snapshot_id"] = snapshot_digest(stored)
        invalid = analyze(stored, now=NOW)["signals"]["AAPL"]
        self.assertEqual(invalid["signal_status"], "data_insufficient")
        self.assertIsNone(invalid["reference_trigger_price"])
        self.assertIn("common_stock_identity_unverified", invalid["missing"])

    def test_research_pool_supports_twenty_four_candidates_and_a_benchmark(self):
        symbols = sorted(ETF_REGISTRY)[:24] + ["^NDX"]
        with self.assertRaises(ValueError):
            snapshot(symbols=symbols)
        stored = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP")],
                          symbols, allow_us_stocks=True)
        result = analyze(stored, symbols, now=NOW)
        self.assertEqual(len(result["signals"]), 25)
        self.assertEqual(result["signals"]["^NDX"]["context_role"], "market_benchmark")
        self.assertTrue(all(result["signals"][symbol]["context_role"] == "instrument" for symbol in symbols[:-1]))





if __name__ == "__main__":
    unittest.main()

"""Registry contract: only whitelisted ETFs, the two Nasdaq indexes and SGE gold resolve."""
from __future__ import annotations

import sys
import unittest
import copy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot.instruments import ETF_REGISTRY, get_instrument, get_research_instrument, normalize_instrument, verify_stock_identity
from _test_market_data import FixtureProvider, NOW, snapshot
from copilot.market_data import snapshot_digest


class RegistryContracts(unittest.TestCase):
    def test_verified_stock_identity_binds_the_saved_nasdaq_record(self):
        sources = [FixtureProvider(asset_class="stock"), FixtureProvider(
            "nasdaq", "Nasdaq US market data", asset_class="stock", security_type="common_stock",
            security_type_source_value="Common Stock", exchange="NASDAQ-GS",
            source_url="https://api.nasdaq.com/api/quote/AAPL/historical?assetclass=stocks",
            identity_source_url="https://api.nasdaq.com/api/quote/AAPL/info?assetclass=stocks",
            identity_retrieved_at=NOW.isoformat())]
        stored = snapshot(sources, ["AAPL"], allow_us_stocks=True)
        identity = verify_stock_identity(stored, "AAPL")
        self.assertEqual(identity["instrument_id"], "AAPL")
        self.assertEqual(identity["security_type"], "common_stock")
        self.assertEqual(identity["execution_scope"], "research_only")
        for changed in ("missing_instruments", "missing_evidence", "future_identity"):
            invalid = copy.deepcopy(stored)
            if changed == "missing_instruments":
                invalid["instruments"] = None
            elif changed == "missing_evidence":
                invalid["evidence"] = None
            else:
                invalid["evidence"][1]["identity_retrieved_at"] = "2099-01-01T00:00:00Z"
            invalid["snapshot_id"] = snapshot_digest(invalid)
            with self.subTest(changed=changed), self.assertRaises(ValueError):
                verify_stock_identity(invalid, "AAPL")
        stored["instruments"]["AAPL"]["identity_evidence_id"] = stored["instruments"]["AAPL"]["evidence_ids"][0]
        stored["snapshot_id"] = snapshot_digest(stored)
        with self.assertRaisesRegex(ValueError, "Nasdaq"):
            verify_stock_identity(stored, "AAPL")

    def test_stock_research_identity_does_not_expand_the_etf_registry(self):
        for symbol in ("AAPL", "NVDA", "BRK-B", "ABCD"):
            item = get_research_instrument(symbol.lower())
            self.assertEqual(item["instrument_id"], symbol)
            self.assertEqual(item["asset_class"], "stock")
            self.assertEqual(item["identity_status"], "unconfirmed")
            self.assertEqual(item["execution_scope"], "research_only")
            self.assertFalse(item["adoption_eligible"])
            self.assertNotIn(symbol, ETF_REGISTRY)
            with self.assertRaises(ValueError):
                get_instrument(symbol)
        self.assertEqual(get_research_instrument("QQQ"), get_instrument("QQQ"))
        with self.assertRaises(ValueError):
            get_research_instrument("AAPL.US")

    def test_whitelisted_etf_is_registered_etf(self):
        for symbol in ("QQQ", "QQQM", "SPY", "VTI", "QQQI", "JEPQ", "JEPI", "IOO"):
            item = get_instrument(symbol)
            self.assertEqual(item["asset_class"], "etf", symbol)
            self.assertEqual(item["identity_status"], "registered", symbol)
            self.assertEqual(item["currency"], "USD")
            self.assertEqual(item["unit"], "share")
            self.assertTrue(item["tradable"])

    def test_registry_has_issuer_urls_for_covered_call_proxies(self):
        for symbol in ("QQQI", "JEPQ", "JEPI"):
            self.assertTrue(get_instrument(symbol)["issuer_url"].startswith("https://"), symbol)

    def test_unknown_us_ticker_is_rejected_not_provisionally_accepted(self):
        for symbol in ("AAPL", "NVDA", "ABCD", "BRK-B"):
            with self.assertRaisesRegex(ValueError, "not in the ETF registry"):
                normalize_instrument(symbol)

    def test_foreign_suffix_and_fx_are_rejected(self):
        for symbol in ("NDQ.AX", "IOO.AX", "0700.HK", "AUDUSD=X", "GC=F"):
            with self.assertRaises(ValueError):
                normalize_instrument(symbol)

    def test_indexes_and_gold_are_unchanged(self):
        self.assertEqual(get_instrument("^NDX")["asset_class"], "index")
        self.assertEqual(get_instrument("纳斯达克100")["instrument_id"], "^NDX")
        self.assertEqual(get_instrument("^IXIC")["unit"], "point")
        gold = get_instrument("GOLD.CNY")
        self.assertEqual(gold["asset_class"], "physical_gold")
        self.assertEqual(gold["quote_role"], "market_benchmark_not_retail_quote")
        self.assertEqual(get_instrument("黄金")["instrument_id"], "GOLD.CNY")

    def test_registry_is_frozen_and_uppercase(self):
        self.assertIsInstance(ETF_REGISTRY, frozenset)
        self.assertTrue(all(s == s.upper() for s in ETF_REGISTRY))
        self.assertGreaterEqual(len(ETF_REGISTRY), 39)


if __name__ == "__main__":
    unittest.main()

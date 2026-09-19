"""Registry contract: only whitelisted ETFs, the two Nasdaq indexes and SGE gold resolve."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot.instruments import ETF_REGISTRY, get_instrument, normalize_instrument


class RegistryContracts(unittest.TestCase):
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

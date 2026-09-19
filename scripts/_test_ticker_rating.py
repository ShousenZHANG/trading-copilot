"""Contracts for scripts/ticker.py and scripts/parse_rating.py (still used by evals/)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_rating import explicit_rating, parse_rating
from ticker import validate_date_component, validate_ticker_component


class TickerContracts(unittest.TestCase):
    def test_safe_tickers_round_trip(self):
        for ticker in ("NVDA", "BRK-B", "0700.HK", "BHP.AX", "GC=F", "XAUUSD=X", "^GSPC"):
            self.assertEqual(validate_ticker_component(ticker), ticker)

    def test_unsafe_tickers_fail(self):
        for bad in ("", ".", "..", "../NVDA", "NV DA", "AAPL\x00", "CON", "NUL.T"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_ticker_component(bad)

    def test_dates_are_strict(self):
        for good in ("2026-01-05", "2026-12-31", "2024-02-29"):
            self.assertEqual(validate_date_component(good), good)
        for bad in ("", "2026-1-5", "20260105", "2026-13-01", "2026-02-30", "../../etc/passwd",
                    "2026-01-05/..", "2026-01-05 ", "2026-01-05T00:00:00", None):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate_date_component(bad)  # type: ignore[arg-type]


class RatingContracts(unittest.TestCase):
    def test_header_beats_decoy_lines(self):
        card = ("**结论卡**\n- 现在做什么: 减半, rating: Buy 只是卡片措辞\n| rating: Buy | 表格诱饵 |\n\n"
                "**Rating**: Underweight\n\n**Executive Summary**: 降配.\n")
        self.assertEqual(parse_rating(card), "Underweight")

    def test_fullwidth_colon_accepted(self):
        self.assertEqual(parse_rating("**Rating**：Sell\n\n**Executive Summary**: x\n"), "Sell")

    def test_unsupported_scale_rejected_and_reduce_maps_to_hold(self):
        for unsupported in ("Reduce", "Avoid", "Strong Buy", "Buy or Sell"):
            with self.assertRaises(ValueError, msg=unsupported):
                explicit_rating(f"**Rating**: {unsupported}\nDo not Buy.\n")
        self.assertEqual(parse_rating("**Rating**: Reduce\nDo not Buy"), "Hold")


if __name__ == "__main__":
    unittest.main()

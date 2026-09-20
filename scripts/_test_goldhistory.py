"""Vendored SGE Au99.99 history: shape, integrity, and the frame it builds."""
from __future__ import annotations

import tempfile
import unittest
from datetime import date
from pathlib import Path

from copilot.backtest import goldhistory

HEADER = "# source_url: https://www.sge.com.cn/sjzx/quotation_daily_new\n" \
         "# upstream: Shanghai Gold Exchange\n" \
         "# refreshed_at: 2026-09-20T00:00:00+00:00\n" \
         "session,close\n"


def write(rows, header=HEADER):
    handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, encoding="utf-8")
    handle.write(header)
    for session, close in rows:
        handle.write(f"{session},{close}\n")
    handle.close()
    return Path(handle.name)


class TheVendoredDatasetIsUsable(unittest.TestCase):
    """The file that ships in the repo, not a fixture."""

    def test_it_exists_and_covers_at_least_nine_years(self):
        rows = goldhistory.rows()
        self.assertGreater(len(rows), 2000, "a decade of SGE sessions is about 2400 rows")
        first = date.fromisoformat(rows[0]["session"])
        last = date.fromisoformat(rows[-1]["session"])
        self.assertLessEqual(first, date(2017, 1, 31), "history must start no later than Jan 2017")
        self.assertGreaterEqual((last - first).days / 365.25, 9.0)

    def test_every_close_is_a_plausible_cny_per_gram_price(self):
        # Au99.99 has traded roughly 230-1000 CNY/gram across this window. A
        # unit slip (per ounce, or per kilo) would blow straight through this.
        for row in goldhistory.rows():
            self.assertGreater(row["close"], 100.0, row)
            self.assertLess(row["close"], 5000.0, row)

    def test_sessions_are_unique_and_ascending(self):
        sessions = [r["session"] for r in goldhistory.rows()]
        self.assertEqual(sessions, sorted(sessions))
        self.assertEqual(len(sessions), len(set(sessions)))

    def test_it_carries_its_own_provenance(self):
        meta = goldhistory.provenance()
        self.assertIn("sge.com.cn", meta["source_url"])
        self.assertEqual(meta["upstream"], "Shanghai Gold Exchange")
        self.assertTrue(meta["refreshed_at"])
        self.assertEqual(meta["row_count"], len(goldhistory.rows()))

    def test_it_builds_a_single_symbol_frame(self):
        frame = goldhistory.load()
        self.assertEqual(frame.symbols, ("GOLD.CNY",))
        self.assertEqual(len(frame.dates), len(goldhistory.rows()))
        self.assertEqual(len(frame.closes[0]), 1)


class TheLoaderRefusesBadInput(unittest.TestCase):
    """A silently-wrong price series is the worst failure mode here."""

    def test_a_descending_file_is_refused_rather_than_sorted(self):
        # Sorting it would hide that the writer is broken. The backtest would
        # then run on a series nobody intended.
        path = write([("2020-01-03", 340.0), ("2020-01-02", 339.0)])
        with self.assertRaisesRegex(ValueError, "ascending"):
            goldhistory.rows(path)

    def test_a_duplicate_session_with_a_different_close_is_refused(self):
        path = write([("2020-01-02", 339.0), ("2020-01-02", 341.0)])
        with self.assertRaisesRegex(ValueError, "conflicting"):
            goldhistory.rows(path)

    def test_a_duplicate_session_with_the_same_close_collapses(self):
        path = write([("2020-01-02", 339.0), ("2020-01-02", 339.0), ("2020-01-03", 340.0)])
        self.assertEqual(len(goldhistory.rows(path)), 2)

    def test_a_non_positive_close_is_refused(self):
        path = write([("2020-01-02", 0.0)])
        with self.assertRaisesRegex(ValueError, "positive"):
            goldhistory.rows(path)

    def test_a_missing_provenance_header_is_refused(self):
        path = write([("2020-01-02", 339.0)], header="session,close\n")
        with self.assertRaisesRegex(ValueError, "provenance"):
            goldhistory.provenance(path)


class TheTailCanBeCheckedAgainstTheLiveExchange(unittest.TestCase):
    """The vendored file is a cache. A cache that drifts unnoticed is a lie."""

    def test_matching_tails_report_no_mismatch(self):
        vendored = [{"session": "2026-09-17", "close": 940.0},
                    {"session": "2026-09-18", "close": 947.09}]
        live = [{"session": "2026-09-17", "close": 940.0},
                {"session": "2026-09-18", "close": 947.09}]
        report = goldhistory.verify_tail(vendored, live, sessions=2)
        self.assertEqual(report["compared"], 2)
        self.assertEqual(report["mismatches"], [])

    def test_a_drifted_close_is_reported_with_both_values(self):
        vendored = [{"session": "2026-09-18", "close": 947.09}]
        live = [{"session": "2026-09-18", "close": 948.00}]
        report = goldhistory.verify_tail(vendored, live, sessions=1)
        self.assertEqual(len(report["mismatches"]), 1)
        self.assertEqual(report["mismatches"][0]["vendored"], 947.09)
        self.assertEqual(report["mismatches"][0]["live"], 948.00)

    def test_a_session_the_live_fetch_lacks_is_not_silently_skipped(self):
        vendored = [{"session": "2026-09-18", "close": 947.09}]
        report = goldhistory.verify_tail(vendored, [], sessions=1)
        self.assertEqual(report["mismatches"][0]["live"], None)


if __name__ == "__main__":
    unittest.main()

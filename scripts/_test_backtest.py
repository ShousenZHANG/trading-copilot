#!/usr/bin/env python3
"""Offline behavioural contracts for the backtest package.

Stdlib only, no network, no clock, no randomness: this file runs inside the CI
matrix job that installs zero third-party packages.
"""
from __future__ import annotations

import json
import statistics
import sys
import unittest
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from copilot.backtest import frame as frame_mod
from copilot.backtest import history
from copilot.backtest import metrics
from copilot.backtest import universe


class UniverseTiers(unittest.TestCase):
    def test_tiers_partition_the_equity_registry(self):
        from copilot.instruments import DEFENSIVE_ETFS, ETF_REGISTRY
        equity = ETF_REGISTRY - DEFENSIVE_ETFS
        covered = (universe.QUALIFIED | universe.NO_2008_BARS | universe.PARTIAL_2008
                   | universe.UNFETCHABLE | universe.INCOME_PROXY_ONLY)
        self.assertEqual(covered, equity)
        self.assertEqual(len(equity), 33)

    def test_tiers_do_not_overlap(self):
        tiers = [universe.QUALIFIED, universe.NO_2008_BARS, universe.PARTIAL_2008,
                 universe.UNFETCHABLE, universe.INCOME_PROXY_ONLY]
        for i, left in enumerate(tiers):
            for right in tiers[i + 1:]:
                self.assertEqual(left & right, frozenset())

    def test_schd_is_not_qualified(self):
        # 14.92y today and 15y on 2026-10-20, but zero 2008 bars forever.
        self.assertIn("SCHD", universe.NO_2008_BARS)

    def test_vt_is_partial_not_qualified(self):
        # 131/253 bars in 2008, starting after the 2007-10 peak.
        self.assertIn("VT", universe.PARTIAL_2008)

    def test_classify_rejects_unregistered(self):
        with self.assertRaises(ValueError):
            universe.classify("NVDA")

    def test_classify_reports_the_reason(self):
        self.assertEqual(universe.classify("SPY").tier, "qualified")
        self.assertTrue(universe.classify("SPY").admissible)
        splg = universe.classify("SPLG")
        self.assertFalse(splg.admissible)
        self.assertIn("404", splg.reason)

    def test_default_candidates_are_exactly_the_qualified_tier(self):
        self.assertEqual(universe.default_candidates(), tuple(sorted(universe.QUALIFIED)))

    def test_classify_normalizes_lowercase(self):
        self.assertEqual(universe.classify("spy").tier, "qualified")

    def test_classify_rejects_non_string(self):
        with self.assertRaises(ValueError):
            universe.classify(None)

    def test_classify_flags_provenance_caveat_in_reason_and_flag(self):
        smh = universe.classify("SMH")
        self.assertIn(universe._PROVENANCE_CAVEAT, smh.reason)
        self.assertTrue(smh.provenance_unverified)


class PriceFrameContract(unittest.TestCase):
    def frame(self):
        return frame_mod.build(
            dates=[date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)],
            symbols=["SPY", "QQQ"],
            closes=[[100.0, 200.0], [101.0, 198.0], [99.0, 202.0]],
        )

    def test_column_returns_one_symbol_series(self):
        self.assertEqual(self.frame().column("QQQ"), (200.0, 198.0, 202.0))

    def test_symbols_are_upper_cased_and_indexed(self):
        f = frame_mod.build(dates=[date(2020, 1, 2)], symbols=["spy"], closes=[[1.0]])
        self.assertEqual(f.symbols, ("SPY",))
        self.assertEqual(f.index_of("spy"), 0)

    def test_rejects_unsorted_dates(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            frame_mod.build(dates=[date(2020, 1, 3), date(2020, 1, 2)],
                            symbols=["SPY"], closes=[[1.0], [2.0]])

    def test_rejects_duplicate_dates(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            frame_mod.build(dates=[date(2020, 1, 2), date(2020, 1, 2)],
                            symbols=["SPY"], closes=[[1.0], [2.0]])

    def test_rejects_duplicate_symbols(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            frame_mod.build(dates=[date(2020, 1, 2)], symbols=["SPY", "spy"], closes=[[1.0, 2.0]])

    def test_rejects_ragged_rows(self):
        with self.assertRaisesRegex(ValueError, "2 values"):
            frame_mod.build(dates=[date(2020, 1, 2)], symbols=["SPY", "QQQ"], closes=[[1.0]])

    def test_rejects_non_positive_and_nan_closes(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                frame_mod.build(dates=[date(2020, 1, 2)], symbols=["SPY"], closes=[[bad]])

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            frame_mod.build(dates=[], symbols=["SPY"], closes=[])

    def test_slice_is_inclusive_on_both_ends(self):
        sliced = self.frame().slice(date(2020, 1, 3), date(2020, 1, 6))
        self.assertEqual(sliced.dates, (date(2020, 1, 3), date(2020, 1, 6)))
        self.assertEqual(sliced.column("SPY"), (101.0, 99.0))

    def test_slice_can_retain_exactly_one_row(self):
        sliced = self.frame().slice(date(2020, 1, 3), date(2020, 1, 3))
        self.assertEqual(sliced.dates, (date(2020, 1, 3),))
        self.assertEqual(sliced.span_years(), 0.0)

    def test_slice_rejects_start_after_end(self):
        with self.assertRaisesRegex(ValueError, "no bars between"):
            self.frame().slice(date(2020, 1, 6), date(2020, 1, 2))

    def test_sessions_in_year_counts_bars(self):
        self.assertEqual(self.frame().sessions_in_year(2020), 3)
        self.assertEqual(self.frame().sessions_in_year(2008), 0)

    def test_span_years_uses_actual_calendar_distance(self):
        f = frame_mod.build(dates=[date(2000, 1, 3), date(2015, 1, 5)],
                            symbols=["SPY"], closes=[[1.0], [2.0]])
        self.assertAlmostEqual(f.span_years(), 15.01, places=1)


def chart_payload(granularity="1d", *, timestamps=None, closes=None, adjcloses=None):
    timestamps = timestamps if timestamps is not None else [1577941200, 1578027600]
    closes = closes if closes is not None else [100.0, 101.0]
    adjcloses = adjcloses if adjcloses is not None else [99.0, 100.0]
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": "SPY", "dataGranularity": granularity,
                 "exchangeTimezoneName": "America/New_York"},
        "timestamp": timestamps,
        "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": adjcloses}]},
    }]}})


class HistoryParsing(unittest.TestCase):
    def test_parses_both_series_separately(self):
        series = history.parse_chart("SPY", chart_payload())
        self.assertEqual(series.symbol, "SPY")
        self.assertEqual(len(series.dates), 2)
        self.assertEqual(series.split_adjusted, (100.0, 101.0))
        self.assertEqual(series.split_and_dividend_adjusted, (99.0, 100.0))

    def test_rejects_monthly_granularity(self):
        # range=max&interval=1d returns HTTP 200 with dataGranularity='1mo'.
        # Without this check a 15-year gate can be passed by ~180 monthly bars.
        with self.assertRaisesRegex(history.HistoryError, "dataGranularity"):
            history.parse_chart("SPY", chart_payload(granularity="1mo"))

    def test_drops_bars_where_either_series_is_null(self):
        payload = chart_payload(timestamps=[1577941200, 1578027600, 1578114000],
                                closes=[100.0, None, 102.0],
                                adjcloses=[99.0, 100.0, 101.0])
        series = history.parse_chart("SPY", payload)
        self.assertEqual(len(series.dates), 2)
        self.assertEqual(series.split_adjusted, (100.0, 102.0))
        self.assertEqual(series.split_and_dividend_adjusted, (99.0, 101.0))
        self.assertEqual(series.dropped_bars, 1)

    def test_drops_bars_where_adjclose_is_null(self):
        # Symmetric to the close=None case above: a null on the OTHER series
        # must drop the same bar and keep both arrays aligned to each other.
        payload = chart_payload(timestamps=[1577941200, 1578027600, 1578114000],
                                closes=[100.0, 101.0, 102.0],
                                adjcloses=[99.0, None, 101.0])
        series = history.parse_chart("SPY", payload)
        self.assertEqual(len(series.dates), 2)
        self.assertEqual(series.split_adjusted, (100.0, 102.0))
        self.assertEqual(series.split_and_dividend_adjusted, (99.0, 101.0))
        self.assertEqual(series.dropped_bars, 1)

    def test_rejects_an_empty_result(self):
        with self.assertRaises(history.HistoryError):
            history.parse_chart("SPY", json.dumps({"chart": {"error": None, "result": []}}))

    def test_surfaces_the_upstream_error_text(self):
        body = json.dumps({"chart": {"error": {"code": "Not Found",
                                               "description": "No data found, symbol may be delisted"},
                                     "result": None}})
        with self.assertRaisesRegex(history.NotCovered, "delisted"):
            history.parse_chart("SPLG", body)

    def test_dates_come_back_in_new_york_for_a_winter_open_stamp(self):
        # 2020-01-02 14:30:00 UTC is the exchange open in EST (UTC-5): 09:30
        # local. int(datetime(2020, 1, 2, 14, 30, tzinfo=timezone.utc)
        # .timestamp()) == 1577975400. Taking the UTC date directly would
        # still give 2020-01-02 here, so this is the case that actually
        # discriminates the offset: the fixed 5-hour subtraction must land on
        # the same session date Yahoo stamps, not shift it.
        series = history.parse_chart(
            "SPY", chart_payload(timestamps=[1577975400], closes=[1.0], adjcloses=[1.0]))
        self.assertEqual(series.dates[0], date(2020, 1, 2))

    def test_dates_come_back_in_new_york_for_a_summer_open_stamp(self):
        # 2020-07-01 13:30:00 UTC is the exchange open in EDT (UTC-4): 09:30
        # local. int(datetime(2020, 7, 1, 13, 30, tzinfo=timezone.utc)
        # .timestamp()) == 1593610200. The loader subtracts the fixed WINTER
        # offset (5h) even in summer, landing on 08:30 -- the wrong clock time
        # but still 2020-07-01, which is all _to_new_york_date promises.
        series = history.parse_chart(
            "SPY", chart_payload(timestamps=[1593610200], closes=[1.0], adjcloses=[1.0]))
        self.assertEqual(series.dates[0], date(2020, 7, 1))


class HistoryUrl(unittest.TestCase):
    def test_url_uses_period1_period2_never_range(self):
        url = history.chart_url("SPY", until_epoch=1789797166)
        self.assertIn("period1=0", url)
        self.assertIn("period2=1789797166", url)
        self.assertIn("interval=1d", url)
        self.assertNotIn("range=", url)

    def test_user_agent_matches_the_one_that_is_not_rate_limited(self):
        self.assertEqual(history.USER_AGENT,
                         "Mozilla/5.0 TradingCopilot/1.0 (personal research)")

    def test_loader_never_touches_the_shared_provider_cache(self):
        source = Path(history.__file__).read_text(encoding="utf-8")
        for forbidden in ("HttpClient", "cache_dir", "_cache("):
            self.assertNotIn(forbidden, source,
                             f"{forbidden} must not appear anywhere in history.py — "
                             "a 22-symbol sweep through providers.py evicts snapshot forensics "
                             "and its 'yahoo' cooldown blocks the live decision path")


class HistoryToFrame(unittest.TestCase):
    def test_intersects_partially_overlapping_series_onto_common_dates(self):
        spy = history.Series(
            symbol="SPY",
            dates=(date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)),
            split_adjusted=(100.0, 101.0, 102.0),
            split_and_dividend_adjusted=(99.0, 100.0, 101.0),
            dropped_bars=0,
        )
        qqq = history.Series(
            symbol="QQQ",
            dates=(date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)),
            split_adjusted=(200.0, 201.0, 202.0),
            split_and_dividend_adjusted=(198.0, 199.0, 200.0),
            dropped_bars=0,
        )
        f = history.to_frame([spy, qqq])
        self.assertEqual(f.dates, (date(2020, 1, 3), date(2020, 1, 6)))
        self.assertEqual(f.column("SPY"), (100.0, 101.0))
        self.assertEqual(f.column("QQQ"), (198.0, 199.0))

    def test_empty_intersection_names_the_binding_symbols(self):
        spy = history.Series(
            symbol="SPY",
            dates=(date(2020, 1, 2),),
            split_adjusted=(100.0,),
            split_and_dividend_adjusted=(99.0,),
            dropped_bars=0,
        )
        newetf = history.Series(
            symbol="NEWETF",
            dates=(date(2021, 1, 4),),
            split_adjusted=(10.0,),
            split_and_dividend_adjusted=(10.0,),
            dropped_bars=0,
        )
        with self.assertRaisesRegex(ValueError, r"binds_start=NEWETF.*binds_end=SPY"):
            history.to_frame([spy, newetf])


class HistoryAlignment(unittest.TestCase):
    def series(self):
        spy = history.Series(
            symbol="SPY",
            dates=(date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)),
            split_adjusted=(100.0, 101.0, 102.0),
            split_and_dividend_adjusted=(99.0, 100.0, 101.0),
            dropped_bars=1,
        )
        qqq = history.Series(
            symbol="QQQ",
            dates=(date(2020, 1, 3), date(2020, 1, 6), date(2020, 1, 7)),
            split_adjusted=(200.0, 201.0, 202.0),
            split_and_dividend_adjusted=(198.0, 199.0, 200.0),
            dropped_bars=2,
        )
        return spy, qqq

    def test_reports_the_binding_symbols_and_bars_lost(self):
        info = history.alignment(self.series())
        self.assertEqual(info["binds_start"], "QQQ")  # latest first-date: 2020-01-03
        self.assertEqual(info["binds_end"], "SPY")  # earliest last-date: 2020-01-06
        self.assertEqual(info["common_first"], "2020-01-03")
        self.assertEqual(info["common_last"], "2020-01-06")
        self.assertEqual(info["common_bars"], 2)
        self.assertEqual(info["bars_lost_vs_longest"], 1)  # longest=3, common=2

    def test_surfaces_dropped_bars_per_symbol(self):
        info = history.alignment(self.series())
        self.assertEqual(info["per_symbol"]["SPY"]["dropped_bars"], 1)
        self.assertEqual(info["per_symbol"]["QQQ"]["dropped_bars"], 2)
        self.assertEqual(info["per_symbol"]["SPY"]["first"], "2020-01-02")
        self.assertEqual(info["per_symbol"]["SPY"]["bars"], 3)


class HistoryFetchThrottle(unittest.TestCase):
    """No network: urlopen is monkeypatched on every path through fetch()."""

    def tearDown(self):
        history._last_request_at = 0.0

    class _FakeResponse:
        def __init__(self, body: bytes):
            self._body = body

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def read(self):
            return self._body

    def test_throttle_timestamp_updates_on_success(self):
        history._last_request_at = 0.0
        body = chart_payload().encode("utf-8")
        with mock.patch.object(history.urllib.request, "urlopen",
                               return_value=self._FakeResponse(body)):
            history.fetch("SPY", until_epoch=1700000000)
        self.assertGreater(history._last_request_at, 0.0)

    def test_throttle_timestamp_updates_on_http_error(self):
        history._last_request_at = 0.0
        error = urllib.error.HTTPError(url="x", code=500, msg="boom", hdrs=None, fp=None)
        with mock.patch.object(history.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(history.HistoryError):
                history.fetch("SPY", until_epoch=1700000000)
        self.assertGreater(history._last_request_at, 0.0)

    def test_throttle_timestamp_updates_on_url_error(self):
        history._last_request_at = 0.0
        error = urllib.error.URLError("no route to host")
        with mock.patch.object(history.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(history.HistoryError):
                history.fetch("SPY", until_epoch=1700000000)
        self.assertGreater(history._last_request_at, 0.0)


class Metrics(unittest.TestCase):
    def curve(self, values, start=date(2020, 1, 2)):
        return [(start + timedelta(days=i), v) for i, v in enumerate(values)]

    def test_max_drawdown_finds_peak_trough_and_duration(self):
        dd = metrics.max_drawdown(self.curve([100, 120, 60, 80, 130]))
        self.assertAlmostEqual(dd.depth, 0.5)
        self.assertEqual(dd.peak_date, date(2020, 1, 3))
        self.assertEqual(dd.trough_date, date(2020, 1, 4))
        self.assertEqual(dd.recovery_date, date(2020, 1, 6))
        self.assertEqual(dd.duration_days, 3)

    def test_unrecovered_drawdown_reports_no_recovery_date(self):
        dd = metrics.max_drawdown(self.curve([100, 120, 60]))
        self.assertIsNone(dd.recovery_date)
        self.assertAlmostEqual(dd.depth, 0.5)

    def test_flat_curve_has_zero_drawdown(self):
        dd = metrics.max_drawdown(self.curve([100, 100, 100]))
        self.assertEqual(dd.depth, 0.0)

    def test_cagr_matches_a_hand_computed_doubling(self):
        curve = [(date(2010, 1, 4), 100.0), (date(2020, 1, 3), 200.0)]
        self.assertAlmostEqual(metrics.cagr(curve), 0.0718, places=3)

    def test_sharpe_is_zero_for_a_flat_curve(self):
        self.assertEqual(metrics.sharpe(self.curve([100, 100, 100, 100])), 0.0)

    def test_sharpe_declares_its_risk_free_rate(self):
        self.assertEqual(metrics.RISK_FREE_RATE, 0.0)

    def test_annual_volatility_annualises_by_sqrt_252(self):
        import math
        curve = self.curve([100, 110, 100, 110, 100])
        daily = [0.10, -1 / 11, 0.10, -1 / 11]
        expected = statistics.stdev(daily) * math.sqrt(252)
        self.assertAlmostEqual(metrics.annual_volatility(curve), expected, places=6)

    def test_turnover_is_annualised_one_way_notional(self):
        # 50 traded on an average value of 100 over exactly one year = 0.5.
        self.assertAlmostEqual(
            metrics.annual_turnover(traded_notional=50.0, average_value=100.0, years=1.0), 0.5)

    def test_turnover_of_a_never_traded_book_is_zero(self):
        self.assertEqual(metrics.annual_turnover(traded_notional=0.0, average_value=100.0, years=3.0), 0.0)

    def test_window_slices_a_calendar_year(self):
        curve = [(date(2007, 12, 31), 100.0), (date(2008, 6, 1), 60.0), (date(2009, 1, 2), 90.0)]
        self.assertEqual(len(metrics.window(curve, 2008)), 1)


if __name__ == "__main__":
    unittest.main()

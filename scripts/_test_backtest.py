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

from copilot.backtest import admission
from copilot.backtest import engine
from copilot.backtest import frame as frame_mod
from copilot.backtest import history
from copilot.backtest import metrics
from copilot.backtest import rules
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
        # Peak 01-03, curve ends 01-04 still underwater: duration is 1 day,
        # peak to the end of the curve, not peak to the (nonexistent) trough.
        self.assertEqual(dd.duration_days, 1)

    def test_a_later_equally_deep_unrecovered_drawdown_beats_an_earlier_recovered_one(self):
        # [100, 50, 100, 50]: the first 50%-drawdown (day0->day1) "recovers" at
        # day2, but the second, equally deep and never recovered by the end of
        # the curve, is the one a governance gate needs to see. A gate that
        # reads recovery_date is not None to mean "worst drawdown is behind
        # us" must not be fooled by the tie going to the earlier occurrence.
        dd = metrics.max_drawdown(self.curve([100, 50, 100, 50]))
        self.assertAlmostEqual(dd.depth, 0.5)
        self.assertIsNone(dd.recovery_date)
        self.assertEqual(dd.trough_date, date(2020, 1, 5))

    def test_flat_curve_has_zero_drawdown(self):
        dd = metrics.max_drawdown(self.curve([100, 100, 100]))
        self.assertEqual(dd.depth, 0.0)

    def test_cagr_matches_a_hand_computed_doubling(self):
        curve = [(date(2010, 1, 4), 100.0), (date(2020, 1, 3), 200.0)]
        self.assertAlmostEqual(metrics.cagr(curve), 0.0718, places=3)

    def test_sharpe_is_zero_for_a_flat_curve(self):
        self.assertEqual(metrics.sharpe(self.curve([100, 100, 100, 100])), 0.0)

    def test_sharpe_matches_a_hand_computed_ratio(self):
        # Independently derived (not by calling cagr()/annual_volatility()):
        # years = 363/365.25 = 0.9965777; cagr = (107/100)**(1/years)-1 =
        # 0.0702486; daily returns [.05, -1/15, 8/56, -5/112] have
        # stdev*sqrt(252) = 1.5249195; sharpe = cagr/vol = 0.0460671.
        dates = [date(2020, 1, 2) + timedelta(days=91 * i) for i in range(5)]
        curve = list(zip(dates, [100.0, 105.0, 98.0, 112.0, 107.0]))
        self.assertAlmostEqual(metrics.sharpe(curve), 0.046067, places=6)

    def test_sharpe_declares_its_risk_free_rate(self):
        self.assertEqual(metrics.RISK_FREE_RATE, 0.0)

    def test_non_positive_or_non_finite_value_is_rejected_everywhere(self):
        # start 100.0, end -5.0 -- verified to make cagr() a complex number
        # and let sharpe() swallow it behind the vol==0 guard before this fix.
        curve = [(date(2020, 1, 2), 100.0), (date(2020, 1, 3), -5.0)]
        for fn in (metrics.cagr, metrics.annual_volatility, metrics.sharpe, metrics.daily_returns):
            with self.assertRaises(ValueError):
                fn(curve)
        with self.assertRaises(ValueError):
            metrics.max_drawdown(curve)
        nan_curve = [(date(2020, 1, 2), 100.0), (date(2020, 1, 3), float("nan"))]
        with self.assertRaises(ValueError):
            metrics.cagr(nan_curve)

    def test_duplicate_or_unordered_dates_are_rejected(self):
        duplicate = [(date(2020, 1, 3), 100.0), (date(2020, 1, 3), 101.0)]
        unordered = [(date(2020, 1, 3), 100.0), (date(2020, 1, 2), 101.0)]
        for curve in (duplicate, unordered):
            with self.assertRaises(ValueError):
                metrics.cagr(curve)
            with self.assertRaises(ValueError):
                metrics.max_drawdown(curve)

    def test_a_single_bar_curve_stays_degenerate_not_malformed(self):
        # _validated() must not reject what the existing 0.0/zero-depth
        # defaults already handle -- a curve too short to carry a metric.
        one_bar = [(date(2020, 1, 2), 100.0)]
        self.assertEqual(metrics.cagr(one_bar), 0.0)
        self.assertEqual(metrics.annual_volatility(one_bar), 0.0)
        self.assertEqual(metrics.sharpe(one_bar), 0.0)
        self.assertEqual(metrics.daily_returns(one_bar), [])
        self.assertEqual(metrics.max_drawdown(one_bar).depth, 0.0)
        self.assertEqual(metrics.max_drawdown([]).depth, 0.0)

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
        self.assertEqual(metrics.window(curve, 2008), [(date(2008, 6, 1), 60.0)])


class CostModel(unittest.TestCase):
    def test_commission_has_a_floor(self):
        model = engine.CostModel()
        self.assertAlmostEqual(model.commission(shares=10, notional=1000.0), 1.00)

    def test_commission_is_per_share_above_the_floor(self):
        model = engine.CostModel()
        self.assertAlmostEqual(model.commission(shares=1000, notional=100000.0), 3.50)

    def test_commission_is_capped_at_one_percent_of_notional(self):
        model = engine.CostModel()
        self.assertAlmostEqual(model.commission(shares=10, notional=50.0), 0.50)

    def test_spread_is_half_the_quoted_width(self):
        model = engine.CostModel(spread_bps=4.0)
        self.assertAlmostEqual(model.spread(notional=10000.0), 2.00)

    def test_zero_cap_means_cap_at_zero_not_uncapped(self):
        # max_pct_of_notional=0.0 must be a real cap, not falsy-for-"no cap":
        # otherwise a deliberately zero-capped model silently charges full
        # commission (1.00 here) instead of the intended 0.0.
        model = engine.CostModel(per_share_usd=0.0035, minimum_usd=1.00,
                                 max_pct_of_notional=0.0, spread_bps=0.0)
        self.assertEqual(model.commission(shares=10, notional=50.0), 0.0)

    def test_free_is_genuinely_uncapped_not_capped_at_zero(self):
        model = engine.CostModel.free()
        self.assertIsNone(model.max_pct_of_notional)
        self.assertEqual(model.commission(shares=1_000_000, notional=1_000_000.0), 0.0)


class EngineLoop(unittest.TestCase):
    def flat_frame(self, n=30):
        return frame_mod.build(dates=[date(2020, 1, 1) + timedelta(days=i) for i in range(n)],
                               symbols=["AAA", "BBB"], closes=[[100.0, 50.0]] * n)

    def test_a_flat_market_with_no_costs_preserves_capital(self):
        result = engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 0.5, "BBB": 0.5}),
                            start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.0)
        self.assertAlmostEqual(result.curve[-1][1], 10000.0, places=6)

    def test_cash_floor_is_never_invaded(self):
        result = engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 1.0}),
                            start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.20)
        self.assertGreaterEqual(min(result.cash_history), 10000.0 * 0.20 - 1e-6)

    def test_integer_shares_are_the_default(self):
        frame = frame_mod.build(dates=[date(2020, 1, 1), date(2020, 1, 2)],
                                symbols=["AAA"], closes=[[333.0], [333.0]])
        result = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=1000.0,
                            cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        self.assertEqual(result.positions_history[0]["AAA"], 3)

    def test_costs_reduce_the_final_value(self):
        frame = self.flat_frame()
        free = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=10000.0,
                          cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        charged = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=10000.0,
                             cost_model=engine.CostModel(), cash_floor_pct=0.0)
        self.assertLess(charged.curve[-1][1], free.curve[-1][1])

    def test_no_lookahead_uses_only_bars_up_to_today(self):
        seen = []

        class Spy(engine.Rule):
            parameters = {}
            name = "spy"

            def weights(self, frame, i):
                seen.append(i)
                return {"AAA": 1.0}

        engine.run(self.flat_frame(10), rule=Spy(), start_cash=1000.0,
                   cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        self.assertTrue(all(i < 10 for i in seen))
        self.assertEqual(max(seen), 9)
        # max(seen) == 9 alone would pass for a rule only ever asked about
        # bars {0, 9}. Every index must actually have been visited.
        self.assertEqual(sorted(set(seen)), list(range(10)))

    def test_weights_that_do_not_sum_to_one_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 0.9}),
                       start_cash=1000.0, cost_model=engine.CostModel.free(), cash_floor_pct=0.0)

    def test_nan_weight_is_rejected_naming_the_symbol(self):
        # With integer_shares=False (a supported path) a NaN weight otherwise
        # sails through both the sum check and the negative check -- IEEE-754
        # makes every comparison against NaN false -- and produces a curve of
        # all-NaN values with zero exceptions raised.
        frame = self.flat_frame(2)
        with self.assertRaisesRegex(ValueError, "AAA"):
            engine._validate({"AAA": float("nan")}, frame)
        with self.assertRaises(ValueError):
            engine.run(frame, rule=engine.StaticWeights({"AAA": float("nan")}),
                       start_cash=1000.0, cost_model=engine.CostModel.free(),
                       cash_floor_pct=0.0, integer_shares=False)

    def test_run_raises_when_a_bar_value_turns_non_positive(self):
        # A cost model with an uncapped, huge minimum fee and a single-dollar
        # start turns portfolio value negative on bar 0. That must raise
        # rather than silently hand a negative "equity curve" to a caller.
        frame = frame_mod.build(dates=[date(2020, 1, 1)], symbols=["AAA"], closes=[[1.0]])
        ruinous = engine.CostModel(per_share_usd=0.0, minimum_usd=1000.0,
                                   max_pct_of_notional=None, spread_bps=0.0)
        with self.assertRaisesRegex(ValueError, "portfolio value"):
            engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=1.0,
                       cost_model=ruinous, cash_floor_pct=0.0)

    def test_result_reports_traded_notional_for_turnover(self):
        result = engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 1.0}),
                            start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.0)
        # 10000 cash, AAA@100, 100% target, no costs: buys exactly 100 shares
        # on bar 0 and never trades again on a flat book -- 100 * 100 = 10000.
        self.assertAlmostEqual(result.traded_notional, 10000.0)

    def test_rebalance_count_only_counts_bars_that_actually_traded(self):
        # 500 bars, $100 cash against a $1000/share price: every target rounds
        # down to zero shares, so should_rebalance fires every bar but nothing
        # ever trades. Before the fix this reported rebalance_count=500 -- the
        # count said "traded constantly" when the true answer is zero.
        frame = frame_mod.build(dates=[date(2020, 1, 1) + timedelta(days=i) for i in range(500)],
                                symbols=["AAA"], closes=[[1000.0]] * 500)
        result = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=100.0,
                            cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        self.assertEqual(result.rebalance_count, 0)
        self.assertEqual(result.total_costs, 0.0)

    def test_warmup_bars_are_skipped_entirely_by_the_engine(self):
        # Bars i < warmup_bars must produce no curve point, no cash entry, and
        # no positions entry -- not just an un-rebalanced entry -- because a
        # rule with a lookback (InverseVolatility, MomentumTopN) cannot compute
        # a real answer there and must never be asked to.
        frame = self.flat_frame(100)
        rule = rules.InverseVolatility(("AAA", "BBB"), lookback_days=60, rebalance_days=21)
        result = engine.run(frame, rule=rule, start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.0)
        self.assertEqual(len(result.curve), 100 - 60)
        self.assertEqual(len(result.cash_history), 100 - 60)
        self.assertEqual(len(result.positions_history), 100 - 60)
        self.assertEqual(result.curve[0][0], frame.dates[60])

    def test_momentum_warmup_matches_its_lookback_in_a_full_engine_run(self):
        frame = frame_mod.build(dates=[date(2020, 1, 1) + timedelta(days=i) for i in range(300)],
                                symbols=["AAA", "BBB", "CCC"],
                                closes=[[100.0 + i, 100.0 + i * 0.5, 100.0] for i in range(300)])
        rule = rules.MomentumTopN(("AAA", "BBB", "CCC"), top_n=2, lookback_days=252, skip_days=21)
        result = engine.run(frame, rule=rule, start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.0)
        self.assertEqual(len(result.curve), 300 - 252)
        self.assertEqual(result.curve[0][0], frame.dates[252])


class RuleFamilies(unittest.TestCase):
    def rising(self, n=400):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [[100.0 + i, 100.0 + i * 0.5, 100.0] for i in range(n)]
        return frame_mod.build(dates=dates, symbols=["FAST", "SLOW", "FLAT"], closes=closes)

    def test_every_family_declares_at_most_three_parameters(self):
        # Q29 rule 4: the only hard brake against overfitting.
        for rule in (rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}),
                     rules.InverseVolatility(("FAST", "SLOW")),
                     rules.MomentumTopN(("FAST", "SLOW", "FLAT"))):
            self.assertLessEqual(len(rule.parameters), 3, rule.name)

    def test_bands_hold_targets_constant(self):
        rule = rules.FixedWeightBands({"FAST": 0.6, "SLOW": 0.4})
        self.assertEqual(rule.weights(self.rising(), 100), {"FAST": 0.6, "SLOW": 0.4})

    def test_relative_band_fires_at_twenty_five_percent_drift(self):
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=0.25,
                                      absolute_band=1.0, calendar_days=10**6)
        frame = self.rising()
        self.assertFalse(rule.should_rebalance(frame, 50, {"FAST": 0.60, "SLOW": 0.40}, 0))
        self.assertTrue(rule.should_rebalance(frame, 50, {"FAST": 0.63, "SLOW": 0.37}, 0))

    def test_absolute_band_fires_at_five_points_on_a_small_target(self):
        # The half of Bogleheads 5/25 that bt's RunIfOutOfBounds does not implement:
        # a 5pp move on a 5% target is a 100% relative move, but a 5pp move on a
        # 50% target is only 10% relative and the relative band alone misses it.
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=1.0,
                                      absolute_band=0.05, calendar_days=10**6)
        frame = self.rising()
        self.assertFalse(rule.should_rebalance(frame, 50, {"FAST": 0.54, "SLOW": 0.46}, 0))
        self.assertTrue(rule.should_rebalance(frame, 50, {"FAST": 0.56, "SLOW": 0.44}, 0))

    def test_bands_bootstrap_when_there_is_no_prior_rebalance(self):
        # Isolates the `last_rebalance_index is None` disjunct: current already
        # matches targets exactly, so the drift loop alone would say "nothing to
        # do" -- but bar 0 never having rebalanced must still force one. This is
        # the bug that leaves bt 1.2.3 in 100% cash for an entire backtest.
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5})
        self.assertTrue(rule.should_rebalance(self.rising(), 0, {"FAST": 0.5, "SLOW": 0.5}, None))

    def test_bands_bootstrap_when_current_holdings_are_empty(self):
        # Isolates the `not current` disjunct: last_rebalance_index is set, but
        # the book holds nothing (every target rounded to zero shares). Bands
        # wide enough that "drift from empty" would not fire on its own must
        # still force a rebalance -- an empty book is never "on target".
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=2.0,
                                      absolute_band=0.6, calendar_days=10**6)
        self.assertTrue(rule.should_rebalance(self.rising(), 50, {}, 0))

    def test_bands_rogue_holding_absent_from_targets_triggers_rebalance(self):
        # A holding with no entry in `targets` at all (spun off into the book,
        # a manual trade, a bug upstream) has an implicit target of 0.0 and must
        # be able to trigger a rebalance like any drifted symbol -- iterating
        # self.targets alone made it invisible to the band check.
        rule = rules.FixedWeightBands({"FAST": 1.0}, relative_band=1.0, absolute_band=0.05,
                                      calendar_days=10**6)
        self.assertTrue(rule.should_rebalance(self.rising(), 50, {"FAST": 1.0, "ROGUE": 10.0}, 0))

    def test_calendar_leg_fires_after_the_interval(self):
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=1.0,
                                      absolute_band=1.0, calendar_days=365)
        frame = self.rising()
        on_target = {"FAST": 0.5, "SLOW": 0.5}
        self.assertFalse(rule.should_rebalance(frame, 100, on_target, 0))
        self.assertTrue(rule.should_rebalance(frame, 370, on_target, 0))

    def test_momentum_refuses_a_skip_that_swallows_the_formation_window(self):
        # A negative formation end wraps through Python negative indexing to a
        # bar near the END of the series: look-ahead dressed as a return.
        with self.assertRaisesRegex(ValueError, "smaller than"):
            rules.MomentumTopN(("FAST", "SLOW"), lookback_days=21, skip_days=21)
        with self.assertRaisesRegex(ValueError, "smaller than"):
            rules.MomentumTopN(("FAST", "SLOW"), lookback_days=21, skip_days=63)

    def test_momentum_refuses_a_non_positive_top_n(self):
        with self.assertRaisesRegex(ValueError, "top_n"):
            rules.MomentumTopN(("FAST", "SLOW"), top_n=0)
        with self.assertRaisesRegex(ValueError, "top_n"):
            rules.MomentumTopN(("FAST", "SLOW"), top_n=-1)

    def test_momentum_top_n_wider_than_the_universe_holds_them_all(self):
        weights = rules.MomentumTopN(("FAST", "SLOW"), top_n=5,
                                     lookback_days=252, skip_days=21).weights(self.rising(), 300)
        self.assertEqual(set(weights), {"FAST", "SLOW"})
        self.assertAlmostEqual(sum(weights.values()), 1.0)

    def test_inverse_volatility_refuses_a_lookback_too_short_for_a_stdev(self):
        with self.assertRaisesRegex(ValueError, "at least 2"):
            rules.InverseVolatility(("FAST", "SLOW"), lookback_days=1)
        with self.assertRaisesRegex(ValueError, "rebalance_days"):
            rules.InverseVolatility(("FAST", "SLOW"), rebalance_days=0)

    def test_rules_are_frozen_so_state_cannot_leak_between_runs(self):
        import dataclasses
        for rule in (rules.FixedWeightBands({"FAST": 1.0}),
                     rules.InverseVolatility(("FAST",)),
                     rules.MomentumTopN(("FAST",))):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                rule.name = "mutated"

    def test_inverse_volatility_gives_the_calmer_asset_more_weight(self):
        w = rules.InverseVolatility(("FAST", "FLAT"), lookback_days=60).weights(self.rising(), 300)
        self.assertGreater(w["FLAT"], w["FAST"])
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_inverse_volatility_matches_a_hand_computed_weight(self):
        # Pins the exact formula, not just an ordering: swapping inverse-stdev
        # for inverse-variance would still pass test_..._more_weight above but
        # fails this. FAST's 60-return stdev at i=300 is 0.00012853613134237014
        # (hand-computed from the fixture's closed form 1/(99+t)); FLAT's is
        # exactly 0.0 and floors to MIN_DAILY_VOLATILITY, so
        # inverse[FLAT]=1e6, inverse[FAST]=1/0.00012853613134237014, and the
        # normalized weights are these two constants to 9 decimal places.
        w = rules.InverseVolatility(("FAST", "FLAT"), lookback_days=60).weights(self.rising(), 300)
        self.assertAlmostEqual(w["FAST"], 0.007719853832572416, places=9)
        self.assertAlmostEqual(w["FLAT"], 0.9922801461674277, places=9)

    def test_inverse_volatility_raises_before_the_lookback_window_is_full(self):
        # Was: falls back to equal weighting below the lookback, an alphabetical-
        # style plausible wrong answer. warmup_bars=lookback_days means the
        # engine must never call weights() here; a direct call still must not
        # return a silently equal-weighted basket -- it must refuse.
        rule = rules.InverseVolatility(("FAST", "SLOW"), lookback_days=60)
        with self.assertRaisesRegex(ValueError, "warmup_bars"):
            rule.weights(self.rising(), 59)

    def test_inverse_volatility_computes_a_real_answer_at_exactly_the_warmup_bar(self):
        # Off-by-one fix: start = i - lookback_days = 0 at i == lookback_days is
        # a complete 61-price / 60-return window, not a partial one. The old
        # `start < 1` threshold fell back to equal weighting here even though
        # the data was complete.
        w = rules.InverseVolatility(("FAST", "SLOW"), lookback_days=60).weights(self.rising(), 60)
        self.assertNotAlmostEqual(w["FAST"], 0.5)

    def ranking_fixture(self, n=400):
        # HIGH starts far above the other two but grows slowly; LOW starts
        # lowest but grows fastest in percentage terms; MID sits between both
        # ways. At bar 350 (formation window 98..329, computed below), ranking
        # by raw end price gives HIGH > MID > LOW, but ranking by percentage
        # return over the formation window gives LOW > MID > HIGH -- the two
        # orderings disagree, which is the point: a bug that ranks by end price
        # instead of by return would pick the wrong top-N and this fixture
        # catches it, unlike a fixture where every symbol starts at the same
        # price (there the two orderings coincide by construction).
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [[1000.0 + i * 0.05, 200.0 + i * 0.5, 50.0 + i * 0.3] for i in range(n)]
        return frame_mod.build(dates=dates, symbols=["HIGH", "MID", "LOW"], closes=closes)

    def test_momentum_picks_the_strongest_and_equal_weights_them(self):
        # Formation window (98, 329): HIGH 1004.9->1016.45 = +1.15%,
        # MID 249->364.5 = +46.39%, LOW 79.4->148.7 = +87.28%. Return-ranked
        # top 2 are LOW and MID; end-price-ranked top 2 would wrongly be
        # HIGH and MID.
        w = rules.MomentumTopN(("HIGH", "MID", "LOW"), top_n=2,
                               lookback_days=252, skip_days=21).weights(self.ranking_fixture(), 350)
        self.assertEqual(set(w), {"LOW", "MID"})
        self.assertAlmostEqual(w["LOW"], 0.5)
        self.assertAlmostEqual(w["MID"], 0.5)

    def test_momentum_skips_the_most_recent_month(self):
        # Faber 2007 / Antonacci 12-1: the skip is what makes it momentum rather
        # than short-term reversal. Assert it is read, not just stored.
        rule = rules.MomentumTopN(("FAST", "SLOW"), lookback_days=252, skip_days=21)
        self.assertEqual(rule.formation_window(300), (300 - 252, 300 - 21))

    def test_momentum_has_no_cash_exit(self):
        # Q38: rotation only, no trend-following exit to cash. The structural
        # cash reserve lives in the engine, not here.
        w = rules.MomentumTopN(("FAST", "SLOW", "FLAT"), top_n=1).weights(self.rising(), 350)
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertNotIn("CASH", w)

    def test_momentum_raises_before_the_formation_window_is_full(self):
        # Was: sorted(universe)[:top_n], an alphabetical portfolio indistinguishable
        # from a real one by schema alone. warmup_bars=lookback_days means the
        # engine must never call weights() here; a direct call still must refuse
        # rather than hand back a plausible-looking basket.
        rule = rules.MomentumTopN(("FAST", "SLOW"), lookback_days=252, skip_days=21)
        with self.assertRaisesRegex(ValueError, "warmup_bars"):
            rule.weights(self.rising(), 251)

    def test_momentum_raises_on_a_non_positive_formation_price_instead_of_dropping_it(self):
        # frame_mod.build() cannot produce this (it validates every close), but
        # PriceFrame itself is a bare frozen dataclass with no __post_init__, so
        # direct construction bypasses that validation. Silently dropping the
        # symbol from scoring turned a 3-name universe into a 2-name portfolio
        # with the same {symbol: float} shape as a real 3-name one -- nothing
        # downstream could tell the difference. It must raise instead, naming
        # the symbol and the formation-start date.
        n = 400
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [[100.0 + i, 100.0 + i * 0.5, 0.0 if i == 98 else 100.0] for i in range(n)]
        frame = frame_mod.PriceFrame(dates=tuple(dates), symbols=("FAST", "SLOW", "FLAT"),
                                     closes=tuple(tuple(row) for row in closes))
        rule = rules.MomentumTopN(("FAST", "SLOW", "FLAT"), top_n=3, lookback_days=252, skip_days=21)
        with self.assertRaisesRegex(ValueError, "FLAT"):
            rule.weights(frame, 350)

    def test_inverse_volatility_should_rebalance_bootstraps_and_respects_interval(self):
        # Zero coverage before this: replacing the body with `return False` or
        # `return True` left every existing test passing.
        rule = rules.InverseVolatility(("FAST", "SLOW"), rebalance_days=21)
        frame = self.rising()
        self.assertTrue(rule.should_rebalance(frame, 100, {}, None))
        self.assertFalse(rule.should_rebalance(frame, 100, {}, 90))
        self.assertTrue(rule.should_rebalance(frame, 111, {}, 90))

    def test_momentum_should_rebalance_bootstraps_and_respects_interval(self):
        # Zero coverage before this: replacing the body with `return False` or
        # `return True` left every existing test passing.
        rule = rules.MomentumTopN(("FAST", "SLOW"), skip_days=21)
        frame = self.rising()
        self.assertTrue(rule.should_rebalance(frame, 300, {}, None))
        self.assertFalse(rule.should_rebalance(frame, 300, {}, 290))
        self.assertTrue(rule.should_rebalance(frame, 311, {}, 290))

    def test_warmup_bars_matches_the_lookback_each_family_needs(self):
        self.assertEqual(rules.FixedWeightBands({"FAST": 1.0}).warmup_bars, 0)
        self.assertEqual(rules.InverseVolatility(("FAST",), lookback_days=40).warmup_bars, 40)
        self.assertEqual(rules.MomentumTopN(("FAST",), lookback_days=200).warmup_bars, 200)


class AdmissionGate(unittest.TestCase):
    def passing_result(self):
        r = engine.Result(rule_name="demo", parameters={"a": 1.0, "b": 2.0})
        start, value = date(2005, 1, 3), 100.0
        for i in range(21 * 252):
            when = start + timedelta(days=int(i * 365.25 / 252))
            value *= 1.0003 if when.year not in (2008, 2020, 2022) else 0.9995
            r.curve.append((when, value))
        r.traded_notional, r.total_costs, r.rebalance_count = 5000.0, 50.0, 21
        return r

    def test_a_long_result_covering_all_three_windows_passes(self):
        report = admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS)
        self.assertTrue(report.admitted, report.failures)

    def test_a_short_backtest_fails_rule_one(self):
        r = engine.Result(rule_name="short", parameters={})
        r.curve = [(date(2020, 1, 2), 100.0), (date(2024, 1, 2), 150.0)]
        report = admission.assess(r, sessions_by_year={})
        self.assertFalse(report.admitted)
        self.assertTrue(any("15" in f for f in report.failures))

    def test_a_missing_stress_window_fails(self):
        r = self.passing_result()
        r.curve = [(w, v) for w, v in r.curve if w.year != 2008]
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("2008" in f for f in report.failures))

    def test_four_parameters_fail_rule_four(self):
        r = self.passing_result()
        r.parameters = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("parameter" in f for f in report.failures))

    def test_zero_cost_results_fail_rule_three(self):
        r = self.passing_result()
        r.total_costs = 0.0
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("cost" in f for f in report.failures))

    def test_report_carries_every_rule_two_metric(self):
        report = admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS)
        for key in ("cagr", "max_drawdown", "drawdown_duration_days", "annual_turnover", "sharpe"):
            self.assertIn(key, report.metrics)

    def test_out_of_sample_segment_is_reported_separately(self):
        report = admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS)
        self.assertIn("out_of_sample", report.metrics)
        self.assertIn("cagr", report.metrics["out_of_sample"])

    def test_stress_session_counts_are_the_verified_xnys_numbers(self):
        self.assertEqual(admission.STRESS_SESSIONS, {2008: 253, 2020: 253, 2022: 251})

    def test_a_waiver_records_its_reason_and_does_not_hide_the_failure(self):
        r = self.passing_result()
        r.curve = [(w, v) for w, v in r.curve if w.year != 2008]
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS,
                                  waivers={"stress_2008": "ADR-0006 clause 5: BXN starts 2009-09-18"})
        self.assertTrue(report.admitted)
        self.assertTrue(report.waived)
        self.assertIn("ADR-0006", " ".join(report.waiver_reasons))
        self.assertTrue(any("2008" in f for f in report.failures))

    def test_an_unknown_waiver_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown waiver"):
            admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS,
                             waivers={"whatever": "because"})


if __name__ == "__main__":
    unittest.main()

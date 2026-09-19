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
from copilot.backtest import bxn
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

    def test_first_bar_covers_every_qualified_symbol_exactly(self):
        # warmup_headroom_bars raises for a symbol with no FIRST_BAR entry
        # rather than guessing; this is what makes that safe to rely on for
        # every symbol classify() can hand back as admissible.
        self.assertEqual(set(universe.FIRST_BAR), universe.QUALIFIED)

    def test_warmup_headroom_flags_vea_as_the_one_short_qualified_symbol(self):
        # VEA's first bar (2007-07-26) is the promise in universe.py's own
        # docstring ("daily bars from at least 2007-07-26"), which is exactly
        # what makes it too short for a 252-bar lookback to reach back before
        # 2008-01-01: 22 qualified symbols, and VEA is the only one under 252.
        headroom = universe.warmup_headroom_bars(universe.QUALIFIED)
        short = {s for s, bars in headroom.items() if bars < 252}
        self.assertEqual(short, {"VEA"})

    def test_warmup_headroom_bars_is_never_negative(self):
        # A symbol whose first bar is on/after `before` has no pre-history to
        # speak of, not a negative amount of it.
        headroom = universe.warmup_headroom_bars(["VEA"], before=date(2000, 1, 1))
        self.assertEqual(headroom["VEA"], 0)

    def test_warmup_headroom_bars_raises_for_an_unrecorded_symbol(self):
        with self.assertRaisesRegex(ValueError, "SCHD"):
            universe.warmup_headroom_bars(["SCHD"])


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

    def test_direct_construction_cannot_bypass_validation(self):
        # PriceFrame used to be a bare frozen dataclass: every check build()
        # performed was skippable by constructing PriceFrame(...) directly.
        # __post_init__ now validates unconditionally, so the same bad input
        # must raise the same way through either entry point.
        with self.assertRaisesRegex(ValueError, "duplicate"):
            frame_mod.PriceFrame(dates=(date(2020, 1, 2),), symbols=("SPY", "SPY"),
                                 closes=((1.0, 2.0),))
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            frame_mod.PriceFrame(dates=(date(2020, 1, 3), date(2020, 1, 2)), symbols=("SPY",),
                                 closes=((1.0,), (2.0,)))
        with self.assertRaises(ValueError):
            frame_mod.PriceFrame(dates=(date(2020, 1, 2),), symbols=("SPY",), closes=((0.0,),))

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

    def test_trade_loop_is_deterministic_across_hash_seeds(self):
        # `for symbol in set(positions) | set(desired)` used to iterate a set
        # of strings, whose order varies with PYTHONHASHSEED, and float
        # addition is not associative -- so cash/total_costs/traded_notional
        # depended on the interpreter's hash seed. Demonstrated: the same
        # frame and rule run under five different seeds produced three
        # distinct bit patterns for the final value alone. This module's own
        # docstring opens with "Deterministic: no clock, no network, no
        # random", so this is run out-of-process (a hash seed is fixed for
        # the life of one interpreter) under several real seeds rather than
        # merely re-asserting the sort is present in the source.
        import os
        import subprocess
        import textwrap
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(Path(__file__).resolve().parent)!r})
            import random
            from datetime import date, timedelta
            from copilot.backtest import engine, frame as frame_mod, rules

            rng = random.Random(20260919)
            symbols = ["A", "B", "C", "D", "E", "F", "G", "H"]
            all_days = [date(2015, 1, 1) + timedelta(days=i) for i in range(1900)]
            dates = [d for d in all_days if d.weekday() < 5][:1200]
            prices = {{s: 100.0 + i * 3.0 for i, s in enumerate(symbols)}}
            closes = []
            for _ in dates:
                row = []
                for s in symbols:
                    prices[s] *= 1.0 + rng.uniform(-0.02, 0.02)
                    row.append(prices[s])
                closes.append(row)
            f = frame_mod.build(dates=dates, symbols=symbols, closes=closes)
            rule = rules.InverseVolatility(tuple(symbols), lookback_days=21, rebalance_days=5)
            result = engine.run(f, rule=rule, start_cash=1_000_000.0,
                                cost_model=engine.CostModel(), cash_floor_pct=0.1)
            print(repr(result.curve[-1][1]))
            print(repr(result.total_costs))
            print(repr(result.traded_notional))
            print(result.rebalance_count)
        """)
        outputs = {}
        for seed in ("0", "1", "2", "3", "42"):
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                                  env={**os.environ, "PYTHONHASHSEED": seed})
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertGreater(int(proc.stdout.strip().splitlines()[-1]), 50,
                              "fixture bug: too few rebalances to exercise the multi-symbol delta "
                              "loop this test targets")
            outputs[seed] = proc.stdout
        self.assertEqual(len(set(outputs.values())), 1,
                         "final value / total costs / traded notional differ across "
                         "PYTHONHASHSEED values: " + repr(outputs))


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

    def test_inverse_volatility_raises_on_a_non_positive_price_instead_of_dropping_it(self):
        # rules._returns used to silently skip a non-positive price instead of
        # raising, unlike MomentumTopN.weights' equivalent guard. A dropped
        # return silently shortens the series statistics.stdev sees, producing
        # a plausible-looking but wrong volatility estimate with no signal
        # anything was wrong. object.__new__ bypasses PriceFrame's own
        # construction-time validation on purpose, to reach this
        # defense-in-depth check the same way the momentum test above does.
        n = 100
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [[100.0 + i, 0.0 if i == 50 else 100.0] for i in range(n)]
        frame = object.__new__(frame_mod.PriceFrame)
        object.__setattr__(frame, "dates", tuple(dates))
        object.__setattr__(frame, "symbols", ("FAST", "SLOW"))
        object.__setattr__(frame, "closes", tuple(tuple(row) for row in closes))
        rule = rules.InverseVolatility(("FAST", "SLOW"), lookback_days=60)
        with self.assertRaisesRegex(ValueError, "non-positive"):
            rule.weights(frame, 80)

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
        # frame_mod.build() cannot produce this (it validates every close),
        # and neither can constructing PriceFrame directly any more --
        # __post_init__ now validates unconditionally (see PriceFrameContract
        # / the frame.py __post_init__ docstring). object.__new__ bypasses
        # __init__/__post_init__ on purpose here, to exercise MomentumTopN's
        # own defense-in-depth check: it is unreachable through any public
        # constructor today, but it is the second line of defense against a
        # non-positive price and is kept for a stranger who copies this
        # file's pattern, or a future PriceFrame that gains another
        # construction path. Silently dropping the symbol from scoring turned
        # a 3-name universe into a 2-name portfolio with the same
        # {symbol: float} shape as a real 3-name one -- nothing downstream
        # could tell the difference. It must raise instead, naming the symbol
        # and the formation-start date.
        n = 400
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [[100.0 + i, 100.0 + i * 0.5, 0.0 if i == 98 else 100.0] for i in range(n)]
        frame = object.__new__(frame_mod.PriceFrame)
        object.__setattr__(frame, "dates", tuple(dates))
        object.__setattr__(frame, "symbols", ("FAST", "SLOW", "FLAT"))
        object.__setattr__(frame, "closes", tuple(tuple(row) for row in closes))
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

    # --- Q29 rule 4's divergence check: with_parameters + caveats ----------

    def test_only_bands_carries_no_caveat(self):
        # Q22=C requires the other two families' caveats to be public; bands
        # genuinely has none.
        self.assertEqual(rules.FixedWeightBands({"FAST": 1.0}).caveats, ())
        self.assertIn("risk parity", " ".join(rules.InverseVolatility(("FAST",)).caveats))
        self.assertIn("no cash exit", " ".join(rules.MomentumTopN(("FAST",)).caveats))

    def test_with_parameters_coerces_int_fields_back_from_float(self):
        # sensitivity_grid's neighbour dicts are always float-valued (every
        # rule's own .parameters property returns floats), including for a
        # field the dataclass declares as int.
        bands = rules.FixedWeightBands({"FAST": 1.0}).with_parameters({"calendar_days": 400.0})
        self.assertEqual(bands.calendar_days, 400)
        self.assertIsInstance(bands.calendar_days, int)

        invvol = rules.InverseVolatility(("FAST",)).with_parameters({"lookback_days": 80.0})
        self.assertEqual(invvol.lookback_days, 80)
        self.assertIsInstance(invvol.lookback_days, int)

        momentum = rules.MomentumTopN(("FAST",)).with_parameters({"top_n": 6.0})
        self.assertEqual(momentum.top_n, 6)
        self.assertIsInstance(momentum.top_n, int)

    def test_with_parameters_leaves_other_fields_untouched(self):
        base = rules.InverseVolatility(("FAST", "SLOW"), lookback_days=63, rebalance_days=21)
        neighbour = base.with_parameters({"lookback_days": 88.0})
        self.assertEqual(neighbour.rebalance_days, 21)
        self.assertEqual(neighbour.universe, ("FAST", "SLOW"))

    def test_with_parameters_still_runs_post_init_guards(self):
        # A neighbour must be refused the same way direct construction is,
        # not silently accepted because it arrived through replace() instead
        # of __init__.
        with self.assertRaisesRegex(ValueError, "at least 2"):
            rules.InverseVolatility(("FAST",), lookback_days=10).with_parameters({"lookback_days": 1.0})
        with self.assertRaisesRegex(ValueError, "smaller than"):
            rules.MomentumTopN(("FAST", "SLOW")).with_parameters({"skip_days": 300.0})
        with self.assertRaisesRegex(ValueError, "top_n"):
            rules.MomentumTopN(("FAST", "SLOW")).with_parameters({"top_n": 0.0})


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

    def test_stress_year_shortfall_names_the_curve_start_date(self):
        # A coverage shortfall alone ("123 of 253 sessions") does not say
        # whether the strategy is broken or the frame's warm-up simply
        # consumed the start of the stress year (universe.warmup_headroom_bars
        # / ADR-0006). Attribute it: name where the curve actually starts.
        r = engine.Result(rule_name="late-start", parameters={})
        r.curve = [(date(2008, 7, 14) + timedelta(days=i), 100.0) for i in range(123)]
        r.total_costs, r.rebalance_count = 10.0, 1
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("curve starts 2008-07-14" in f for f in report.failures), report.failures)

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

    # --- mutation-review follow-up: waivers keyed to rules, not text --------

    def test_waiver_cannot_silence_a_rule_four_failure_by_text_coincidence(self):
        # Mutation-testing proof: the old _matches() paired a waiver to a
        # failure by checking whether the failure message started with the
        # numeral in the waiver's key. A rule-4 message that happens to start
        # with "2008" (because the result has 2008 parameters) must still
        # fail admission under a stress_2008 waiver -- the waiver is for an
        # unrelated rule and must never reach rule 4.
        r = self.passing_result()
        r.parameters = {f"p{i}": float(i) for i in range(2008)}
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS,
                                  waivers={"stress_2008": "unrelated waiver, must not reach rule 4"})
        self.assertFalse(report.admitted)
        self.assertTrue(any(f.startswith("2008 parameters") for f in report.failures))

    def test_stress_2008_waiver_does_not_silence_rule_three(self):
        r = self.passing_result()
        r.total_costs = 0.0
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS,
                                  waivers={"stress_2008": "unrelated waiver, must not reach rule 3"})
        self.assertFalse(report.admitted)
        self.assertTrue(any("cost" in f for f in report.failures))

    def test_stress_2008_waiver_does_not_silence_rule_four(self):
        r = self.passing_result()
        r.parameters = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS,
                                  waivers={"stress_2008": "unrelated waiver, must not reach rule 4"})
        self.assertFalse(report.admitted)
        self.assertTrue(any("parameter" in f for f in report.failures))

    def test_stress_2008_waiver_does_not_silence_rule_five(self):
        r = engine.Result(rule_name="iso5", parameters={})
        r.curve = [(date(2003, 1, 1), 100.0), (date(2019, 1, 5), 200.0), (date(2020, 6, 1), 210.0)]
        r.total_costs, r.rebalance_count = 10.0, 1
        report = admission.assess(r, sessions_by_year={},
                                  waivers={"stress_2008": "unrelated waiver, must not reach rule 5"})
        self.assertFalse(report.admitted)
        self.assertTrue(any("out-of-sample" in f for f in report.failures))

    def test_rule_four_boundary_exactly_three_passes_four_fails(self):
        # Mutating `>` to `>=` in the rule-4 check currently survives without
        # a boundary test.
        r3 = self.passing_result()
        r3.parameters = {"a": 1.0, "b": 2.0, "c": 3.0}
        self.assertTrue(admission.assess(r3, sessions_by_year=admission.STRESS_SESSIONS).admitted)

        r4 = self.passing_result()
        r4.parameters = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
        self.assertFalse(admission.assess(r4, sessions_by_year=admission.STRESS_SESSIONS).admitted)

    def test_rule_one_span_boundary(self):
        # 15 * 365.25 = 5478.75 days, not a whole number, so "exactly 15.0
        # years" cannot occur with real dates (span is always a whole number
        # of days divided by 365.25). This brackets the threshold as tightly
        # as integer-day arithmetic allows: one day short of it fails, one
        # day at-or-past it passes.
        short = engine.Result(rule_name="short-span", parameters={})
        short.curve = [(date(2005, 1, 1), 100.0), (date(2005, 1, 1) + timedelta(days=5478), 150.0)]
        short.total_costs, short.rebalance_count = 10.0, 1
        report = admission.assess(short, sessions_by_year={})
        self.assertTrue(any("rule 1 requires at least" in f for f in report.failures))

        long = engine.Result(rule_name="long-span", parameters={})
        long.curve = [(date(2005, 1, 1), 100.0), (date(2005, 1, 1) + timedelta(days=5479), 150.0)]
        long.total_costs, long.rebalance_count = 10.0, 1
        report2 = admission.assess(long, sessions_by_year={})
        self.assertFalse(any("rule 1 requires at least" in f for f in report2.failures))

    def test_rule_five_rejects_a_two_bar_sliver_and_accepts_a_three_year_segment(self):
        base = [(date(2003, 1, 1), 100.0)]

        sliver = engine.Result(rule_name="sliver", parameters={})
        sliver.curve = base + [(date(2020, 1, 6), 150.0), (date(2020, 1, 13), 151.0)]
        sliver.total_costs, sliver.rebalance_count = 10.0, 1
        report = admission.assess(sliver, sessions_by_year={})
        self.assertFalse(report.admitted)
        self.assertTrue(any("out-of-sample" in f for f in report.failures))

        long_segment = engine.Result(rule_name="long", parameters={})
        long_segment.curve = base + [(date(2019, 1, 6), 150.0), (date(2022, 1, 6), 180.0)]
        long_segment.total_costs, long_segment.rebalance_count = 10.0, 1
        report2 = admission.assess(long_segment, sessions_by_year={})
        self.assertTrue(report2.admitted, report2.failures)

    def test_a_blank_waiver_reason_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "reason"):
            admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS,
                             waivers={"stress_2008": ""})
        with self.assertRaisesRegex(ValueError, "reason"):
            admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS,
                             waivers={"stress_2008": "   "})

    def test_stress_coverage_requires_ninety_nine_percent(self):
        self.assertEqual(admission.MIN_STRESS_COVERAGE, 0.99)

    def test_missing_a_single_month_of_a_stress_year_fails_even_with_high_coverage(self):
        # A coverage ratio can stay above the floor while a whole month is
        # silently absent. Give 2008 a bar on every calendar day except
        # September -- 336 bars, far more than the 253 expected (so the ratio
        # check alone would pass) -- but zero bars in month 9.
        r = self.passing_result()
        r.curve = [(w, v) for w, v in r.curve if w.year != 2008]
        daily_2008 = [date(2008, 1, 1) + timedelta(days=i) for i in range(366)]
        padded = [(d, 100.0) for d in daily_2008 if d.month != 9]
        r.curve = sorted(r.curve + padded, key=lambda pair: pair[0])

        observed = len([w for w, _ in r.curve if w.year == 2008])
        self.assertGreaterEqual(observed / admission.STRESS_SESSIONS[2008], admission.MIN_STRESS_COVERAGE,
                                "fixture bug: the ratio check should pass here so only the month "
                                "check is what fails")

        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("2008" in f and "month" in f for f in report.failures))

    def test_bxn_q29_waiver_waives_2008_in_admission_while_keeping_it_visible(self):
        # A waiver does not delete a failure -- it records a reason beside it and
        # flips `admitted`. Prove both halves for bxn.Q29_WAIVER specifically, not
        # just that admitted becomes True.
        r = self.passing_result()
        r.curve = [(w, v) for w, v in r.curve if w.year != 2008]
        without_waiver = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(without_waiver.admitted)
        self.assertTrue(any("2008" in f for f in without_waiver.failures))

        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS,
                                  waivers=bxn.Q29_WAIVER)
        self.assertTrue(report.admitted, report.failures)
        self.assertTrue(report.waived)
        self.assertIn("ADR-0006", " ".join(report.waiver_reasons))
        # Still visible: waiving does not delete the failure from the record.
        self.assertTrue(any("2008" in f for f in report.failures))


class BxnProxy(unittest.TestCase):
    CSV = "DATE,BXN\n09/18/2009,298.140000\n09/21/2009,299.500000\n09/22/2009,301.250000\n"

    def test_parses_the_two_column_close_only_file(self):
        frame = bxn.parse_csv(self.CSV)
        self.assertEqual(frame.symbols, ("^BXN",))
        self.assertEqual(frame.dates[0], date(2009, 9, 18))
        self.assertAlmostEqual(frame.column("^BXN")[0], 298.14)

    def test_rejects_an_unexpected_header(self):
        with self.assertRaisesRegex(ValueError, "header"):
            bxn.parse_csv("DATE,BXNT\n09/18/2009,1.0\n")

    def test_rejects_an_empty_file(self):
        with self.assertRaises(ValueError):
            bxn.parse_csv("DATE,BXN\n")

    def test_the_waiver_text_cites_the_adr(self):
        self.assertIn("ADR-0006", bxn.Q29_WAIVER["stress_2008"])
        self.assertIn("2009-09-18", bxn.Q29_WAIVER["stress_2008"])

    def test_waiver_covers_2008_only(self):
        self.assertEqual(set(bxn.Q29_WAIVER), {"stress_2008"})

    def test_the_label_never_claims_the_funds_passed(self):
        label = bxn.evidence_label(("QQQI", "JEPQ"))
        self.assertIn("index proxy", label.lower())
        self.assertNotIn("passed", label.lower())
        for symbol in ("QQQI", "JEPQ"):
            self.assertIn(symbol, label)

    def test_module_never_writes_to_disk(self):
        # Cboe's terms forbid storing the file. urlopen( contains open( as a
        # substring, so the filesystem call is matched with a negative lookbehind
        # rather than a bare `in` check.
        import re
        source = Path(bxn.__file__).read_text(encoding="utf-8")
        for forbidden in ("write_text", "write_bytes", "pathlib", "mkdir", "shutil", "tempfile"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIsNone(re.search(r"(?<!url)open\(", source),
                          "bxn.py must not open a file; the CSV stays in memory")

    # --- "verify rather than assume" items beyond the plan's 7 tests ---

    def test_stress_2008_key_is_actually_waivable(self):
        self.assertIn("stress_2008", admission.WAIVABLE)

    def test_rejects_a_malformed_date_instead_of_silently_misparsing(self):
        with self.assertRaises(ValueError):
            bxn.parse_csv("DATE,BXN\n2009-09-18,298.140000\n")


class CliContract(unittest.TestCase):
    def test_cli_module_imports_without_third_party_packages(self):
        import importlib
        module = importlib.import_module("backtest_cli")
        self.assertTrue(hasattr(module, "main"))

    def _qualifying_series(self, symbol, start=date(2005, 1, 3), end=date(2023, 1, 1)):
        from copilot.backtest import history
        dates, d = [], start
        while d < end:
            if d.weekday() < 5:
                dates.append(d)
            d += timedelta(days=1)
        closes = tuple(100.0 + i * 0.01 for i in range(len(dates)))
        return history.Series(symbol=symbol, dates=tuple(dates), split_adjusted=closes,
                              split_and_dividend_adjusted=closes, dropped_bars=0)

    def test_universe_is_fetched_exactly_once_per_symbol_not_per_family(self):
        # Three families over 12 symbols would be 36 Yahoo requests if each
        # family loaded its own data, against a rate-limit evidence base of one
        # 30-request run. It also lets the three backtests disagree if Yahoo
        # revised a bar mid-run. A source-level count of "history.fetch(" only
        # proves one call SITE exists in the text -- moving load_universe(symbols)
        # inside the per-family loop (reintroducing exactly this bug) left that
        # count at 1 while making 8 real fetch calls for a 2-symbol, 3-family
        # run. Count actual calls instead.
        import contextlib
        import io
        import tempfile
        import backtest_cli
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=lambda s: self._qualifying_series(s)) as fetched, \
             tempfile.TemporaryDirectory() as tmp, \
             contextlib.redirect_stdout(io.StringIO()):
            code = backtest_cli.main(["--universe", "SPY,QQQ", "--family", "all", "--out-dir", tmp])
        self.assertEqual(code, 0)
        self.assertEqual(fetched.call_count, 2)

    def test_sensitivity_walks_each_parameter_both_ways(self):
        import backtest_cli
        grid = backtest_cli.sensitivity_grid({"lookback_days": 252.0, "top_n": 5.0})
        self.assertIn({"lookback_days": 227.0, "top_n": 5.0}, grid)
        self.assertIn({"lookback_days": 277.0, "top_n": 5.0}, grid)
        self.assertIn({"lookback_days": 252.0, "top_n": 4.0}, grid)
        self.assertIn({"lookback_days": 252.0, "top_n": 6.0}, grid)
        self.assertEqual(len(grid), 4)

    def test_output_directory_is_gitignored_audit(self):
        import backtest_cli
        self.assertTrue(str(backtest_cli.DEFAULT_OUT_DIR).replace("\\", "/").endswith("data/audit"))

    def test_verify_universe_accepts_an_unfetchable_symbol_staying_unfetchable(self):
        # SPLG is tiered UNFETCHABLE because the chart endpoint 404s for it. A
        # live run reporting that is the tier table being RIGHT; counting it as
        # a problem made --verify-universe exit 1 on a correct classification.
        import contextlib
        import io
        import backtest_cli
        from copilot.backtest import history
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=history.NotCovered("SPLG: HTTP 404")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            problems = backtest_cli.verify_universe(["SPLG"])
        self.assertEqual(problems, 0)
        self.assertIn("ok ", out.getvalue())

    def test_verify_universe_flags_an_unexpected_disappearance(self):
        import contextlib
        import io
        import backtest_cli
        from copilot.backtest import history
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=history.NotCovered("SPY: HTTP 404")), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            problems = backtest_cli.verify_universe(["SPY"])
        self.assertEqual(problems, 1)
        self.assertIn("DRIFT", out.getvalue())

    def test_verify_universe_flags_an_unfetchable_symbol_that_now_fetches(self):
        # The other direction: the tier table claims SPLG cannot be fetched. If
        # it can, the table is stale -- without this branch the symbol fell
        # through to the qualified/not-qualified comparison and, having a short
        # history, was reported "ok" for the wrong reason.
        import contextlib
        import io
        import backtest_cli
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=lambda s: self._qualifying_series(s)), \
             contextlib.redirect_stdout(io.StringIO()) as out:
            problems = backtest_cli.verify_universe(["SPLG"])
        self.assertEqual(problems, 1)
        self.assertIn("DRIFT", out.getvalue())
        self.assertIn("tiered unfetchable", out.getvalue())

    def test_verify_universe_uses_price_frame_helpers_not_hand_rolled_arithmetic(self):
        # frame.PriceFrame.span_years()/sessions_in_year() were implemented,
        # documented and unit-tested with no production caller; verify_universe
        # hand-rolled the identical (last - first).days / 365.25 formula
        # instead of using them.
        import backtest_cli
        source = Path(backtest_cli.__file__).read_text(encoding="utf-8")
        self.assertIn(".span_years()", source)
        self.assertIn(".sessions_in_year(", source)
        self.assertNotIn("sum(1 for d in series.dates", source)

    def test_verify_universe_defaults_to_default_candidates(self):
        # universe.default_candidates() was likewise only called from its own
        # test. verify_universe's default symbol set now comes from it instead
        # of a second, independently maintained hardcoded set.
        import contextlib
        import io
        import backtest_cli
        from copilot.backtest import universe
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=lambda s: self._qualifying_series(s)) as fetched, \
             contextlib.redirect_stdout(io.StringIO()):
            backtest_cli.verify_universe()
        self.assertEqual(sorted(c.args[0] for c in fetched.call_args_list),
                         list(universe.default_candidates()))

    def test_verify_universe_checks_only_the_symbols_it_is_given(self):
        import contextlib
        import io
        import backtest_cli
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=lambda s: self._qualifying_series(s)) as fetched, \
             contextlib.redirect_stdout(io.StringIO()):
            problems = backtest_cli.verify_universe(["SPY", "QQQ"])
        self.assertEqual(sorted(c.args[0] for c in fetched.call_args_list), ["QQQ", "SPY"])
        self.assertEqual(problems, 0)

    def test_load_universe_turns_a_fetch_failure_into_a_clean_systemexit(self):
        # verify_universe already handled this gracefully; the real
        # report-writing path let the exception propagate out of main()
        # uncaught -- a transient Yahoo 404 gave a raw traceback naming
        # internal module paths, and nothing was written to data/audit/.
        import backtest_cli
        from copilot.backtest import history

        def flaky(symbol):
            if symbol == "QQQ":
                raise history.NotCovered("QQQ: HTTP 404 from the chart endpoint")
            return self._qualifying_series(symbol)

        with mock.patch.object(backtest_cli, "_fetch", side_effect=flaky):
            with self.assertRaises(SystemExit) as ctx:
                backtest_cli.load_universe(("SPY", "QQQ"))
        self.assertIn("QQQ", str(ctx.exception))
        self.assertIsNotNone(ctx.exception.__cause__)

    def test_income_proxy_tier_names_the_flag_that_exists_for_it(self):
        # A user who lists QQQI in --universe got a bare SystemExit for tier
        # 'income_proxy_only' with no pointer to the --income-proxy flag that
        # exists precisely for it.
        import backtest_cli
        with self.assertRaises(SystemExit) as ctx:
            backtest_cli.load_universe(("QQQI",))
        self.assertIn("--income-proxy", str(ctx.exception))

    def test_universe_rejects_duplicate_symbols_before_any_fetch(self):
        import contextlib
        import io
        import backtest_cli
        with mock.patch.object(backtest_cli, "_fetch") as fetched, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                backtest_cli.main(["--universe", "SPY,SPY,QQQ"])
        fetched.assert_not_called()

    def test_universe_rejects_a_degenerate_all_commas_value_before_any_fetch(self):
        import contextlib
        import io
        import backtest_cli
        with mock.patch.object(backtest_cli, "_fetch") as fetched, \
             contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                backtest_cli.main(["--universe", " , , "])
        fetched.assert_not_called()

    def test_sensitivity_is_actually_evaluated_not_just_reported(self):
        # sensitivity_grid() only ever produced neighbour parameter dicts;
        # nothing ran a backtest on them or compared metrics. Q29 rule 4
        # requires reporting parameter sensitivity, and a grid nobody
        # evaluates is not a report.
        import contextlib
        import io
        import tempfile
        import backtest_cli
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=lambda s: self._qualifying_series(s)), \
             tempfile.TemporaryDirectory() as tmp, \
             contextlib.redirect_stdout(io.StringIO()):
            code = backtest_cli.main(["--universe", "SPY,QQQ", "--family", "invvol", "--out-dir", tmp])
            report = json.loads(next(Path(tmp).glob("backtest-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        family = report["families"][0]
        self.assertEqual(family["rule"], "inverse_volatility")
        neighbours = family["sensitivity"]["neighbours"]
        self.assertEqual(len(neighbours), 4)  # 2 parameters x 2 directions
        with_metrics = [n for n in neighbours if "cagr" in n]
        self.assertTrue(with_metrics, "no neighbour produced real metrics")
        for n in with_metrics:
            self.assertIsInstance(n["cagr"], float)
            self.assertIsInstance(n["max_drawdown"], float)
        self.assertIsInstance(family["sensitivity"]["max_abs_cagr_delta"], float)
        # No invented threshold: the note says so, and nothing in the report
        # flips admitted/failures based on the divergence.
        self.assertIn("no automatic", family["sensitivity"]["note"].lower())

    def test_sensitivity_records_a_rejected_neighbour_instead_of_crashing(self):
        # A neighbour whose parameters fail with_parameters' __post_init__
        # guard (or whose backtest cannot run) must be recorded, not raised --
        # one bad neighbour must not crash the whole family's report.
        import backtest_cli
        from copilot.backtest import rules
        frame = None  # unused when with_parameters itself raises
        result = backtest_cli._evaluate_neighbour(
            frame, rules.MomentumTopN(("FAST", "SLOW")),
            {"skip_days": 300.0}, start_cash=10000.0, cash_floor_pct=0.1)
        self.assertIn("error", result)
        self.assertNotIn("cagr", result)

    def test_report_carries_each_familys_own_caveats(self):
        import contextlib
        import io
        import tempfile
        import backtest_cli
        from copilot.backtest import rules
        with mock.patch.object(backtest_cli, "_fetch",
                               side_effect=lambda s: self._qualifying_series(s)), \
             tempfile.TemporaryDirectory() as tmp:
            captured = io.StringIO()
            with contextlib.redirect_stdout(captured):
                code = backtest_cli.main(["--universe", "SPY,QQQ", "--family", "all", "--out-dir", tmp])
            report = json.loads(next(Path(tmp).glob("backtest-*.json")).read_text(encoding="utf-8"))
        self.assertEqual(code, 0)
        by_rule = {f["rule"]: f for f in report["families"]}
        self.assertEqual(by_rule["fixed_weight_bands"]["caveats"], [])
        self.assertEqual(by_rule["inverse_volatility"]["caveats"], list(rules.InverseVolatility.caveats))
        self.assertEqual(by_rule["momentum_top_n"]["caveats"], list(rules.MomentumTopN.caveats))
        # Reaches the console, not just the JSON: a stranger reading
        # `momentum_top_n ADMITTED, CAGR +11%` in the terminal has no other
        # way to learn the strategy has no cash exit.
        console = captured.getvalue()
        self.assertIn("no cash exit", console)
        self.assertIn("risk parity", console)

    def test_cross_check_script_is_never_imported_by_shipped_code(self):
        # Matched as an import statement, not as a substring: rules.py's own
        # docstring names "scripts/_cross_check_bt.py" as the oracle that
        # cross-checks it (Q39's "cross-check against independent
        # implementations"), and a bare substring search would flag that
        # documentation as if it were a real import.
        import subprocess
        hits = subprocess.run(
            ["git", "grep", "-lE", r"^\s*(import|from)\s+_cross_check_bt\b",
             "--", "scripts", "mcps", "evals"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
        # git grep exits 1 for "no matches" (the expected/passing case here)
        # and 0 when it finds one (the failure this test exists to catch).
        # Any other code -- no .git present, e.g. a `git archive` export;
        # git not on PATH; wrong cwd -- must not be silently read as "no
        # matches": before this check, a literal "import _cross_check_bt"
        # added to backtest_cli.py still passed this test when run outside a
        # git working tree, because an empty stdout from a failed command
        # looks identical to an empty stdout from a successful one that
        # truly found nothing.
        self.assertIn(hits.returncode, (0, 1),
                     f"git grep exited {hits.returncode}, expected 0 or 1: {hits.stderr}")
        found = [line for line in hits.stdout.splitlines()
                 if not line.endswith(("_cross_check_bt.py", "_test_backtest.py"))]
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()

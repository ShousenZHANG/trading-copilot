"""Offline market-data contracts. No network, keys, or personal-state writes.

Run: python scripts/_test_market_data.py
Optional real-calendar contracts: uv run --with exchange-calendars==4.13.2
    --with tzdata==2026.3 python scripts/_test_market_data.py --calendars
"""
from __future__ import annotations

import copy
import io
import json
import math
import os
import sys
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from email.message import Message
from types import ModuleType
from urllib.error import HTTPError
from unittest.mock import patch

from copilot.data_calendar import CalendarUnavailable, MarketCalendar, iso, parse_time
from copilot.instruments import get_instrument, normalize_instrument
from copilot.market_data import collect_snapshot, compute_indicators, snapshot_digest, verify_snapshot
from copilot.providers import HttpClient, NasdaqEquityProvider, NasdaqIndexProvider, ProviderError, SGEProvider, YahooProvider, parse_sge_daily, parse_sge_shau, safe_url

NOW = datetime(2026, 9, 6, 12, tzinfo=timezone.utc)


class FixtureCalendar:
    """Explicit test-only weekday fixture; production never uses this class."""
    def is_session(self, instrument, session):
        return date.fromisoformat(session).weekday() < 5

    def close(self, instrument, session):
        return datetime.combine(date.fromisoformat(session), time(20), timezone.utc)

    def window(self, instrument, decision_at):
        return "2026-09-04", NOW + timedelta(hours=12)


def history(count=280, *, gold=False):
    days, cursor = [], date(2026, 9, 4)
    while len(days) < count:
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor -= timedelta(days=1)
    return [{"session": day, "open": 100 + index / 10, "high": 101 + index / 10,
             "low": 99 + index / 10, "close": 100 + index / 10,
             "adjusted_close": 100 + index / 10, "volume": 1000 + index}
            for index, day in enumerate(reversed(days))]


class FixtureProvider:
    def __init__(self, name="yahoo", upstream="Yahoo Finance", *, bars=None, error=None, **extra):
        self.name, self.upstream, self.bars, self.error = name, upstream, bars or history(), error
        self.extra = extra

    def fetch(self, instrument, start, end, decision_at):
        if self.error:
            raise ProviderError(self.error, "fixture failure")
        return {"provider": self.name, "upstream": self.upstream,
                "source_url": "https://example.com/market", "retrieved_at": iso(NOW), "status": "ok",
                **{key: instrument[key] for key in ("instrument_id", "currency", "unit", "price_kind", "adjustment")},
                "bars": copy.deepcopy(self.bars), "indicator_basis": "total_return_adjusted", **self.extra}


def snapshot(providers=None, symbols=None, **kwargs):
    return collect_snapshot(symbols or ["QQQ"], providers=providers if providers is not None else [FixtureProvider()],
                            calendar=FixtureCalendar(), clock=lambda: NOW, **kwargs)


class SnapshotContracts(unittest.TestCase):
    def test_batch_deadline_stops_later_providers_and_marks_unknown(self):
        elapsed, calls = [0.0], []
        class SlowProvider(FixtureProvider):
            def fetch(self, *args):
                calls.append("slow")
                elapsed[0] = 2.0
                return super().fetch(*args)
        class MustNotRun(FixtureProvider):
            def fetch(self, *args):
                calls.append("unexpected")
                return super().fetch(*args)
        result = snapshot([SlowProvider(), MustNotRun("alpaca", "Alpaca SIP")], ["QQQ", "QQQM"],
                          max_collection_seconds=1.0, time_fn=lambda: elapsed[0])
        self.assertEqual(calls, ["slow"])
        self.assertEqual(result["instruments"]["QQQ"]["quality_status"], "unknown")
        self.assertEqual(result["instruments"]["QQQM"]["quality_status"], "unknown")
        self.assertNotIn("stale", str(result))
        self.assertTrue(any("deadline_exceeded" in issue for issue in result["issues"]))

    def test_registered_etf_is_confirmed_by_provider_metadata(self):
        seen = []
        class Corroborator(FixtureProvider):
            def fetch(self, instrument, *args):
                seen.append(instrument["asset_class"])
                return super().fetch(instrument, *args)
        sources = [FixtureProvider(asset_class="etf"), Corroborator("alpaca", "Alpaca SIP", asset_class="etf")]
        result = snapshot(sources, ["JEPI"])
        item = result["instruments"]["JEPI"]
        self.assertEqual(item["asset_class"], "etf")
        self.assertEqual(item["identity_status"], "provider_confirmed")
        self.assertEqual(seen, ["etf"])
        self.assertEqual(item["quality_status"], "pass")

    def test_registered_etf_cannot_be_reclassified_to_stock(self):
        result = snapshot([FixtureProvider(asset_class="stock")])
        self.assertEqual(result["instruments"]["QQQ"]["asset_class"], "etf")
        self.assertEqual(result["status"], "blocked")

    def test_unconfirmed_yahoo_stays_unknown(self):
        result = snapshot()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["instruments"]["QQQ"]["quality_status"], "unknown")
        self.assertEqual(result["instruments"]["QQQ"]["latest_session"], "2026-09-04")
        self.assertTrue(verify_snapshot(result))

    def test_independent_matching_source_passes(self):
        result = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP")])
        self.assertEqual(result["status"], "ready")
        self.assertEqual(len(result["instruments"]["QQQ"]["comparisons"]), 5)

    def test_same_upstream_wrapper_is_not_confirmation(self):
        result = snapshot([FixtureProvider(), FixtureProvider("wrapper", "Yahoo Finance")])
        self.assertEqual(result["instruments"]["QQQ"]["quality_status"], "unknown")

    def test_conflicting_recent_close_blocks(self):
        other = history()
        other[-1].update(close=130, high=131)
        result = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP", bars=other)])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("cross_provider_price_conflict", result["instruments"]["QQQ"]["issues"])

    def test_stale_secondary_does_not_confirm_fresh_primary(self):
        result = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP", bars=history()[:-1])])
        self.assertEqual(result["instruments"]["QQQ"]["quality_status"], "unknown")
        self.assertIn("alpaca: stale", result["instruments"]["QQQ"]["issues"])

    def test_nasdaq_equity_corroboration_rejects_split_or_unknown_adjustment(self):
        for split, quality in ((0, "pass"), (4, "unknown"), (None, "unknown")):
            records = history()
            if split is not None:
                records[-1]["splits"] = split
            current = [{"session": records[-1]["session"], "close": records[-1]["close"]}]
            sources = [FixtureProvider(bars=records), FixtureProvider("nasdaq", "Nasdaq US market data", bars=current,
                        adjustment="unadjusted_latest_session", indicator_basis="single_current_session")]
            self.assertEqual(snapshot(sources)["instruments"]["QQQ"]["quality_status"], quality)

    def test_explicit_alpaca_adjustment_can_validate_on_split_day(self):
        records = history()
        records[-1]["splits"] = 4
        current = [{"session": records[-1]["session"], "close": records[-1]["close"]}]
        sources = [FixtureProvider(bars=records), FixtureProvider("alpaca", "Alpaca SIP", bars=records),
                   FixtureProvider("nasdaq", "Nasdaq US market data", bars=current,
                                   adjustment="unadjusted_latest_session", indicator_basis="single_current_session")]
        self.assertEqual(snapshot(sources)["instruments"]["QQQ"]["quality_status"], "pass")

    def test_empty_and_nan_not_success(self):
        for bad in ([], [{"session": "2026-09-04", "close": math.nan}]):
            provider = FixtureProvider()
            provider.bars = bad
            result = snapshot([provider])
            self.assertEqual(result["status"], "blocked")
            self.assertIsNone(result["instruments"]["QQQ"]["price"])
            json.dumps(result, allow_nan=False)

    def test_wrong_currency_is_rejected(self):
        result = snapshot([FixtureProvider(currency="CNY")])
        self.assertEqual(result["status"], "blocked")

    def test_conflicting_duplicate_day_rejected(self):
        records = history()
        records.append({**records[-1], "volume": 9000})
        self.assertEqual(snapshot([FixtureProvider(bars=records)])["status"], "blocked")

    def test_history_gaps_remove_session_based_indicators(self):
        records = history()
        del records[-10]
        result = snapshot([FixtureProvider(bars=records), FixtureProvider("alpaca", "Alpaca SIP", bars=records)])
        item = result["instruments"]["QQQ"]
        self.assertEqual(item["quality_status"], "unknown")
        self.assertNotIn("sma200", item["indicators"])

    def test_insufficient_history_cannot_pass(self):
        result = snapshot([FixtureProvider(bars=history(50)), FixtureProvider("alpaca", "Alpaca SIP", bars=history(50))])
        item = result["instruments"]["QQQ"]
        self.assertEqual(item["quality_status"], "unknown")
        self.assertIsNone(item["indicators"]["sma200"])

    def test_403_and_no_key_are_capabilities_not_empty_prices(self):
        for error in ("not_entitled", "rate_limited", "not_configured"):
            result = snapshot([FixtureProvider(error=error)])
            self.assertEqual(result["status"], "blocked")
            self.assertEqual(result["capabilities"][0]["status"], error)

    def test_calendar_failure_blocks_before_provider_called(self):
        class Broken(FixtureCalendar):
            def window(self, *args):
                raise CalendarUnavailable("test unavailable")
        result = collect_snapshot(["QQQ"], clock=lambda: NOW, calendar=Broken(), providers=[FixtureProvider()])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["evidence"], [])

    def test_historical_query_cannot_fake_point_in_time(self):
        with self.assertRaises(ValueError):
            snapshot(decision_at="2025-09-05T12:00:00Z")
        with self.assertRaises(ValueError):
            snapshot(decision_at="2026-09-06T12:00:00")
        with self.assertRaises(ValueError):
            snapshot(decision_at="2026-09-06T12:03:00Z")

    def test_future_publication_cannot_pass_current_decision(self):
        result = snapshot([FixtureProvider(available_at="2026-09-07T12:00:00Z")])
        self.assertEqual(result["status"], "blocked")

    def test_future_and_nontrading_bars_are_rejected(self):
        for day in ("2026-09-09", "2026-09-05"):
            records = history()
            records.append({**records[-1], "session": day})
            self.assertEqual(snapshot([FixtureProvider(bars=records)])["status"], "blocked")

    def test_mutation_and_history_revision_change_snapshot_id(self):
        result = snapshot()
        previous = result["snapshot_id"]
        result["instruments"]["QQQ"]["price"] += 1
        self.assertFalse(verify_snapshot(result))
        self.assertNotEqual(previous, snapshot_digest(result))
        revised = history()
        revised[0]["adjusted_close"] -= 1
        self.assertNotEqual(previous, snapshot([FixtureProvider(bars=revised)])["snapshot_id"])

    def test_adjusted_missing_never_falls_back_to_raw(self):
        records = history()
        del records[0]["adjusted_close"]
        self.assertEqual(snapshot([FixtureProvider(bars=records)])["status"], "blocked")

    def test_authoritative_gold_benchmark_is_not_retail_price(self):
        result = snapshot([FixtureProvider("sge", "Shanghai Gold Exchange", authoritative=True,
                                           bars=history(1), indicator_basis="unadjusted_benchmark")], ["gold"])
        item = result["instruments"]["GOLD.CNY"]
        self.assertEqual(item["quality_status"], "pass")
        self.assertFalse(item["tradable"])
        self.assertEqual(item["price_kind"], "sge_au9999_close")
        self.assertIn("retail_product_sell_buyback_quotes_required_for_purchase_price", item["issues"])

    def test_shau_does_not_invent_fixing_timestamp(self):
        result = snapshot([FixtureProvider("sge", "Shanghai Gold Exchange", authoritative=True,
                                           bars=history(1), indicator_basis="unadjusted_benchmark")], ["SHAU"])
        self.assertIsNone(result["evidence"][0]["observed_at"])
        self.assertEqual(result["evidence"][0]["observation_date"], "2026-09-04")

    def test_scope_rejects_futures_fx_foreign_symbols(self):
        for value in ("GC=F", "XAUUSD=X", "0700.HK", "BHP.AX", "../QQQ"):
            with self.assertRaises(ValueError):
                normalize_instrument(value)
        self.assertEqual(normalize_instrument("NDX"), "^NDX")
        self.assertEqual(get_instrument("QQQM")["asset_class"], "etf")
        self.assertEqual(get_instrument("^IXIC")["unit"], "point")


class IndicatorContracts(unittest.TestCase):
    def test_wilder_uptrend_and_flat_prices(self):
        result = compute_indicators(history(), "total_return_adjusted")
        self.assertEqual(result["rsi14"], 100)
        self.assertAlmostEqual(result["atr14"], 2.0)
        self.assertAlmostEqual(result["sma200"], sum(100 + x / 10 for x in range(80, 280)) / 200)
        flat = [{"close": 100, "open": 100, "high": 100, "low": 100} for _ in range(260)]
        self.assertEqual(compute_indicators(flat)["rsi14"], 50)
        self.assertEqual(compute_indicators(flat)["atr14"], 0)

    def test_split_and_dividend_basis_is_explicit(self):
        records = [{"close": 200, "adjusted_close": 100, "open": 200, "high": 202, "low": 198} for _ in range(30)]
        adjusted = compute_indicators(records, "total_return_adjusted")
        split = compute_indicators(records, "split_adjusted")
        self.assertAlmostEqual(adjusted["sma20"], 100)
        self.assertAlmostEqual(split["sma20"], 200)
        self.assertAlmostEqual(adjusted["atr14"], 2)
        self.assertAlmostEqual(split["atr14"], 4)

    def test_252_session_return_needs_253_prices(self):
        self.assertIsNone(compute_indicators(history(252))["return_252_sessions"])
        self.assertIsNotNone(compute_indicators(history(253))["return_252_sessions"])

    def test_finite_inputs_with_overflow_do_not_create_infinity(self):
        with self.assertRaises(ProviderError):
            compute_indicators([{"close": 1e308} for _ in range(260)])


class SGEParserContracts(unittest.TestCase):
    def test_au9999_close_not_weighted_average(self):
        html = '<table><tr><th>日期</th><th>合约</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>涨跌</th><th>涨跌幅</th><th>加权平均</th><th>成交量</th></tr><tr><!-- <td>1</td> --><td>2026-09-04</td><td>Au99.99</td><td>958.20</td><td>975</td><td>958.20</td><td>965.95</td><td>8.45</td><td>0.88%</td><td>966.56</td><td>5029.98</td></tr></table>'
        bars = parse_sge_daily(html)
        self.assertEqual(bars[0]["close"], 965.95)
        self.assertEqual(bars[0]["weighted_average"], 966.56)
        self.assertEqual(bars[0]["volume_unit"], "kilogram")

    def test_shau_pm_uses_last_round_not_au9999(self):
        head = '<table><tr><th>SerialNo.</th><th>Trade Date</th><th>CONT</th><th>Session</th><th>RND</th><th>PRC</th></tr>'
        rows = ''.join(f'<tr><td>1</td><td>2026-09-04</td><td>SHAU</td><td>{session}</td><td>{number}</td><td>{price}</td></tr>' for session, number, price in (("早盘", 1, 968.3), ("午盘", 1, 962), ("午盘", 2, 963.44)))
        parsed = parse_sge_shau(head + rows + '</table>')
        self.assertEqual(parsed[0]["close"], 963.44)
        self.assertEqual(parsed[0]["benchmark_session"], "PM")

    def test_changed_columns_do_not_parse_as_success(self):
        self.assertEqual(parse_sge_daily('<table><tr><th>unexpected schema</th></tr></table>'), [])
        self.assertEqual(parse_sge_shau('<table><tr><th>unexpected schema</th></tr></table>'), [])


class ProviderContracts(unittest.TestCase):
    def test_yahoo_refresh_clears_process_cache_and_keeps_explicit_adjustment(self):
        calls = []
        yf, yf_data = ModuleType("yfinance"), ModuleType("yfinance.data")
        class Data:
            def cache_get(self):
                pass
            cache_get.cache_clear = lambda: calls.append("clear")
        class Frame:
            empty = False
            def iterrows(self):
                return iter([(datetime(2026, 9, 4), {"Close": 100, "Adj Close": 99, "Open": 99,
                                                    "High": 101, "Low": 98, "Volume": 200})])
        class Ticker:
            def __init__(self, symbol):
                self.symbol = symbol
            def history(self, **kwargs):
                calls.append(kwargs)
                return Frame()
            def get_history_metadata(self):
                return {"symbol": "QQQ", "currency": "USD", "instrumentType": "ETF",
                        "exchangeName": "NMS", "exchangeTimezoneName": "America/New_York"}
        yf.Ticker, yf_data.YfData = Ticker, Data
        yf.set_tz_cache_location = lambda path: calls.append(("cache_dir", path))
        with patch.dict(sys.modules, {"yfinance": yf, "yfinance.data": yf_data}):
            for _ in range(2):
                source = YahooProvider().fetch(get_instrument("QQQ"), "2025-01-01", "2026-09-05", NOW)
                self.assertEqual(source["adjustment"], "split")
                self.assertEqual(source["bars"][0]["adjusted_close"], 99)
        self.assertEqual(calls.count("clear"), 2)
        locations = [call[1] for call in calls if isinstance(call, tuple) and call[0] == "cache_dir"]
        self.assertTrue(all("trading-copilot-provider-cache" in path and "process-" in path for path in locations))
        request = next(call for call in calls if isinstance(call, dict))
        self.assertFalse(request["auto_adjust"])
        self.assertFalse(request["repair"])
        self.assertFalse(request["prepost"])
        self.assertTrue(request["keepna"])

    def test_nasdaq_ingests_only_recent_index_close_not_unverified_ohlc(self):
        class Client:
            def get(self, url, provider, headers):
                self.url = url
                return {"body": json.dumps({"status": {"rCode": 200}, "data": {"symbol": "NDX", "totalRecords": 1,
                    "tradesTable": {"headers": {"date": "Date", "close": "Close/Last"},
                                    "rows": [{"date": "09/04/2026", "close": "29,544.16", "open": "invalid"}]}}}),
                        "source_url": url, "retrieved_at": iso(NOW)}
        client = Client()
        source = NasdaqIndexProvider(client).fetch(get_instrument("^NDX"), "2025-01-01", "2026-09-05", NOW)
        self.assertEqual(source["bars"], [{"session": "2026-09-04", "close": 29544.16}])
        self.assertIn("fromdate=2026-08-21", client.url)

    def test_nasdaq_equity_uses_historical_session_not_info_timestamp(self):
        class Client:
            def get(self, url, provider, headers):
                if "/info?" in url:
                    payload = {"data": {"symbol": "QQQ", "assetClass": "ETF", "exchange": "NASDAQ-GM",
                               "primaryData": {"lastSalePrice": "$100", "lastTradeTimestamp": "Sep 3, 2026"}}}
                else:
                    self.history_url = url
                    payload = {"status": {"rCode": 200}, "data": {"symbol": "QQQ", "totalRecords": 2,
                        "tradesTable": {"headers": {"date": "Date", "close": "Close/Last"}, "rows": [
                            {"date": "09/03/2026", "close": "99"}, {"date": "09/04/2026", "close": "100"}]}}}
                return {"body": json.dumps(payload), "source_url": url, "retrieved_at": iso(NOW)}
        client = Client()
        source = NasdaqEquityProvider(client).fetch(get_instrument("QQQ"), "2025-01-01", "2026-09-05", NOW)
        self.assertEqual(source["bars"], [{"session": "2026-09-04", "close": 100}])
        self.assertEqual(source["adjustment"], "unadjusted_latest_session")
        self.assertIn("fromdate=2026-08-28", client.history_url)
        self.assertIn("todate=2026-09-04", client.history_url)

    def test_sge_history_requests_never_exceed_one_month(self):
        from urllib.parse import parse_qs, urlsplit
        class Client:
            def __init__(self):
                self.calls = []
            def get(self, url, provider):
                params = parse_qs(urlsplit(url).query)
                first, last = date.fromisoformat(params["start_date"][0]), date.fromisoformat(params["end_date"][0])
                self.calls.append((last - first).days)
                columns = ("日期", "合约", "开盘", "最高", "最低", "收盘", "涨跌", "涨幅", "加权均价", "成交量")
                head = '<table><tr>' + ''.join('<th>' + value + '</th>' for value in columns) + '</tr>'
                rows = []
                day = last
                while day >= first:
                    if day.weekday() < 5:
                        values = [day.isoformat(), "Au99.99", "100", "101", "99", "100", "0", "0", "100", "10"]
                        rows.append('<tr>' + ''.join('<td>' + value + '</td>' for value in values) + '</tr>')
                    day -= timedelta(days=1)
                return {"body": head + ''.join(rows) + '</table>', "source_url": url, "retrieved_at": iso(NOW)}
        client = Client()
        source = SGEProvider(client, history_bars=280).fetch(get_instrument("GOLD.CNY"), "2025-01-01", "2026-09-05", NOW)
        self.assertTrue(all(span <= 27 for span in client.calls))
        self.assertGreaterEqual(len(source["bars"]), 280)
        self.assertLessEqual(len(client.calls), 36)


class HttpContracts(unittest.TestCase):
    def test_slow_request_uses_remaining_budget_and_never_retries_after_deadline(self):
        elapsed, timeouts = [0.0], []
        def slow(request, timeout):
            timeouts.append(timeout)
            elapsed[0] += timeout
            raise TimeoutError("simulated blocked request")
        http = HttpClient(timeout=15, attempts=3, budget_seconds=3, time_fn=lambda: elapsed[0], opener=slow)
        with self.assertRaises(ProviderError) as failure:
            http.get("https://example.com/slow", "deadline-test")
        self.assertEqual(failure.exception.status, "deadline_exceeded")
        self.assertEqual(timeouts, [3.0])
        with self.assertRaises(ProviderError):
            http.get("https://example.com/another", "other-provider")
        self.assertEqual(len(timeouts), 1)

    def test_retry_delay_cannot_exceed_remaining_budget(self):
        elapsed, calls, sleeps = [0.0], [], []
        def unavailable(request, timeout):
            calls.append(timeout)
            elapsed[0] += 1
            raise HTTPError(request.full_url, 503, "slow", {}, None)
        http = HttpClient(timeout=15, attempts=3, budget_seconds=1.5, time_fn=lambda: elapsed[0],
                          opener=unavailable, sleeper=lambda seconds: sleeps.append(seconds))
        with self.assertRaises(ProviderError) as failure:
            http.get("https://example.com/slow", "deadline-retry-test")
        self.assertEqual(failure.exception.status, "deadline_exceeded")
        self.assertEqual(calls, [1.5])
        self.assertEqual(sleeps, [])

    def test_403_classification_hides_key(self):
        def denied(request, timeout):
            raise HTTPError(request.full_url, 403, "credential=SUPERSECRET", {}, None)
        with self.assertRaises(ProviderError) as failure:
            HttpClient(opener=denied, attempts=1).get("https://example.com/?api_key=SUPERSECRET", "alpaca")
        self.assertEqual(failure.exception.status, "not_entitled")
        self.assertNotIn("SUPERSECRET", str(failure.exception))

    def test_429_long_retry_after_does_not_hammer(self):
        calls = []
        def limited(request, timeout):
            calls.append(request)
            raise HTTPError(request.full_url, 429, "slow", {"Retry-After": "120"}, None)
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ProviderError) as failure:
                HttpClient(directory, opener=limited).get("https://example.com/", "fixture-quota")
            self.assertEqual(failure.exception.status, "rate_limited")
            self.assertEqual(len(calls), 1)

    def test_cached_success_does_not_mask_current_failure(self):
        calls = []
        class Response(io.BytesIO):
            headers = Message()
        def answer(request, timeout):
            calls.append(request)
            if len(calls) == 1:
                return Response(b'{"value": 10}')
            raise HTTPError(request.full_url, 503, "failed", {}, None)
        with tempfile.TemporaryDirectory() as directory:
            http = HttpClient(directory, opener=answer, attempts=1)
            first = http.get("https://example.com/?api_key=secret", "fixture-cache")
            self.assertNotIn("secret", first["source_url"])
            with self.assertRaises(ProviderError):
                http.get("https://example.com/?api_key=secret", "fixture-cache")
            self.assertEqual(len(calls), 2)


@unittest.skipUnless("--calendars" in sys.argv, "optional pinned calendar runtime contract")
class RealCalendarContracts(unittest.TestCase):
    def test_us_weekend_labor_day_early_close_and_dst(self):
        calendar, instrument = MarketCalendar(), get_instrument("QQQ")
        for timestamp, expected in [("2026-09-06T12:00:00Z", "2026-09-04"),
                                    ("2026-09-07T23:00:00Z", "2026-09-04"),
                                    ("2026-11-27T18:20:00Z", "2026-11-25"),
                                    ("2026-11-27T18:31:00Z", "2026-11-27")]:
            self.assertEqual(calendar.window(instrument, parse_time(timestamp))[0], expected)
        self.assertEqual(iso(calendar.close(instrument, "2026-03-06")), "2026-03-06T21:00:00Z")
        self.assertEqual(iso(calendar.close(instrument, "2026-03-09")), "2026-03-09T20:00:00Z")

    def test_sge_chinese_holidays_night_bar_and_unknown_year(self):
        calendar, instrument = MarketCalendar(), get_instrument("GOLD.CNY")
        for timestamp, expected in [("2026-02-23T12:00:00Z", "2026-02-13"),
                                    ("2026-10-07T12:00:00Z", "2026-09-30"),
                                    ("2026-09-04T15:00:00Z", "2026-09-04"),
                                    ("2026-09-07T02:00:00Z", "2026-09-04")]:
            self.assertEqual(calendar.window(instrument, parse_time(timestamp))[0], expected)
        self.assertFalse(calendar.is_session(instrument, "2026-02-28"))
        with self.assertRaises(CalendarUnavailable):
            calendar.is_session(instrument, "2027-01-04")


class SafeUrlContracts(unittest.TestCase):
    def test_substring_named_secrets_are_dropped(self):
        cleaned = safe_url("https://example.test/v1?api_key=abc&token=xyz&symbol=QQQ")
        self.assertNotIn("abc", cleaned)
        self.assertNotIn("xyz", cleaned)
        self.assertIn("symbol=QQQ", cleaned)

    def test_short_secret_parameter_names_are_dropped(self):
        """IBKR Flex passes its token as ?t= — one character, no substring match."""
        cleaned = safe_url("https://example.test/FlexWebService/SendRequest?t=SECRETTOKEN&q=12345&v=3")
        self.assertNotIn("SECRETTOKEN", cleaned)
        self.assertIn("q=12345", cleaned)
        self.assertIn("v=3", cleaned)

    def test_live_credential_value_is_scrubbed_under_any_parameter_name(self):
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "live-fixture-value"}, clear=True):
            cleaned = safe_url("https://example.test/q?unexpected=live-fixture-value&symbol=QQQ")
        self.assertNotIn("live-fixture-value", cleaned)
        self.assertIn("symbol=QQQ", cleaned)

    def test_credential_embedded_in_the_path_is_scrubbed(self):
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "live-fixture-value"}, clear=True):
            cleaned = safe_url("https://example.test/v1/live-fixture-value/quote")
        self.assertNotIn("live-fixture-value", cleaned)

    def test_fragment_does_not_survive(self):
        self.assertNotIn("#", safe_url("https://example.test/p?a=1#secret-fragment"))

    def test_non_secret_urls_round_trip(self):
        with patch.dict(os.environ, {}, clear=True):
            url = "https://finance.yahoo.com/quote/QQQ/history/"
            self.assertEqual(safe_url(url), url)

    def test_no_live_credentials_means_no_scrubbing_work(self):
        with patch.dict(os.environ, {}, clear=True):
            cleaned = safe_url("https://example.test/q?symbol=QQQ&range=1y")
        self.assertIn("symbol=QQQ", cleaned)
        self.assertIn("range=1y", cleaned)


if __name__ == "__main__":
    unittest.main(argv=[argument for argument in sys.argv if argument not in {"--calendars", "--self-test"}])

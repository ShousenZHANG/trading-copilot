"""Injected HTTP tests for scoped research; no real credentials/network/state."""
from __future__ import annotations

import copy
import json
import os
import sys
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot import research_data as research
from copilot.providers import ProviderError

AS_OF = "2026-01-06T20:00:00Z"
ACCN = "0000000123-26-000001"
FUTURE_ACCN = "0000000123-26-000002"


class Client:
    def __init__(self, routes=None):
        self.routes = routes or {}
        self.calls = []
        self.retrieved = "2026-01-06T20:01:00Z"
        self.cache_status = "live"

    def get(self, url, provider, headers=None):
        self.calls.append((url, provider))
        payload = self.routes.get(urlsplit(url).path, ProviderError("unavailable", "mock has no route"))
        if isinstance(payload, Exception):
            raise payload
        return {"body": json.dumps(copy.deepcopy(payload)), "source_url": url,
                "retrieved_at": self.retrieved, "cache_status": self.cache_status, "sha256": "fixture"}


def observation(value="2.1", day="2026-01-05", **overrides):
    return dict({"date": day, "value": value, "realtime_start": "2026-01-06", "realtime_end": "2026-01-06"}, **overrides)


def fred_routes():
    return {"/fred/series/observations": {"observations": [observation()]},
            "/fred/series/release": {"releases": [{"id": 18}]},
            "/fred/release/dates": {"release_dates": [{"release_id": 18, "date": "2026-01-05"}]},
            "/fred/series/vintagedates": {"vintage_dates": ["2026-01-05"]}}


def sec_routes():
    recent = {"accessionNumber": [ACCN, FUTURE_ACCN],
              "acceptanceDateTime": ["2026-01-06T10:00:00Z", "2026-01-06T22:00:00Z"],
              "form": ["10-Q", "10-Q/A"], "filingDate": ["2026-01-06", "2026-01-06"],
              "reportDate": ["2025-12-31", "2025-12-31"], "primaryDocument": ["report.htm", "amendment.htm"]}
    fact = {"val": 100, "end": "2025-12-31", "filed": "2026-01-06", "accn": ACCN, "form": "10-Q"}
    return {"/submissions/CIK0000000123.json": {"cik": "123", "filings": {"recent": recent}},
            "/api/xbrl/companyfacts/CIK0000000123.json": {"cik": 123, "facts": {"us-gaap": {"Assets": {"units": {"USD": [fact, dict(fact, val=900, accn=FUTURE_ACCN), dict(fact, val=999, accn="0000000123-25-000003")]}}}}}}


def article(**kwargs):
    result = {"id": 1, "datetime": 1767718800, "url": "https://issuer.example/announcement", "source": "Issuer", "headline": "Company update", "summary": "Source report"}
    result.update(kwargs)
    return result


class ResearchTests(unittest.TestCase):
    def setUp(self):
        self.environment = patch.dict(os.environ, {}, clear=True)
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def snapshot(self, asset_class="stock"):
        return {"decision_at": AS_OF, "status": "partial", "instruments": {"AAPL": {"asset_class": asset_class, "quality_status": "fail"}}, "evidence": []}

    def fred(self, client=None, series="DFII10", **kwargs):
        os.environ["FRED_API_KEY"] = "fake-fred-secret"
        return research.fred_series(series, client or Client(fred_routes()), as_of=kwargs.get("as_of", AS_OF))

    def sec(self, client=None):
        os.environ["SEC_USER_AGENT"] = "Test test@example.invalid"
        return research.sec_company("AAPL", client or Client(sec_routes()), as_of=AS_OF, ticker_map={"0": {"ticker": "AAPL", "cik_str": 123}})

    def news(self, rows, client=None):
        os.environ["FINNHUB_API_KEY"] = "fake-news-secret"
        return research.company_news("AAPL", client or Client({"/api/v1/company-news": rows}), as_of=AS_OF)

    def test_missing_configuration_typed_and_no_network(self):
        client = Client()
        result = research.enrich(self.snapshot(), client=client)
        self.assertEqual(client.calls, [])
        self.assertTrue(all(row["status"] == "not_configured" for row in result["research_issues"]))
        self.assertEqual(result["instruments"]["AAPL"]["research"]["filings"]["status"], "not_configured")
        self.assertEqual(result["instruments"]["AAPL"]["quality_status"], "fail")

    def test_errors_keep_entitlement_rate_limit_and_redact_exception(self):
        os.environ["FINNHUB_API_KEY"] = "fake-news-secret"
        for status in ("not_entitled", "rate_limited", "not_covered", "stale"):
            with self.subTest(status=status):
                client = Client({"/api/v1/company-news": ProviderError(status, "URL token=fake-news-secret")})
                result = research.enrich(self.snapshot("etf"), client=client)
                self.assertEqual(result["instruments"]["AAPL"]["research"]["news"]["status"], status)
                self.assertNotIn("fake-news-secret", json.dumps(result))

    def test_fred_verifies_release_and_vintage_without_inventing_timestamp(self):
        client = Client(fred_routes())
        result = self.fred(client)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["critical_evidence_eligible"])
        self.assertEqual(result["data"]["latest_release_date"], "2026-01-05")
        self.assertEqual(result["data"]["latest_vintage_date"], "2026-01-05")
        self.assertIsNone(result["available_at"])
        self.assertIsNone(result["data"]["publication_time"])
        self.assertEqual(len(client.calls), 4)
        self.assertNotIn("fake-fred-secret", json.dumps(result))
        self.assertEqual(parse_qs(urlsplit(client.calls[0][0]).query)["realtime_end"], ["2026-01-06"])

    def test_fred_same_day_vintage_cannot_prove_intraday_history(self):
        routes = fred_routes()
        routes["/fred/series/vintagedates"] = {"vintage_dates": ["2026-01-06"]}
        result = self.fred(Client(routes))
        self.assertEqual(result["status"], "unknown")
        self.assertFalse(result["critical_evidence_eligible"])

    def test_fred_unavailable_calendar_preserves_typed_gap(self):
        routes = fred_routes()
        routes["/fred/series/release"] = ProviderError("not_entitled", "secret URL")
        result = self.fred(Client(routes))
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["data"]["release_verification_issue"]["status"], "not_entitled")

    def test_fred_stale_observation_or_cached_response_ineligible(self):
        routes = fred_routes()
        routes["/fred/series/observations"] = {"observations": [observation(day="2025-10-01")]}
        result = self.fred(Client(routes))
        self.assertEqual(result["status"], "stale")
        self.assertFalse(result["critical_evidence_eligible"])
        client = Client(fred_routes())
        client.cache_status = "cached"
        result = self.fred(client)
        self.assertEqual(result["status"], "stale")

    def test_fred_future_vintage_and_nonfinite_rows_rejected(self):
        for row, status in [(observation(realtime_start="2026-01-07"), "unavailable"), (observation(value="NaN"), "malformed"), (observation(day="2026-99-01"), "malformed")]:
            with self.subTest(row=row):
                routes = fred_routes()
                routes["/fred/series/observations"] = {"observations": [row]}
                with self.assertRaises(ProviderError) as raised:
                    self.fred(Client(routes))
                self.assertEqual(raised.exception.status, status)

    def test_fred_cpi_yoy_uses_matching_month_separate_from_index(self):
        routes = fred_routes()
        routes["/fred/series/observations"] = {"observations": [observation(value="303", day="2025-12-01"), observation(value="300", day="2024-12-01")]}
        result = self.fred(Client(routes), series="CPIAUCSL")
        self.assertEqual(result["data"]["value"], 303)
        self.assertAlmostEqual(result["data"]["yoy_percent"], 1)
        self.assertEqual(result["data"]["unit"], "index_1982_1984_100")

    def test_sec_fact_requires_visible_accession_not_same_filed_date(self):
        result = self.sec()
        facts = result[1]["data"]["facts"]["Assets"]["USD"]
        self.assertEqual([row["val"] for row in facts], [100])
        self.assertEqual(facts[0]["accepted_at"], "2026-01-06T10:00:00+00:00")
        self.assertEqual(result[1]["data"]["excluded_without_visible_accession_or_future_period"], 2)
        self.assertEqual(len(result[0]["data"]["filings"]), 1)

    def test_sec_keeps_units_tags_periods_separate(self):
        routes = sec_routes()
        facts = routes["/api/xbrl/companyfacts/CIK0000000123.json"]["facts"]["us-gaap"]
        base = facts["Assets"]["units"]["USD"][0]
        facts["Assets"]["units"]["EUR"] = [dict(base, val=80)]
        facts["Revenues"] = {"units": {"USD": [dict(base, val=30, start="2025-10-01"), dict(base, val=120, start="2025-01-01")]}}
        result = self.sec(Client(routes))[1]["data"]
        self.assertEqual(set(result["facts"]["Assets"]), {"USD", "EUR"})
        self.assertEqual(len(result["facts"]["Revenues"]["USD"]), 2)
        self.assertNotIn("currency", result)

    def test_sec_malformed_cik_document_array_and_nan(self):
        for mutation in ("cik", "document", "array", "nan"):
            with self.subTest(mutation=mutation):
                routes = sec_routes()
                submission = routes["/submissions/CIK0000000123.json"]
                if mutation == "cik":
                    submission["cik"] = "999"
                elif mutation == "document":
                    submission["filings"]["recent"]["primaryDocument"][0] = "../../secret"
                elif mutation == "array":
                    submission["filings"]["recent"]["form"] = []
                else:
                    routes["/api/xbrl/companyfacts/CIK0000000123.json"]["facts"]["us-gaap"]["Assets"]["units"]["USD"][0]["val"] = float("nan")
                with self.assertRaises(ProviderError) as raised:
                    self.sec(Client(routes))
                self.assertEqual(raised.exception.status, "malformed")

    def test_news_empty_future_and_old_do_not_prove_no_events(self):
        result = self.news([article(datetime=1999999999), article(datetime=1)])
        self.assertEqual(result["status"], "unavailable")
        self.assertEqual(result["data"]["articles"], [])
        self.assertIn("empty does not prove no events", result["data"]["coverage"])

    def test_news_redacts_keys_urls_and_keeps_aggregator_tier(self):
        result = self.news([article(url="https://issuer.example/news?token=fake-news-secret", summary="fake-news-secret text")])
        self.assertEqual(result["status"], "ok")
        self.assertFalse(result["critical_evidence_eligible"])
        self.assertNotIn("fake-news-secret", json.dumps(result))
        self.assertNotIn("token=", json.dumps(result))

    def test_news_malformed_url_id_or_numeric_rejected(self):
        for changes in ({"url": "javascript:alert(1)"}, {"url": "https://user:secret@issuer.example/news"}, {"id": "1"}, {"datetime": "NaN"}):
            with self.subTest(changes=changes), self.assertRaises(ProviderError):
                self.news([article(**changes)])

    def test_future_retrieval_timestamp_rejected(self):
        client = Client({"/api/v1/company-news": [article()]})
        client.retrieved = "2099-01-01T00:00:00Z"
        with self.assertRaises(ProviderError):
            self.news([], client)


if __name__ == "__main__":
    unittest.main(verbosity=2)

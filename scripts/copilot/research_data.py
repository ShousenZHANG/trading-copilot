"""First-party filing/macro evidence and free news, with explicit availability.

This supplements prices; it never upgrades a failed price quality status. Every
value retains its published period/units. Missing credentials are visible gaps.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit

from .providers import HttpClient, ProviderError, finite_number, safe_url

_HOSTS = {"fred": {"api.stlouisfed.org", "fred.stlouisfed.org"}, "sec": {"data.sec.gov", "www.sec.gov"}, "finnhub": {"finnhub.io"}}
_STATUSES = {"not_configured", "not_entitled", "not_covered", "rate_limited", "stale", "malformed", "unavailable", "unknown"}


def _time(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone missing")
        return result.astimezone(timezone.utc)
    except (ValueError, AttributeError, TypeError):
        raise ProviderError("malformed", "invalid timestamp or missing timezone") from None


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ProviderError("malformed", "date must be YYYY-MM-DD")
    try:
        date.fromisoformat(value)
    except ValueError:
        raise ProviderError("malformed", "invalid calendar date") from None
    return value


def _url(value, provider=None):
    if not isinstance(value, str) or any(character.isspace() for character in value):
        raise ProviderError("malformed", "invalid source URL")
    parsed = urlsplit(value)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
        raise ProviderError("malformed", "source URL must be public HTTP(S)")
    if provider and (parsed.scheme != "https" or parsed.hostname not in _HOSTS[provider]):
        raise ProviderError("malformed", "source URL does not match provider")
    return safe_url(value)


def _redact(value):
    """Scrub live credential values from anything that may reach a model or disk.

    The name list is not repeated here: it comes from service.KEY_NAMES through
    secret_values(), because a second hardcoded copy had already drifted -
    SEC_USER_AGENT was loaded and never redacted.
    """
    from .service import secret_values
    if isinstance(value, str):
        for secret in secret_values():
            value = value.replace(secret, "[redacted]")
        return value
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, dict):
        return {key: _redact(item) for key, item in value.items()}
    return value


def _body(result, expected_type=dict):
    try:
        payload = json.loads(result["body"], parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite JSON")))
    except (ValueError, KeyError, TypeError):
        raise ProviderError("malformed", "source returned invalid JSON") from None
    if not isinstance(payload, expected_type):
        raise ProviderError("malformed", "unexpected JSON shape")
    return payload


def _stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def _evidence(provider, result, content, *, as_of, observed_at=None, status="ok", critical=True) -> dict:
    cutoff, retrieved = _time(as_of), _time(result.get("retrieved_at"))
    if retrieved > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ProviderError("malformed", "future retrieval timestamp")
    if observed_at is not None and (_time(observed_at) > cutoff or _time(observed_at) > retrieved):
        raise ProviderError("malformed", "future observation timestamp")
    if result.get("cache_status", "live") != "live" or retrieved < cutoff - timedelta(days=1):
        status = "stale"
    item = {"provider": provider, "upstream": provider, "source_url": _url(result["source_url"], provider),
            "retrieved_at": retrieved.isoformat(), "observed_at": observed_at, "available_at": observed_at,
            "status": status, "sha256": result.get("sha256"), "data": content,
            "critical_evidence_eligible": critical and status == "ok"}
    item = _redact(item)
    item["evidence_id"] = "research-" + hashlib.sha256(json.dumps(item, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()).hexdigest()[:24]
    return item


def _gap(provider, status, detail):
    return {"provider": provider, "status": status if status in _STATUSES else "unavailable", "detail": detail}


def _fred_release(series_id, client, key, cutoff):
    """Date-level check: calendar dates do not prove FRED/ALFRED availability.

    See https://fred.stlouisfed.org/docs/api/fred/release_dates.html and
    https://fred.stlouisfed.org/docs/api/fred/series_vintagedates.html .
    Same-day vintages cannot establish historical intraday visibility.
    """
    day = cutoff.date().isoformat()
    common = {"api_key": key, "file_type": "json", "realtime_end": day}
    responses = []

    def fetch(endpoint, **params):
        result = client.get("https://api.stlouisfed.org/fred/" + endpoint + "?" + urlencode(dict(common, **params)), "fred")
        _url(result["source_url"], "fred")
        responses.append(result)
        return _body(result)

    mapping = fetch("series/release", series_id=series_id, realtime_start=day)
    releases = mapping.get("releases")
    if not isinstance(releases, list) or len(releases) != 1 or not isinstance(releases[0], dict):
        raise ProviderError("malformed", "series release identity missing or ambiguous")
    release_id = releases[0].get("id")
    if type(release_id) is not int or release_id <= 0:
        raise ProviderError("malformed", "invalid FRED release ID")
    dates = fetch("release/dates", release_id=release_id, sort_order="desc", limit=10, include_release_dates_with_no_data="false").get("release_dates")
    if not isinstance(dates, list):
        raise ProviderError("malformed", "release dates missing")
    valid_dates = []
    for row in dates:
        if not isinstance(row, dict) or row.get("release_id") != release_id:
            raise ProviderError("malformed", "release calendar identity mismatch")
        row_date = _date(row.get("date"))
        if row_date <= day:
            valid_dates.append(row_date)
    vintages = fetch("series/vintagedates", series_id=series_id, sort_order="desc", limit=10).get("vintage_dates")
    if not isinstance(vintages, list):
        raise ProviderError("malformed", "vintage dates missing")
    valid_vintages = [_date(value) for value in vintages if _date(value) <= day]
    latest_release, latest_vintage = max(valid_dates, default=None), max(valid_vintages, default=None)
    freshness = "verified_through_release_date" if latest_release and latest_vintage and latest_release <= latest_vintage < day else "unknown"
    for response in responses:
        retrieved = _time(response.get("retrieved_at"))
        if response.get("cache_status", "live") != "live" or retrieved < cutoff - timedelta(days=1) or retrieved > datetime.now(timezone.utc) + timedelta(minutes=5):
            freshness = "unknown"
    return {"release_id": release_id, "latest_release_date": latest_release,
            "latest_vintage_date": latest_vintage, "release_freshness": freshness,
            "publication_time": None, "availability_precision": "date",
            "calendar_source_url": _url(responses[1]["source_url"], "fred"),
            "vintage_source_url": _url(responses[2]["source_url"], "fred")}


def fred_series(series_id, client, *, as_of: str) -> dict:
    if series_id not in {"DFII10", "DGS10", "DTWEXBGS", "CPIAUCSL"}:
        raise ProviderError("not_covered", "unsupported FRED series")
    cutoff = _time(as_of)
    day = cutoff.date().isoformat()
    key = os.getenv("FRED_API_KEY")
    if not key:
        raise ProviderError("not_configured", "FRED_API_KEY missing")
    params = {"series_id": series_id, "api_key": key, "file_type": "json",
              "realtime_start": day, "realtime_end": day,
              "sort_order": "desc", "limit": 410}
    result = client.get("https://api.stlouisfed.org/fred/series/observations?" + urlencode(params), "fred")
    payload = _body(result)
    observations = payload.get("observations")
    if not isinstance(observations, list):
        raise ProviderError("malformed", "FRED observations must be an array")
    clean, seen = [], {}
    for row in observations:
        if not isinstance(row, dict):
            raise ProviderError("malformed", "FRED observation must be an object")
        row_date = _date(row.get("date"))
        if row.get("value") == "." or row_date > day:
            continue
        start, end = _date(row.get("realtime_start")), _date(row.get("realtime_end"))
        if not start <= day <= end:
            continue
        value = finite_number(row.get("value"))
        if row_date in seen and seen[row_date] != value:
            raise ProviderError("malformed", "conflicting observations in a FRED vintage")
        seen[row_date] = value
        clean.append({"date": row_date, "value": value, "realtime_start": start, "realtime_end": end})
    if not clean:
        raise ProviderError("unavailable", f"{series_id} has no usable observations")
    clean.sort(key=lambda x: x["date"], reverse=True)
    descriptions = {"DFII10": ("10-year real yield", "percent"),
                    "DGS10": ("10-year nominal Treasury yield", "percent"),
                    "DTWEXBGS": ("nominal broad trade-weighted US dollar index; not DXY", "index"),
                    "CPIAUCSL": ("seasonally adjusted CPI price index; not YoY", "index_1982_1984_100")}
    label, unit = descriptions[series_id]
    data = {"series_id": series_id, "label": label, "unit": unit,
            "observation_date": clean[0]["date"], "value": clean[0]["value"],
            "requested_vintage_date": day, "release_freshness": "unknown", "publication_time": None,
            "note": "Observation and release-calendar dates are not publication timestamps. Same-day vintages cannot prove historical intraday visibility."}
    try:
        data.update(_fred_release(series_id, client, key, cutoff))
    except ProviderError as exc:
        data["release_verification_issue"] = _gap("fred", exc.status, "release/vintage metadata unavailable; latest release unverified")
    status = "ok" if data["release_freshness"] == "verified_through_release_date" else "unknown"
    if (cutoff.date() - date.fromisoformat(clean[0]["date"])).days > (100 if series_id == "CPIAUCSL" else 14):
        status, data["release_freshness"] = "stale", "stale"
    if series_id == "CPIAUCSL":
        year, month = map(int, clean[0]["date"][:7].split("-"))
        previous = next((r for r in clean if r["date"][:7] == f"{year-1:04}-{month:02}"), None)
        data["yoy_percent"] = finite_number((clean[0]["value"] / previous["value"] - 1) * 100) if previous and previous["value"] > 0 else None
        data["yoy_basis"] = "seasonally_adjusted_index_12_month_change"
    result["source_url"] = "https://fred.stlouisfed.org/series/" + series_id
    return _evidence("fred", result, data, as_of=as_of, status=status)


def sec_company(ticker, client, *, as_of: str, ticker_map: dict) -> list[dict]:
    user_agent = os.getenv("SEC_USER_AGENT")
    if not user_agent:
        raise ProviderError("not_configured", "SEC_USER_AGENT identifying your application/contact is required")
    cutoff, day = _time(as_of), _time(as_of).date().isoformat()
    if not isinstance(ticker_map, dict) or not all(isinstance(row, dict) for row in ticker_map.values()):
        raise ProviderError("malformed", "SEC ticker mapping invalid")
    entry = next((r for r in ticker_map.values() if r.get("ticker") == ticker), None)
    if not entry:
        raise ProviderError("not_covered", "ticker not found in SEC company mapping")
    if isinstance(entry.get("cik_str"), bool) or not re.fullmatch(r"[0-9]{1,10}", str(entry.get("cik_str"))) or int(entry["cik_str"]) <= 0:
        raise ProviderError("malformed", "invalid SEC CIK")
    cik = f"{int(entry['cik_str']):010}"
    headers = {"User-Agent": user_agent}
    result = client.get(f"https://data.sec.gov/submissions/CIK{cik}.json", "sec", headers)
    payload = _body(result)
    if str(payload.get("cik", "")).zfill(10) != cik:
        raise ProviderError("malformed", "SEC submissions CIK mismatch")
    recent = payload.get("filings", {}).get("recent", {})
    fields = ("accessionNumber", "acceptanceDateTime", "form", "filingDate", "reportDate", "primaryDocument")
    if not isinstance(recent, dict) or not all(isinstance(recent.get(field), list) for field in fields) or len({len(recent[field]) for field in fields}) != 1:
        raise ProviderError("malformed", "SEC filing arrays missing or inconsistent")
    visible = {}
    for index, accession in enumerate(recent.get("accessionNumber", [])):
        if not isinstance(accession, str) or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
            raise ProviderError("malformed", "invalid SEC accession")
        accepted = recent["acceptanceDateTime"][index]
        if not accepted:
            continue
        # Actual accepted time establishes historical visibility, not report period.
        accepted_time = _time(accepted)
        if accepted_time > cutoff:
            continue
        filed = _date(recent["filingDate"][index])
        if filed > day:
            continue
        report = recent["reportDate"][index]
        if report:
            _date(report)
        document = recent["primaryDocument"][index]
        if not isinstance(document, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,200}", document) or ".." in document:
            raise ProviderError("malformed", "invalid SEC primary-document path")
        filing = {"accession": accession, "form": recent["form"][index], "accepted_at": accepted_time.isoformat(), "filed": filed,
                  "report_date": report, "document_url": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/{document}"}
        if accession in visible and visible[accession] != filing:
            raise ProviderError("malformed", "conflicting SEC accession metadata")
        visible[accession] = filing
    filings = sorted(visible.values(), key=lambda row: row["accepted_at"], reverse=True)
    if not filings:
        raise ProviderError("unavailable", "no timestamped SEC filings available before cutoff")
    filing_status = "stale" if cutoff - _time(filings[0]["accepted_at"]) > timedelta(days=400) else "ok"
    evidence = [_evidence("sec", result, {"ticker": ticker, "cik": cik, "filings": filings[:15], "coverage": "recent_submissions_slice_only"}, as_of=as_of, observed_at=filings[0]["accepted_at"], status=filing_status)]
    facts_result = client.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json", "sec", headers)
    facts_payload = _body(facts_result)
    if str(facts_payload.get("cik", "")).zfill(10) != cik:
        raise ProviderError("malformed", "SEC companyfacts CIK mismatch")
    facts = facts_payload.get("facts", {}).get("us-gaap", {})
    tags = ("RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "NetIncomeLoss",
            "Assets", "Liabilities", "CashAndCashEquivalentsAtCarryingValue", "StockholdersEquity")
    selected, excluded, latest_end, latest_accepted = {}, 0, None, None
    for tag in tags:
        units = facts.get(tag, {}).get("units", {})
        if not isinstance(units, dict):
            raise ProviderError("malformed", "SEC fact units invalid")
        for unit, rows in units.items():
            if not isinstance(unit, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9/_.-]{0,50}", unit) or not isinstance(rows, list):
                raise ProviderError("malformed", "SEC unit/rows invalid")
            clean = []
            for row in rows:
                if not isinstance(row, dict):
                    raise ProviderError("malformed", "SEC fact must be an object")
                filing = visible.get(row.get("accn"))
                if not filing:
                    excluded += 1
                    continue
                end, filed = _date(row.get("end")), _date(row.get("filed"))
                if end > day or filed > day:
                    excluded += 1
                    continue
                if row.get("start") and _date(row["start"]) > end:
                    raise ProviderError("malformed", "SEC fact period invalid")
                value = finite_number(row.get("val"))
                item = {key: row.get(key) for key in ("start", "end", "filed", "form", "accn", "fy", "fp", "frame")}
                item.update(val=row["val"] if type(row["val"]) is int else value, unit=unit, taxonomy="us-gaap", tag=tag, accepted_at=filing["accepted_at"])
                clean.append(item)
                latest_end = max(latest_end or end, end)
                latest_accepted = max(latest_accepted or filing["accepted_at"], filing["accepted_at"])
            if clean:
                clean.sort(key=lambda row: (row["end"], row["accepted_at"], row.get("start") or ""), reverse=True)
                selected.setdefault(tag, {})[unit] = clean[:3]
    facts_status = "unavailable" if not selected else "stale" if (cutoff.date() - date.fromisoformat(latest_end)).days > 400 else filing_status
    evidence.append(_evidence("sec", facts_result, {"ticker": ticker, "facts": selected,
                     "excluded_without_visible_accession_or_future_period": excluded,
                     "coverage": "facts_cross_referenced_to_recent_timestamped_submissions_only",
                     "note": "Tags, units, periods and amendments remain separate; no TTM or cross-unit totals inferred."}, as_of=as_of, observed_at=latest_accepted, status=facts_status))
    return evidence


def company_news(ticker, client, *, as_of: str) -> dict:
    key = os.getenv("FINNHUB_API_KEY")
    if not key:
        raise ProviderError("not_configured", "FINNHUB_API_KEY missing")
    if not isinstance(ticker, str) or not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,15}", ticker):
        raise ProviderError("malformed", "invalid news ticker")
    cutoff = _time(as_of)
    window = cutoff - timedelta(days=7)
    params = {"symbol": ticker, "from": window.date().isoformat(),
              "to": cutoff.date().isoformat(), "token": key}
    result = client.get("https://finnhub.io/api/v1/company-news?" + urlencode(params), "finnhub")
    payload = _body(result, list)
    seen, articles = set(), []
    for row in payload:
        if not isinstance(row, dict):
            raise ProviderError("malformed", "news article must be an object")
        published = finite_number(row.get("datetime"), positive=True)
        if published > cutoff.timestamp() or published < window.timestamp():
            continue
        if type(row.get("id")) is not int or row["id"] < 0:
            raise ProviderError("malformed", "invalid news article ID")
        url = _url(row.get("url"))
        if not isinstance(row.get("headline"), str) or not row["headline"].strip() or not isinstance(row.get("summary", ""), str):
            raise ProviderError("malformed", "news text invalid")
        if url in seen:
            continue
        seen.add(url)
        articles.append({"id": row["id"], "published_at": datetime.fromtimestamp(published, timezone.utc).isoformat(),
                         "source": row.get("source"), "url": url, "headline": row.get("headline", ""),
                         "summary": row.get("summary", "")[:1500]})
    articles.sort(key=lambda row: row["published_at"], reverse=True)
    articles = articles[:15]
    return _evidence("finnhub", result, {"ticker": ticker, "articles": articles,
                     "coverage": "provider_returned_articles_only; empty does not prove no events",
                     "source_tier": "aggregator; verify issuer/regulator source for material claims"}, as_of=as_of,
                     observed_at=articles[0]["published_at"] if articles else None,
                     status="ok" if articles else "unavailable", critical=False)


def enrich(snapshot: dict, *, client=None) -> dict:
    """Append explicitly scoped research evidence. Caller must seal the final snapshot."""
    client = client or HttpClient(timeout=10, attempts=1)
    as_of = snapshot["decision_at"]
    _time(as_of)
    tasks = []
    equities = [symbol for symbol, item in snapshot["instruments"].items() if item["asset_class"] in {"stock", "etf"}]
    for symbol in equities:
        tasks.append((symbol, "news", "finnhub", lambda s=symbol: [company_news(s, client, as_of=as_of)]))
    for series in ("DFII10", "DGS10", "DTWEXBGS", "CPIAUCSL"):
        tasks.append(("macro", series, "fred", lambda s=series: [fred_series(s, client, as_of=as_of)]))
    stocks = [symbol for symbol in equities if snapshot["instruments"][symbol]["asset_class"] == "stock"]
    mapping_issue = None
    if stocks and os.getenv("SEC_USER_AGENT"):
        try:
            mapping = _body(client.get("https://www.sec.gov/files/company_tickers.json", "sec", {"User-Agent": os.environ["SEC_USER_AGENT"]}))
            for symbol in stocks:
                tasks.append((symbol, "filings", "sec", lambda s=symbol: sec_company(s, client, as_of=as_of, ticker_map=mapping)))
        except (ProviderError, ValueError, KeyError, TypeError) as exc:
            mapping_issue = _gap("sec", getattr(exc, "status", "malformed"), "SEC mapping unavailable")
    elif stocks:
        mapping_issue = _gap("sec", "not_configured", "SEC_USER_AGENT missing")
    if mapping_issue:
        for symbol in stocks:
            snapshot.setdefault("research_issues", []).append({"instrument_id": symbol, **mapping_issue})
            snapshot["instruments"][symbol].setdefault("research", {})["filings"] = {"status": mapping_issue["status"], "evidence_ids": [], "critical_evidence_eligible": False}

    def execute(task):
        symbol, kind, provider, fn = task
        try:
            return symbol, kind, fn(), None
        except ProviderError as exc:
            return symbol, kind, [], _gap(provider, exc.status, "source unavailable; no facts inferred")
        except (ValueError, KeyError, TypeError, OverflowError, IndexError, AttributeError):
            return symbol, kind, [], _gap(provider, "malformed", "source content invalid; no facts inferred")

    with ThreadPoolExecutor(max_workers=3) as pool:
        for symbol, kind, items, issue in pool.map(execute, tasks):
            snapshot["evidence"].extend(items)
            if issue:
                snapshot.setdefault("research_issues", []).append({"instrument_id": symbol, "kind": kind, **issue})
            for item in items:
                if item["status"] != "ok":
                    snapshot.setdefault("research_issues", []).append({"instrument_id": symbol, "kind": kind, **_gap(item["provider"], item["status"], "retained evidence has explicit quality limitations")})
            targets = snapshot["instruments"].values() if symbol == "macro" else [snapshot["instruments"][symbol]]
            for target in targets:
                status = issue["status"] if issue else "available" if items and all(i["status"] == "ok" for i in items) else next((item["status"] for item in items if item["status"] != "ok"), "unknown")
                target.setdefault("research", {})[kind] = {"status": status, "evidence_ids": [i["evidence_id"] for i in items],
                                                          "critical_evidence_eligible": bool(items) and all(i["critical_evidence_eligible"] for i in items)}
    return snapshot

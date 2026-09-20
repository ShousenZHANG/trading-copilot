"""Free daily price adapters; public source semantics remain explicit.

Yahoo Close is split-adjusted even when auto_adjust=False. Adj Close additionally
adjusts dividends. Alpaca is requested with feed=sip and adjustment=split; its
permission is proved by the actual historical response, never assumed from keys.
SGE Au99.99 close and SHAU afternoon benchmark are different price kinds.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import threading
import time
from contextlib import closing
from datetime import date, datetime, timedelta
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit, quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .data_calendar import iso, parse_time, utc_now


class ProviderError(Exception):
    def __init__(self, status: str, message: str):
        self.status = status
        super().__init__(message)


# Parameter names that carry a credential but contain none of the substrings
# below. IBKR's Flex Web Service passes its token as a single character, `t`.
_SECRET_PARAM_NAMES = frozenset({"t", "auth", "sig", "signature", "session", "sid", "pwd", "credential"})
_SECRET_PARAM_SUBSTRINGS = ("key", "token", "secret", "password")


def safe_url(url: str) -> str:
    """Drop credential-bearing query parameters, then scrub any live secret value.

    Two layers, because the first alone has failed: name matching cannot cover a
    parameter name nobody has seen yet, and this URL is written into evidence
    records and persisted by HttpClient._cache().

    The scrub runs on DECODED components, before urlencode re-encodes them.
    Scrubbing the finished string instead would miss any secret containing a
    character urlencode escapes - `/` and `+` in a base64 secret, a space in
    SEC_USER_AGENT - leaving the value on disk in recoverable percent-encoded
    form.
    """
    parts = urlsplit(url)
    # Imported here rather than at module scope: providers.py must stay
    # importable on its own, and service.py is the higher layer.
    from .service import secret_values
    secrets = secret_values()

    def scrub(text: str) -> str:
        for value in secrets:
            text = text.replace(value, "[redacted]")
        return text

    query = [(scrub(key), scrub(value)) for key, value in parse_qsl(parts.query)
             if key.lower() not in _SECRET_PARAM_NAMES
             and not any(secret in key.lower() for secret in _SECRET_PARAM_SUBSTRINGS)]
    return urlunsplit((parts.scheme, scrub(parts.netloc), scrub(parts.path),
                       urlencode(query), ""))


def finite_number(value, *, positive: bool = False) -> float:
    if isinstance(value, bool):
        raise ProviderError("malformed", "boolean is not a market number")
    try:
        result = float(str(value).replace(",", ""))
    except (TypeError, ValueError, OverflowError) as exc:
        raise ProviderError("malformed", "missing or invalid numeric value") from exc
    if not math.isfinite(result) or (positive and result <= 0):
        raise ProviderError("malformed", "non-finite or non-positive price")
    return result


# Minimum seconds between requests, per provider. The default is deliberately
# conservative for a source whose published limit nobody has checked.
#
# LIMITATION: this is a minimum-gap model, not a rolling window. A provider
# capped at N requests per minute cannot be expressed here - a 1 req/s gap
# still permits a burst of sixty inside the first minute. Adding such a
# provider requires a windowed limiter, not a new row. IBKR's Flex Web Service
# (1 req/s AND 10 req/min) is the known case waiting on this.
_THROTTLE_SECONDS = {"sge": 0.4, "sec": 0.25, "fred": 0.6, "alpaca": 0.4}
_THROTTLE_DEFAULT = 0.5


class HttpClient:
    """Bounded GET + retries; no credentials or stale-success substitution.

    A shared sqlite quota file coordinates simultaneous Claude/Codex processes.
    Cache keeps bounded raw HTTP response versions for audit only. Semantic
    success is checked by the provider/collector; successful evidence snapshots
    are retained by the journal. This client ALWAYS contacts the upstream.
    """
    _lock = threading.Lock()
    _next = {}

    def __init__(self, cache_dir: str | Path | None = None, *, timeout: int = 15,
                 attempts: int = 2, clock=utc_now, opener=urlopen, sleeper=time.sleep,
                 budget_seconds: float | None = None, deadline: float | None = None,
                 time_fn=time.monotonic):
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.timeout = min(max(timeout, 1), 30)
        self.attempts = min(max(attempts, 1), 3)
        self.clock, self.opener, self.sleeper = clock, opener, sleeper
        self.time_fn = time_fn
        if budget_seconds is not None and (not math.isfinite(budget_seconds) or budget_seconds <= 0):
            raise ValueError("HTTP budget_seconds must be a positive finite duration")
        if budget_seconds is not None and deadline is not None:
            raise ValueError("supply either budget_seconds or deadline")
        if deadline is not None and not math.isfinite(deadline):
            raise ValueError("HTTP deadline must be finite")
        self.deadline = time_fn() + budget_seconds if budget_seconds is not None else deadline
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    def check_budget(self) -> None:
        if self.deadline is not None and self.time_fn() >= self.deadline:
            raise ProviderError("deadline_exceeded", "shared data collection time budget exhausted")

    def remaining_timeout(self) -> float:
        if self.deadline is None:
            return self.timeout
        remaining = self.deadline - self.time_fn()
        if remaining <= 0:
            raise ProviderError("deadline_exceeded", "shared data collection time budget exhausted")
        return min(self.timeout, remaining)

    def _sleep(self, seconds: float) -> None:
        self.check_budget()
        if self.deadline is not None and seconds >= self.deadline - self.time_fn():
            raise ProviderError("deadline_exceeded", "provider delay exceeds remaining collection budget")
        self.sleeper(seconds)
        self.check_budget()

    def _throttle(self, provider: str):
        self.check_budget()
        interval = _THROTTLE_SECONDS.get(provider, _THROTTLE_DEFAULT)
        if self.cache_dir:
            with closing(sqlite3.connect(self.cache_dir / "provider-quota.sqlite3", timeout=5)) as con, con:
                con.execute("CREATE TABLE IF NOT EXISTS quota(provider TEXT PRIMARY KEY, next_at REAL NOT NULL)")
                con.execute("BEGIN IMMEDIATE")
                row = con.execute("SELECT next_at FROM quota WHERE provider=?", (provider,)).fetchone()
                now = time.time()
                slot = max(now, row[0] if row else now)
                # A long cooldown is explicit; do not hang a conversation for minutes.
                if slot - now > 10:
                    raise ProviderError("rate_limited", "provider cooldown remains active")
                con.execute("INSERT OR REPLACE INTO quota VALUES (?,?)", (provider, slot + interval))
        else:
            with self._lock:
                now = time.time()
                slot = max(now, self._next.get(provider, now))
                self._next[provider] = slot + interval
        if slot > now:
            self._sleep(slot - now)
        self.check_budget()

    def _cooldown(self, provider: str, seconds: float):
        self.check_budget()
        if self.cache_dir:
            with closing(sqlite3.connect(self.cache_dir / "provider-quota.sqlite3", timeout=5)) as con, con:
                con.execute("INSERT OR REPLACE INTO quota VALUES (?,?)", (provider, time.time() + seconds))
        else:
            with self._lock:
                self._next[provider] = time.time() + seconds

    def get(self, url: str, provider: str, headers: dict | None = None) -> dict:
        if urlsplit(url).scheme != "https":
            raise ProviderError("malformed", "only HTTPS data endpoints are supported")
        request_headers = {"User-Agent": "Mozilla/5.0 TradingCopilot/1.0 (personal research)",
                           "Accept": "application/json,text/html;q=0.9,*/*;q=0.5"}
        request_headers.update(headers or {})
        for attempt in range(self.attempts):
            self._throttle(provider)
            try:
                with self.opener(Request(url, headers=request_headers), timeout=self.remaining_timeout()) as response:
                    raw = response.read(12 * 1024 * 1024 + 1)
                    self.check_budget()
                    if len(raw) > 12 * 1024 * 1024:
                        raise ProviderError("malformed", "response exceeds bounded payload size")
                    encoding = response.headers.get_content_charset() or "utf-8"
                    body = raw.decode(encoding)
                    result = {"body": body, "retrieved_at": iso(self.clock()), "source_url": safe_url(url),
                              "status": "ok", "cache_status": "live", "sha256": hashlib.sha256(raw).hexdigest()}
                    if self.cache_dir:
                        self._cache(result)
                    return result
            except HTTPError as exc:
                status = "not_entitled" if exc.code in {401, 403} else "not_covered" if exc.code == 404 else "rate_limited" if exc.code == 429 else "unavailable"
                delay = 1.0 + attempt
                if exc.code == 429:
                    retry = exc.headers.get("Retry-After", "")
                    try:
                        delay = max(0, float(retry))
                    except ValueError:
                        try:
                            delay = max(0, (parsedate_to_datetime(retry) - self.clock()).total_seconds())
                        except (ValueError, TypeError):
                            pass
                    self._cooldown(provider, min(delay, 3600))
                if exc.code not in {429, 500, 502, 503, 504} or attempt + 1 >= self.attempts or delay > 10:
                    raise ProviderError(status, f"{provider} HTTP {exc.code}") from None
                self._sleep(delay)
            except (URLError, TimeoutError, OSError, UnicodeError):
                self.check_budget()
                if attempt + 1 >= self.attempts:
                    raise ProviderError("unavailable", f"{provider} network or decoding failure") from None
                self._sleep(1 + attempt)
        raise ProviderError("unavailable", f"{provider} exhausted bounded retries")

    def _cache(self, result: dict):
        # Different content never overwrites a prior response version. In
        # particular, HTTP 200 with an empty/error payload cannot replace a good
        # payload for the same URL. No cached response is used for a live decision.
        key = hashlib.sha256((result["source_url"] + "#" + result["sha256"]).encode()).hexdigest()
        target = self.cache_dir / f"{key}.json"
        # Full URL has been sanitized; never persist HTTP headers or raw exceptions.
        fd, name = tempfile.mkstemp(prefix=".cache-", dir=self.cache_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, allow_nan=False)
            os.replace(name, target)
        finally:
            if os.path.exists(name):
                os.unlink(name)
        # Bound raw HTTP audit cache. Validated long-term history lives in snapshots.
        files = []
        for path in self.cache_dir.glob("*.json"):
            if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name):
                continue
            try:
                stat = path.stat()
            except FileNotFoundError:
                # Another CLI/MCP process may prune the same bounded cache.
                continue
            files.append((stat.st_mtime, stat.st_size, path))
        files.sort(key=lambda item: item[0], reverse=True)
        total = 0
        for index, (_, size, path) in enumerate(files):
            total += size
            if index >= 64 or total > 64 * 1024 * 1024:
                path.unlink(missing_ok=True)


class YahooProvider:
    name, upstream = "yahoo", "Yahoo Finance"
    _configuration_lock = threading.Lock()
    _cache_path = None

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()

    def supports(self, instrument: dict) -> bool:
        return instrument["currency"] == "USD"

    def fetch(self, instrument: dict, start: str, end: str, decision_at: datetime) -> dict:
        self.http.check_budget()
        try:
            import yfinance as yf
            from yfinance.data import YfData
        except ImportError:
            raise ProviderError("unavailable", "yfinance missing; use the pinned runtime") from None
        # yfinance otherwise opens a user-global cookie/timezone SQLite cache,
        # shared with the separately running legacy Yahoo MCP. That can block a
        # long-lived copilot process before its HTTP timeout is even reached.
        # Use a private process directory under the copilot cache instead. This
        # changes only this Python process, not the user's global configuration.
        cache_root = self.http.cache_dir or Path(tempfile.gettempdir()) / "trading-copilot-provider-cache"
        yahoo_cache = cache_root / "yfinance" / f"process-{os.getpid()}"
        with self._configuration_lock:
            if self._cache_path != str(yahoo_cache):
                yahoo_cache.mkdir(parents=True, exist_ok=True)
                yf.set_tz_cache_location(str(yahoo_cache))
                type(self)._cache_path = str(yahoo_cache)
        self.http.check_budget()
        # yfinance's process-wide LRU can return a previous historical response
        # for identical start/end arguments. Clear it on EVERY decision refresh,
        # otherwise corporate-action revisions remain invisible in a long-lived MCP.
        cache_clear = getattr(YfData().cache_get, "cache_clear", None)
        if cache_clear is None:
            raise ProviderError("unavailable", "unsupported yfinance cache API; use the pinned runtime")
        cache_clear()
        ticker = yf.Ticker(instrument["instrument_id"])
        for attempt in range(self.http.attempts):
            self.http._throttle(self.name)
            try:
                frame = ticker.history(start=start, end=end, interval="1d", auto_adjust=False,
                                       back_adjust=False, actions=True, repair=False, keepna=True,
                                       prepost=False, rounding=False, timeout=self.http.remaining_timeout(),
                                       raise_errors=True)
                metadata = ticker.get_history_metadata()
                self.http.check_budget()
                break
            except ProviderError:
                raise
            except Exception as exc:
                # Never serialize exception text: upstream URLs can contain auth crumbs.
                name = type(exc).__name__
                status = "rate_limited" if "RateLimit" in name else "not_covered" if "Missing" in name else "unavailable"
                if status == "not_covered" or attempt + 1 >= self.http.attempts:
                    raise ProviderError(status, f"Yahoo history failed ({name})") from None
                # yfinance exposes no Retry-After header. Apply our own bounded
                # cooldown without claiming this is the server-specified delay.
                self.http._cooldown(self.name, 2.0)
                self.http._sleep(2.0)
                cache_clear()
        if frame is None or frame.empty:
            raise ProviderError("not_covered", "Yahoo returned no daily bars")
        if str(metadata.get("symbol", "")).upper() != instrument["instrument_id"]:
            raise ProviderError("malformed", "Yahoo symbol identity does not match the requested instrument")
        if metadata.get("currency") != "USD":
            raise ProviderError("malformed", "Yahoo did not confirm USD instrument identity")
        kind = str(metadata.get("instrumentType", "")).upper()
        if kind not in {"EQUITY", "ETF", "INDEX"}:
            raise ProviderError("not_covered", "Yahoo asset class outside supported US equity/ETF/index scope")
        exchange = str(metadata.get("exchangeName", "")).upper()
        if not exchange or metadata.get("exchangeTimezoneName") != "America/New_York":
            raise ProviderError("not_covered", "Yahoo did not confirm a US exchange timezone")
        is_index = instrument["asset_class"] == "index"
        if is_index != (kind == "INDEX"):
            raise ProviderError("malformed", "Yahoo asset type does not match instrument registry")
        bars = []
        for stamp, row in frame.iterrows():
            session = stamp.date().isoformat()
            close = finite_number(row["Close"], positive=True)
            if "Adj Close" not in row:
                raise ProviderError("malformed", "Yahoo adjusted close is missing; raw substitution forbidden")
            adjusted = finite_number(row["Adj Close"], positive=True)
            bars.append({"session": session,
                         "open": finite_number(row["Open"], positive=True),
                         "high": finite_number(row["High"], positive=True),
                         "low": finite_number(row["Low"], positive=True), "close": close,
                         "volume": finite_number(row["Volume"]), "adjusted_close": adjusted,
                         "dividends": finite_number(row.get("Dividends", 0)),
                         "splits": finite_number(row.get("Stock Splits", 0))})
        return {"provider": self.name, "upstream": self.upstream,
                "source_url": f"https://finance.yahoo.com/quote/{quote(instrument['instrument_id'], safe='')}/history/",
                "instrument_id": instrument["instrument_id"], "currency": "USD", "unit": instrument["unit"],
                "price_kind": instrument["price_kind"], "adjustment": instrument["adjustment"],
                "indicator_basis": "total_return_adjusted" if not is_index else "index_points",
                "asset_class": "etf" if kind == "ETF" else "index" if is_index else "stock",
                "feed": "yahoo_daily_regular", "authoritative": False, "bars": bars,
                "retrieved_at": iso(self.http.clock()), "available_at": None, "published_at": None,
                "status": "ok", "cache_status": "live", "exchange": exchange,
                "parameters": {"start": start, "end_exclusive": end, "interval": "1d",
                               "auto_adjust": False, "back_adjust": False, "repair": False,
                               "actions": True, "prepost": False, "keepna": True}}


class AlpacaProvider:
    name, upstream = "alpaca", "Alpaca SIP"

    def __init__(self, http: HttpClient | None = None, *, key: str | None = None, secret: str | None = None):
        self.http = http or HttpClient()
        self.key = key or os.environ.get("APCA_API_KEY_ID") or os.environ.get("ALPACA_API_KEY")
        self.secret = secret or os.environ.get("APCA_API_SECRET_KEY") or os.environ.get("ALPACA_SECRET_KEY")

    def supports(self, instrument: dict) -> bool:
        return instrument["currency"] == "USD" and instrument["asset_class"] != "index"

    def fetch(self, instrument: dict, start: str, end: str, decision_at: datetime) -> dict:
        if not self.key or not self.secret:
            raise ProviderError("not_configured", "free Alpaca key pair is not configured")
        # End no newer than 16 min avoids Basic recent-SIP entitlement restriction.
        cutoff = min(parse_time(decision_at) - timedelta(minutes=16),
                     datetime.fromisoformat(end).replace(tzinfo=ZoneInfo("America/New_York")))
        url = f"https://data.alpaca.markets/v2/stocks/{quote(instrument['instrument_id'], safe='')}/bars"
        params = {"start": start, "end": iso(cutoff), "timeframe": "1Day", "adjustment": "split",
                  "feed": "sip", "currency": "USD", "sort": "asc", "limit": 1000}
        bars, tokens, response = [], set(), None
        for _ in range(4):
            response = self.http.get(url + "?" + urlencode(params), self.name,
                                     {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret})
            try:
                payload = json.loads(response["body"])
            except ValueError:
                raise ProviderError("malformed", "Alpaca returned invalid JSON") from None
            if payload.get("symbol") != instrument["instrument_id"]:
                raise ProviderError("malformed", "Alpaca symbol identity mismatch")
            if not isinstance(payload.get("bars"), list):
                raise ProviderError("malformed", "Alpaca missing bars array")
            for row in payload["bars"]:
                stamp = parse_time(row["t"])
                bars.append({"session": stamp.astimezone(ZoneInfo("America/New_York")).date().isoformat(),
                             **{field: finite_number(row[key], positive=True)
                                for field, key in (("open", "o"), ("high", "h"), ("low", "l"), ("close", "c"))},
                             "volume": finite_number(row["v"])})
            token = payload.get("next_page_token")
            if not token:
                break
            if token in tokens:
                raise ProviderError("malformed", "Alpaca repeated pagination token")
            tokens.add(token)
            params["page_token"] = token
        else:
            raise ProviderError("malformed", "Alpaca pagination exceeded bounded history request")
        if not bars:
            raise ProviderError("not_covered", "Alpaca returned no SIP bars")
        return {"provider": self.name, "upstream": self.upstream, "source_url": url,
                "instrument_id": instrument["instrument_id"], "currency": "USD", "unit": "share",
                "price_kind": "regular_session_close", "adjustment": "split", "indicator_basis": "split_adjusted",
                "feed": "sip", "permission_verified": True, "authoritative": False,
                "bars": bars, "status": "ok", "retrieved_at": response["retrieved_at"],
                "available_at": None, "published_at": None, "cache_status": "live",
                "comparison_scope": "session_close_only; volume/extended-hours OHLC not cross-validated"}


class NasdaqIndexProvider:
    """First-party public historical index table, with a bounded live contract.

    Nasdaq's public website endpoint has no availability SLA. Schema change,
    denial, or an empty table is a capability failure, never a stale fallback.
    A Nasdaq index level is a point value, not an ETF's executable USD price.
    """
    name, upstream = "nasdaq", "Nasdaq index publisher"
    symbols = {"^NDX": "NDX", "^IXIC": "COMP"}

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()

    def supports(self, instrument: dict) -> bool:
        return instrument["instrument_id"] in self.symbols

    def fetch(self, instrument: dict, start: str, end: str, decision_at: datetime) -> dict:
        symbol = self.symbols[instrument["instrument_id"]]
        last = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
        # Use this endpoint only to corroborate recent closing levels. Its older
        # historical Open fields failed our OHLC canary (outside High/Low), so
        # they are deliberately not ingested or used to compute ATR/price ranges.
        recent_start = max(start, (date.fromisoformat(last) - timedelta(days=14)).isoformat())
        url = f"https://api.nasdaq.com/api/quote/{symbol}/historical?" + urlencode(
            {"assetclass": "index", "fromdate": recent_start, "todate": last, "limit": 100})
        response = self.http.get(url, self.name, {"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"})
        try:
            payload = json.loads(response["body"])
            data = payload.get("data") or {}
            if payload.get("status", {}).get("rCode") != 200 or data.get("symbol") != symbol:
                raise ProviderError("not_covered", "Nasdaq did not confirm the requested index")
            table = data.get("tradesTable") or {}
            if table.get("headers", {}).get("date") != "Date" or "Close" not in table.get("headers", {}).get("close", ""):
                raise ProviderError("malformed", "Nasdaq historical table columns changed")
            rows = table.get("rows") or []
            if not rows:
                raise ProviderError("not_covered", "Nasdaq returned an empty historical index table")
            if int(data["totalRecords"]) != len(rows):
                raise ProviderError("malformed", "Nasdaq history is truncated; pagination contract changed")
            bars = [{"session": datetime.strptime(row["date"], "%m/%d/%Y").date().isoformat(),
                     "close": finite_number(row["close"], positive=True)}
                    for row in rows]
        except (ValueError, TypeError, KeyError):
            raise ProviderError("malformed", "Nasdaq index response violates the verified table contract") from None
        return {"provider": self.name, "upstream": self.upstream, "source_url": response["source_url"],
                "instrument_id": instrument["instrument_id"], "currency": "USD", "unit": "point",
                "price_kind": "index_close", "adjustment": "none", "indicator_basis": "index_points",
                "asset_class": "index", "authoritative": True, "bars": bars, "status": "ok",
                "retrieved_at": response["retrieved_at"], "available_at": None, "published_at": None,
                "cache_status": "live", "feed": "nasdaq_public_historical_index",
                "comparison_scope": "recent_index_closes_only; historical_OHLC_not_verified",
                "currency_semantics": "USD-denominated index; value unit is index points, not a monetary trade quote"}


#: Every exchange label Nasdaq reported across all 39 registry symbols when
#: probed on 2026-09-19: PSE for 29, NASDAQ-GM for 9, and one symbol (SPLG)
#: Nasdaq does not know at all. PSE is the legacy Pacific Exchange code for
#: NYSE Arca, a registered US national securities exchange and the primary
#: listing venue for most ETFs; omitting it left Yahoo as the only upstream, so
#: cross-provider confirmation was impossible for 29 of 39 symbols.
#:
#: Matched EXACTLY, never as a prefix. A prefix match admits real, currently
#: operating foreign venues that happen to start with an accepted label:
#: "PSE.PHILIPPINES" (Philippine Stock Exchange), "NASDAQ DUBAI", and
#: "NYSE EURONEXT PARIS" all satisfy startswith("PSE")/("NASDAQ")/("NYSE").
#: "NASDAQ", "NYSE", "AMEX", "ARCA" and "NYSE ARCA" are kept as exact
#: alternatives for plausible relabelling even though only "PSE" and
#: "NASDAQ-GM" were ever observed; nothing else speculative was added — a
#: whitelist that guesses is not one.
SUPPORTED_US_EXCHANGES = frozenset({"NASDAQ", "NYSE", "AMEX", "ARCA", "NYSE ARCA", "PSE", "NASDAQ-GM"})

#: Nasdaq's own listing tiers. Only NASDAQ-GM appeared across the 39-symbol
#: probe, but Global Select and Capital Market are the other two tiers a US ETF
#: can sit on, and a fund moving between them is an ordinary event that would
#: otherwise drop it to a single upstream and — per ADR-0007 clause 6 — pause
#: the whole sleeve. The hyphen is what makes this safe to accept as a family:
#: "NASDAQ DUBAI" separates with a space, so it stays refused.
_NASDAQ_TIER = re.compile(r"^NASDAQ-[A-Z]{2}$")


def is_supported_us_exchange(label: str) -> bool:
    text = str(label or "").upper()
    return text in SUPPORTED_US_EXCHANGES or bool(_NASDAQ_TIER.match(text))


def unsupported_exchange_detail(label: str) -> str:
    """Name the label so a future relabelling is diagnosable from the error alone."""
    return (f"Nasdaq reported exchange {label!r}, which is not one of the supported "
            f"US venues {sorted(SUPPORTED_US_EXCHANGES)}")


class NasdaqEquityProvider:
    """Corroborate only the latest completed US share close, without an API key.

    The public history endpoint does not expose an adjustment flag. Therefore
    older equity closes are NEVER declared split-adjusted. The market-data gate
    permits comparison of this single current-session close only when Yahoo
    confirms the same USD identity and reports no split in that session. Split
    sessions require Alpaca's explicitly adjusted history, otherwise unknown.
    """
    name, upstream = "nasdaq", "Nasdaq US market data"

    def __init__(self, http: HttpClient | None = None):
        self.http = http or HttpClient()

    def supports(self, instrument: dict) -> bool:
        return instrument["currency"] == "USD" and instrument["asset_class"] in {"stock", "etf"}

    def fetch(self, instrument: dict, start: str, end: str, decision_at: datetime) -> dict:
        symbol = instrument["instrument_id"]
        kind = "etf" if instrument["asset_class"] == "etf" else "stocks"
        headers = {"Origin": "https://www.nasdaq.com", "Referer": "https://www.nasdaq.com/"}
        info_url = f"https://api.nasdaq.com/api/quote/{quote(symbol, safe='')}/info?assetclass={kind}"
        info_response = self.http.get(info_url, self.name, headers)
        try:
            identity = json.loads(info_response["body"]).get("data") or {}
            if identity.get("symbol") != symbol or identity.get("assetClass") not in {"ETF", "STOCKS"}:
                raise ProviderError("malformed", "Nasdaq did not confirm equity identity")
            exchange = str(identity.get("exchange", "")).upper()
            if not is_supported_us_exchange(exchange):
                raise ProviderError("not_covered", unsupported_exchange_detail(exchange))
            if not str((identity.get("primaryData") or {}).get("lastSalePrice", "")).startswith("$"):
                raise ProviderError("malformed", "Nasdaq US equity quote lacks dollar denomination")
            # Info is used for instrument identity ONLY: its timestamp failed a
            # live freshness canary despite matching the newest historical price.
            last = (date.fromisoformat(end) - timedelta(days=1)).isoformat()
            first = (date.fromisoformat(last) - timedelta(days=7)).isoformat()
            url = f"https://api.nasdaq.com/api/quote/{quote(symbol, safe='')}/historical?" + urlencode(
                {"assetclass": kind, "fromdate": first, "todate": last, "limit": 10})
            response = self.http.get(url, self.name, headers)
            payload = json.loads(response["body"])
            data = payload.get("data") or {}
            table = data.get("tradesTable") or {}
            rows = table.get("rows") or []
            if payload.get("status", {}).get("rCode") != 200 or data.get("symbol") != symbol:
                raise ProviderError("not_covered", "Nasdaq did not return the requested equity")
            if table.get("headers", {}).get("date") != "Date" or "Close" not in table.get("headers", {}).get("close", ""):
                raise ProviderError("malformed", "Nasdaq equity historical columns changed")
            if not rows or int(data.get("totalRecords", 0)) != len(rows):
                raise ProviderError("not_covered", "Nasdaq latest completed close is absent or ambiguous")
            matching = [row for row in rows if datetime.strptime(row["date"], "%m/%d/%Y").date().isoformat() == last]
            if len(matching) != 1:
                raise ProviderError("stale", "Nasdaq equity close is not the expected session")
            row = matching[0]
            session = datetime.strptime(row["date"], "%m/%d/%Y").date().isoformat()
            if session != last:
                raise ProviderError("stale", "Nasdaq equity close is not the expected session")
            close = finite_number(str(row["close"]).removeprefix("$"), positive=True)
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ProviderError("malformed", "Nasdaq equity response violates verified contract") from None
        return {"provider": self.name, "upstream": self.upstream, "source_url": response["source_url"],
                "instrument_id": symbol, "currency": "USD", "unit": "share",
                "price_kind": "regular_session_close", "adjustment": "unadjusted_latest_session",
                "indicator_basis": "single_current_session", "asset_class": "etf" if identity["assetClass"] == "ETF" else "stock",
                "authoritative": False, "bars": [{"session": session, "close": close}],
                "status": "ok", "retrieved_at": response["retrieved_at"], "available_at": None, "published_at": None,
                "cache_status": "live", "feed": "nasdaq_public_us_equity_close", "identity_source_url": info_url,
                "identity_retrieved_at": info_response["retrieved_at"],
                "comparison_scope": "latest_completed_USD_close_only; no_split_session; historical_adjustment_unknown"}


class _Tables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables, self.table, self.row, self.cell = [], None, None, None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self.table = []
        elif self.table is not None and tag == "tr":
            self.row = []
        elif self.row is not None and tag in {"td", "th"}:
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.row.append(" ".join("".join(self.cell).split()))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.table.append(self.row)
            self.row = None
        elif tag == "table" and self.table is not None:
            self.tables.append(self.table)
            self.table = None


def parse_sge_daily(html: str) -> list[dict]:
    parser = _Tables()
    parser.feed(html)
    bars = []
    for table in parser.tables:
        if not table or len(table[0]) < 10 or "日期" not in table[0][0] or "收盘" not in table[0][5]:
            continue
        for row in table[1:]:
            if len(row) < 10 or row[1] != "Au99.99":
                continue
            session = date.fromisoformat(row[0]).isoformat()
            bars.append({"session": session,
                         **{field: finite_number(row[index], positive=True)
                            for index, field in enumerate(("open", "high", "low", "close"), start=2)},
                         "volume": finite_number(row[9]), "volume_unit": "kilogram",
                         "weighted_average": finite_number(row[8], positive=True)})
    return bars


def parse_sge_shau(html: str) -> list[dict]:
    parser = _Tables()
    parser.feed(html)
    by_session = {}
    for table in parser.tables:
        if not table or len(table[0]) < 6 or "Trade Date" not in table[0][1] or "PRC" not in table[0][5]:
            continue
        for row in table[1:]:
            if len(row) < 6 or row[2] != "SHAU":
                continue
            session = date.fromisoformat(row[1]).isoformat()
            label = row[3].strip().lower()
            # The official page provides AM/PM rounds; never call its first price a close.
            if label not in {"午盘", "下午", "pm", "afternoon"}:
                continue
            round_number = int(row[4])
            record = {"session": session, "close": finite_number(row[5], positive=True),
                      "benchmark_session": "PM", "round": round_number}
            previous = by_session.get(session)
            if previous is None or round_number > previous["round"]:
                by_session[session] = record
            elif round_number == previous["round"] and record["close"] != previous["close"]:
                raise ProviderError("malformed", "SGE duplicate benchmark round conflicts")
    return list(by_session.values())


class SGEProvider:
    name, upstream = "sge", "Shanghai Gold Exchange"

    def __init__(self, http: HttpClient | None = None, *, history_bars: int = 280):
        self.http = http or HttpClient()
        self.history_bars = min(max(history_bars, 1), 320)

    def supports(self, instrument: dict) -> bool:
        return instrument["instrument_id"] in {"GOLD.CNY", "SGE.SHAU"}

    def fetch(self, instrument: dict, start: str, end: str, decision_at: datetime) -> dict:
        shau = instrument["instrument_id"] == "SGE.SHAU"
        path = "shanghaiAuAuto" if shau else "quotation_daily_new"
        parser = parse_sge_shau if shau else parse_sge_daily
        base_url = f"https://www.sge.com.cn/sjzx/{path}"
        last = date.fromisoformat(end) - timedelta(days=1)
        # First exact-session request proves the freshness watermark independently
        # of pagination. SGE's published UI restricts history queries to one month;
        # use 28-day chunks (at most 20 weekdays), never an unsupported yearly query.
        latest = self.http.get(base_url + "?" + urlencode({"start_date": last.isoformat(), "end_date": last.isoformat()}), self.name)
        latest_bars = parser(latest["body"])
        if not latest_bars or not any(bar["session"] == last.isoformat() for bar in latest_bars):
            raise ProviderError("stale", "SGE did not publish the expected completed session")
        bars = {bar["session"]: bar for bar in latest_bars}
        request_count, history_issues = 1, []
        cursor = last - timedelta(days=1)
        while len(bars) < self.history_bars and cursor >= date.fromisoformat(start) and request_count < 36:
            chunk_start = max(date.fromisoformat(start), cursor - timedelta(days=27))
            for page in range(1, 15):
                params = {"start_date": chunk_start.isoformat(), "end_date": cursor.isoformat(), "p": page}
                if not shau:
                    params["inst_ids"] = "Au99.99"
                try:
                    response = self.http.get(base_url + "?" + urlencode(params), self.name)
                    request_count += 1
                    records = parser(response["body"])
                    if not records:
                        history_issues.append("history_no_records")
                        break
                    before = len(bars)
                    for record in records:
                        if record["session"] in bars and record != bars[record["session"]]:
                            raise ProviderError("malformed", "SGE history has conflicting duplicate dates")
                        bars[record["session"]] = record
                    if len(bars) >= self.history_bars or len(bars) == before or request_count >= 36:
                        break
                    # Only request a next page when official HTML advertises it.
                    if not re.search(r"gotoPage\([^\n]*?,['\"]" + str(page + 1) + r"['\"]\)", response["body"]):
                        break
                except ProviderError as exc:
                    history_issues.append(f"history_{exc.status}")
                    break
            if history_issues:
                break
            cursor = chunk_start - timedelta(days=1)
        return {"provider": self.name, "upstream": self.upstream, "source_url": latest["source_url"],
                "instrument_id": instrument["instrument_id"], "currency": "CNY", "unit": "gram",
                "price_kind": instrument["price_kind"], "adjustment": "none", "indicator_basis": "unadjusted_benchmark",
                "authoritative": True, "bars": sorted(bars.values(), key=lambda b: b["session"]),
                "retrieved_at": latest["retrieved_at"], "available_at": None, "published_at": None,
                "status": "ok", "cache_status": "live", "history_issues": history_issues,
                "requests": request_count, "quote_role": "market_benchmark_not_retail_quote"}

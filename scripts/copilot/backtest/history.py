"""Daily history for backtests. Deliberately NOT routed through providers.py.

Two mechanical reasons, both in ADR-0006. First, providers.py caches every
response unconditionally into a 64-slot store pruned by mtime, so a 22-symbol
sweep would evict the raw HTTP forensics that back earlier snapshots. Second,
its throttle and cooldown key on the provider name "yahoo" in a shared quota,
so one HTTP 429 here would block the next live decision call for an arbitrary
time -- Yahoo sends no Retry-After header.

The two price series are kept apart on purpose. Yahoo's quote.close is
split-adjusted but not dividend-adjusted; adjclose is both. Neither is the
as-traded price, which this transport does not serve.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from .frame import PriceFrame, build

CHART_HOST = "https://query1.finance.yahoo.com/v8/finance/chart"

#: Observed 2026-09-19: no UA, "Python-urllib/3.13" and "curl/8.5.0" each drew
#: HTTP 429 on the first request with no Retry-After. This one returned 200.
USER_AGENT = "Mozilla/5.0 TradingCopilot/1.0 (personal research)"

#: New York is UTC-5 or UTC-4. A daily bar is stamped at the exchange open, so
#: converting naively in UTC shifts bars across midnight. Five hours is the
#: winter offset; four in summer. Subtracting the winter offset and taking the
#: date is correct in both, because the open is 09:30 local either way.
_NEW_YORK_WINTER_OFFSET = timedelta(hours=5)

#: One request every 1.5s, single process. Observed: 30 consecutive requests at
#: 1.05-2.85s intervals all returned 200. Behaviour above that volume is
#: unmeasured, so this is a floor chosen for politeness, not a proven safe rate.
_MIN_INTERVAL_SECONDS = 1.5
_last_request_at = 0.0


class HistoryError(RuntimeError):
    """The response is unusable: wrong granularity, empty, or malformed."""


class NotCovered(HistoryError):
    """Upstream says this symbol has no data. Skip it; do not abort the sweep."""


@dataclass(frozen=True)
class Series:
    symbol: str
    dates: tuple[date, ...]
    split_adjusted: tuple[float, ...]
    split_and_dividend_adjusted: tuple[float, ...]
    dropped_bars: int


def chart_url(symbol: str, *, until_epoch: int) -> str:
    """period1/period2, never range.

    `range=max&interval=1d` returns HTTP 200 with meta.dataGranularity='1mo'
    and about 405 monthly bars while still echoing range='max'.
    """
    return (f"{CHART_HOST}/{symbol.upper()}"
            f"?period1=0&period2={int(until_epoch)}&interval=1d&events=div%2Csplit")


def _to_new_york_date(epoch: int) -> date:
    return (datetime.fromtimestamp(epoch, tz=timezone.utc) - _NEW_YORK_WINTER_OFFSET).date()


def parse_chart(symbol: str, body: str) -> Series:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HistoryError(f"{symbol}: response is not JSON: {exc}") from exc
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise NotCovered(f"{symbol}: {error.get('code')}: {error.get('description')}")
    results = chart.get("result") or []
    if not results:
        raise HistoryError(f"{symbol}: chart.result is empty")
    result = results[0]
    granularity = (result.get("meta") or {}).get("dataGranularity")
    if granularity != "1d":
        raise HistoryError(
            f"{symbol}: dataGranularity is {granularity!r}, expected '1d' — "
            "a monthly series would pass a 15-year gate with about 180 bars")
    stamps: Sequence[int] = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]
    closes: Sequence[float | None] = quote.get("close") or []
    adjcloses: Sequence[float | None] = adj.get("adjclose") or []
    if not (len(stamps) == len(closes) == len(adjcloses)):
        raise HistoryError(f"{symbol}: {len(stamps)} timestamps, {len(closes)} closes, "
                           f"{len(adjcloses)} adjcloses")
    dates, raw, adjusted, dropped = [], [], [], 0
    for stamp, close, adjclose in zip(stamps, closes, adjcloses):
        if close is None or adjclose is None or close <= 0 or adjclose <= 0:
            dropped += 1
            continue
        dates.append(_to_new_york_date(stamp))
        raw.append(float(close))
        adjusted.append(float(adjclose))
    if not dates:
        raise HistoryError(f"{symbol}: every bar was null")
    return Series(symbol=symbol.upper(), dates=tuple(dates), split_adjusted=tuple(raw),
                  split_and_dividend_adjusted=tuple(adjusted), dropped_bars=dropped)


def fetch(symbol: str, *, until_epoch: int | None = None, timeout: float = 30.0) -> Series:
    """One symbol, one request, nothing written to disk."""
    global _last_request_at
    if until_epoch is None:
        until_epoch = int(time.time())
    wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(chart_url(symbol, until_epoch=until_epoch),
                                     headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotCovered(f"{symbol}: HTTP 404 from the chart endpoint") from exc
        raise HistoryError(f"{symbol}: HTTP {exc.code} from the chart endpoint") from exc
    except urllib.error.URLError as exc:
        raise HistoryError(f"{symbol}: {exc.reason}") from exc
    finally:
        _last_request_at = time.monotonic()
    return parse_chart(symbol, body)


def to_frame(series: Sequence[Series], *, dividend_adjusted: bool = True) -> PriceFrame:
    """Intersect several symbols onto their common dates.

    Intersection, not union: a rule that sees a forward-filled price for a
    symbol that had no bar that day trades on a number nobody could have got.
    """
    if not series:
        raise ValueError("no series to align")
    common = set(series[0].dates)
    for s in series[1:]:
        common &= set(s.dates)
    if not common:
        raise ValueError("these symbols share no common trading dates")
    dates = sorted(common)
    lookup = {}
    for s in series:
        chosen = s.split_and_dividend_adjusted if dividend_adjusted else s.split_adjusted
        lookup[s.symbol] = dict(zip(s.dates, chosen))
    symbols = [s.symbol for s in series]
    closes = [[lookup[sym][d] for sym in symbols] for d in dates]
    return build(dates=dates, symbols=symbols, closes=closes)

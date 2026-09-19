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

#: New York is UTC-5 (winter) or UTC-4 (summer). Subtracting the fixed winter
#: offset and taking the date is correct for any stamp from 05:00 UTC onward,
#: which covers every open (13:30-14:30 UTC) and close (20:00-21:00 UTC) stamp
#: Yahoo actually emits for a daily bar. A stamp within five hours of UTC
#: midnight would be misfiled by this fixed offset, but Yahoo does not emit
#: those. Proven, not assumed: verified against the open stamps this module's
#: tests use. Do not replace this with zoneinfo -- ZoneInfo("America/New_York")
#: raises on Windows without the tzdata package, which this stdlib-only path
#: deliberately does not depend on.
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
    #: Consumed by `alignment()`, per symbol -- not dead weight. A symbol
    #: whose feed is mostly null still produces a structurally valid Series,
    #: so this is the only place a caller can see that before trusting it.
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


def alignment(series: Sequence[Series]) -> dict:
    """Report per-symbol date coverage and the intersection. Reports; never judges.

    This does not raise on thin or non-overlapping coverage and invents no
    threshold -- `to_frame` silently returning a valid but tiny frame is a
    real failure mode (one one-bar symbol in a 22-symbol sweep quietly caps
    the whole backtest at a single day), and the fix is attribution a caller
    can log or gate on, not a judgment made here.
    """
    if not series:
        raise ValueError("no series to align")
    per_symbol = {
        s.symbol: {
            "first": s.dates[0].isoformat(),
            "last": s.dates[-1].isoformat(),
            "bars": len(s.dates),
            "dropped_bars": s.dropped_bars,
        }
        for s in series
    }
    common = set(series[0].dates)
    for s in series[1:]:
        common &= set(s.dates)
    common_sorted = sorted(common)
    binds_start = max(series, key=lambda s: s.dates[0]).symbol
    binds_end = min(series, key=lambda s: s.dates[-1]).symbol
    longest = max(len(s.dates) for s in series)
    return {
        "per_symbol": per_symbol,
        "common_first": common_sorted[0].isoformat() if common_sorted else None,
        "common_last": common_sorted[-1].isoformat() if common_sorted else None,
        "common_bars": len(common_sorted),
        "binds_start": binds_start,
        "binds_end": binds_end,
        "bars_lost_vs_longest": longest - len(common_sorted),
    }


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
        info = alignment(series)
        start = info["binds_start"]
        end = info["binds_end"]
        raise ValueError(
            "these symbols share no common trading dates — "
            f"binds_start={start} (first bar {info['per_symbol'][start]['first']}), "
            f"binds_end={end} (last bar {info['per_symbol'][end]['last']})")
    dates = sorted(common)
    lookup = {}
    for s in series:
        chosen = s.split_and_dividend_adjusted if dividend_adjusted else s.split_adjusted
        lookup[s.symbol] = dict(zip(s.dates, chosen))
    symbols = [s.symbol for s in series]
    closes = [[lookup[sym][d] for sym in symbols] for d in dates]
    return build(dates=dates, symbols=symbols, closes=closes)

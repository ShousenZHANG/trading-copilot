#!/usr/bin/env python3
"""Build the offline JSON price map that the backtest engine consumes.

WHY THIS EXISTS
---------------
``evals/stockbench/runner.py --replay --prices <json>`` is the only zero-token,
deterministic measurement this repo has — and until now nothing in the tree
produced that ``<json>``. The only alternative (``--yfinance``) needs a
third-party package the project deliberately does not depend on. This script
closes that gap using stdlib ``urllib`` against the keyless Yahoo v8 chart
endpoint.

OUTPUT FORMAT — do not invent a new one
---------------------------------------
Backward-compatible with ``evals/stockbench/backtest_engine.JsonPriceSource``::

    {"NVDA": [{"date": "2026-04-01", "close": 123.45, "adjustment": "split_dividend"}, ...]}

Keys are ticker symbols verbatim (``GC=F``, ``NDQ.AX``, ``^AXJO``); each value
is a list of bars sorted by ISO date, one bar per trading day.

DETERMINISM
-----------
Parsing, merging and date conversion are pure functions — no clock, no network,
no randomness. Only ``fetch_*`` touches the wire, and ``--self-test`` runs FULLY
OFFLINE against hand-written payloads.

WHY gmtoffset MATTERS
---------------------
Yahoo timestamps are epoch seconds at the exchange's session open. Converting
them in UTC puts an ASX bar under daylight saving (UTC+11) on the PREVIOUS
calendar day, which silently shifts every ``.AX`` signal by one bar. The chart
payload carries ``meta.gmtoffset``; we apply it before taking the date.

CLI
---
    python scripts/prices.py --tickers NVDA,GC=F,SPY --start 2026-04-01 --end 2026-09-30
    python scripts/prices.py --tickers SPY --start 2026-04-01 --end 2026-09-30 --merge
    python scripts/prices.py --self-test
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from runtime import force_utf8_stdio
from ticker import validate_ticker_component

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "evals" / "prices" / "prices.json"

YAHOO_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
# Yahoo rejects the stdlib default User-Agent ("Python-urllib/3.x") outright.
USER_AGENT = "Mozilla/5.0 (compatible; trading-copilot/1.0; +offline-price-map)"
DEFAULT_TIMEOUT_SECONDS = 20.0
SECONDS_PER_DAY = 86400

PriceMap = dict[str, list[dict[str, object]]]


# ---------------------------------------------------------------------------
# Pure date helpers
# ---------------------------------------------------------------------------
def epoch_to_iso(epoch: int | float, gmtoffset: int = 0) -> str:
    """Epoch seconds -> exchange-local ISO date.

    ``gmtoffset`` is the exchange's UTC offset in seconds, taken from
    ``meta.gmtoffset``. Shifting first and reading the date in UTC afterwards
    gives the exchange's own calendar day without needing a tz database.
    """
    shifted = datetime.fromtimestamp(float(epoch) + gmtoffset, tz=timezone.utc)
    return shifted.date().isoformat()


def iso_to_epoch(iso: str) -> int:
    """ISO date -> epoch seconds at UTC midnight. Raises on a malformed date."""
    parsed = date.fromisoformat(iso)
    return int(datetime(parsed.year, parsed.month, parsed.day,
                        tzinfo=timezone.utc).timestamp())


# ---------------------------------------------------------------------------
# Pure payload parsing
# ---------------------------------------------------------------------------
def chart_json_to_bars(payload: object,
                       *, adjusted: bool = True) -> tuple[list[dict[str, object]], list[str]]:
    """Turn one Yahoo v8 chart payload into ``(bars, notes)``.

    Behaviour is deliberately forgiving about *data* and strict about *shape*:

    * missing / non-dict ``chart`` -> ``ValueError`` (we were served something
      that is not a chart payload at all)
    * ``chart.error`` set -> ``ValueError`` carrying Yahoo's own description
    * ``chart.result`` empty or null -> ``([], [note])``, not an exception; a
      ticker with no coverage must degrade into a skip, never a crash
    * ``null`` closes (halted / no-trade days) are dropped and counted
    * ``timestamp`` and the close series of different lengths are truncated to
      the shorter one and counted — pairing them positionally past the shorter
      array would invent prices.
    """
    notes: list[str] = []
    if not isinstance(payload, dict) or not isinstance(payload.get("chart"), dict):
        raise ValueError("not a Yahoo v8 chart payload (no 'chart' object)")
    chart = payload["chart"]
    error = chart.get("error")
    if error:
        description = error.get("description") if isinstance(error, dict) else error
        raise ValueError(f"Yahoo returned an error: {description}")

    results = chart.get("result") or []
    if not isinstance(results, list) or not results:
        return [], ["chart.result is empty — symbol unknown, delisted, or window has no bars"]
    result = results[0]
    if not isinstance(result, dict):
        raise ValueError("chart.result[0] is not an object")

    meta = result.get("meta") if isinstance(result.get("meta"), dict) else {}
    gmtoffset = int(meta.get("gmtoffset") or 0)
    timestamps = result.get("timestamp") or []
    closes = _close_series(result, adjusted=adjusted)

    if len(timestamps) != len(closes):
        notes.append(f"timestamp/close length mismatch ({len(timestamps)} vs {len(closes)}) "
                     "— truncated to the shorter series")
    bars: list[dict[str, object]] = []
    dropped = 0
    for epoch, close in zip(timestamps, closes):
        if epoch is None or close is None:
            dropped += 1
            continue
        try:
            value = float(close)
        except (TypeError, ValueError):
            dropped += 1
            continue
        if not math.isfinite(value) or value <= 0 or isinstance(close, bool):
            dropped += 1
            continue
        try:
            session = epoch_to_iso(epoch, gmtoffset)
        except (ValueError, TypeError, OverflowError, OSError):
            dropped += 1
            continue
        bars.append({"date": session, "close": value,
                     "adjustment": "split_dividend" if adjusted else "none"})
    if dropped:
        notes.append(f"dropped {dropped} bar(s) with a null/unparseable/nonfinite/nonpositive price or time")
    bars.sort(key=lambda b: str(b["date"]))
    if len({bar["date"] for bar in bars}) != len(bars):
        raise ValueError("duplicate daily sessions in chart response")
    return bars, notes


def _close_series(result: dict[str, object], *, adjusted: bool) -> list[object]:
    """Return exactly the requested price basis; never substitute raw closes."""
    indicators = result.get("indicators")
    if not isinstance(indicators, dict):
        return []
    if adjusted:
        adj = indicators.get("adjclose")
        if isinstance(adj, list) and adj and isinstance(adj[0], dict):
            series = adj[0].get("adjclose")
            if isinstance(series, list):
                return series
        raise ValueError("requested split/dividend adjusted closes are missing; raw close fallback refused")
    quote = indicators.get("quote")
    if isinstance(quote, list) and quote and isinstance(quote[0], dict):
        series = quote[0].get("close")
        if isinstance(series, list):
            return series
    return []


def clip_bars(bars: list[dict[str, object]], start: str, end: str) -> list[dict[str, object]]:
    """Keep only bars inside the inclusive ``[start, end]`` ISO window."""
    return [b for b in bars if start <= str(b["date"]) <= end]


# ---------------------------------------------------------------------------
# Pure merging
# ---------------------------------------------------------------------------
def merge_price_maps(base: PriceMap, incoming: PriceMap) -> PriceMap:
    """Merge only when adjustment identity is known and revisions cannot splice.

    Adjusted or legacy unknown-basis histories must be refreshed over the whole
    retained window, because a dividend/split can revise every historical bar.
    Separate tickers can still be updated independently. Extra bar metadata is
    compatible with JsonPriceSource and records the requested basis explicitly.
    """
    merged: PriceMap = {}
    for ticker in sorted(set(base) | set(incoming)):
        old, new = base.get(ticker, []), incoming.get(ticker, [])
        for series in (old, new):
            if not isinstance(series, list) or any(not isinstance(b, dict) or "date" not in b or "close" not in b for b in series):
                raise ValueError(f"{ticker}: expected price rows with date and close")
            if len({b.get("adjustment", "unknown") for b in series}) > 1:
                raise ValueError(f"{ticker}: mixed adjustment basis")
            if len({b["date"] for b in series}) != len(series):
                raise ValueError(f"{ticker}: duplicate session in price map")
            for bar in series:
                date.fromisoformat(str(bar["date"]))
                if isinstance(bar["close"], bool) or not math.isfinite(float(bar["close"])) or float(bar["close"]) <= 0:
                    raise ValueError(f"{ticker}: close must be finite and positive")
        if old and new:
            old_basis = {b.get("adjustment", "unknown") for b in old}
            new_basis = {b.get("adjustment", "unknown") for b in new}
            if len(old_basis) != 1 or len(new_basis) != 1:
                raise ValueError(f"{ticker}: mixed adjustment basis")
            full_refresh = {b["date"] for b in old}.issubset({b["date"] for b in new})
            if old_basis != new_basis and not full_refresh:
                raise ValueError(f"{ticker}: adjustment basis changed; refetch full retained window")
            if old_basis != {"none"} or new_basis != {"none"}:
                if not full_refresh:
                    raise ValueError(f"{ticker}: adjusted/unknown history requires full retained-window refresh")
                old = []
        by_date: dict[str, dict[str, object]] = {}
        for bar in [*old, *new]:
            by_date[str(bar["date"])] = {**bar, "date": str(bar["date"]), "close": float(bar["close"])}
        merged[ticker] = [by_date[d] for d in sorted(by_date)]
    return merged


def load_price_map(path: Path) -> PriceMap:
    """Read an existing price map. Missing file -> empty map (first run)."""
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path}: price map must be a JSON object keyed by ticker")
    return {str(k): list(v) for k, v in raw.items()}


# ---------------------------------------------------------------------------
# Network (the only impure part)
# ---------------------------------------------------------------------------
def fetch_yahoo_chart(symbol: str, start: str, end: str,
                      timeout: float = DEFAULT_TIMEOUT_SECONDS) -> dict[str, object]:
    """GET one Yahoo v8 daily chart payload. Raises ValueError with a clear message."""
    # period2 is exclusive-ish; pad a day so `end` itself is included.
    params = urllib.parse.urlencode({
        "period1": iso_to_epoch(start),
        "period2": iso_to_epoch(end) + SECONDS_PER_DAY,
        "interval": "1d",
        "events": "div,split",
    })
    url = f"{YAHOO_CHART_URL.format(symbol=urllib.parse.quote(symbol, safe=''))}?{params}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                                   "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ValueError(
            f"{symbol}: Yahoo chart endpoint returned HTTP {exc.code} {exc.reason}. "
            "The keyless endpoint rate-limits and occasionally blocks; retry later, "
            "or fetch with --source yfinance."
        ) from exc
    except urllib.error.URLError as exc:
        raise ValueError(f"{symbol}: cannot reach the Yahoo chart endpoint ({exc.reason})") from exc
    except TimeoutError as exc:
        raise ValueError(f"{symbol}: Yahoo chart request timed out after {timeout:.0f}s") from exc
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{symbol}: Yahoo returned non-JSON ({body[:120]!r})") from exc


def fetch_bars_yahoo(symbol: str, start: str, end: str,
                     timeout: float = DEFAULT_TIMEOUT_SECONDS
                     ) -> tuple[list[dict[str, object]], list[str]]:
    """Keyless stdlib path: chart endpoint -> clipped bars."""
    bars, notes = chart_json_to_bars(fetch_yahoo_chart(symbol, start, end, timeout))
    return clip_bars(bars, start, end), notes


def fetch_bars_yfinance(symbol: str, start: str, end: str,
                        timeout: float = DEFAULT_TIMEOUT_SECONDS
                        ) -> tuple[list[dict[str, object]], list[str]]:
    """Opt-in third-party path. Lazy import keeps ``scripts/`` stdlib-only."""
    del timeout  # yfinance manages its own session timeouts
    try:
        import yfinance  # noqa: PLC0415
    except ImportError as exc:
        raise ValueError(
            "yfinance is not installed, so --source yfinance cannot run. "
            "Either `pip install yfinance`, or drop the flag to use the keyless "
            "stdlib Yahoo chart endpoint (the default)."
        ) from exc
    end_exclusive = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
    frame = yfinance.Ticker(symbol).history(start=start, end=end_exclusive, auto_adjust=True)
    bars = [{"date": str(index.date()), "close": float(row["Close"]), "adjustment": "split_dividend"}
            for index, row in frame.iterrows()]
    if any(not math.isfinite(b["close"]) or b["close"] <= 0 for b in bars):
        raise ValueError("yfinance returned nonfinite/nonpositive adjusted prices")
    bars.sort(key=lambda b: str(b["date"]))
    return clip_bars(bars, start, end), []


def build_price_map(tickers: list[str], start: str, end: str, *, source: str = "yahoo",
                    timeout: float = DEFAULT_TIMEOUT_SECONDS) -> tuple[PriceMap, list[str]]:
    """Fetch every ticker into one price map. One bad ticker never kills the run."""
    fetcher = fetch_bars_yfinance if source == "yfinance" else fetch_bars_yahoo
    prices: PriceMap = {}
    notes: list[str] = []
    for symbol in tickers:
        try:
            bars, ticker_notes = fetcher(symbol, start, end, timeout)
        except ValueError as exc:
            notes.append(f"{symbol}: FAILED — {exc}")
            continue
        notes.extend(f"{symbol}: {note}" for note in ticker_notes)
        if not bars:
            notes.append(f"{symbol}: no bars in {start}..{end}")
            continue
        prices[symbol] = bars
    return prices, notes


# ---------------------------------------------------------------------------
# Offline self-test
# ---------------------------------------------------------------------------
def _payload(timestamps: list[object], closes: list[object], *,
             gmtoffset: int = 0, error: object = None) -> dict[str, object]:
    """Hand-built Yahoo-shaped payload for the offline tests."""
    if error is not None:
        return {"chart": {"result": None, "error": error}}
    return {"chart": {"error": None, "result": [{
        "meta": {"gmtoffset": gmtoffset},
        "timestamp": timestamps,
        "indicators": {"adjclose": [{"adjclose": closes}]},
    }]}}


def _self_test() -> int:  # noqa: C901 - a flat list of independent assertions
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    # --- chart_json_to_bars ------------------------------------------------
    day = iso_to_epoch("2026-04-01") + 13 * 3600      # 09:30 New York = 13:30 UTC
    nxt = day + SECONDS_PER_DAY
    bars, notes = chart_json_to_bars(_payload([day, nxt], [100.0, 101.5], gmtoffset=-14400))
    check("well-formed payload -> two dated bars",
          bars == [{"date": "2026-04-01", "close": 100.0, "adjustment": "split_dividend"},
                   {"date": "2026-04-02", "close": 101.5, "adjustment": "split_dividend"}] and not notes,
          f"{bars} {notes}")

    bars, notes = chart_json_to_bars(_payload([], []))
    check("empty result arrays -> no bars, no exception", bars == [] and not notes, str(notes))

    bars, notes = chart_json_to_bars({"chart": {"result": [], "error": None}})
    check("chart.result empty -> [] plus an explaining note",
          bars == [] and len(notes) == 1 and "empty" in notes[0], str(notes))

    bars, notes = chart_json_to_bars(_payload([day, nxt, nxt + SECONDS_PER_DAY],
                                              [100.0, None, 102.0]))
    check("null close is dropped and counted",
          len(bars) == 2 and any("null/unparseable" in n for n in notes), f"{bars} {notes}")

    bars, notes = chart_json_to_bars(_payload([day, nxt, nxt + SECONDS_PER_DAY], [100.0, 101.0]))
    check("mismatched array lengths truncate to the shorter series",
          len(bars) == 2 and any("length mismatch" in n for n in notes), f"{bars} {notes}")

    try:
        chart_json_to_bars(_payload([], [], error={"description": "No data found"}))
        ok, detail = False, "no exception raised"
    except ValueError as exc:
        ok, detail = "No data found" in str(exc), str(exc)
    check("Yahoo error object surfaces as ValueError", ok, detail)

    try:
        chart_json_to_bars({"nope": 1})
        ok, detail = False, "no exception raised"
    except ValueError as exc:
        ok, detail = "chart" in str(exc), str(exc)
    check("non-chart payload raises ValueError", ok, detail)

    # A missing requested basis is an error, never a switch to raw quotes.
    raw = {"chart": {"error": None, "result": [{
        "meta": {"gmtoffset": 0}, "timestamp": [day],
        "indicators": {"quote": [{"close": [99.0]}]}}]}}
    try:
        chart_json_to_bars(raw)
        refused = False
    except ValueError:
        refused = True
    check("missing adjusted price refuses raw fallback", refused)
    bars, _ = chart_json_to_bars(raw, adjusted=False)
    check("explicit raw close records its basis", bars[0]["adjustment"] == "none")
    bars, _ = chart_json_to_bars(_payload([day, nxt, nxt + SECONDS_PER_DAY], [float("nan"), float("inf"), -1]))
    check("NaN/Inf/nonpositive bars cannot reach price map", not bars)

    # --- epoch / date boundaries -------------------------------------------
    check("epoch_to_iso at UTC midnight", epoch_to_iso(iso_to_epoch("2026-04-01")) == "2026-04-01")
    check("epoch_to_iso one second before midnight",
          epoch_to_iso(iso_to_epoch("2026-04-02") - 1) == "2026-04-01")
    # ASX under daylight saving: 10:00 AEDT (UTC+11) is 23:00 UTC the day BEFORE.
    aedt_open = iso_to_epoch("2026-02-02") - 3600
    check("gmtoffset repairs the ASX daylight-saving off-by-one",
          epoch_to_iso(aedt_open) == "2026-02-01"
          and epoch_to_iso(aedt_open, 11 * 3600) == "2026-02-02")
    check("iso_to_epoch round-trips", iso_to_epoch(epoch_to_iso(1_775_000_000)) % SECONDS_PER_DAY == 0)
    try:
        iso_to_epoch("2026-13-01")
        ok = False
    except ValueError:
        ok = True
    check("iso_to_epoch rejects an impossible date", ok)

    # --- merge_price_maps ---------------------------------------------------
    a: PriceMap = {"AAA": [{"date": "2026-04-01", "close": 1.0}]}
    b: PriceMap = {"BBB": [{"date": "2026-04-01", "close": 2.0}]}
    check("disjoint maps union", merge_price_maps(a, b) == {**a, **b}, str(merge_price_maps(a, b)))

    older: PriceMap = {"AAA": [{"date": "2026-04-01", "close": 1.0},
                               {"date": "2026-04-02", "close": 2.0}]}
    newer: PriceMap = {"AAA": [{"date": "2026-04-02", "close": 2.0},
                               {"date": "2026-04-03", "close": 3.0}]}
    try:
        merge_price_maps(older, newer)
        refused = False
    except ValueError:
        refused = True
    check("partial adjusted/unknown history refresh rejected", refused)
    try:
        merge_price_maps({}, {"AAA": [{"date": "2026-04-01", "close": 1, "adjustment": "none"}, {"date": "2026-04-02", "close": 2, "adjustment": "split_dividend"}]})
        refused = False
    except ValueError:
        refused = True
    check("mixed basis rejected even on first import", refused)
    for bar in older["AAA"] + newer["AAA"]:
        bar["adjustment"] = "none"
    merged = merge_price_maps(older, newer)
    check("overlapping ranges dedupe by date",
          [bar["date"] for bar in merged["AAA"]] == ["2026-04-01", "2026-04-02", "2026-04-03"],
          str(merged))

    conflict = merge_price_maps({"AAA": [{"date": "2026-04-01", "close": 1.0}]},
                                {"AAA": [{"date": "2026-04-01", "close": 9.0}]})
    check("conflicting close: incoming wins", conflict["AAA"] == [{"date": "2026-04-01", "close": 9.0}],
          str(conflict))

    frozen_base = {"AAA": [{"date": "2026-04-01", "close": 1.0}]}
    merge_price_maps(frozen_base, {"AAA": [{"date": "2026-04-01", "close": 9.0}]})
    check("merge does not mutate its arguments",
          frozen_base == {"AAA": [{"date": "2026-04-01", "close": 1.0}]}, str(frozen_base))

    check("merging with an empty map is identity", merge_price_maps({}, a) == a
          and merge_price_maps(a, {}) == a)

    # --- clip + engine round-trip ------------------------------------------
    check("clip_bars is inclusive on both ends",
          [b["date"] for b in clip_bars(merged["AAA"], "2026-04-02", "2026-04-03")]
          == ["2026-04-02", "2026-04-03"])

    # The whole point of the format: the engine must accept what we emit.
    sys.path.insert(0, str(ROOT / "evals" / "stockbench"))
    from backtest_engine import JsonPriceSource  # noqa: PLC0415

    source = JsonPriceSource(prices=merge_price_maps({}, {"NVDA": [
        {"date": "2026-04-01", "close": 100.0}, {"date": "2026-04-02", "close": 110.0}]}))
    got = source.get_bars("NVDA", "2026-04-01", "2026-04-02")
    check("JsonPriceSource consumes our output verbatim",
          len(got) == 2 and got[1].close == 110.0, str(got))

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        flag = "ok " if ok else "XX "
        suffix = f"  [{detail}]" if (detail and not ok) else ""
        print(f"  {flag} {name}{suffix}")
    print(f"\n{passed}/{len(results)} prices unit tests passed.")
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Fetch a JSON price map for the offline backtest engine")
    ap.add_argument("--tickers", help="comma-separated symbols, e.g. NVDA,GC=F,SPY")
    ap.add_argument("--start", help="inclusive ISO start date")
    ap.add_argument("--end", help="inclusive ISO end date")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help=f"output JSON (default {DEFAULT_OUT})")
    ap.add_argument("--merge", action="store_true",
                    help="merge into the existing --out file instead of replacing it")
    ap.add_argument("--source", choices=("yahoo", "yfinance"), default="yahoo",
                    help="yahoo = keyless stdlib endpoint (default); yfinance = lazy import")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS,
                    help="per-request timeout in seconds")
    ap.add_argument("--self-test", action="store_true", help="run built-in offline unit tests")
    return ap


def main() -> int:
    args = build_parser().parse_args()
    if args.self_test:
        return _self_test()
    if not args.tickers or not args.start or not args.end:
        print("usage: prices.py --tickers NVDA,SPY --start YYYY-MM-DD --end YYYY-MM-DD "
              "[--out PATH] [--merge] [--source yahoo|yfinance] | --self-test", file=sys.stderr)
        return 2

    try:
        tickers = [validate_ticker_component(t.strip())
                   for t in args.tickers.split(",") if t.strip()]
        if not tickers:
            raise ValueError("--tickers resolved to an empty list")
        if date.fromisoformat(args.start) > date.fromisoformat(args.end):
            raise ValueError(f"--start {args.start} is after --end {args.end}")
        out_path = Path(args.out)
        base = load_price_map(out_path) if args.merge else {}
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    fetched, notes = build_price_map(tickers, args.start, args.end,
                                     source=args.source, timeout=args.timeout)
    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    if not fetched:
        print("error: no bars fetched for any ticker — nothing written", file=sys.stderr)
        return 1

    try:
        price_map = merge_price_maps(base, fetched)
    except ValueError as exc:
        print(f"error: {exc}; nothing written", file=sys.stderr)
        return 2
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(price_map, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                        encoding="utf-8")
    for symbol in sorted(price_map):
        series = price_map[symbol]
        span = f"{series[0]['date']}..{series[-1]['date']}" if series else "empty"
        print(f"  {symbol:<10} {len(series):>4} bars  {span}")
    print(f"\nWrote {out_path.resolve()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

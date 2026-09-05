#!/usr/bin/env python3
"""Polymarket market-implied probabilities via the free Gamma API.

WHY THIS EXISTS
---------------
The macro/news agents kept emitting SUBJECTIVE probabilities ("hot CPI 35%",
"hike <5%") — model guesses. Polymarket carries real-money prediction markets
on Fed decisions, CPI prints, recession odds, etc. Market-implied odds priced
by people betting actual dollars are a strictly better probability source than
an LLM's hunch. This script fetches them keyless.

Pattern ported from mvanhorn/last30days-skill (MIT) `lib/polymarket.py`,
re-implemented stdlib-only to keep this repo dependency-free.

API: https://gamma-api.polymarket.com/public-search  (no auth, generous limits)

USAGE
-----
    python scripts/polymarket_odds.py "fed rate hike"
    python scripts/polymarket_odds.py "CPI inflation" --limit 8
    python scripts/polymarket_odds.py "fed decision june" --json
    python scripts/polymarket_odds.py --self-test     # offline, fixtures only

Agents: cite these as `market-implied P(...) = X% (Polymarket, $Vol)` — they are
tool-sourced numbers, NOT [UNSOURCED]. They are still forecasts by crowds, not
guarantees; cite volume so thin markets are visible.

ROBUSTNESS
----------
The Gamma API is a third party with no contract to us: it can return HTML from a
proxy, a JSON list where a dict was expected, or a market whose `outcomePrices`
is a string, a null, or garbage. Every one of those DEGRADES to "no rows" plus a
warning on stderr — never a traceback, because this is called mid-pipeline and a
crash there loses the whole analyst run. Probabilities are CLAMPED to [0, 1] but
never rescaled: the market's own prices are the market-implied probabilities;
renormalising them would invent numbers. A multi-outcome market whose prices do
not sum to ~1 is FLAGGED instead, so the reader can distrust it.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from runtime import force_utf8_stdio

    force_utf8_stdio()
except Exception:
    pass

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
UA = "trading-copilot/0.2 (research; github.com/ShousenZHANG/trading-copilot)"

#: Every outbound call gets this. Never omit a timeout: a hung Gamma socket
#: would stall the analyst run that called us with no diagnostic at all.
HTTP_TIMEOUT_SECONDS = 20

#: A multi-outcome market's prices should sum to ~1. Beyond this deviation the
#: row is flagged rather than rescaled — see ROBUSTNESS in the module docstring.
PROB_SUM_TOLERANCE = 0.05


def _get(url: str, timeout: int = HTTP_TIMEOUT_SECONDS) -> object:
    """Fetch and JSON-decode. Raises; every caller must handle failure."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="replace"))


def _parse_maybe_json_list(value: object) -> list:
    """Gamma returns some list fields as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def normalize_outcomes(
    outcomes: object, prices: object
) -> tuple[list[tuple[str, float]], bool]:
    """Pair outcome labels with probabilities.

    Returns ``(pairs, probs_suspect)``. Non-numeric prices are dropped;
    probabilities are CLAMPED to [0, 1] but never rescaled. ``probs_suspect`` is
    True when a multi-outcome market's clamped prices do not sum to ~1, which is
    how a stale or malformed payload announces itself.
    """
    pairs: list[tuple[str, float]] = []
    for label, price in zip(_parse_maybe_json_list(outcomes), _parse_maybe_json_list(prices)):
        if isinstance(price, bool) or price is None:
            continue
        try:
            value = float(price)
        except (TypeError, ValueError):
            continue
        if value != value:  # NaN
            continue
        pairs.append((str(label), min(1.0, max(0.0, value))))
    suspect = len(pairs) > 1 and abs(sum(p for _, p in pairs) - 1.0) > PROB_SUM_TOLERANCE
    return pairs, suspect


def _market_row(event_title: str, market: object) -> dict | None:
    """Flatten one Gamma market into a row, or None if it is unusable."""
    if not isinstance(market, dict):
        return None
    pairs, suspect = normalize_outcomes(market.get("outcomes"), market.get("outcomePrices"))
    if not pairs:
        return None
    try:
        volume = float(market.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    end_date = market.get("endDate")
    return {
        "event": event_title,
        "question": market.get("question") or event_title or "(untitled market)",
        "outcomes": pairs,
        "prob_sum": round(sum(p for _, p in pairs), 4),
        "probs_suspect": suspect,
        "volume_usd": round(volume, 0),
        "end_date": end_date[:10] if isinstance(end_date, str) else "",
        "closed": bool(market.get("closed")),
    }


def parse_search_payload(data: object, seen_market_ids: set[str]) -> list[dict]:
    """Flatten one Gamma search response. Any unexpected shape yields []."""
    if not isinstance(data, dict):
        return []
    events = data.get("events")
    if not isinstance(events, list):
        return []
    rows: list[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        title = ev.get("title")
        title = title if isinstance(title, str) else ""
        markets = ev.get("markets")
        for m in markets if isinstance(markets, list) else []:
            mid = str(m.get("id") or m.get("conditionId") or "") if isinstance(m, dict) else ""
            if mid and mid in seen_market_ids:
                continue
            row = _market_row(title, m)
            if row is None:
                continue
            if mid:
                seen_market_ids.add(mid)
            rows.append(row)
    return rows


def sort_rows(rows: list[dict]) -> list[dict]:
    """Open markets first, then by volume (real money = real signal)."""
    return sorted(rows, key=lambda r: (r["closed"], -r["volume_usd"]))


def search(query: str, pages: int = 2) -> list[dict]:
    """Search Polymarket events; return flattened market rows (never raises)."""
    rows: list[dict] = []
    seen_market_ids: set[str] = set()
    for page in range(1, pages + 1):
        qs = urllib.parse.urlencode({"q": query, "page": page})
        try:
            data = _get(f"{SEARCH_URL}?{qs}")
        except Exception as exc:  # network/HTTP/non-JSON — report and stop paging
            print(f"warning: fetch failed page {page}: {exc}", file=sys.stderr)
            break
        page_rows = parse_search_payload(data, seen_market_ids)
        if not page_rows:
            # Either the page is empty or the shape was unexpected; either way
            # there is nothing more to page through.
            break
        rows += page_rows
    return sort_rows(rows)


def format_rows(rows: list[dict], limit: int) -> str:
    if not rows:
        return "No Polymarket markets found for this query."
    out = ["=== Polymarket market-implied odds (real-money) ===", ""]
    for r in rows[:limit]:
        status = "CLOSED" if r["closed"] else "open"
        odds = "  ".join(f"{o}: {p:.0%}" for o, p in r["outcomes"][:4])
        out.append(f"[{status}] {r['question']}")
        out.append(f"   {odds}")
        out.append(f"   volume ${r['volume_usd']:,.0f} | ends {r['end_date'] or 'n/a'}")
        if r.get("probs_suspect"):
            out.append(
                f"   !! prices sum to {r['prob_sum']:.2f}, not ~1.00 — treat as unreliable"
            )
        out.append("")
    out.append("Note: crowd forecasts, not guarantees. Thin volume = weak signal.")
    return "\n".join(out)


# --------------------------------------------------------------------------
# Built-in unit tests. Recorded payload shapes only — NO network.
# Run: python scripts/polymarket_odds.py --self-test
# --------------------------------------------------------------------------
# Shape recorded from gamma-api.polymarket.com/public-search: list fields arrive
# as JSON-encoded STRINGS, which is the detail that breaks naive parsers.
_FIXTURE_OK = {
    "events": [{
        "title": "Fed decision in June",
        "markets": [
            {
                "id": "512345",
                "question": "Will the Fed cut rates in June?",
                "outcomes": '["Yes", "No"]',
                "outcomePrices": '["0.72", "0.28"]',
                "volume": "1840233.5",
                "endDate": "2026-06-18T12:00:00Z",
                "closed": False,
            },
            {
                "conditionId": "0xabc",
                "question": "Will the Fed hike in June?",
                "outcomes": ["Yes", "No"],
                "outcomePrices": [0.03, 0.97],
                "volume": 4210,
                "endDate": "2026-06-18T12:00:00Z",
                "closed": True,
            },
        ],
    }]
}

_FIXTURE_DEGENERATE = {
    "events": [
        "not-a-dict",
        {"title": "Broken", "markets": "not-a-list"},
        {"title": "Broken 2", "markets": [None, 42, {}]},
        {"title": "No prices", "markets": [{"id": "9", "outcomes": '["Yes"]',
                                            "outcomePrices": "not-json"}]},
        {"title": "Junk prices", "markets": [{"id": "10", "outcomes": '["Yes","No"]',
                                              "outcomePrices": '["abc", null]'}]},
    ]
}


def _self_test() -> int:
    seen: set[str] = set()
    ok_rows = parse_search_payload(_FIXTURE_OK, seen)
    dup_rows = parse_search_payload(_FIXTURE_OK, seen)  # same ids -> deduped
    clamped, clamp_suspect = normalize_outcomes('["Yes","No"]', '["1.4","-0.3"]')
    lopsided, lopsided_suspect = normalize_outcomes('["Yes","No"]', '["0.9","0.9"]')
    sorted_rows = sort_rows(list(ok_rows))

    cases: list[tuple[str, bool]] = [
        # --- payload parsing -------------------------------------------------
        ("well-formed payload yields 2 rows", len(ok_rows) == 2),
        ("JSON-string list fields are decoded",
            ok_rows[0]["outcomes"] == [("Yes", 0.72), ("No", 0.28)]),
        ("native list fields also work",
            ok_rows[1]["outcomes"] == [("Yes", 0.03), ("No", 0.97)]),
        ("string volume is coerced", ok_rows[0]["volume_usd"] == 1840234.0),
        ("endDate is truncated to a date", ok_rows[0]["end_date"] == "2026-06-18"),
        ("closed flag preserved", ok_rows[0]["closed"] is False and ok_rows[1]["closed"] is True),
        ("event title carried onto the row", ok_rows[0]["event"] == "Fed decision in June"),
        ("conditionId works as the dedup key", "0xabc" in seen),
        ("repeat page is fully deduped", dup_rows == []),
        # --- degenerate shapes degrade, never raise --------------------------
        ("a JSON list instead of an object -> []", parse_search_payload([1, 2, 3], set()) == []),
        ("a bare string -> []", parse_search_payload("<html>502</html>", set()) == []),
        ("None -> []", parse_search_payload(None, set()) == []),
        ("missing events key -> []", parse_search_payload({}, set()) == []),
        ("events not a list -> []", parse_search_payload({"events": "x"}, set()) == []),
        ("every degenerate event is skipped", parse_search_payload(_FIXTURE_DEGENERATE, set()) == []),
        # --- probability normalisation ---------------------------------------
        ("out-of-range probabilities are clamped to [0, 1]",
            clamped == [("Yes", 1.0), ("No", 0.0)]),
        ("clamped 1.0/0.0 still sums to 1, so not suspect", clamp_suspect is False),
        ("prices that do not sum to ~1 are flagged", lopsided_suspect is True),
        ("lopsided prices are reported, not rescaled",
            lopsided == [("Yes", 0.9), ("No", 0.9)]),
        ("a clean binary market is not flagged", ok_rows[0]["probs_suspect"] is False),
        ("prob_sum is reported", ok_rows[0]["prob_sum"] == 1.0),
        ("non-numeric prices are dropped",
            normalize_outcomes('["Yes","No"]', '["abc","0.5"]')[0] == [("No", 0.5)]),
        ("null price is dropped", normalize_outcomes('["Yes"]', "[null]")[0] == []),
        ("booleans are not treated as numbers",
            normalize_outcomes('["Yes"]', "[true]")[0] == []),
        ("single-outcome market is never flagged",
            normalize_outcomes('["Yes"]', '["0.4"]')[1] is False),
        # --- ranking + rendering ----------------------------------------------
        ("open markets rank before closed ones", sorted_rows[0]["closed"] is False),
        ("empty result renders a message, not a crash",
            format_rows([], 6).startswith("No Polymarket markets")),
        ("suspect rows are called out in the report",
            "treat as unreliable" in format_rows(
                [{"question": "q", "outcomes": [("Yes", 0.9), ("No", 0.9)], "prob_sum": 1.8,
                  "probs_suspect": True, "volume_usd": 10, "end_date": "", "closed": False}], 1)),
        ("timeout constant is set", HTTP_TIMEOUT_SECONDS > 0),
    ]

    passed = 0
    for label, ok in cases:
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {label}")
    print(f"\n{passed}/{len(cases)} polymarket_odds unit tests passed.")
    return 0 if passed == len(cases) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Polymarket market-implied probabilities")
    ap.add_argument("query", nargs="?", default=None,
                    help='e.g. "fed rate hike", "CPI inflation", "recession 2026"')
    ap.add_argument("--limit", type=int, default=6, help="max markets to show")
    ap.add_argument("--pages", type=int, default=2, help="search pages to fetch")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="run built-in unit tests (offline)")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()
    if not args.query:
        print("usage: polymarket_odds.py QUERY | --self-test", file=sys.stderr)
        return 2

    rows = search(args.query, pages=args.pages)
    if args.json:
        json.dump(rows[: args.limit], sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(format_rows(rows, args.limit))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())

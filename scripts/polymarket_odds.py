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

Agents: cite these as `market-implied P(...) = X% (Polymarket, $Vol)` — they are
tool-sourced numbers, NOT [UNSOURCED]. They are still forecasts by crowds, not
guarantees; cite volume so thin markets are visible.
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


def _get(url: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _parse_maybe_json_list(value) -> list:
    """Gamma returns some list fields as JSON-encoded strings."""
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def search(query: str, pages: int = 2) -> list[dict]:
    """Search Polymarket events; return flattened market rows."""
    rows: list[dict] = []
    seen_market_ids: set[str] = set()
    for page in range(1, pages + 1):
        qs = urllib.parse.urlencode({"q": query, "page": page})
        try:
            data = _get(f"{SEARCH_URL}?{qs}")
        except Exception as exc:  # network/HTTP — report and stop paging
            print(f"warning: fetch failed page {page}: {exc}", file=sys.stderr)
            break
        events = data.get("events") or []
        if not events:
            break
        for ev in events:
            ev_title = ev.get("title") or ""
            for m in ev.get("markets") or []:
                mid = str(m.get("id") or m.get("conditionId") or "")
                if mid in seen_market_ids:
                    continue
                seen_market_ids.add(mid)
                outcomes = _parse_maybe_json_list(m.get("outcomes"))
                prices = _parse_maybe_json_list(m.get("outcomePrices"))
                pairs = []
                for o, p in zip(outcomes, prices):
                    try:
                        pairs.append((str(o), float(p)))
                    except (TypeError, ValueError):
                        continue
                if not pairs:
                    continue
                try:
                    volume = float(m.get("volume") or 0)
                except (TypeError, ValueError):
                    volume = 0.0
                rows.append({
                    "event": ev_title,
                    "question": m.get("question") or ev_title,
                    "outcomes": pairs,
                    "volume_usd": round(volume, 0),
                    "end_date": (m.get("endDate") or "")[:10],
                    "closed": bool(m.get("closed")),
                })
    # Rank: open markets first, then by volume (real money = real signal)
    rows.sort(key=lambda r: (r["closed"], -r["volume_usd"]))
    return rows


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
        out.append("")
    out.append("Note: crowd forecasts, not guarantees. Thin volume = weak signal.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description="Polymarket market-implied probabilities")
    ap.add_argument("query", help='e.g. "fed rate hike", "CPI inflation", "recession 2026"')
    ap.add_argument("--limit", type=int, default=6, help="max markets to show")
    ap.add_argument("--pages", type=int, default=2, help="search pages to fetch")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rows = search(args.query, pages=args.pages)
    if args.json:
        json.dump(rows[: args.limit], sys.stdout, indent=2, ensure_ascii=False)
        print()
    else:
        print(format_rows(rows, args.limit))
    return 0 if rows else 1


if __name__ == "__main__":
    sys.exit(main())

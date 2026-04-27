#!/usr/bin/env python3
"""Telegram notification helper for Trading Copilot.

Two modes:
- push-new-alerts: scan data/decisions/_scan-<TODAY>.md, push high-conviction
  calls (Buy/Sell + high confidence) that haven't been pushed before.
  Dedups via data/state/pushed_alerts.json (sha256 of ticker+date+rating).
- push-weekly: push the headline of the latest data/decisions/_weekly-*.md.

Reads:
- TELEGRAM_BOT_TOKEN
- TELEGRAM_CHAT_ID
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import io
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data" / "state"
SEEN_FILE = STATE_DIR / "pushed_alerts.json"
DECISIONS_DIR = ROOT / "data" / "decisions"


def _send(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram credentials not set — skipping push", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                print(f"Telegram API error: {payload}", file=sys.stderr)
                return False
            return True
    except Exception as e:
        print(f"Telegram send failed: {e}", file=sys.stderr)
        return False


def _load_seen() -> set[str]:
    if not SEEN_FILE.exists():
        return set()
    try:
        return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
    except Exception:
        return set()


def _save_seen(seen: set[str]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_FILE.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")


def _hash_alert(ticker: str, date: str, rating: str) -> str:
    return hashlib.sha256(f"{ticker}|{date}|{rating}".encode("utf-8")).hexdigest()[:16]


def push_new_alerts() -> int:
    today = dt.date.today().isoformat()
    scan_file = DECISIONS_DIR / f"_scan-{today}.md"
    if not scan_file.exists():
        print(f"No scan file for {today} — nothing to push")
        return 0

    text = scan_file.read_text(encoding="utf-8")
    # Parse scan summary table — looks for rows like:
    #   | NVDA | Buy | high | $1100 | 3-6m | [link](NVDA-2026-04-27.md) |
    row_re = re.compile(
        r"\|\s*([A-Z0-9.\-=^]{1,12})\s*\|\s*(Buy|Sell|Overweight|Underweight)\s*\|\s*(high|medium|low)?\s*\|"
    )
    matches = row_re.findall(text)

    if not matches:
        print("No actionable rows in scan summary")
        return 0

    seen = _load_seen()
    new_count = 0

    for ticker, rating, conviction in matches:
        h = _hash_alert(ticker, today, rating)
        if h in seen:
            continue
        # Only push high-conviction Buy/Sell
        if rating in ("Buy", "Sell") and (not conviction or conviction == "high"):
            link = f"data/decisions/{ticker}-{today}.md"
            msg = (
                f"📊 *{ticker}* — {rating}"
                + (f" ({conviction})" if conviction else "")
                + f"\nReport: `{link}`\n\n_⚠️ 教育用途, 非投资建议._"
            )
            if _send(msg):
                seen.add(h)
                new_count += 1

    _save_seen(seen)
    print(f"Pushed {new_count} new alert(s)")
    return new_count


def push_weekly() -> bool:
    files = sorted(DECISIONS_DIR.glob("_weekly-*.md"))
    if not files:
        print("No weekly review files found")
        return False
    latest = files[-1]
    text = latest.read_text(encoding="utf-8")

    # Extract first paragraph after first H1 (the headline).
    headline_match = re.search(r"^#\s+(.+?)\n+(.+?)(?=\n#|$)", text, re.DOTALL | re.MULTILINE)
    if not headline_match:
        snippet = text[:600]
    else:
        snippet = f"*{headline_match.group(1).strip()}*\n\n{headline_match.group(2).strip()[:500]}"

    msg = f"{snippet}\n\nFull: `{latest.name}`\n\n_⚠️ 教育用途._"
    return _send(msg)


def main() -> int:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("push-new-alerts", help="Push high-conviction Buy/Sell from today's /scan")
    sub.add_parser("push-weekly", help="Push the latest /weekly-review headline")

    p = sub.add_parser("test", help="Send a test message")
    p.add_argument("--text", default="✅ Trading Copilot Telegram test message.")

    args = parser.parse_args()

    if args.cmd == "push-new-alerts":
        push_new_alerts()
        return 0
    if args.cmd == "push-weekly":
        push_weekly()
        return 0
    if args.cmd == "test":
        ok = _send(args.text)
        return 0 if ok else 1

    return 1


if __name__ == "__main__":
    sys.exit(main())

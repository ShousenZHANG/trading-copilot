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

EXIT-CODE CONTRACT
------------------
0 = everything that should have been pushed was pushed (including "nothing new",
    which is dedup working as intended).
1 = a push was attempted and failed, credentials are missing, or the input file
    the command needs does not exist.

This used to always return 0, so a total notification blackout — expired token,
wrong chat id, Telegram down — looked exactly like a quiet day. It no longer
does. There is no scheduled workflow calling this any more; it is kept for local
scheduling (Task Scheduler / cron), where a non-zero exit is the only signal.

TOKEN SAFETY
------------
The bot token is embedded in the request URL. Every message this module prints —
including exception text, which for a malformed URL contains the URL — goes
through :func:`_redact` first, so the token can never reach stdout or stderr.

CLI
---
    python scripts/notify.py push-new-alerts
    python scripts/notify.py push-weekly
    python scripts/notify.py test --text "hello"
    python scripts/notify.py --self-test      # offline, no network, temp paths
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from runtime import force_utf8_stdio

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "data" / "state"
SEEN_FILE = STATE_DIR / "pushed_alerts.json"
DECISIONS_DIR = ROOT / "data" / "decisions"

#: Network timeout for every outbound call. Never omit it: a hung socket in a
#: scheduled task blocks forever with no output at all.
HTTP_TIMEOUT_SECONDS = 10

#: Scan summary table rows look like:
#:   | NVDA | Buy | high | $1100 | 3-6m | [link](NVDA-2026-04-27.md) |
#: The Chinese 结论卡 that now sits above the table is also a markdown table, but
#: it can never contain an English rating word (a hard project rule, because
#: parse_rating falls back to "first rating word anywhere"), so it cannot match.
#: The padding class is `[ \t]`, NOT `\s`: `\s` matches newlines, which let a
#: truncated row stitch itself to the start of the next line and produce a
#: phantom alert. A table row must live on one line.
SCAN_ROW_RE = re.compile(
    r"\|[ \t]*([A-Z0-9.\-=^]{1,12})[ \t]*\|[ \t]*"
    r"(Buy|Sell|Overweight|Underweight)[ \t]*\|[ \t]*(high|medium|low)?[ \t]*\|"
)


@dataclass(frozen=True)
class PushOutcome:
    """How many messages were attempted and how many actually went out."""

    attempted: int
    sent: int

    @property
    def failed(self) -> int:
        return self.attempted - self.sent

    def exit_code(self) -> int:
        return 1 if self.failed else 0


def _redact(text: str) -> str:
    """Strip the bot token out of anything about to be printed."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        text = text.replace(token, "***REDACTED***")
    # Belt and braces: also mask any `/bot<...>/` path segment, in case the
    # token was rotated mid-process or reaches us via a different string.
    return re.sub(r"/bot[^/\s]+/", "/bot***REDACTED***/", text)


def _send(message: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram credentials not set — cannot push", file=sys.stderr)
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    try:
        req = urllib.request.Request(url, data=data, method="POST")
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
            if not payload.get("ok"):
                print(_redact(f"Telegram API error: {payload}"), file=sys.stderr)
                return False
            return True
    except Exception as e:
        # Request() itself can raise on a malformed token, and its message
        # carries the URL — redact before printing.
        print(_redact(f"Telegram send failed: {e}"), file=sys.stderr)
        return False


def _load_seen(path: Path = SEEN_FILE) -> set[str]:
    if not path.exists():
        return set()
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return set()
    return set(loaded) if isinstance(loaded, list) else set()


def _save_seen(seen: set[str], path: Path = SEEN_FILE) -> bool:
    """Persist the dedup set. Returns False (with a warning) on I/O failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(sorted(seen), indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"warning: could not write {path} ({exc})", file=sys.stderr)
        return False
    return True


def _hash_alert(ticker: str, date: str, rating: str) -> str:
    return hashlib.sha256(f"{ticker}|{date}|{rating}".encode("utf-8")).hexdigest()[:16]


def parse_scan_rows(text: str) -> list[tuple[str, str, str]]:
    """Extract (ticker, rating, conviction) rows from a /scan summary table."""
    return SCAN_ROW_RE.findall(text)


def is_pushable(rating: str, conviction: str) -> bool:
    """Only high-conviction Buy/Sell is worth interrupting someone's day."""
    return rating in ("Buy", "Sell") and (not conviction or conviction == "high")


def push_new_alerts(seen_path: Path = SEEN_FILE) -> int:
    """Push today's new high-conviction calls. Returns a process exit code."""
    today = dt.date.today().isoformat()
    scan_file = DECISIONS_DIR / f"_scan-{today}.md"
    if not scan_file.exists():
        print(f"No scan file for {today} — nothing to push", file=sys.stderr)
        return 1

    matches = parse_scan_rows(scan_file.read_text(encoding="utf-8"))
    if not matches:
        print("No actionable rows in scan summary")
        return 0

    seen = _load_seen(seen_path)
    attempted = sent = 0
    for ticker, rating, conviction in matches:
        h = _hash_alert(ticker, today, rating)
        if h in seen or not is_pushable(rating, conviction):
            continue
        link = f"data/decisions/{ticker}-{today}.md"
        msg = (
            f"📊 *{ticker}* — {rating}"
            + (f" ({conviction})" if conviction else "")
            + f"\nReport: `{link}`\n\n_⚠️ 教育用途, 非投资建议._"
        )
        attempted += 1
        if _send(msg):
            seen.add(h)
            sent += 1

    outcome = PushOutcome(attempted, sent)
    _save_seen(seen, seen_path)
    print(f"Pushed {outcome.sent}/{outcome.attempted} new alert(s)")
    return outcome.exit_code()


def weekly_snippet(text: str, max_chars: int = 500) -> str:
    """First paragraph after the first H1 — the headline of a weekly review."""
    match = re.search(r"^#\s+(.+?)\n+(.+?)(?=\n#|$)", text, re.DOTALL | re.MULTILINE)
    if not match:
        return text[:600]
    return f"*{match.group(1).strip()}*\n\n{match.group(2).strip()[:max_chars]}"


def push_weekly() -> int:
    """Push the latest weekly-review headline. Returns a process exit code."""
    files = sorted(DECISIONS_DIR.glob("_weekly-*.md"))
    if not files:
        print("No weekly review files found — nothing to push", file=sys.stderr)
        return 1
    latest = files[-1]
    snippet = weekly_snippet(latest.read_text(encoding="utf-8"))
    msg = f"{snippet}\n\nFull: `{latest.name}`\n\n_⚠️ 教育用途._"
    return PushOutcome(1, 1 if _send(msg) else 0).exit_code()


# --------------------------------------------------------------------------
# Built-in unit tests — pure parsing/hashing/state only. No network, no writes
# outside a temp dir (data/state/ is gitignored; the test must not create it).
# Run: python scripts/notify.py --self-test
# --------------------------------------------------------------------------
_GOOD_TABLE = """
| Ticker | Rating | Conviction | Target | Horizon | Report |
|--------|--------|-----------|--------|---------|--------|
| NVDA | Buy | high | $1100 | 3-6m | [link](NVDA-2026-04-27.md) |
| INTC | Sell | medium | $18 | 3-6m | [link](INTC-2026-04-27.md) |
| BHP.AX | Overweight | high | A$47 | 6-12m | [link](BHP.AX-2026-04-27.md) |
"""

# The 结论卡 a human reads sits above the machine table. It is also a markdown
# table, so it is exactly the thing that could produce phantom rows.
_TABLE_WITH_CARD = """
**今天做什么**: 维持仓位, 不加仓 — 触发线未破。

| 项目 | 说明 |
|------|------|
| 现在做什么 | 不动, 继续定投 |
| 什么时候再看 | 下周五收盘 |
| 最大风险 | 单一名称集中度 |
| 和上次比变了什么 | 无实质变化 |

## Scan summary

| Ticker | Rating | Conviction | Target | Horizon | Report |
|--------|--------|-----------|--------|---------|--------|
| NVDA | Buy | high | $1100 | 3-6m | [link](NVDA-2026-04-27.md) |
"""

_MALFORMED = """
| NVDA | Buy |
| WAY-TOO-LONG-TICKER | Buy | high |
| NVDA | Accumulate | high |
| | Buy | high |
NVDA Buy high
"""


def _self_test() -> int:
    import tempfile

    # data/state/ is gitignored; running the tests must not bring it into being.
    state_dir_existed = STATE_DIR.exists()
    good = parse_scan_rows(_GOOD_TABLE)
    carded = parse_scan_rows(_TABLE_WITH_CARD)
    malformed = parse_scan_rows(_MALFORMED)
    h = _hash_alert("NVDA", "2026-04-27", "Buy")

    cases: list[tuple[str, bool]] = [
        # --- scan-table row regex ------------------------------------------
        ("well-formed table yields 3 rows", len(good) == 3),
        ("row 1 parsed", good[0] == ("NVDA", "Buy", "high")),
        ("row 2 parsed", good[1] == ("INTC", "Sell", "medium")),
        ("suffixed ticker parsed", good[2] == ("BHP.AX", "Overweight", "high")),
        ("Chinese card above the table yields no phantom rows", len(carded) == 1),
        ("the one real row survives the card", carded[0] == ("NVDA", "Buy", "high")),
        ("malformed rows yield nothing", malformed == []),
        ("empty text yields nothing", parse_scan_rows("") == []),
        # --- push filter ----------------------------------------------------
        ("high-conviction Buy is pushable", is_pushable("Buy", "high")),
        ("high-conviction Sell is pushable", is_pushable("Sell", "high")),
        ("medium-conviction Sell is not", not is_pushable("Sell", "medium")),
        ("Overweight is not pushable", not is_pushable("Overweight", "high")),
        ("missing conviction still pushes a Buy", is_pushable("Buy", "")),
        # --- _hash_alert stability -------------------------------------------
        ("hash is stable across calls", h == _hash_alert("NVDA", "2026-04-27", "Buy")),
        ("hash length is 16", len(h) == 16),
        ("rating change -> different hash", h != _hash_alert("NVDA", "2026-04-27", "Sell")),
        ("date change -> different hash", h != _hash_alert("NVDA", "2026-04-28", "Buy")),
        ("ticker change -> different hash", h != _hash_alert("AMD", "2026-04-27", "Buy")),
        # --- weekly snippet ---------------------------------------------------
        ("weekly snippet keeps the H1 as the headline",
            weekly_snippet("# 本周复盘\n\n组合小幅跑赢。\n\n# 明细\n").startswith("*本周复盘*")),
        ("weekly snippet degrades on a headless file",
            weekly_snippet("no heading here") == "no heading here"),
        # --- PushOutcome exit codes -------------------------------------------
        ("all sent -> exit 0", PushOutcome(3, 3).exit_code() == 0),
        ("nothing attempted -> exit 0", PushOutcome(0, 0).exit_code() == 0),
        ("partial failure -> exit 1", PushOutcome(3, 2).exit_code() == 1),
        ("total blackout -> exit 1", PushOutcome(3, 0).exit_code() == 1),
        # --- token redaction ---------------------------------------------------
        ("bot path segment is redacted",
            "secret" not in _redact("https://api.telegram.org/botsecret123/sendMessage")),
        ("redaction leaves ordinary text alone",
            _redact("Telegram API error: {'ok': False}") == "Telegram API error: {'ok': False}"),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        seen_path = Path(tmpdir) / "state" / "pushed_alerts.json"
        corrupt = Path(tmpdir) / "corrupt.json"
        corrupt.write_text("{not json", encoding="utf-8")
        wrong_shape = Path(tmpdir) / "wrong.json"
        wrong_shape.write_text('{"a": 1}', encoding="utf-8")
        saved = _save_seen({h, "deadbeef"}, seen_path)
        cases += [
            ("missing seen file -> empty set", _load_seen(Path(tmpdir) / "nope.json") == set()),
            ("save creates parent dirs", saved and seen_path.exists()),
            ("seen-set round trip", _load_seen(seen_path) == {h, "deadbeef"}),
            ("corrupt seen file degrades to empty", _load_seen(corrupt) == set()),
            ("unexpected seen shape degrades to empty", _load_seen(wrong_shape) == set()),
            # data/state/ is gitignored — the test must never create or read it.
            ("self-test uses a temp path, not the real seen file", seen_path != SEEN_FILE),
            ("self-test did not create data/state/", STATE_DIR.exists() == state_dir_existed),
        ]

    passed = 0
    for label, ok in cases:
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {label}")
    print(f"\n{passed}/{len(cases)} notify unit tests passed.")
    return 0 if passed == len(cases) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Telegram notifications for Trading Copilot")
    parser.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("push-new-alerts", help="Push high-conviction Buy/Sell from today's /scan")
    sub.add_parser("push-weekly", help="Push the latest /weekly-review headline")

    p = sub.add_parser("test", help="Send a test message")
    p.add_argument("--text", default="✅ Trading Copilot Telegram test message.")

    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if args.cmd == "push-new-alerts":
        return push_new_alerts()
    if args.cmd == "push-weekly":
        return push_weekly()
    if args.cmd == "test":
        return 0 if _send(args.text) else 1

    parser.print_usage(sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Memory log management for Trading Copilot.

Direct port of TradingAgents' `agents/utils/memory.py` — append-only markdown
log of trading decisions, with two-phase outcome resolution.

Format:
    [YYYY-MM-DD | TICKER | RATING | pending]
    DECISION:
    <full decision text>

    <!-- ENTRY_END -->

After T+5d resolution:
    [YYYY-MM-DD | TICKER | RATING | +X.X% | +Y.Y% | Nd]
    DECISION:
    <unchanged>
    REFLECTION:
    <2-4 sentence retrospective>

    <!-- ENTRY_END -->

CLI:
    python scripts/memory.py list-pending
    python scripts/memory.py append --ticker NVDA --date 2026-04-27 --rating Buy --decision-file path/to/decision.md
    python scripts/memory.py past-context --ticker NVDA [--n-same 5] [--n-cross 3]
    python scripts/memory.py resolve --ticker NVDA --date 2026-04-27 \\
        --raw 0.052 --alpha 0.018 --days 5 --reflection "..."
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Force UTF-8 on stdout/stderr so Chinese reflections survive Windows cp936.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "buffer"):
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

SEPARATOR = "\n\n<!-- ENTRY_END -->\n\n"
DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)


def memory_path() -> Path:
    return Path(__file__).resolve().parent.parent / "data" / "memory" / "trading_memory.md"


@dataclass(frozen=True)
class Entry:
    date: str
    ticker: str
    rating: str
    pending: bool
    raw: Optional[str]
    alpha: Optional[str]
    holding: Optional[str]
    decision: str
    reflection: str

    def format_full(self) -> str:
        if self.pending:
            tag = f"[{self.date} | {self.ticker} | {self.rating} | pending]"
        else:
            raw = self.raw or "n/a"
            alpha = self.alpha or "n/a"
            holding = self.holding or "n/a"
            tag = f"[{self.date} | {self.ticker} | {self.rating} | {raw} | {alpha} | {holding}]"
        parts = [tag, f"DECISION:\n{self.decision}"]
        if self.reflection:
            parts.append(f"REFLECTION:\n{self.reflection}")
        return "\n\n".join(parts)

    def format_reflection_only(self) -> str:
        tag = f"[{self.date} | {self.ticker} | {self.rating} | {self.raw or 'n/a'}]"
        if self.reflection:
            return f"{tag}\n{self.reflection}"
        snippet = self.decision[:300] + ("..." if len(self.decision) > 300 else "")
        return f"{tag}\n{snippet}"


def load_entries() -> list[Entry]:
    path = memory_path()
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    raw_blocks = [b.strip() for b in text.split(SEPARATOR) if b.strip()]
    entries: list[Entry] = []
    for raw in raw_blocks:
        parsed = _parse_block(raw)
        if parsed:
            entries.append(parsed)
    return entries


def _parse_block(raw: str) -> Optional[Entry]:
    lines = raw.strip().splitlines()
    # Find the first line that looks like a tag — skip leading content
    # (e.g. the file header HTML comment that precedes the first entry).
    tag_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]") and "|" in stripped:
            tag_idx = i
            break
    if tag_idx is None:
        return None
    tag_line = lines[tag_idx].strip()
    fields = [f.strip() for f in tag_line[1:-1].split("|")]
    if len(fields) < 4:
        return None
    body = "\n".join(lines[tag_idx + 1 :]).strip()
    decision_match = DECISION_RE.search(body)
    reflection_match = REFLECTION_RE.search(body)
    return Entry(
        date=fields[0],
        ticker=fields[1],
        rating=fields[2],
        pending=fields[3] == "pending",
        raw=fields[3] if fields[3] != "pending" else None,
        alpha=fields[4] if len(fields) > 4 else None,
        holding=fields[5] if len(fields) > 5 else None,
        decision=decision_match.group(1).strip() if decision_match else "",
        reflection=reflection_match.group(1).strip() if reflection_match else "",
    )


def append_pending(ticker: str, date: str, rating: str, decision: str) -> None:
    """Phase A: append a pending entry. Idempotent on (date, ticker)."""
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.startswith(f"[{date} | {ticker} |") and line.endswith("| pending]"):
                return  # already exists
    tag = f"[{date} | {ticker} | {rating} | pending]"
    block = f"{tag}\n\nDECISION:\n{decision}{SEPARATOR}"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)


def list_pending() -> list[Entry]:
    return [e for e in load_entries() if e.pending]


def past_context(ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
    """Format recent resolved entries for injection to Portfolio Manager prompt."""
    entries = [e for e in load_entries() if not e.pending]
    if not entries:
        return ""
    same: list[Entry] = []
    cross: list[Entry] = []
    for e in reversed(entries):
        if len(same) >= n_same and len(cross) >= n_cross:
            break
        if e.ticker == ticker and len(same) < n_same:
            same.append(e)
        elif e.ticker != ticker and len(cross) < n_cross:
            cross.append(e)
    if not same and not cross:
        return ""
    parts: list[str] = []
    if same:
        parts.append(f"Past analyses of {ticker} (most recent first):")
        parts.extend(e.format_full() for e in same)
    if cross:
        parts.append("Recent cross-ticker lessons:")
        parts.extend(e.format_reflection_only() for e in cross)
    return "\n\n".join(parts)


def resolve(ticker: str, date: str, raw: float, alpha: float, days: int, reflection: str) -> bool:
    """Phase B: replace the pending entry with resolved tag + reflection.

    Atomic: writes to .tmp then renames. Returns True if updated, False if no
    matching pending entry was found.
    """
    path = memory_path()
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    blocks = text.split(SEPARATOR)
    pending_prefix = f"[{date} | {ticker} |"
    raw_pct = f"{raw:+.1%}"
    alpha_pct = f"{alpha:+.1%}"
    updated = False
    new_blocks: list[str] = []
    for block in blocks:
        stripped = block.strip()
        if not stripped:
            new_blocks.append(block)
            continue
        lines = stripped.splitlines()
        # Find tag line — skip leading content (e.g. file header in first block).
        tag_idx = None
        for i, line in enumerate(lines):
            ln = line.strip()
            if ln.startswith("[") and ln.endswith("]") and "|" in ln:
                tag_idx = i
                break
        if tag_idx is None:
            new_blocks.append(block)
            continue
        tag_line = lines[tag_idx].strip()
        if (
            not updated
            and tag_line.startswith(pending_prefix)
            and tag_line.endswith("| pending]")
        ):
            fields = [f.strip() for f in tag_line[1:-1].split("|")]
            rating = fields[2]
            new_tag = f"[{date} | {ticker} | {rating} | {raw_pct} | {alpha_pct} | {days}d]"
            preamble = "\n".join(lines[:tag_idx]).rstrip()
            rest = "\n".join(lines[tag_idx + 1 :]).lstrip()
            updated_block = f"{new_tag}\n\n{rest}\n\nREFLECTION:\n{reflection}"
            new_blocks.append(
                f"{preamble}\n{updated_block}" if preamble else updated_block
            )
            updated = True
        else:
            new_blocks.append(block)
    if not updated:
        return False
    new_text = SEPARATOR.join(new_blocks)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(path)
    return True


# ---- CLI ----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Copilot memory log management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-pending", help="List all pending entries as JSON")

    sub.add_parser("list-all", help="List all entries (pending + resolved) as JSON")

    p = sub.add_parser("append", help="Append a pending entry")
    p.add_argument("--ticker", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument("--rating", required=True, help="Buy|Overweight|Hold|Underweight|Sell")
    p.add_argument("--decision-file", required=True, help="Path to decision markdown file")

    p = sub.add_parser("past-context", help="Print past-context for a ticker")
    p.add_argument("--ticker", required=True)
    p.add_argument("--n-same", type=int, default=5)
    p.add_argument("--n-cross", type=int, default=3)

    p = sub.add_parser("resolve", help="Resolve a pending entry with outcome + reflection")
    p.add_argument("--ticker", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--raw", type=float, required=True, help="Raw return as decimal, e.g. 0.052 for +5.2%")
    p.add_argument("--alpha", type=float, required=True, help="Alpha vs SPY as decimal")
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--reflection", required=True, help="Reflection text (2-4 sentences)")

    args = parser.parse_args()

    if args.cmd == "list-pending":
        entries = [e.__dict__ for e in list_pending()]
        json.dump(entries, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "list-all":
        entries = [e.__dict__ for e in load_entries()]
        json.dump(entries, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0

    if args.cmd == "append":
        decision_text = Path(args.decision_file).read_text(encoding="utf-8").strip()
        append_pending(args.ticker, args.date, args.rating, decision_text)
        print(f"appended pending: {args.ticker} {args.date} {args.rating}")
        return 0

    if args.cmd == "past-context":
        ctx = past_context(args.ticker, n_same=args.n_same, n_cross=args.n_cross)
        sys.stdout.write(ctx + ("\n" if ctx and not ctx.endswith("\n") else ""))
        return 0

    if args.cmd == "resolve":
        ok = resolve(
            args.ticker,
            args.date,
            args.raw,
            args.alpha,
            args.days,
            args.reflection,
        )
        if ok:
            print(f"resolved: {args.ticker} {args.date}")
            return 0
        print(f"no pending entry found: {args.ticker} {args.date}", file=sys.stderr)
        return 1

    return 1


if __name__ == "__main__":
    sys.exit(main())

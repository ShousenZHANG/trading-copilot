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
    BENCHMARK: ^AXJO (exchange suffix .AX)
    DECISION:
    <unchanged>
    REFLECTION:
    <2-4 sentence retrospective>

    <!-- ENTRY_END -->

The 6-field header shape is frozen — legacy entries must keep parsing. The
benchmark alpha was measured against is therefore recorded as a labelled
``BENCHMARK:`` field in the entry BODY, between the tag and ``DECISION:``.
Entries written before this field existed simply have ``benchmark=None``; they
were all resolved against SPY regardless of listing venue, which is only correct
for US names (see scripts/benchmarks.py).

CLI:
    python scripts/memory.py list-pending
    python scripts/memory.py append --ticker NVDA --date 2026-04-27 --rating Buy --decision-file path/to/decision.md
    python scripts/memory.py past-context --ticker NVDA [--n-same 5] [--n-cross 3]
    python scripts/memory.py resolve --ticker NVDA --date 2026-04-27 \\
        --raw 0.052 --alpha 0.018 --days 5 --reflection "..." [--benchmark ^AXJO]
    python scripts/memory.py resolve-pending --prices prices.json [--dry-run]

Tests can set TRADING_COPILOT_MEMORY_PATH to isolate the log from real trading
state.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from runtime import force_utf8_stdio

force_utf8_stdio()

#: Hard block delimiter. LLM output cannot accidentally produce it — but a
#: decision body could QUOTE it, which would split one entry into two and
#: silently corrupt every later parse. Everything written into an entry body
#: goes through :func:`escape_delimiter` first.
ENTRY_END_TOKEN = "<!-- ENTRY_END -->"
ENTRY_END_ESCAPED = "<!-- ENTRY_END_ESCAPED -->"
SEPARATOR = f"\n\n{ENTRY_END_TOKEN}\n\n"
DECISION_RE = re.compile(r"DECISION:\n(.*?)(?=\nREFLECTION:|\Z)", re.DOTALL)
REFLECTION_RE = re.compile(r"REFLECTION:\n(.*?)$", re.DOTALL)
BENCHMARK_RE = re.compile(r"^BENCHMARK:\s*(\S+)", re.MULTILINE)

#: Written by ``resolve-pending`` when the arithmetic is settled but no Haiku
#: reflection has been generated. Deliberately greppable.
REFLECTION_PLACEHOLDER = (
    "(反思待补 / reflection pending — 本条由 memory.py resolve-pending 以价格算术结算, "
    "尚未生成回顾文字.)"
)

# Re-exported from parse_rating.py to keep validation logic in one place.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from benchmarks import is_alpha_meaningful, resolve_benchmark  # noqa: E402
from parse_rating import RATINGS_5_TIER, explicit_rating, first_rating_word, parse_rating  # noqa: E402
from ticker import validate_date_component, validate_ticker_component  # noqa: E402
from validate_outputs import rating_field  # noqa: E402

VALID_RATINGS = set(RATINGS_5_TIER)


def escape_delimiter(text: str) -> str:
    """Neutralise a quoted ``<!-- ENTRY_END -->`` before it enters an entry body."""
    return text.replace(ENTRY_END_TOKEN, ENTRY_END_ESCAPED)


def memory_path() -> Path:
    override = os.environ.get("TRADING_COPILOT_MEMORY_PATH")
    if override:
        return Path(override)
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
    # Benchmark alpha was measured against. None on legacy entries written
    # before the field existed (those were all measured against SPY).
    benchmark: Optional[str] = None

    def format_full(self) -> str:
        if self.pending:
            tag = f"[{self.date} | {self.ticker} | {self.rating} | pending]"
        else:
            raw = self.raw or "n/a"
            alpha = self.alpha or "n/a"
            holding = self.holding or "n/a"
            tag = f"[{self.date} | {self.ticker} | {self.rating} | {raw} | {alpha} | {holding}]"
        parts = [tag]
        if self.benchmark:
            parts.append(f"BENCHMARK: {self.benchmark}")
        parts.append(f"DECISION:\n{self.decision}")
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
    """Parse every block in the log. Unparseable blocks warn, never crash.

    A block that looks like an entry (it has a ``DECISION:`` section) but has no
    readable tag is a corruption signal — most likely a decision body that
    quoted the hard delimiter before :func:`escape_delimiter` existed. Warn to
    stderr so the operator sees it instead of silently losing the entry.
    """
    path = memory_path()
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    raw_blocks = [b.strip() for b in text.split(SEPARATOR) if b.strip()]
    entries: list[Entry] = []
    for index, raw in enumerate(raw_blocks):
        parsed, reason = _parse_block(raw)
        if parsed:
            entries.append(parsed)
        elif reason:
            print(
                f"warning: {path}: block {index + 1} is not a parseable entry ({reason})",
                file=sys.stderr,
            )
    return entries


def _parse_block(raw: str) -> tuple[Optional[Entry], Optional[str]]:
    """Return ``(entry, skip_reason)``. ``reason`` is None for benign blocks."""
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
        # The file header (an HTML comment before the first entry) legitimately
        # has no tag; only an entry-shaped block is worth warning about.
        return None, "no [date | ticker | ...] tag line" if "DECISION:" in raw else None
    tag_line = lines[tag_idx].strip()
    fields = [f.strip() for f in tag_line[1:-1].split("|")]
    if len(fields) < 4:
        return None, f"tag has {len(fields)} field(s), expected at least 4: {tag_line}"
    body = "\n".join(lines[tag_idx + 1 :]).strip()
    decision_match = DECISION_RE.search(body)
    reflection_match = REFLECTION_RE.search(body)
    # The BENCHMARK field lives between the tag and DECISION:. Search only that
    # header slice so a "BENCHMARK:" line inside free-form decision prose cannot
    # be mistaken for the recorded field.
    header = body.split("DECISION:", 1)[0]
    benchmark_match = BENCHMARK_RE.search(header)
    entry = Entry(
        date=fields[0],
        ticker=fields[1],
        rating=fields[2],
        pending=fields[3] == "pending",
        raw=fields[3] if fields[3] != "pending" else None,
        alpha=fields[4] if len(fields) > 4 else None,
        holding=fields[5] if len(fields) > 5 else None,
        decision=decision_match.group(1).strip() if decision_match else "",
        reflection=reflection_match.group(1).strip() if reflection_match else "",
        benchmark=benchmark_match.group(1) if benchmark_match else None,
    )
    return entry, None


def append_pending(ticker: str, date: str, rating: str, decision: str) -> str:
    """Phase A: append a pending entry. Idempotent on (date, ticker).

    Returns one of ``"appended"``, ``"duplicate-pending"``,
    ``"duplicate-resolved"``. The duplicate check matches ANY tag for the pair,
    not just a pending one: re-running ``/analyze`` Step 7 after the entry was
    already resolved by ``/weekly-review`` used to append a second copy of the
    same decision, which then double-counts in every performance stat.
    """
    ticker = validate_ticker_component(ticker)
    date = validate_date_component(date)
    path = memory_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f"[{date} | {ticker} |"
    if path.exists():
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if line.strip().startswith(prefix):
                return (
                    "duplicate-pending"
                    if line.strip().endswith("| pending]")
                    else "duplicate-resolved"
                )
    tag = f"[{date} | {ticker} | {rating} | pending]"
    block = f"{tag}\n\nDECISION:\n{escape_delimiter(decision)}{SEPARATOR}"
    with path.open("a", encoding="utf-8") as f:
        f.write(block)
    return "appended"


def list_pending() -> list[Entry]:
    return [e for e in load_entries() if e.pending]


def past_context(ticker: str, n_same: int = 5, n_cross: int = 3) -> str:
    """Format recent resolved entries for injection to Portfolio Manager prompt."""
    ticker = validate_ticker_component(ticker)
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


def benchmark_line(ticker: str, benchmark: Optional[str] = None) -> str:
    """Render the labelled BENCHMARK field stored inside a resolved entry.

    Recorded so a future reader can tell what the alpha number actually means:
    ``+1.8%`` vs ``^AXJO`` and ``+1.8%`` vs ``SPY`` are different claims.
    """
    if benchmark:
        return f"BENCHMARK: {benchmark} (explicitly supplied)"
    choice = resolve_benchmark(ticker)
    return f"BENCHMARK: {choice.benchmark} ({choice.reason})"


def resolve(
    ticker: str,
    date: str,
    raw: float,
    alpha: Optional[float],
    days: int,
    reflection: str,
    benchmark: Optional[str] = None,
) -> bool:
    """Phase B: replace the pending entry with resolved tag + reflection.

    ``benchmark`` defaults to the region-derived benchmark for ``ticker``
    (scripts/benchmarks.py). Alpha vs SPY is only correct for US listings — an
    ASX or HKEX name measured against SPY mixes in an unhedged FX and market
    mismatch, so the benchmark actually used is written into the entry body.

    ``alpha=None`` writes ``n/a`` into the alpha slot. That is the honest value
    for commodities, FX and indices, where "alpha vs an equity index" is a
    category error. The 6-field header shape is unchanged either way, so legacy
    entries keep parsing.

    Atomic: writes to .tmp then renames. Returns True if updated, False if no
    matching pending entry was found.
    """
    ticker = validate_ticker_component(ticker)
    date = validate_date_component(date)
    bench_field = benchmark_line(ticker, benchmark)
    path = memory_path()
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    blocks = text.split(SEPARATOR)
    pending_prefix = f"[{date} | {ticker} |"
    raw_pct = f"{raw:+.1%}"
    alpha_pct = "n/a" if alpha is None else f"{alpha:+.1%}"
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
            updated_block = (
                f"{new_tag}\n\n{bench_field}\n\n{rest}\n\n"
                f"REFLECTION:\n{escape_delimiter(reflection)}"
            )
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


# ---- Deterministic T+5d resolution --------------------------------------
#
# Everything about closing a pending entry except the reflection prose is
# arithmetic over a price series. It used to require an interactive LLM session
# AND a live market MCP, so the T+5d loop stalled whenever either was missing.
# The functions below are pure: given a price map they always produce the same
# numbers, and they never touch the log. Only ``resolve()`` writes.


@dataclass(frozen=True)
class PriceBar:
    """One daily close. Mirrors ``evals/stockbench/backtest_engine.Bar``."""

    date: str
    close: float


@dataclass(frozen=True)
class ResolutionPlan:
    """The fully-computed outcome of one pending entry — not yet written."""

    ticker: str
    date: str
    rating: str
    entry_date: str
    exit_date: str
    raw: float
    alpha: Optional[float]
    benchmark: str
    days: int


def load_price_map(path: Path) -> dict[str, list[PriceBar]]:
    """Read the shared offline price-map format, sorted and validated.

    Format is byte-identical to what ``JsonPriceSource`` in
    ``evals/stockbench/backtest_engine.py`` consumes, so one price file feeds
    both the backtest and the weekly resolution::

        {"NVDA": [{"date": "2026-04-27", "close": 123.45}, ...], "SPY": [...]}
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("price map must be a JSON object keyed by ticker")
    out: dict[str, list[PriceBar]] = {}
    for ticker, rows in raw.items():
        if not isinstance(rows, list):
            raise ValueError(f"{ticker}: price series must be a list")
        bars: list[PriceBar] = []
        for row in rows:
            if not isinstance(row, dict) or "date" not in row or "close" not in row:
                raise ValueError(f"{ticker}: every bar needs 'date' and 'close', got {row!r}")
            try:
                close = float(row["close"])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{ticker}: bad close in {row!r}") from exc
            bars.append(PriceBar(date=str(row["date"]), close=close))
        bars.sort(key=lambda b: b.date)
        out[str(ticker)] = bars
    return out


def bars_as_of(bars: list[PriceBar], as_of: Optional[str]) -> list[PriceBar]:
    """Drop bars after ``as_of`` so a review can be replayed at a past date."""
    if not as_of:
        return list(bars)
    return [b for b in bars if b.date <= as_of]


def entry_index(bars: list[PriceBar], entry_date: str) -> Optional[int]:
    """Index of the first bar on or after ``entry_date`` (None if there is none).

    The decision date is not necessarily a trading day — a Sunday ``/analyze``
    run is normal — so the entry snaps forward to the next available close.
    """
    for i, bar in enumerate(bars):
        if bar.date >= entry_date:
            return i
    return None


def _window_return(
    bars: list[PriceBar], entry_date: str, holding_days: int
) -> tuple[Optional[tuple[PriceBar, PriceBar, float]], Optional[str]]:
    """Return ``((entry_bar, exit_bar, pct), None)`` or ``(None, reason)``."""
    if not bars:
        return None, "no price series in the price map"
    start = entry_index(bars, entry_date)
    if start is None:
        return None, f"no bar on or after {entry_date} (last bar {bars[-1].date})"
    exit_idx = start + holding_days
    if exit_idx >= len(bars):
        available = len(bars) - 1 - start
        return None, (
            f"needs {holding_days} bars after the entry bar {bars[start].date}, "
            f"only {available} available"
        )
    entry_bar, exit_bar = bars[start], bars[exit_idx]
    if entry_bar.close <= 0:
        return None, f"non-positive entry close {entry_bar.close} on {entry_bar.date}"
    return (entry_bar, exit_bar, exit_bar.close / entry_bar.close - 1.0), None


def _benchmark_return(
    bars: list[PriceBar], entry_date: str, exit_date: str
) -> tuple[Optional[float], Optional[str]]:
    """Benchmark return over the SAME calendar window as the position.

    The benchmark trades on its own calendar (``^AXJO`` and ``SPY`` do not share
    holidays), so the window is matched by DATE, not by bar count: first close on
    or after the entry date, last close on or before the exit date.
    """
    if not bars:
        return None, "benchmark series missing from the price map"
    start = entry_index(bars, entry_date)
    ends = [i for i, b in enumerate(bars) if b.date <= exit_date]
    if start is None or not ends or ends[-1] <= start:
        return None, (
            f"benchmark series does not cover {entry_date}..{exit_date} "
            f"(has {bars[0].date}..{bars[-1].date})"
        )
    first, last = bars[start], bars[ends[-1]]
    if first.close <= 0:
        return None, f"non-positive benchmark close {first.close} on {first.date}"
    return last.close / first.close - 1.0, None


def plan_resolution(
    entry: Entry,
    prices: dict[str, list[PriceBar]],
    holding_days: int = 5,
    as_of: Optional[str] = None,
) -> tuple[Optional[ResolutionPlan], Optional[str]]:
    """Compute one entry's outcome. Returns ``(plan, None)`` or ``(None, reason)``.

    Eligible when the ticker has at least ``holding_days`` bars after its entry
    bar. Alpha is ``None`` — written as ``n/a`` — whenever
    ``benchmarks.is_alpha_meaningful`` is False for the ticker.
    """
    bars = bars_as_of(prices.get(entry.ticker, []), as_of)
    window, reason = _window_return(bars, entry.date, holding_days)
    if window is None:
        return None, f"{entry.ticker} {entry.date}: {reason}"
    entry_bar, exit_bar, raw = window
    choice = resolve_benchmark(entry.ticker)
    if not choice.alpha_meaningful:
        return ResolutionPlan(
            ticker=entry.ticker,
            date=entry.date,
            rating=entry.rating,
            entry_date=entry_bar.date,
            exit_date=exit_bar.date,
            raw=raw,
            alpha=None,
            benchmark=choice.benchmark,
            days=holding_days,
        ), None
    bench_bars = bars_as_of(prices.get(choice.benchmark, []), as_of)
    bench_return, bench_reason = _benchmark_return(bench_bars, entry_bar.date, exit_bar.date)
    if bench_return is None:
        return None, f"{entry.ticker} {entry.date}: {choice.benchmark}: {bench_reason}"
    return ResolutionPlan(
        ticker=entry.ticker,
        date=entry.date,
        rating=entry.rating,
        entry_date=entry_bar.date,
        exit_date=exit_bar.date,
        raw=raw,
        alpha=raw - bench_return,
        benchmark=choice.benchmark,
        days=holding_days,
    ), None


def plan_pending(
    entries: list[Entry],
    prices: dict[str, list[PriceBar]],
    *,
    holding_days: int = 5,
    as_of: Optional[str] = None,
    ticker: Optional[str] = None,
    date: Optional[str] = None,
) -> tuple[list[ResolutionPlan], list[str]]:
    """Plan every eligible entry. Pure — returns ``(plans, skip_reasons)``."""
    plans: list[ResolutionPlan] = []
    skipped: list[str] = []
    for entry in entries:
        if ticker is not None and entry.ticker != ticker:
            continue
        if date is not None and entry.date != date:
            continue
        plan, reason = plan_resolution(entry, prices, holding_days, as_of)
        if plan:
            plans.append(plan)
        else:
            skipped.append(reason or f"{entry.ticker} {entry.date}: not eligible")
    return plans, skipped


def alpha_argument_error(
    ticker: str, alpha: Optional[float], force_alpha: bool = False
) -> Optional[str]:
    """Guard ``resolve --alpha`` against the two ways it can be a lie."""
    meaningful = is_alpha_meaningful(ticker)
    if not meaningful and alpha is not None and not force_alpha:
        return (
            f"{ticker} is an index/futures/FX instrument: alpha vs an equity benchmark "
            "is a category error. Omit --alpha (it writes n/a), or pass --force-alpha "
            "if you really mean a custom comparison."
        )
    if meaningful and alpha is None:
        return (
            f"{ticker} is an equity listing: --alpha is required. Compute it against "
            f"{resolve_benchmark(ticker).benchmark} (see scripts/benchmarks.py)."
        )
    return None


def format_plan_table(plans: list[ResolutionPlan]) -> str:
    """ASCII table of planned resolutions (Windows console safe)."""
    header = (
        f"{'TICKER':<10} {'DATE':<11} {'RATING':<12} {'WINDOW':<24} "
        f"{'RAW':>8} {'ALPHA':>8}  BENCHMARK"
    )
    rows = [header, "-" * len(header)]
    for p in plans:
        alpha = "n/a" if p.alpha is None else f"{p.alpha:+.1%}"
        raw = f"{p.raw:+.1%}"
        window = f"{p.entry_date}..{p.exit_date}"
        rows.append(
            f"{p.ticker:<10} {p.date:<11} {p.rating:<12} {window:<24} "
            f"{raw:>8} {alpha:>8}  {p.benchmark}"
        )
    return "\n".join(rows)


# ---- CLI ----------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description="Trading Copilot memory log management")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list-pending", help="List all pending entries as JSON")

    sub.add_parser("list-all", help="List all entries (pending + resolved) as JSON")

    p = sub.add_parser("append", help="Append a pending entry")
    p.add_argument("--ticker", required=True)
    p.add_argument("--date", required=True, help="YYYY-MM-DD")
    p.add_argument(
        "--rating",
        required=False,
        default=None,
        help="Buy|Overweight|Hold|Underweight|Sell. If omitted, parsed from --decision-file.",
    )
    p.add_argument("--decision-file", required=True, help="Path to decision markdown file")

    p = sub.add_parser("past-context", help="Print past-context for a ticker")
    p.add_argument("--ticker", required=True)
    p.add_argument("--n-same", type=int, default=5)
    p.add_argument("--n-cross", type=int, default=3)

    p = sub.add_parser("resolve", help="Resolve a pending entry with outcome + reflection")
    p.add_argument("--ticker", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--raw", type=float, required=True, help="Raw return as decimal, e.g. 0.052 for +5.2%")
    p.add_argument(
        "--alpha",
        type=float,
        default=None,
        help=(
            "Alpha vs the region benchmark as decimal (see --benchmark). Omit for "
            "commodities/FX/indices, where the tag records alpha as n/a."
        ),
    )
    p.add_argument("--days", type=int, required=True)
    p.add_argument("--reflection", required=True, help="Reflection text (2-4 sentences)")
    p.add_argument(
        "--benchmark",
        default=None,
        help=(
            "Benchmark alpha was measured against. Omit to derive from the exchange "
            "suffix via scripts/benchmarks.py (.AX -> ^AXJO, .HK -> ^HSI, ... else SPY)."
        ),
    )
    p.add_argument(
        "--force-alpha",
        action="store_true",
        help="Allow --alpha on an instrument where alpha is not meaningful.",
    )

    p = sub.add_parser(
        "resolve-pending",
        help="Deterministically resolve every eligible pending entry from a price map",
    )
    p.add_argument(
        "--prices",
        required=True,
        help=(
            'JSON price map {"TICKER": [{"date": "YYYY-MM-DD", "close": 1.23}]} — the '
            "same format evals/stockbench/backtest_engine.JsonPriceSource consumes."
        ),
    )
    p.add_argument("--as-of", default=None, help="Ignore bars after this date (YYYY-MM-DD)")
    p.add_argument("--holding-days", type=int, default=5)
    p.add_argument("--ticker", default=None, help="Restrict to one ticker")
    p.add_argument("--date", default=None, help="Restrict to one entry date")
    p.add_argument(
        "--reflection-placeholder",
        action="store_true",
        help="Required to write: stores a greppable placeholder instead of a reflection.",
    )
    p.add_argument("--dry-run", action="store_true", help="Print the table, write nothing")

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
        rating = args.rating
        try:
            declared = explicit_rating(decision_text)
        except ValueError as exc:
            print(f"error: {exc}; refusing to append an ambiguous legacy rating", file=sys.stderr)
            return 2
        if rating is not None and rating != declared:
            print(f"rating disagreement: --rating {rating} versus explicit header {declared}", file=sys.stderr)
            return 2
        if rating is None:
            # /analyze and /gold call append WITHOUT --rating, so this parser is
            # the final authority on what enters the append-only log. Cross-check
            # it against the validator's own anchored **Rating** read: if the two
            # disagree the file is ambiguous, and writing either one is a guess.
            parsed = parse_rating(decision_text)
            validated = first_rating_word(rating_field(decision_text))
            if validated != parsed:
                print(
                    f"rating disagreement in {args.decision_file}: "
                    f"parse_rating -> {parsed}, **Rating** field -> {validated}. "
                    "Refusing to guess; fix the decision file and re-run.",
                    file=sys.stderr,
                )
                return 2
            rating = parsed
        if rating not in VALID_RATINGS:
            print(
                f"invalid rating '{rating}'. Expected one of {sorted(VALID_RATINGS)}.",
                file=sys.stderr,
            )
            return 2
        try:
            status = append_pending(args.ticker, args.date, rating, decision_text)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if status == "duplicate-resolved":
            print(f"already resolved, nothing written: {args.ticker} {args.date}")
        elif status == "duplicate-pending":
            print(f"already pending, nothing written: {args.ticker} {args.date}")
        else:
            print(f"appended pending: {args.ticker} {args.date} {rating}")
        return 0

    if args.cmd == "past-context":
        ctx = past_context(args.ticker, n_same=args.n_same, n_cross=args.n_cross)
        sys.stdout.write(ctx + ("\n" if ctx and not ctx.endswith("\n") else ""))
        return 0

    if args.cmd == "resolve":
        problem = alpha_argument_error(args.ticker, args.alpha, args.force_alpha)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return 2
        try:
            ok = resolve(
                args.ticker,
                args.date,
                args.raw,
                args.alpha,
                args.days,
                args.reflection,
                benchmark=args.benchmark,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if ok:
            choice = resolve_benchmark(args.ticker)
            used = args.benchmark or choice.benchmark
            note = "" if choice.alpha_meaningful else " [alpha n/a - raw return is the number]"
            print(f"resolved: {args.ticker} {args.date} (benchmark {used}){note}")
            return 0
        print(f"no pending entry found: {args.ticker} {args.date}", file=sys.stderr)
        return 1

    if args.cmd == "resolve-pending":
        return _cmd_resolve_pending(args)

    return 1


def _cmd_resolve_pending(args: argparse.Namespace) -> int:
    """Deterministic T+5d loop: price arithmetic here, prose stays with the LLM."""
    try:
        prices = load_price_map(Path(args.prices))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"error: cannot read price map {args.prices}: {exc}", file=sys.stderr)
        return 2
    if args.as_of:
        try:
            validate_date_component(args.as_of)
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    plans, skipped = plan_pending(
        list_pending(),
        prices,
        holding_days=args.holding_days,
        as_of=args.as_of,
        ticker=args.ticker,
        date=args.date,
    )

    if plans:
        print(format_plan_table(plans))
    else:
        print("no eligible pending entries.")
    for reason in skipped:
        print(f"skipped: {reason}", file=sys.stderr)

    if args.dry_run:
        print("\n-- dry run: nothing written. Reflection commands to run next --")
        for p in plans:
            alpha = "" if p.alpha is None else f" --alpha {p.alpha:.4f}"
            print(
                f'python scripts/memory.py resolve --ticker {p.ticker} --date {p.date} '
                f'--raw {p.raw:.4f}{alpha} --days {p.days} --reflection "<Haiku reflection>"'
            )
        return 0

    if not args.reflection_placeholder:
        print(
            "error: refusing to write without a reflection. Either re-run with --dry-run "
            "and feed each printed 'resolve' command a real reflection, or pass "
            "--reflection-placeholder to store a greppable stub.",
            file=sys.stderr,
        )
        return 2

    written = 0
    for p in plans:
        if resolve(p.ticker, p.date, p.raw, p.alpha, p.days, REFLECTION_PLACEHOLDER):
            written += 1
        else:
            print(f"warning: no pending entry to update: {p.ticker} {p.date}", file=sys.stderr)
    print(f"\nresolved {written}/{len(plans)} entr(y|ies) with a placeholder reflection.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

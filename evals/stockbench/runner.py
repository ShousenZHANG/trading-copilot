#!/usr/bin/env python3
"""StockBench-subset evaluator — zero-LLM-cost replay backtester.

The old version of this file was a print-only scaffold. It now actually runs a
backtest, because the decisions have ALREADY been paid for: every
``data/runs/<TICKER>-<DATE>/08-portfolio-decision.md`` on disk is a rating the
pipeline emitted at a known point in time. Replaying those against historical
prices costs nothing and is the only falsifiable measurement this repo has.

Modes
-----
``--replay``            scan data/runs/ for portfolio decisions -> Signals
``--signals <jsonl>``   read pre-collected signals
(neither)               live-dispatch path: refuses to spend without
                        ``--yes-i-accept-cost``

Price sources
-------------
``--prices <json>``  offline JsonPriceSource (DEFAULT, deterministic)
``--yfinance``       lazily-imported online source

Artifacts (qlib SignalRecord / SigAnaRecord separation: predictions and scores
are distinct artifacts) are written to ``evals/results/<run-name>/``:
    predictions.jsonl   one record per signal
    metrics.json        config + metrics + trades + skipped + equity curve

Nothing is ever written into ``data/`` — that directory is personal trading
state and is read-only here.

Exit codes: 0 ok | 2 input error or refused spend | 3 not implemented.

Usage
-----
    python evals/stockbench/runner.py --replay --prices evals/prices/prices.json
    python evals/stockbench/runner.py --replay --yfinance --tickers NVDA,AAPL
    python evals/stockbench/runner.py --signals evals/signals.jsonl --prices p.json
    python evals/stockbench/runner.py --self-test
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from dataclasses import asdict, dataclass, replace
from datetime import date, timedelta
from pathlib import Path

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(_HERE))

from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

from parse_rating import parse_rating  # noqa: E402
from ticker import validate_ticker_component  # noqa: E402

from backtest_engine import (  # noqa: E402
    DEFAULT_MIN_TRADES,
    BacktestResult,
    JsonPriceSource,
    PriceSource,
    Signal,
    YFinancePriceSource,
    conviction_for_rating,
    format_metrics,
    run_backtest,
)

RUNS_DIR = ROOT / "data" / "runs"
RESULTS_DIR = ROOT / "evals" / "results"
DECISION_FILE = "08-portfolio-decision.md"
COST_PER_PIPELINE_RUN_USD = 0.50

_RUN_DIR_RE = re.compile(r"^(?P<ticker>.+)-(?P<date>\d{4}-\d{2}-\d{2})$")

# The `.+` above is greedy and `scripts/ticker.py` legitimately permits `-` (for
# share classes such as BRK-B), so the run-directory name alone cannot tell a
# ticker from a session label: `CPI-NIGHT-2026-06-10` parsed as ticker
# "CPI-NIGHT" and got backtested as if it were a tradeable symbol. This second
# pattern is the actual shape of an exchange symbol.
_SYMBOL_RE = re.compile(
    r"^\^?"                # optional index prefix:  ^AXJO, ^GSPC
    r"[A-Z0-9]{1,6}"       # root:                   NVDA, 0700, XAUUSD
    r"(?:-[A-Z]{1,2})?"    # share class:            BRK-B, RDS-A
    r"(?:\.[A-Z]{1,3})?"   # exchange suffix:        .AX .HK .SS .T
    r"(?:=[A-Z])?$"        # futures / FX:           GC=F, XAUUSD=X
)

# Words that satisfy _SYMBOL_RE by accident but are session labels in this repo's
# own run directories. A regex cannot separate "WEEKLY" from a real 6-letter
# ticker, so the distinction is a deliberate, visible list rather than a silent
# heuristic. GOLD is here because `/gold` writes `GOLD-<date>` run folders; it
# collides with Barrick Gold's NYSE ticker, so if you ever analyse Barrick pass
# `--allow-ticker GOLD` to override.
SESSION_LABELS = frozenset({
    "WEEKLY", "DAILY", "MONTHLY", "MULTI", "SCAN", "REVIEW", "FORECAST",
    "GOLD", "MACRO", "NIGHT", "WEEK", "ETF", "TEST", "DEMO",
})


@dataclass(frozen=True)
class RunDirName:
    """Classification of one ``data/runs/`` directory name.

    Either ``ticker``/``date`` are set, or ``reason`` explains the rejection.
    A rejection is always reported to the caller — never silently dropped.
    """

    name: str
    ticker: str | None = None
    run_date: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return self.ticker is not None


def parse_run_dir_name(name: str, allow: frozenset[str] = frozenset()) -> RunDirName:
    """Classify a run-directory name as a real ticker run or a session label.

    Pure: no filesystem access. ``allow`` force-accepts symbols that would
    otherwise be caught by ``SESSION_LABELS``.
    """
    match = _RUN_DIR_RE.match(name)
    if not match:
        return RunDirName(name, reason="not a <TICKER>-<YYYY-MM-DD> run directory")
    candidate = match.group("ticker")
    upper = candidate.upper()
    if upper not in allow:
        if not _SYMBOL_RE.match(upper):
            return RunDirName(name, reason=f"{candidate!r} is not an exchange symbol "
                                           "(looks like a session label, not a ticker)")
        if upper in SESSION_LABELS:
            return RunDirName(name, reason=f"{candidate!r} is a reserved session label; "
                                           f"pass --allow-ticker {candidate} if it is a real symbol")
    try:
        ticker = validate_ticker_component(candidate)
    except ValueError as exc:
        return RunDirName(name, reason=str(exc))
    return RunDirName(name, ticker=ticker, run_date=match.group("date"))


# ---------------------------------------------------------------------------
# Signal collection
# ---------------------------------------------------------------------------
def parse_window(spec: str) -> tuple[str, str]:
    """Parse ``YYYY-MM-DD:YYYY-MM-DD`` into (start, end). Raises if malformed."""
    try:
        start, end = spec.split(":")
        date.fromisoformat(start), date.fromisoformat(end)
    except ValueError as exc:
        raise ValueError(f"bad --window {spec!r}; expected YYYY-MM-DD:YYYY-MM-DD") from exc
    return start, end


def trading_days(start: str, end: str) -> list[str]:
    """Weekday enumeration (ignores holidays). Only used for cost estimates."""
    days: list[str] = []
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    while current <= last:
        if current.weekday() < 5:
            days.append(current.isoformat())
        current += timedelta(days=1)
    return days


def collect_replay_signals(runs_dir: Path = RUNS_DIR,
                           allow: frozenset[str] = frozenset()) -> tuple[list[Signal], list[str]]:
    """Turn every stored Portfolio Manager decision into a Signal.

    Zero token spend: the ratings were produced by past pipeline runs. Directory
    names go through ``parse_run_dir_name`` (real symbols only) and then
    ``scripts/ticker.py``. Every rejected directory lands in ``notes`` — a
    silently dropped run and a silently *accepted* session label are both ways
    to get a wrong number out of this.
    """
    signals: list[Signal] = []
    notes: list[str] = []
    if not runs_dir.exists():
        return signals, [f"no runs directory at {runs_dir}"]
    for entry in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        parsed = parse_run_dir_name(entry.name, allow)
        if not parsed.ok:
            notes.append(f"{entry.name}: SKIPPED — {parsed.reason}")
            continue
        decision = entry / DECISION_FILE
        if not decision.exists():
            notes.append(f"{entry.name}: SKIPPED — no {DECISION_FILE}")
            continue
        rating = parse_rating(decision.read_text(encoding="utf-8"))
        signals.append(Signal(ticker=str(parsed.ticker), date=str(parsed.run_date),
                              conviction=conviction_for_rating(rating), source=rating))
    return signals, notes


def apply_direction(signals: list[Signal], direction: str) -> tuple[list[Signal], list[str]]:
    """Drop short signals under ``long-only`` (the default).

    The maintainer holds ETFs in a cash account and would never open the short
    that an Underweight/Sell conviction models. Backtesting those shorts scores
    a strategy nobody would run. Dropped signals are returned as skip lines, not
    discarded, so ``trades + skipped`` still accounts for every prediction.
    """
    if direction == "both":
        return signals, []
    kept: list[Signal] = []
    dropped: list[str] = []
    for sig in signals:
        if sig.conviction < 0:
            dropped.append(f"{sig.ticker} {sig.date}: long-only mode - short signal "
                           f"({sig.source}, conviction={sig.conviction:+.2f}) not traded")
            continue
        kept.append(sig)
    return kept, dropped


def load_signal_file(path: Path) -> list[Signal]:
    """Read a JSONL signal file: {"ticker","date","rating"|"conviction"} per line."""
    signals: list[Signal] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        record = json.loads(line)
        try:
            ticker = validate_ticker_component(str(record["ticker"]))
            signal_date = date.fromisoformat(str(record["date"])).isoformat()
        except (KeyError, ValueError) as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from exc
        if "conviction" in record:
            conviction = float(record["conviction"])
            source = str(record.get("rating") or "conviction")
        else:
            source = str(record["rating"])
            conviction = conviction_for_rating(source)
        signals.append(Signal(ticker, signal_date, conviction, source))
    return signals


def filter_signals(signals: list[Signal], tickers: list[str],
                   window: tuple[str, str] | None) -> list[Signal]:
    """Apply --tickers / --window filters. Both are optional."""
    wanted = {t.upper() for t in tickers}
    out = []
    for sig in signals:
        if wanted and sig.ticker.upper() not in wanted:
            continue
        if window and not (window[0] <= sig.date <= window[1]):
            continue
        out.append(sig)
    return out


# ---------------------------------------------------------------------------
# Artifacts
# ---------------------------------------------------------------------------
def build_price_source(args: argparse.Namespace) -> PriceSource:
    if args.prices:
        return JsonPriceSource(path=Path(args.prices))
    return YFinancePriceSource()


def write_artifacts(out_dir: Path, signals: list[Signal], result: BacktestResult,
                    config: dict[str, object]) -> None:
    """Write predictions.jsonl + metrics.json (separate qlib-style artifacts)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    predictions = "\n".join(
        json.dumps({"ticker": s.ticker, "date": s.date, "rating": s.source,
                    "conviction": s.conviction}, ensure_ascii=False)
        for s in sorted(signals, key=lambda s: (s.date, s.ticker))
    )
    (out_dir / "predictions.jsonl").write_text(
        predictions + ("\n" if predictions else ""), encoding="utf-8")
    payload = {
        "config": config,
        "metrics": result.metrics,
        "trades": [asdict(t) for t in result.trades],
        "equity_curve": result.equity_curve,
        "skipped": result.skipped,
    }
    (out_dir / "metrics.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def run_live_dispatch(args: argparse.Namespace) -> int:
    """Cost gate for the (unimplemented) live pipeline-dispatch path."""
    if not args.window or not args.tickers:
        print("live dispatch needs --window and --tickers "
              "(or use --replay / --signals for the free path)", file=sys.stderr)
        return 2
    start, end = parse_window(args.window)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    runs = len(trading_days(start, end)) * len(tickers)
    estimate = runs * COST_PER_PIPELINE_RUN_USD
    print(f"Live dispatch would run the full pipeline {runs} times "
          f"({len(tickers)} ticker(s) x {runs // max(len(tickers), 1)} trading days).")
    print(f"Honest cost estimate @ ${COST_PER_PIPELINE_RUN_USD:.2f}/run: ${estimate:,.2f}")
    print("Free alternative: --replay backtests decisions you already paid for.")
    if not args.yes_i_accept_cost:
        print("\nRefusing to spend. Re-run with --yes-i-accept-cost to proceed.",
              file=sys.stderr)
        return 2
    print("\nNOT IMPLEMENTED: wire a Claude Code SDK / `claude -p` adapter here, "
          "write each decision to data/runs/, then re-run with --replay.",
          file=sys.stderr)
    return 3


# ---------------------------------------------------------------------------
# Offline self-test. Builds its own fixture tree — never reads data/.
# ---------------------------------------------------------------------------
_FIXTURE_DIRS: list[tuple[str, str | None]] = [
    # (directory name, decision-file rating or None for "no decision file")
    ("NVDA-2026-04-27", "Buy"),            # plain US ticker
    ("NDQ.AX-2026-06-17", "Overweight"),   # exchange-suffixed ticker
    ("GC=F-2026-05-18", "Sell"),           # futures ticker
    ("BRK-B-2026-05-04", "Hold"),          # share-class hyphen must survive
    ("^AXJO-2026-05-05", "Buy"),           # index prefix
    ("CPI-NIGHT-2026-06-10", "Buy"),       # session label, multi-word
    ("WEEKLY-2026-06-07", "Buy"),          # session label, single word
    ("GOLD-2026-05-26", "Buy"),            # ambiguous label (Barrick vs /gold)
    ("NVDA-2026-05-01", None),             # valid ticker, no decision file
    ("CON-2026-05-02", "Buy"),             # Windows reserved device name
    ("not-a-run-directory", None),         # no trailing date
]


def _make_fixture_tree(root: Path) -> None:
    """Write the fixture run tree. Decision text mirrors the real PM contract."""
    for name, rating in _FIXTURE_DIRS:
        folder = root / name
        folder.mkdir(parents=True, exist_ok=True)
        if rating is not None:
            (folder / DECISION_FILE).write_text(
                f"**Rating**: {rating}\n\n**Executive Summary**: fixture.\n",
                encoding="utf-8")


def _self_test() -> int:  # noqa: C901 - a flat list of independent assertions
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    # --- pure name classification -----------------------------------------
    accepted = ["NVDA", "NDQ.AX", "GC=F", "0700.HK", "XAUUSD=X", "^AXJO", "BRK-B", "IOO.AX"]
    for symbol in accepted:
        parsed = parse_run_dir_name(f"{symbol}-2026-06-17")
        check(f"accepts real symbol {symbol}",
              parsed.ok and parsed.ticker == symbol and parsed.run_date == "2026-06-17",
              parsed.reason or "")

    rejected = ["CPI-NIGHT", "CPI-FORECAST", "ETF-PORTFOLIO", "GOLD-REVIEW", "FOMC-WEEK",
                "ETF-NEXTWEEK", "GOLD-DIRECTION", "PORTFOLIO", "FORECAST", "WEEKLY",
                "MULTI", "GOLD", "IOO.AX+GC=F"]
    for label in rejected:
        parsed = parse_run_dir_name(f"{label}-2026-06-17")
        check(f"rejects session label {label}", not parsed.ok and bool(parsed.reason),
              f"ticker={parsed.ticker}")

    parsed = parse_run_dir_name("GOLD-2026-05-26", frozenset({"GOLD"}))
    check("--allow-ticker overrides the session-label list",
          parsed.ok and parsed.ticker == "GOLD", parsed.reason or "")
    check("a directory with no trailing date is rejected",
          not parse_run_dir_name("not-a-run-directory").ok)

    # --- fixture-tree partition (no data/ access) --------------------------
    tmp = Path(tempfile.mkdtemp(prefix="stockbench-selftest-"))
    try:
        runs = tmp / "runs"
        _make_fixture_tree(runs)
        signals, notes = collect_replay_signals(runs)
        tickers = sorted(s.ticker for s in signals)
        check("fixture partition: only real tickers become signals",
              tickers == ["BRK-B", "GC=F", "NDQ.AX", "NVDA", "^AXJO"], str(tickers))
        check("fixture partition: every rejected directory is reported",
              len(notes) == len(_FIXTURE_DIRS) - len(tickers), str(notes))
        check("session-named runs never become tickers",
              not any(s.ticker in ("CPI-NIGHT", "WEEKLY", "GOLD") for s in signals), str(tickers))
        check("a run with no decision file is reported, not dropped",
              any("no " + DECISION_FILE in n and n.startswith("NVDA-2026-05-01") for n in notes),
              str(notes))
        check("Windows reserved device name is rejected",
              any(n.startswith("CON-2026-05-02") for n in notes), str(notes))
        check("ratings survive as the signal source",
              {s.ticker: s.source for s in signals}["NVDA"] == "Buy",
              str([(s.ticker, s.source) for s in signals]))

        allowed, _ = collect_replay_signals(runs, frozenset({"GOLD"}))
        check("--allow-ticker admits the allowed symbol only",
              sorted(s.ticker for s in allowed) == ["BRK-B", "GC=F", "GOLD", "NDQ.AX",
                                                    "NVDA", "^AXJO"],
              str(sorted(s.ticker for s in allowed)))

        empty, notes = collect_replay_signals(tmp / "does-not-exist")
        check("a missing runs dir is a note, not a crash",
              empty == [] and len(notes) == 1 and "no runs directory" in notes[0], str(notes))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    # --- direction filter ---------------------------------------------------
    stream = [Signal("A", "2026-06-01", 1.0, "Buy"), Signal("B", "2026-06-01", -1.0, "Sell"),
              Signal("C", "2026-06-01", 0.0, "Hold"), Signal("D", "2026-06-01", -0.5,
                                                             "Underweight")]
    kept, dropped = apply_direction(stream, "long-only")
    check("long-only drops shorts and records them",
          [s.ticker for s in kept] == ["A", "C"] and len(dropped) == 2, f"{kept} {dropped}")
    check("long-only skip lines name the rating",
          all("long-only mode" in d for d in dropped), str(dropped))
    kept, dropped = apply_direction(stream, "both")
    check("direction=both keeps every signal", len(kept) == 4 and dropped == [])

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        flag = "ok " if ok else "XX "
        suffix = f"  [{detail}]" if (detail and not ok) else ""
        print(f"  {flag} {name}{suffix}")
    print(f"\n{passed}/{len(results)} stockbench runner unit tests passed.")
    return 0 if passed == len(results) else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Backtest Trading Copilot decisions (replay = zero LLM cost)")
    ap.add_argument("--replay", action="store_true",
                    help="backtest stored data/runs/*/08-portfolio-decision.md")
    ap.add_argument("--signals", help="JSONL of pre-collected signals")
    ap.add_argument("--prices", help="JSON price map -> offline JsonPriceSource (default)")
    ap.add_argument("--yfinance", action="store_true",
                    help="use the lazily-imported yfinance price source")
    ap.add_argument("--tickers", help="comma-separated ticker filter")
    ap.add_argument("--window", help="YYYY-MM-DD:YYYY-MM-DD date filter")
    ap.add_argument("--holding-days", type=int, default=5, help="bars held per trade")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="minimum |conviction| to open a position")
    ap.add_argument("--snap-forward", action="store_true",
                    help="enter on the next bar when the signal date is not a trading day")
    ap.add_argument("--direction", choices=("long-only", "both"), default="long-only",
                    help="long-only (default) ignores Underweight/Sell shorts; "
                         "both also models the short side")
    ap.add_argument("--min-trades", type=int, default=DEFAULT_MIN_TRADES,
                    help=f"below this trade count the inferential metrics are withheld "
                         f"(default {DEFAULT_MIN_TRADES})")
    ap.add_argument("--runs-dir", default=str(RUNS_DIR),
                    help=f"directory of <TICKER>-<DATE> run folders (default {RUNS_DIR})")
    ap.add_argument("--out-dir", default=str(RESULTS_DIR),
                    help=f"artifact root; --run-name is a folder under it (default {RESULTS_DIR})")
    ap.add_argument("--allow-ticker",
                    help="comma-separated symbols to force past the session-label list")
    ap.add_argument("--run-name", help="output folder under --out-dir")
    ap.add_argument("--yes-i-accept-cost", action="store_true",
                    help="required to attempt the paid live-dispatch path")
    ap.add_argument("--self-test", action="store_true",
                    help="run built-in offline unit tests (never touches data/)")
    return ap


def _gather(args: argparse.Namespace) -> tuple[list[Signal], list[str]]:
    """Collect signals. NOTE: ``--runs-dir`` used to be accepted and ignored here."""
    if args.replay:
        allow = frozenset(t.strip().upper()
                          for t in (args.allow_ticker or "").split(",") if t.strip())
        return collect_replay_signals(Path(args.runs_dir), allow)
    return load_signal_file(Path(args.signals)), []


def main() -> int:  # noqa: C901 - linear CLI flow, one branch per failure mode
    args = build_parser().parse_args()
    if args.self_test:
        return _self_test()
    if not args.replay and not args.signals:
        return run_live_dispatch(args)
    if not args.prices and not args.yfinance:
        print("need a price source: --prices <path.json> (offline) or --yfinance",
              file=sys.stderr)
        return 2

    try:
        window = parse_window(args.window) if args.window else None
        signals, notes = _gather(args)
        price_source = build_price_source(args)
    except (ValueError, OSError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    for note in notes:
        print(f"note: {note}", file=sys.stderr)
    signals = filter_signals(signals, [t.strip() for t in (args.tickers or "").split(",")
                                       if t.strip()], window)
    if not signals:
        print("no signals to backtest (replay needs completed runs in --runs-dir)",
              file=sys.stderr)
        return 2

    tradeable, direction_skips = apply_direction(signals, args.direction)
    result = run_backtest(tradeable, price_source, holding_days=args.holding_days,
                          threshold=args.threshold, snap_forward=args.snap_forward,
                          min_trades=args.min_trades)
    # Keep every prediction accounted for: direction-filtered signals are skips,
    # not disappearances. Immutable — a new result, no mutation of the engine's.
    result = replace(result, skipped=direction_skips + result.skipped)
    mode = "replay" if args.replay else "signals"
    config = {
        "mode": mode,
        "holding_days": args.holding_days,
        "threshold": args.threshold,
        "snap_forward": args.snap_forward,
        "direction": args.direction,
        "min_trades": args.min_trades,
        "price_source": "json" if args.prices else "yfinance",
        "prices_path": args.prices,
        "signal_count": len(signals),
        "tradeable_signal_count": len(tradeable),
        "runs_dir": str(Path(args.runs_dir)) if args.replay else None,
        "window": args.window,
        "tickers": args.tickers,
    }
    out_dir = Path(args.out_dir) / (args.run_name or f"backtest-{mode}")
    write_artifacts(out_dir, signals, result, config)

    print(f"Signals: {len(signals)}  |  Trades: {len(result.trades)}  "
          f"|  Skipped: {len(result.skipped)}")
    print(format_metrics(result.metrics))
    for reason in result.skipped[:10]:
        print(f"  skip: {reason}")
    if len(result.skipped) > 10:
        print(f"  ... {len(result.skipped) - 10} more skips in metrics.json")
    print(f"\nArtifacts: {out_dir / 'predictions.jsonl'}")
    print(f"           {out_dir / 'metrics.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    python evals/stockbench/runner.py --replay --prices evals/prices.json
    python evals/stockbench/runner.py --replay --yfinance --tickers NVDA,AAPL
    python evals/stockbench/runner.py --signals evals/signals.jsonl --prices p.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict
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


def collect_replay_signals(runs_dir: Path = RUNS_DIR) -> tuple[list[Signal], list[str]]:
    """Turn every stored Portfolio Manager decision into a Signal.

    Zero token spend: the ratings were produced by past pipeline runs. Directory
    names are validated through ``scripts/ticker.py`` before use.
    """
    signals: list[Signal] = []
    notes: list[str] = []
    if not runs_dir.exists():
        return signals, [f"no runs directory at {runs_dir}"]
    for entry in sorted(p for p in runs_dir.iterdir() if p.is_dir()):
        match = _RUN_DIR_RE.match(entry.name)
        if not match:
            notes.append(f"{entry.name}: not a <TICKER>-<YYYY-MM-DD> run directory")
            continue
        decision = entry / DECISION_FILE
        if not decision.exists():
            notes.append(f"{entry.name}: no {DECISION_FILE}")
            continue
        try:
            ticker = validate_ticker_component(match.group("ticker"))
        except ValueError as exc:
            notes.append(f"{entry.name}: {exc}")
            continue
        rating = parse_rating(decision.read_text(encoding="utf-8"))
        signals.append(Signal(ticker=ticker, date=match.group("date"),
                              conviction=conviction_for_rating(rating), source=rating))
    return signals, notes


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
    ap.add_argument("--run-name", help="output folder under evals/results/")
    ap.add_argument("--yes-i-accept-cost", action="store_true",
                    help="required to attempt the paid live-dispatch path")
    return ap


def _gather(args: argparse.Namespace) -> tuple[list[Signal], list[str]]:
    if args.replay:
        return collect_replay_signals()
    return load_signal_file(Path(args.signals)), []


def main() -> int:
    args = build_parser().parse_args()
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
        print("no signals to backtest (replay needs completed runs in data/runs/)",
              file=sys.stderr)
        return 2

    result = run_backtest(signals, price_source, holding_days=args.holding_days,
                          threshold=args.threshold, snap_forward=args.snap_forward)
    mode = "replay" if args.replay else "signals"
    config = {
        "mode": mode,
        "holding_days": args.holding_days,
        "threshold": args.threshold,
        "snap_forward": args.snap_forward,
        "price_source": "json" if args.prices else "yfinance",
        "signal_count": len(signals),
        "window": args.window,
        "tickers": args.tickers,
    }
    out_dir = RESULTS_DIR / (args.run_name or f"backtest-{mode}")
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

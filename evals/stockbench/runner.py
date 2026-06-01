#!/usr/bin/env python3
"""StockBench-subset evaluator.

Replays the full Trading Copilot pipeline on a historical window, then computes
risk-adjusted performance metrics vs buy-and-hold SPY.

This is a SCAFFOLD. The headless invocation of slash commands needs the Claude
Code SDK; this script lays out the protocol.

Protocol (StockBench-aligned):
1. Pick a window (rolling 3-4 months) from windows.json
2. For each trading day in the window:
   - Run /analyze TICKER for each ticker in the universe
   - Record the rating + price target + stop loss
   - Hold the position 5 trading days, then exit
3. Compute Sharpe, Sortino, max-DD, Calmar, hit rate
4. Compare vs equal-weight SPY buy-and-hold over the same window

Usage:
    python evals/stockbench/runner.py --window=2024-06-01:2024-09-30 \\
        --tickers=NVDA,AAPL,MSFT,GOOGL --out=results/stockbench-q3-2024.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except (AttributeError, ValueError):
    pass

ROOT = Path(__file__).resolve().parent.parent.parent
WINDOWS_FILE = Path(__file__).resolve().parent / "windows.json"
RESULTS_DIR = ROOT / "evals" / "results"


def parse_window(spec: str) -> tuple[datetime, datetime]:
    start, end = spec.split(":")
    return (datetime.strptime(start, "%Y-%m-%d"),
            datetime.strptime(end, "%Y-%m-%d"))


def trading_days(start: datetime, end: datetime) -> list[datetime]:
    """Approximate trading-day enumeration (skips weekends, ignores US holidays)."""
    days = []
    d = start
    while d <= end:
        if d.weekday() < 5:  # Mon-Fri
            days.append(d)
        d += timedelta(days=1)
    return days


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--window", required=True,
                        help="YYYY-MM-DD:YYYY-MM-DD")
    parser.add_argument("--tickers", required=True,
                        help="Comma-separated tickers")
    parser.add_argument("--out", required=True,
                        help="Output JSON path")
    args = parser.parse_args()

    start, end = parse_window(args.window)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    days = trading_days(start, end)

    print(f"Window: {args.window} ({len(days)} trading days)")
    print(f"Tickers: {tickers}")
    print()
    print("This scaffold does not yet invoke the pipeline. Implement:")
    print()
    print("  for day in days:")
    print("    for ticker in tickers:")
    print("      decision = run_pipeline(ticker, day)        # via Claude Code SDK")
    print("      outcome  = fetch_actual_returns(ticker, day, hold_days=5)")
    print("      record(ticker, day, decision, outcome)")
    print()
    print("  metrics = compute_metrics(records)              # Sharpe / Sortino / DD")
    print("  baseline = compute_metrics(spy_buy_and_hold)")
    print("  write_report(metrics, baseline)")
    print()
    print("Required external pieces:")
    print("  - Claude Code SDK Python bindings (or `claude -p` subprocess wrapper)")
    print("  - yfinance to backfill actual returns")
    print("  - benchmarks.py (Sharpe / Sortino / max-DD / Calmar implementations)")
    print()
    print("Cost guardrail: a 60-day window x 4 tickers = 240 pipeline runs.")
    print(f"Estimated cost @ $0.50/run = ${240 * 0.50:.2f}")
    print("Confirm before running.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

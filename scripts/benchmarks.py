#!/usr/bin/env python3
"""Region-aware benchmark mapping for alpha computation.

WHY THIS EXISTS
---------------
The memory log resolved every T+5d entry against SPY. For a US listing that is
correct. For ``BHP.AX`` it is not alpha at all — it is alpha plus an unhedged
AUD/USD move plus an ASX-vs-S&P-500 market mismatch. Same story for ``0700.HK``,
``7203.T`` and ``VOD.L``. Those contaminated entries are the replay input for the
backtest, so a wrong benchmark does not just mislabel one review, it pollutes the
eval.

The mapping below follows the regional benchmark convention used upstream in
TauricResearch/TradingAgents (Apache-2.0): resolve the exchange suffix to the
local broad-market index.

NON-EQUITY INSTRUMENTS
----------------------
Futures (``GC=F``), FX (``XAUUSD=X``) and indices (``^GSPC``) have no natural
equity benchmark. "Gold's alpha vs the S&P 500" is a category error: they are
different asset classes, not a skill comparison. ``benchmark_for`` still returns
``DEFAULT_BENCHMARK`` for them so callers keep a stable return shape, but
``is_alpha_meaningful`` returns False — for these instruments the RAW return is
the number that means something and alpha should be reported as n/a.

DETERMINISM
-----------
Pure string mapping. No clock, no network, no randomness.

CLI
---
    python scripts/benchmarks.py --ticker NDQ.AX
    python scripts/benchmarks.py --self-test
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

#: Benchmark used when no regional mapping applies (US listings, fallbacks).
DEFAULT_BENCHMARK = "SPY"

#: Yahoo-Finance exchange suffix -> local broad-market benchmark ticker.
BENCHMARK_BY_SUFFIX: dict[str, str] = {
    ".AX": "^AXJO",      # Australia — S&P/ASX 200
    ".HK": "^HSI",       # Hong Kong — Hang Seng
    ".T": "^N225",       # Japan — Nikkei 225
    ".L": "^FTSE",       # United Kingdom — FTSE 100
    ".TO": "^GSPTSE",    # Canada — S&P/TSX Composite
    ".NS": "^NSEI",      # India (NSE) — Nifty 50
    ".BO": "^BSESN",     # India (BSE) — Sensex
    ".SS": "000001.SS",  # China (Shanghai) — SSE Composite
    ".SZ": "399001.SZ",  # China (Shenzhen) — SZSE Component
    ".SI": "^STI",       # Singapore — Straits Times
    ".KS": "^KS11",      # South Korea — KOSPI
    ".NZ": "^NZ50",      # New Zealand — S&P/NZX 50
    ".DE": "^GDAXI",     # Germany — DAX
    ".PA": "^FCHI",      # France — CAC 40
    ".SW": "^SSMI",      # Switzerland — SMI
}

_NON_EQUITY_SUFFIXES = ("=F", "=X")
_INDEX_PREFIX = "^"

# Longest suffix first: ".TO" must win before a shorter overlapping key.
_SUFFIXES_LONGEST_FIRST: tuple[str, ...] = tuple(
    sorted(BENCHMARK_BY_SUFFIX, key=len, reverse=True)
)


@dataclass(frozen=True)
class BenchmarkChoice:
    """Resolved benchmark plus the audit trail for why it was chosen."""

    ticker: str
    benchmark: str
    alpha_meaningful: bool
    reason: str


def _normalize(ticker: str) -> str:
    """Upper-case and trim; return '' for anything that is not a real string."""
    if not isinstance(ticker, str):
        return ""
    return ticker.strip().upper()


def is_alpha_meaningful(ticker: str) -> bool:
    """False for indices, futures and FX pairs — raw return is the honest number."""
    normalized = _normalize(ticker)
    if not normalized:
        return False
    if normalized.startswith(_INDEX_PREFIX):
        return False
    return not normalized.endswith(_NON_EQUITY_SUFFIXES)


def resolve_benchmark(ticker: str) -> BenchmarkChoice:
    """Map ``ticker`` to its benchmark, case-insensitively, longest suffix wins."""
    normalized = _normalize(ticker)
    if not normalized:
        return BenchmarkChoice("", DEFAULT_BENCHMARK, False, "empty ticker; defaulted")
    if not is_alpha_meaningful(normalized):
        return BenchmarkChoice(
            normalized,
            DEFAULT_BENCHMARK,
            False,
            "index/futures/FX has no equity benchmark; use raw return, alpha is n/a",
        )
    for suffix in _SUFFIXES_LONGEST_FIRST:
        if normalized.endswith(suffix):
            return BenchmarkChoice(
                normalized, BENCHMARK_BY_SUFFIX[suffix], True, f"exchange suffix {suffix}"
            )
    if "." in normalized:
        return BenchmarkChoice(
            normalized,
            DEFAULT_BENCHMARK,
            True,
            "unmapped exchange suffix; alpha vs SPY carries FX/market mismatch",
        )
    return BenchmarkChoice(normalized, DEFAULT_BENCHMARK, True, "no exchange suffix; US listing")


def benchmark_for(ticker: str) -> str:
    """Return the benchmark ticker for ``ticker`` (see :func:`resolve_benchmark`)."""
    return resolve_benchmark(ticker).benchmark


# --------------------------------------------------------------------------
# Built-in unit tests (deterministic). Run: python scripts/benchmarks.py --self-test
# --------------------------------------------------------------------------
_MAPPING_CASES: tuple[tuple[str, str], ...] = (
    ("NVDA", "SPY"),            # bare US ticker
    ("BRK-B", "SPY"),           # US ticker with a class separator
    ("BHP.AX", "^AXJO"),
    ("0700.HK", "^HSI"),
    ("7203.T", "^N225"),
    ("VOD.L", "^FTSE"),
    ("SHOP.TO", "^GSPTSE"),
    ("INFY.NS", "^NSEI"),
    ("500209.BO", "^BSESN"),
    ("600519.SS", "000001.SS"),
    ("000858.SZ", "399001.SZ"),
    ("D05.SI", "^STI"),
    ("005930.KS", "^KS11"),
    ("FPH.NZ", "^NZ50"),
    ("SAP.DE", "^GDAXI"),
    ("MC.PA", "^FCHI"),
    ("NESN.SW", "^SSMI"),
    ("FOO.XYZ", "SPY"),         # unknown suffix falls back
    ("ndq.ax", "^AXJO"),        # lowercase input
    ("  bhp.ax  ", "^AXJO"),    # whitespace tolerated
    ("GC=F", "SPY"),            # futures
    ("XAUUSD=X", "SPY"),        # FX
    ("^GSPC", "SPY"),           # index
    ("", "SPY"),                # degenerate input
)

_MEANINGFUL_CASES: tuple[tuple[str, bool], ...] = (
    ("NVDA", True),
    ("BHP.AX", True),
    ("0700.HK", True),
    ("GC=F", False),
    ("XAUUSD=X", False),
    ("^GSPC", False),
    ("^AXJO", False),
)


def _self_test() -> int:
    passed = 0
    for ticker, expected in _MAPPING_CASES:
        actual = benchmark_for(ticker)
        ok = actual == expected
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} benchmark_for({ticker!r}) -> {actual} (expected {expected})")
    for ticker, expected_flag in _MEANINGFUL_CASES:
        actual_flag = is_alpha_meaningful(ticker)
        ok = actual_flag == expected_flag
        passed += ok
        print(
            f"  {'ok ' if ok else 'XX '} is_alpha_meaningful({ticker!r}) -> {actual_flag} "
            f"(expected {expected_flag})"
        )
    total = len(_MAPPING_CASES) + len(_MEANINGFUL_CASES)
    print(f"\n{passed}/{total} benchmark unit tests passed.")
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Region-aware benchmark mapping")
    parser.add_argument("--ticker", help="Ticker to resolve, e.g. NDQ.AX")
    parser.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if not args.ticker:
        print("usage: benchmarks.py --ticker TICKER | --self-test", file=sys.stderr)
        return 2
    choice = resolve_benchmark(args.ticker)
    print(choice.benchmark)
    print(f"  ticker={choice.ticker}  alpha_meaningful={choice.alpha_meaningful}")
    print(f"  reason={choice.reason}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

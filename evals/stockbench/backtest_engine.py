#!/usr/bin/env python3
"""Deterministic, offline backtest engine for Trading Copilot decisions.

WHY THIS EXISTS
---------------
The eval harness has never actually run a backtest. Every rating the pipeline
ever emitted was unfalsifiable. This module closes that gap: it turns a stream
of ``Signal`` objects (a rating on a date) into realised ``Trade`` objects and a
risk-metric table, with **zero LLM cost** and **zero network access** in the
default path.

PATTERN SOURCES (re-implemented stdlib-only, nothing vendored)
-------------------------------------------------------------
- ``virattt/ai-hedge-fund`` (MIT) — signal/position/portfolio loop shape.
- ``microsoft/qlib`` ``contrib/evaluate.py::risk_analysis`` (MIT) — the metric
  formulas (annualized return, volatility, information ratio, max drawdown).
Neither dependency is required; no paid data vendor is used.

DETERMINISM CONTRACT
--------------------
No clock, no network, no randomness anywhere in the scoring path. The default
``JsonPriceSource`` reads a static price map from disk, so the same inputs
always produce byte-identical metrics. ``YFinancePriceSource`` exists for
convenience but is opt-in and lazily imported.

CLI
---
    python evals/stockbench/backtest_engine.py --self-test
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Protocol, Sequence

_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_ROOT / "scripts"))

from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

from parse_rating import RATINGS_5_TIER  # noqa: E402

TRADING_DAYS_PER_YEAR = 252

# Below this many trades the inferential metrics (annualisation, IR, volatility,
# hit rate) are noise dressed as a result: two winning 5-day trades annualise to
# a three-digit "return". They are published as None instead, with an explicit
# `insufficient_sample` marker. 20 is the conventional floor for a sample std to
# mean anything at all — it is a guard against self-deception, not a claim that
# 20 trades is enough to conclude anything.
DEFAULT_MIN_TRADES = 20

# Extra calendar days of price history a caller should request beyond the last
# signal date so that every signal has a full holding window available.
TAIL_PAD_MULTIPLIER = 2
TAIL_PAD_CONSTANT = 10

# 5-tier project scale -> directional conviction in [-1.0, +1.0].
RATING_TO_CONVICTION: dict[str, float] = {
    "Buy": 1.0,
    "Overweight": 0.5,
    "Hold": 0.0,
    "Underweight": -0.5,
    "Sell": -1.0,
}


def _assert_rating_mapping_complete() -> None:
    """Fail loudly at import time if the upstream 5-tier list drifts."""
    missing = [r for r in RATINGS_5_TIER if r not in RATING_TO_CONVICTION]
    extra = [r for r in RATING_TO_CONVICTION if r not in RATINGS_5_TIER]
    if missing or extra:
        raise RuntimeError(
            "RATING_TO_CONVICTION is out of sync with parse_rating.RATINGS_5_TIER "
            f"(missing={missing}, extra={extra}). Update the mapping deliberately — "
            "a silent default would corrupt every backtest."
        )


_assert_rating_mapping_complete()


def conviction_for_rating(rating: str) -> float:
    """Map a 5-tier rating string to conviction. Raises on unknown ratings."""
    key = (rating or "").strip().strip("*").capitalize()
    if key not in RATING_TO_CONVICTION:
        raise ValueError(
            f"unknown rating {rating!r}; expected one of {list(RATINGS_5_TIER)}"
        )
    return RATING_TO_CONVICTION[key]


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Bar:
    date: str          # ISO "YYYY-MM-DD"
    close: float


@dataclass(frozen=True)
class Signal:
    ticker: str
    date: str          # ISO "YYYY-MM-DD"
    conviction: float  # [-1.0, +1.0]
    source: str = "unknown"


@dataclass(frozen=True)
class Trade:
    ticker: str
    entry_date: str
    entry_price: float
    exit_date: str
    exit_price: float
    direction: int     # +1 long, -1 short
    return_pct: float


@dataclass(frozen=True)
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)
    equity_curve: list[float] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Price sources
# ---------------------------------------------------------------------------
class PriceSource(Protocol):
    """Anything that can hand back a sorted daily bar series."""

    def get_bars(self, ticker: str, start: str, end: str) -> list[Bar]:
        ...


class JsonPriceSource:
    """Offline price map: ``{"TICKER": [{"date": "YYYY-MM-DD", "close": 1.23}]}``.

    THE DEFAULT. Deterministic, unit-testable, no network. Unknown tickers
    return an empty series rather than raising, so a partially-covered universe
    degrades into ``BacktestResult.skipped`` entries instead of a crash.
    """

    def __init__(self, path: str | Path | None = None,
                 prices: dict[str, list[dict[str, object]]] | None = None) -> None:
        if (path is None) == (prices is None):
            raise ValueError("JsonPriceSource needs exactly one of path= or prices=")
        raw = prices
        if raw is None:
            file_path = Path(path)  # type: ignore[arg-type]
            if not file_path.exists():
                raise FileNotFoundError(f"price file not found: {file_path}")
            raw = json.loads(file_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("price map must be a JSON object keyed by ticker")
        self._bars: dict[str, list[Bar]] = {
            str(ticker): _coerce_bars(str(ticker), rows) for ticker, rows in raw.items()
        }

    def get_bars(self, ticker: str, start: str, end: str) -> list[Bar]:
        return [b for b in self._bars.get(ticker, []) if start <= b.date <= end]


def _coerce_bars(ticker: str, rows: object) -> list[Bar]:
    """Validate + normalise one ticker's raw rows into a sorted ``Bar`` list."""
    if not isinstance(rows, list):
        raise ValueError(f"{ticker}: price series must be a list, got {type(rows).__name__}")
    bars: list[Bar] = []
    for row in rows:
        if not isinstance(row, dict) or "date" not in row or "close" not in row:
            raise ValueError(f"{ticker}: every bar needs 'date' and 'close', got {row!r}")
        try:
            close = float(row["close"])  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{ticker}: bad close in {row!r}") from exc
        if not math.isfinite(close) or close <= 0 or isinstance(row["close"], bool):
            raise ValueError(f"{ticker}: close must be finite and positive")
        date.fromisoformat(str(row["date"]))
        bars.append(Bar(date=str(row["date"]), close=close))
    if len({b.date for b in bars}) != len(bars):
        raise ValueError(f"{ticker}: duplicate price sessions")
    bars.sort(key=lambda b: b.date)
    return bars


class YFinancePriceSource:
    """Opt-in online source. Lazy import so the repo stays stdlib-only."""

    def __init__(self) -> None:
        try:
            import yfinance  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on env
            raise RuntimeError(
                "yfinance is not installed. Either run `pip install yfinance`, "
                "or use the offline default: --prices <path.json>."
            ) from exc
        self._yf = yfinance

    def get_bars(self, ticker: str, start: str, end: str) -> list[Bar]:  # pragma: no cover
        # `end` is exclusive in yfinance; pad by one day so it stays inclusive here.
        end_exclusive = (date.fromisoformat(end) + timedelta(days=1)).isoformat()
        frame = self._yf.Ticker(ticker).history(start=start, end=end_exclusive,
                                                auto_adjust=True)
        bars = [
            Bar(date=str(index.date()), close=float(row["Close"]))
            for index, row in frame.iterrows()
        ]
        bars.sort(key=lambda b: b.date)
        return bars


# ---------------------------------------------------------------------------
# Backtest core
# ---------------------------------------------------------------------------
def required_end_date(last_signal_date: str, holding_days: int) -> str:
    """Calendar end date a caller should request so tails are covered.

    ``holding_days * 2 + 10`` extra calendar days covers weekends and a normal
    run of market holidays for a 5-day hold. Pure date arithmetic — no clock.
    """
    pad = holding_days * TAIL_PAD_MULTIPLIER + TAIL_PAD_CONSTANT
    return (date.fromisoformat(last_signal_date) + timedelta(days=pad)).isoformat()


def _entry_index(bars: Sequence[Bar], signal_date: str, snap_forward: bool) -> int | None:
    """Next close AFTER a daily signal; the signal bar cannot be its fill.

    Without an intraday decision timestamp the safe assumption is that the
    signal could have consumed its date's close. Off-grid dates still require
    explicit snap_forward for backward compatibility.
    """
    for i, bar in enumerate(bars):
        if bar.date == signal_date:
            return i + 1 if i + 1 < len(bars) else None
        if snap_forward and bar.date > signal_date:
            return i
    return None


def _simulate_ticker(
    ticker: str,
    signals: Sequence[Signal],
    bars: Sequence[Bar],
    holding_days: int,
    threshold: float,
    snap_forward: bool,
) -> tuple[list[Trade], list[str]]:
    """Walk one ticker's signal stream over its own trading-day grid.

    Edge-triggered arming: after an entry the ticker is DISARMED and only
    re-arms once conviction falls back below ``threshold``. Without this a
    persistently bullish stream would open one overlapping position per day.
    """
    trades: list[Trade] = []
    skipped: list[str] = []
    armed = True
    occupied_until: str | None = None
    for sig in signals:
        strength = abs(sig.conviction)
        if strength < threshold:
            armed = True  # edge re-arm
            skipped.append(f"{ticker} {sig.date}: below threshold "
                           f"(|conviction|={strength:.2f} < {threshold:.2f})")
            continue
        if not armed:
            skipped.append(f"{ticker} {sig.date}: disarmed - a position is already open "
                           "from an earlier signal (edge-triggered arming)")
            continue
        if not bars:
            skipped.append(f"{ticker} {sig.date}: no price bars available from the price source")
            continue
        idx = _entry_index(bars, sig.date, snap_forward)
        if idx is None:
            skipped.append(f"{ticker} {sig.date}: signal date is not a trading bar "
                           f"(bar range {bars[0].date}..{bars[-1].date}); "
                           "pass snap_forward=True to enter on the next bar")
            continue
        if occupied_until is not None and bars[idx].date <= occupied_until:
            skipped.append(f"{ticker} {sig.date}: position overlaps existing holding through {occupied_until}")
            continue
        exit_idx = idx + holding_days
        if exit_idx >= len(bars):
            available = len(bars) - 1 - idx
            skipped.append(
                f"{ticker} {sig.date}: insufficient tail data - needs {holding_days} bars "
                f"after entry, only {available} available; request "
                f"holding_days*{TAIL_PAD_MULTIPLIER}+{TAIL_PAD_CONSTANT} extra days of bars"
            )
            continue
        entry, exit_bar = bars[idx], bars[exit_idx]
        if entry.close <= 0:
            skipped.append(f"{ticker} {sig.date}: non-positive entry price {entry.close}")
            continue
        direction = 1 if sig.conviction > 0 else -1
        trades.append(Trade(
            ticker=ticker,
            entry_date=entry.date,
            entry_price=entry.close,
            exit_date=exit_bar.date,
            exit_price=exit_bar.close,
            direction=direction,
            return_pct=direction * (exit_bar.close / entry.close - 1.0),
        ))
        armed = False
        occupied_until = exit_bar.date
    return trades, skipped


def run_backtest(
    signals: Sequence[Signal],
    price_source: PriceSource,
    holding_days: int = 5,
    threshold: float = 0.5,
    *,
    snap_forward: bool = False,
    trading_days_per_year: int = TRADING_DAYS_PER_YEAR,
    min_trades: int = DEFAULT_MIN_TRADES,
) -> BacktestResult:
    """Replay ``signals`` against ``price_source`` on a fixed-hold schedule.

    Semantics
    ---------
    * The trading-day grid is the bar series itself — never the calendar.
    * Enter at the next available CLOSE AFTER the signal date when above threshold;
      ``direction = sign(conviction)``.
    * Exit at the CLOSE exactly ``holding_days`` bars later.
    * Edge-triggered arming prevents overlapping duplicate positions.
    * Signals with too little tail data are recorded in ``result.skipped``,
      never silently dropped.
    * ``min_trades`` gates the inferential metrics — see ``compute_metrics``.

    Invariant: ``len(trades) + len(skipped) == len(signals)``.
    """
    if holding_days < 1:
        raise ValueError("holding_days must be >= 1")
    if threshold <= 0:
        raise ValueError("threshold must be positive")

    by_ticker: dict[str, list[Signal]] = {}
    for sig in sorted(signals, key=lambda s: (s.date, s.ticker, s.source)):
        date.fromisoformat(sig.date)
        if not math.isfinite(sig.conviction) or not -1 <= sig.conviction <= 1:
            raise ValueError("conviction must be finite in [-1, 1]")
        by_ticker.setdefault(sig.ticker, []).append(sig)

    trades: list[Trade] = []
    skipped: list[str] = []
    for ticker in sorted(by_ticker):
        ticker_signals = by_ticker[ticker]
        start = ticker_signals[0].date
        end = required_end_date(ticker_signals[-1].date, holding_days)
        bars = price_source.get_bars(ticker, start, end)
        # Custom/YFinance PriceSources must satisfy the same contract as JSON.
        bars = _coerce_bars(ticker, [{"date": b.date, "close": b.close} for b in bars])
        t, s = _simulate_ticker(ticker, ticker_signals, bars, holding_days,
                                threshold, snap_forward)
        trades.extend(t)
        skipped.extend(s)

    trades.sort(key=lambda t: (t.exit_date, t.ticker, t.entry_date))
    periods = periods_per_year_for_hold(holding_days, trading_days_per_year)
    return BacktestResult(
        trades=trades,
        metrics=compute_metrics(trades, periods_per_year=periods, min_trades=min_trades),
        equity_curve=equity_curve(trades),
        skipped=skipped,
    )


def periods_per_year_for_hold(holding_days: int,
                              trading_days_per_year: int = TRADING_DAYS_PER_YEAR) -> float:
    """Independent return periods per year for a fixed ``holding_days`` hold.

    One "period" is one trade, and one trade occupies ``holding_days`` trading
    days. A 5-day hold therefore yields 252 / 5 = 50.4 periods per year — NOT
    252. Using 252 here would inflate annualized return by ~5x.
    """
    return float(trading_days_per_year) / float(holding_days)


def equity_curve(trades: Sequence[Trade]) -> list[float]:
    """Cumulative sum of trade returns, NOT portfolio equity; starts at 0.0."""
    curve = [0.0]
    total = 0.0
    for trade in trades:
        total += trade.return_pct
        curve.append(total)
    return curve


def compute_metrics(trades: Sequence[Trade],
                    periods_per_year: float = float(TRADING_DAYS_PER_YEAR),
                    min_trades: int = DEFAULT_MIN_TRADES) -> dict[str, object]:
    """qlib ``risk_analysis``-style metrics over per-trade returns.

    Formulas (N = ``periods_per_year``, r = per-trade returns, sample std ddof=1)::

        annualized_return = mean(r) * N
        volatility        = std(r) * sqrt(N)
        information_ratio = mean(r) / std(r) * sqrt(N)     # Sharpe-like, rf = 0
        max_drawdown      = min(cumsum(r) - running_max(cumsum(r)))
        hit_rate          = count(r > 0) / count(r)

    ``N`` for a fixed-hold strategy is ``252 / holding_days`` — see
    ``periods_per_year_for_hold``. ``run_backtest`` passes the derived value;
    the 252 default here only fits a daily-rebalanced series.

    Small-sample guard
    ------------------
    With ``len(trades) < min_trades`` the four INFERENTIAL metrics —
    ``annualized_return``, ``information_ratio``, ``volatility``, ``hit_rate`` —
    are set to ``None`` and ``insufficient_sample`` carries
    ``{"trade_count": n, "min_trades": min_trades}``. Each of them extrapolates
    from the sample to a population; on two trades that extrapolation is
    fiction (two good 5-day holds annualise to a three-digit percentage).

    The three DESCRIPTIVE metrics — ``avg_return``, ``cumulative_return``,
    ``max_drawdown`` — are still published at any n, because they are literal
    facts about the trades that actually happened, not estimates of anything.

    Numeric guards remain independent of the sample guard: fewer than 2 trades
    leaves the sample std undefined and ``std == 0`` leaves the ratio undefined.
    Both return ``None`` — never ``ZeroDivisionError``, never ``NaN``.
    """
    returns = [t.return_pct for t in trades]
    if any(not math.isfinite(r) for r in returns):
        raise ValueError("trade return must be finite")
    if not math.isfinite(periods_per_year) or periods_per_year <= 0 or min_trades < 1:
        raise ValueError("periods_per_year and min_trades must be positive")
    n = len(returns)
    base: dict[str, object] = {
        "metric_scope": "trade_sample_not_portfolio",
        "execution_assumption": "next_session_close_after_signal_date",
        "limitations": ["no capital allocation, cash, daily marked equity, fees or slippage", "annualized sample statistics are not strategy returns"],
        "trade_count": n,
        "periods_per_year": round(periods_per_year, 6),
        "min_trades": min_trades,
        "insufficient_sample": None,
        "avg_return": None,
        "cumulative_return": None,
        "annualized_return": None,
        "volatility": None,
        "information_ratio": None,
        "max_drawdown": None,
        "hit_rate": None,
    }
    if n < min_trades:
        base["insufficient_sample"] = {"trade_count": n, "min_trades": min_trades}
    if n == 0:
        return base

    mean = sum(returns) / n
    base["avg_return"] = mean
    base["cumulative_return"] = sum(returns)
    base["max_drawdown"] = _max_drawdown(returns)
    if base["insufficient_sample"] is not None:
        return base

    base["annualized_return"] = mean * periods_per_year
    base["hit_rate"] = sum(1 for r in returns if r > 0) / n
    if n >= 2:
        variance = sum((r - mean) ** 2 for r in returns) / (n - 1)
        std = math.sqrt(variance)
        base["volatility"] = std * math.sqrt(periods_per_year)
        if std > 0:
            base["information_ratio"] = mean / std * math.sqrt(periods_per_year)
    return base


def _max_drawdown(returns: Sequence[float]) -> float:
    """min(cumsum - running_max). Non-positive; 0.0 for a monotone-up series."""
    cumulative = 0.0
    peak = 0.0
    worst = 0.0
    for r in returns:
        cumulative += r
        peak = max(peak, cumulative)
        worst = min(worst, cumulative - peak)
    return worst


def format_metrics(metrics: dict[str, object]) -> str:
    """Human-readable metric table (ASCII only — Windows console safe)."""
    def fmt(key: str, pct: bool = True) -> str:
        value = metrics.get(key)
        if value is None:
            return "n/a"
        if pct:
            return f"{float(value) * 100:+.2f}%"
        return f"{float(value):.4f}"

    rows = [
        ("trades", str(metrics.get("trade_count", 0))),
        ("periods/year (N)", f"{float(metrics.get('periods_per_year') or 0):.2f}"),
        ("avg return / trade", fmt("avg_return")),
        ("sum of trade returns", fmt("cumulative_return")),
        ("sample mean x N (hypothetical)", fmt("annualized_return")),
        ("sample volatility x sqrt(N)", fmt("volatility")),
        ("sample ratio (no benchmark)", fmt("information_ratio", pct=False)),
        ("trade-ordered cumulative decline", fmt("max_drawdown")),
        ("hit rate", fmt("hit_rate")),
    ]
    width = max(len(label) for label, _ in rows)
    lines = ["=== Trade-sample statistics; NOT portfolio performance ===",
             "No capital allocation, cash, marked daily equity, fees, or slippage."]
    lines += [f"  {label.ljust(width)} : {value}" for label, value in rows]
    lines += _sample_banner(metrics)
    return "\n".join(lines)


def _sample_banner(metrics: dict[str, object]) -> list[str]:
    """Loud, unmissable banner when the sample is too small to infer anything."""
    marker = metrics.get("insufficient_sample")
    if not isinstance(marker, dict):
        return []
    n = marker.get("trade_count")
    floor = marker.get("min_trades")
    return [
        "",
        "  !!! SAMPLE TOO SMALL — DO NOT QUOTE THESE AS PERFORMANCE !!!",
        f"      {n} trade(s), below the --min-trades floor of {floor}.",
        "      annualized return / volatility / information ratio / hit rate are",
        "      withheld (None) because annualising a handful of trades produces a",
        "      number that looks like a result and is not one.",
        "      avg / cumulative return and max drawdown above describe ONLY these",
        f"      {n} trade(s) — they are not an estimate of future performance.",
    ]


# ---------------------------------------------------------------------------
# Built-in deterministic tests. Run: python backtest_engine.py --self-test
# ---------------------------------------------------------------------------
def _linear_prices(start: float, step: float, n: int, first_day: int = 1) -> list[dict[str, object]]:
    """Synthetic January bars: 2024-01-<first_day> onwards, one bar per entry."""
    return [{"date": f"2024-01-{first_day + i:02d}", "close": start + step * i}
            for i in range(n)]


def _demo_source() -> JsonPriceSource:
    """UP: 100 -> 110 over 10 steps. DOWN: 100 -> 90. FLAT: constant 50."""
    return JsonPriceSource(prices={
        "UP": _linear_prices(100.0, 1.0, 11),     # 2024-01-01 .. 2024-01-11
        "DOWN": _linear_prices(100.0, -1.0, 11),
        "FLAT": _linear_prices(50.0, 0.0, 11),
        "SHORTTAIL": _linear_prices(100.0, 1.0, 4),  # only 4 bars
    })


def _approx(a: float | None, b: float, tol: float = 1e-9) -> bool:
    return a is not None and abs(float(a) - b) <= tol


def _self_test() -> int:  # noqa: C901 - a flat list of independent assertions
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, bool(ok), detail))

    src = _demo_source()

    # 1. Daily signal on Jan 1: enter Jan 2 @101, exit Jan 7 @106.
    r = run_backtest([Signal("UP", "2024-01-01", 1.0, "test")], src, holding_days=5)
    ok = len(r.trades) == 1 and _approx(r.trades[0].return_pct, 106 / 101 - 1) and r.trades[0].entry_date == "2024-01-02"
    check("long trade enters after signal day", ok, f"trades={len(r.trades)}")

    # 2. known-return short: DOWN falls 100 -> 95, short earns +5%.
    r = run_backtest([Signal("DOWN", "2024-01-01", -1.0, "test")], src, holding_days=5)
    ok = len(r.trades) == 1 and r.trades[0].direction == -1 and _approx(r.trades[0].return_pct, 1 - 94 / 99)
    check("short trade returns use next-session entry", ok)

    # 2b. short on a rising tape loses.
    r = run_backtest([Signal("UP", "2024-01-01", -1.0, "test")], src, holding_days=5)
    ok = len(r.trades) == 1 and _approx(r.trades[0].return_pct, -(106 / 101 - 1))
    check("short trade on rising tape loses using next entry", ok)

    # 3. sub-threshold signal produces no trade but IS recorded.
    r = run_backtest([Signal("UP", "2024-01-01", 0.0, "hold")], src, holding_days=5)
    ok = not r.trades and len(r.skipped) == 1 and "below threshold" in r.skipped[0]
    check("sub-threshold signal skipped with reason", ok, str(r.skipped))

    # 4. edge-triggered arming: 3 consecutive Buys -> exactly 1 trade.
    stream = [Signal("UP", f"2024-01-0{d}", 1.0, "test") for d in (1, 2, 3)]
    r = run_backtest(stream, src, holding_days=5)
    ok = len(r.trades) == 1 and sum("disarmed" in s for s in r.skipped) == 2
    check("edge arming: no overlapping duplicate positions", ok,
          f"trades={len(r.trades)} skipped={len(r.skipped)}")

    # 5. re-arm only after conviction drops below threshold.
    stream = [Signal("UP", "2024-01-01", 1.0, "t"),
              Signal("UP", "2024-01-02", 0.0, "t"),
              Signal("UP", "2024-01-03", 1.0, "t")]
    r = run_backtest(stream, src, holding_days=5)
    ok = len(r.trades) == 1 and r.trades[0].entry_date == "2024-01-02" and any("overlaps" in s for s in r.skipped)
    check("Buy/Hold/Buy cannot overlap an existing holding", ok,
          f"entries={[t.entry_date for t in r.trades]}")
    stream.extend([Signal("UP", "2024-01-07", 0.0, "t"), Signal("UP", "2024-01-08", 1.0, "t")])
    r = run_backtest(stream, src, holding_days=1)
    check("rearmed signal can enter after prior exit", len(r.trades) == 3)
    check("statistics explicitly declare trade sample scope", r.metrics["metric_scope"] == "trade_sample_not_portfolio")
    for value in (float("nan"), float("inf"), 0.0):
        try:
            JsonPriceSource(prices={"BAD": [{"date": "2024-01-01", "close": value}]})
            rejected = False
        except ValueError:
            rejected = True
        check(f"invalid close {value} rejected", rejected)

    # 6. tail guard: SHORTTAIL has 4 bars, a 5-bar hold cannot complete.
    r = run_backtest([Signal("SHORTTAIL", "2024-01-01", 1.0, "t")], src, holding_days=5)
    ok = not r.trades and len(r.skipped) == 1 and "insufficient tail data" in r.skipped[0]
    check("tail-data guard records a skip instead of dropping", ok, str(r.skipped))

    # 7. metric math on hand-computable returns [+0.10, -0.05, +0.20].
    fake = [Trade("X", "d1", 100.0, "d2", 110.0, 1, 0.10),
            Trade("X", "d3", 100.0, "d4", 95.0, 1, -0.05),
            Trade("X", "d5", 100.0, "d6", 120.0, 1, 0.20)]
    # min_trades=1 disables the small-sample guard so the raw formulas are testable.
    m = compute_metrics(fake, periods_per_year=50.4, min_trades=1)
    mean = 0.25 / 3
    std = math.sqrt(((0.10 - mean) ** 2 + (-0.05 - mean) ** 2 + (0.20 - mean) ** 2) / 2)
    ok = (_approx(m["avg_return"], mean) and                               # type: ignore[arg-type]
          _approx(m["annualized_return"], mean * 50.4) and                 # type: ignore[arg-type]
          _approx(m["volatility"], std * math.sqrt(50.4)) and              # type: ignore[arg-type]
          _approx(m["information_ratio"], mean / std * math.sqrt(50.4)) and  # type: ignore[arg-type]
          _approx(m["max_drawdown"], -0.05) and                            # type: ignore[arg-type]
          _approx(m["hit_rate"], 2 / 3) and m["trade_count"] == 3)
    check("metric math matches hand-computed values", ok, json.dumps(m, default=str))

    # 8. empty input is safe.
    m = compute_metrics([])
    r = run_backtest([], src)
    ok = (m["trade_count"] == 0 and m["information_ratio"] is None
          and m["volatility"] is None and not r.trades and r.equity_curve == [0.0])
    check("empty input returns zeroed metrics, no exception", ok)

    # 9. single trade: sample std undefined -> None, not NaN / ZeroDivisionError.
    #    min_trades=1 so this exercises the NUMERIC guard, not the sample guard.
    m = compute_metrics([fake[0]], periods_per_year=50.4, min_trades=1)
    ok = (m["trade_count"] == 1 and m["volatility"] is None
          and m["information_ratio"] is None and _approx(m["hit_rate"], 1.0))  # type: ignore[arg-type]
    check("single trade: std/IR undefined -> None", ok, json.dumps(m, default=str))

    # 10. zero-variance returns -> IR None (no ZeroDivisionError).
    flat = [Trade("F", "d1", 50.0, "d2", 50.0, 1, 0.0) for _ in range(3)]
    m = compute_metrics(flat, periods_per_year=50.4, min_trades=1)
    ok = m["information_ratio"] is None and _approx(m["volatility"], 0.0)  # type: ignore[arg-type]
    check("zero-variance returns: IR None, volatility 0", ok)

    # 10a-d. small-sample guard at the n = 1 / 2 / 19 / 20 boundaries.
    def _n_trades(count: int) -> list[Trade]:
        return [Trade("N", f"e{i}", 100.0, f"x{i}", 101.0, 1, 0.01) for i in range(count)]

    inferential = ("annualized_return", "information_ratio", "volatility", "hit_rate")
    for n_trades in (1, 2, 19):
        m = compute_metrics(_n_trades(n_trades), periods_per_year=50.4, min_trades=20)
        ok = (m["insufficient_sample"] == {"trade_count": n_trades, "min_trades": 20}
              and all(m[k] is None for k in inferential)
              and _approx(m["cumulative_return"], 0.01 * n_trades)  # type: ignore[arg-type]
              and _approx(m["avg_return"], 0.01))                   # type: ignore[arg-type]
        check(f"n={n_trades} < min_trades: inferential metrics withheld, descriptive kept",
              ok, json.dumps(m, default=str))
        banner = "\n".join(_sample_banner(m))
        check(f"n={n_trades}: SAMPLE TOO SMALL banner fires",
              "SAMPLE TOO SMALL" in banner and f"{n_trades} trade(s)" in banner, banner)

    m = compute_metrics(_n_trades(20), periods_per_year=50.4, min_trades=20)
    ok = (m["insufficient_sample"] is None
          and _approx(m["annualized_return"], 0.01 * 50.4)  # type: ignore[arg-type]
          and _approx(m["hit_rate"], 1.0)                   # type: ignore[arg-type]
          and _approx(m["volatility"], 0.0))                # type: ignore[arg-type]
    check("n=20 == min_trades: metrics published, no banner", ok, json.dumps(m, default=str))
    check("n=20: no banner lines", _sample_banner(m) == [])

    # 10e. the default is the guard, not the raw math: run_backtest must inherit it.
    r = run_backtest([Signal("UP", "2024-01-01", 1.0, "t")], src, holding_days=5)
    ok = (len(r.trades) == 1 and r.metrics["annualized_return"] is None
          and isinstance(r.metrics["insufficient_sample"], dict))
    check("run_backtest defaults to the small-sample guard", ok,
          json.dumps(r.metrics, default=str))

    # 11. rating mapping covers exactly the upstream 5 tiers.
    ok = ([conviction_for_rating(r) for r in RATINGS_5_TIER] == [1.0, 0.5, 0.0, -0.5, -1.0]
          and set(RATING_TO_CONVICTION) == set(RATINGS_5_TIER))
    check("RATING_TO_CONVICTION covers every 5-tier rating", ok)

    # 12. invariant: every signal is either a trade or a recorded skip.
    stream = [Signal("UP", "2024-01-01", 1.0, "t"), Signal("UP", "2024-01-02", 1.0, "t"),
              Signal("FLAT", "2024-01-01", 0.2, "t"), Signal("MISSING", "2024-01-01", 1.0, "t"),
              Signal("FLAT", "2024-01-20", 1.0, "t")]
    r = run_backtest(stream, src, holding_days=5)
    ok = len(r.trades) + len(r.skipped) == len(stream)
    check("invariant: trades + skipped == signals", ok,
          f"{len(r.trades)}+{len(r.skipped)} vs {len(stream)}")

    # 13. unknown ticker and off-grid dates are reported, not crashed on.
    ok = (any("no price bars" in s for s in r.skipped)
          and any("not a trading bar" in s for s in r.skipped))
    check("unknown ticker / off-grid date reported in skipped", ok, str(r.skipped))

    # 14. snap_forward enters on the next available bar.
    r = run_backtest([Signal("UP", "2023-12-25", 1.0, "t")], src,
                     holding_days=5, snap_forward=True)
    ok = len(r.trades) == 1 and r.trades[0].entry_date == "2024-01-01"
    check("snap_forward enters on the next trading bar", ok)

    # 15. determinism: identical inputs -> identical output.
    a = run_backtest(stream, _demo_source(), holding_days=5)
    b = run_backtest(stream, _demo_source(), holding_days=5)
    ok = a.trades == b.trades and a.metrics == b.metrics and a.skipped == b.skipped
    check("determinism: same inputs -> same output", ok)

    # 16. periods_per_year derivation.
    ok = (_approx(periods_per_year_for_hold(5), 50.4)
          and _approx(periods_per_year_for_hold(1), 252.0))
    check("periods_per_year_for_hold = 252 / holding_days", ok)

    passed = sum(1 for _, ok, _ in results if ok)
    for name, ok, detail in results:
        flag = "ok " if ok else "XX "
        suffix = f"  [{detail}]" if (detail and not ok) else ""
        print(f"  {flag} {name}{suffix}")
    print(f"\n{passed}/{len(results)} backtest engine unit tests passed.")
    return 0 if passed == len(results) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Deterministic backtest engine")
    ap.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    args = ap.parse_args()
    if args.self_test:
        return _self_test()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())

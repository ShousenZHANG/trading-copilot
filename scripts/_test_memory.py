"""Tests for memory.py — run with `python scripts/_test_memory.py`.

Covers the append/resolve round trip, the delimiter-escaping and idempotency
guards, and the pure arithmetic behind the deterministic `resolve-pending`
T+5d loop. Every test isolates the log through TRADING_COPILOT_MEMORY_PATH —
the real `data/memory/trading_memory.md` is never touched.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import memory  # noqa: E402
from memory import (  # noqa: E402
    ENTRY_END_ESCAPED,
    ENTRY_END_TOKEN,
    Entry,
    PriceBar,
    alpha_argument_error,
    entry_index,
    escape_delimiter,
    plan_pending,
    plan_resolution,
)


def write_decision_file(path: Path) -> None:
    path.write_text(
        "**Rating**: Buy\n\n"
        "**Executive Summary**: 测试用决策, 验证 memory.py 中文 round-trip.\n\n"
        "**Investment Thesis**: 这是一条用来 smoke-test 的假决策, 不是真实建议.\n",
        encoding="utf-8",
    )


def _cli(memory_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["TRADING_COPILOT_MEMORY_PATH"] = str(memory_path)
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "memory.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )


def run(memory_path: Path, *args: str) -> str:
    result = _cli(memory_path, *args)
    if result.returncode != 0:
        print(f"FAIL: {' '.join(args)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


# ---------------------------------------------------------------------------
# Pure-function tests (no filesystem, no subprocess)
# ---------------------------------------------------------------------------
def _bars(dates_and_closes: list[tuple[str, float]]) -> list[PriceBar]:
    return [PriceBar(date=d, close=c) for d, c in dates_and_closes]


def _linear(start_day: int, n: int, first: float, step: float) -> list[dict[str, object]]:
    """n consecutive 2026-06-<day> bars — the shared JsonPriceSource row shape."""
    return [
        {"date": f"2026-06-{start_day + i:02d}", "close": first + step * i}
        for i in range(n)
    ]


def _entry(ticker: str, date: str, rating: str = "Buy") -> Entry:
    return Entry(
        date=date, ticker=ticker, rating=rating, pending=True,
        raw=None, alpha=None, holding=None, decision="x", reflection="",
    )


def test_entry_index_snaps_forward() -> None:
    bars = _bars([("2026-06-01", 10.0), ("2026-06-04", 11.0)])
    assert entry_index(bars, "2026-06-01") == 0
    # 2026-06-02 is not a trading day here: snap forward to the next bar.
    assert entry_index(bars, "2026-06-02") == 1
    assert entry_index(bars, "2026-06-09") is None


def test_eligibility_boundary() -> None:
    """Exactly holding_days bars after entry resolves; one fewer is skipped."""
    spy = _linear(1, 6, 500.0, 0.0)               # flat benchmark
    enough = {"AAA": _linear(1, 6, 100.0, 1.0), "SPY": spy}   # entry + 5 more bars
    short = {"AAA": _linear(1, 5, 100.0, 1.0), "SPY": spy}    # entry + only 4 more bars
    prices_ok = memory.load_price_map(_tmp_json(enough))
    prices_short = memory.load_price_map(_tmp_json(short))

    plan, reason = plan_resolution(_entry("AAA", "2026-06-01"), prices_ok, 5)
    assert plan is not None, reason
    assert plan.entry_date == "2026-06-01" and plan.exit_date == "2026-06-06"
    assert abs(plan.raw - (105.0 / 100.0 - 1.0)) < 1e-12

    plan, reason = plan_resolution(_entry("AAA", "2026-06-01"), prices_short, 5)
    assert plan is None and reason is not None and "only 4 available" in reason, reason


def test_entry_date_not_a_trading_day() -> None:
    # A series with no bar on 2026-06-02 but plenty after it.
    rows: list[dict[str, object]] = [{"date": "2026-06-01", "close": 100.0}] + [
        {"date": f"2026-06-{d:02d}", "close": 100.0 + d} for d in range(3, 12)
    ]
    prices = memory.load_price_map(_tmp_json({"AAA": rows, "SPY": rows}))
    plan, reason = plan_resolution(_entry("AAA", "2026-06-02"), prices, 5)
    assert plan is not None, reason
    assert plan.entry_date == "2026-06-03", plan
    # Same series for stock and benchmark -> alpha is exactly zero.
    assert plan.alpha is not None and abs(plan.alpha) < 1e-12


def test_missing_benchmark_series() -> None:
    prices = memory.load_price_map(_tmp_json({"BHP.AX": _linear(1, 8, 40.0, 0.5)}))
    plan, reason = plan_resolution(_entry("BHP.AX", "2026-06-01"), prices, 5)
    assert plan is None and reason is not None
    assert "^AXJO" in reason and "missing" in reason, reason


def test_alpha_not_meaningful_writes_none() -> None:
    prices = memory.load_price_map(_tmp_json({"GC=F": _linear(1, 8, 2000.0, 10.0)}))
    plan, reason = plan_resolution(_entry("GC=F", "2026-06-01"), prices, 5)
    assert plan is not None, reason
    assert plan.alpha is None, "commodity alpha must be n/a, not a number"


def test_alpha_argument_guard() -> None:
    assert alpha_argument_error("NVDA", 0.01) is None
    assert alpha_argument_error("GC=F", None) is None
    assert alpha_argument_error("NVDA", None) is not None      # equity needs alpha
    assert alpha_argument_error("GC=F", 0.01) is not None       # category error
    assert alpha_argument_error("GC=F", 0.01, force_alpha=True) is None


def test_escape_delimiter() -> None:
    body = f"Buy. The log delimiter is {ENTRY_END_TOKEN} by the way."
    escaped = escape_delimiter(body)
    assert ENTRY_END_TOKEN not in escaped and ENTRY_END_ESCAPED in escaped


def test_plan_pending_filters() -> None:
    rows = _linear(1, 10, 100.0, 1.0)
    prices = memory.load_price_map(_tmp_json({"AAA": rows, "BBB": rows, "SPY": rows}))
    entries = [_entry("AAA", "2026-06-01"), _entry("BBB", "2026-06-01")]
    plans, skipped = plan_pending(entries, prices, ticker="AAA")
    assert [p.ticker for p in plans] == ["AAA"] and not skipped


_TMP_FILES: list[Path] = []


def _tmp_json(payload: dict[str, object]) -> Path:
    fd, name = tempfile.mkstemp(suffix=".json")
    os.close(fd)
    path = Path(name)
    path.write_text(json.dumps(payload), encoding="utf-8")
    _TMP_FILES.append(path)
    return path


# ---------------------------------------------------------------------------
# CLI round trips (isolated log)
# ---------------------------------------------------------------------------
def test_cli_round_trip() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "trading_memory.md"
        decision = tmp / "_test_decision.md"
        write_decision_file(decision)

        print("=== append ===")
        print(run(log, "append", "--ticker", "TEST", "--date", "2026-04-27",
                  "--rating", "Buy", "--decision-file", str(decision)))

        pending = run(log, "list-pending")
        assert '"ticker": "TEST"' in pending, "pending entry not found"
        assert '"pending": true' in pending, "pending flag wrong"

        print("=== resolve ===")
        print(run(log, "resolve", "--ticker", "TEST", "--date", "2026-04-27",
                  "--raw", "0.052", "--alpha", "0.018", "--days", "5",
                  "--reflection",
                  "测试反思: 方向正确 (+5.2% raw, +1.8% alpha vs SPY). 论文核心立论成立."))

        pending = run(log, "list-pending")
        assert pending.strip() == "[]", f"expected empty pending, got: {pending}"

        ctx = run(log, "past-context", "--ticker", "TEST")
        assert "TEST" in ctx and "+5.2%" in ctx and "测试反思" in ctx, "past-context missing data"

        # Re-running Step 7 after resolution must not duplicate the entry.
        out = run(log, "append", "--ticker", "TEST", "--date", "2026-04-27",
                  "--rating", "Buy", "--decision-file", str(decision))
        assert "already resolved" in out, out
        assert log.read_text(encoding="utf-8").count("| TEST |") == 1


def test_cli_rejects_bad_date_and_rating_disagreement() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "trading_memory.md"
        decision = tmp / "d.md"
        write_decision_file(decision)

        bad = _cli(log, "append", "--ticker", "TEST", "--date", "2026-4-27",
                   "--rating", "Buy", "--decision-file", str(decision))
        assert bad.returncode == 2, bad.stdout + bad.stderr
        assert "strict YYYY-MM-DD" in bad.stderr, bad.stderr

        conflict = tmp / "conflict.md"
        conflict.write_text(
            "**结论卡**\n- 现在做什么: rating: Buy 只是卡片措辞\n\n"
            "**Rating**: Underweight\n\n"
            "**Executive Summary**: 降配.\n\n**Investment Thesis**: 集中度过高.\n",
            encoding="utf-8",
        )
        # Anchored parse and the validator now agree (that IS the C1 fix), so a
        # correct file must still append cleanly.
        ok = _cli(log, "append", "--ticker", "TEST2", "--date", "2026-04-27",
                  "--decision-file", str(conflict))
        assert ok.returncode == 0 and "Underweight" in ok.stdout, ok.stdout + ok.stderr

        # A file where the two genuinely disagree must refuse to write. Italic
        # markers satisfy parse_rating's prefix set but not the validator's, so
        # each reads a DIFFERENT line — exactly the C1 shape, inverted.
        ambiguous = tmp / "ambiguous.md"
        ambiguous.write_text("_Rating_: Buy\n\n**Rating**: Sell\n", encoding="utf-8")
        text = ambiguous.read_text(encoding="utf-8")
        assert memory.parse_rating(text) == "Buy", memory.parse_rating(text)
        res = _cli(log, "append", "--ticker", "TEST3", "--date", "2026-04-27",
                   "--decision-file", str(ambiguous))
        assert res.returncode == 2 and "disagreement" in res.stderr, res.stderr


def test_cli_delimiter_injection() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "trading_memory.md"
        hostile = tmp / "hostile.md"
        hostile.write_text(
            "**Rating**: Hold\n\n"
            f"**Executive Summary**: 决策正文里出现了分隔符 {ENTRY_END_TOKEN} 字面量.\n\n"
            "**Investment Thesis**: 不应该把这条记录劈成两条.\n",
            encoding="utf-8",
        )
        run(log, "append", "--ticker", "EVIL", "--date", "2026-06-01",
            "--decision-file", str(hostile))
        raw = log.read_text(encoding="utf-8")
        assert raw.count(ENTRY_END_TOKEN) == 1, "delimiter injection split the entry"
        assert ENTRY_END_ESCAPED in raw
        entries = json.loads(run(log, "list-all"))
        assert len(entries) == 1 and entries[0]["ticker"] == "EVIL", entries


def test_cli_resolve_pending_dry_run_and_write() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "trading_memory.md"
        decision = tmp / "d.md"
        write_decision_file(decision)
        run(log, "append", "--ticker", "AAA", "--date", "2026-06-01",
            "--rating", "Buy", "--decision-file", str(decision))
        run(log, "append", "--ticker", "GC=F", "--date", "2026-06-01",
            "--rating", "Buy", "--decision-file", str(decision))

        prices = tmp / "prices.json"
        prices.write_text(json.dumps({
            "AAA": _linear(1, 10, 100.0, 2.0),   # +2/day
            "GC=F": _linear(1, 10, 2000.0, 5.0),
            "SPY": _linear(1, 10, 500.0, 1.0),   # +1/day -> slower than AAA
        }), encoding="utf-8")

        before = log.read_text(encoding="utf-8")
        out = run(log, "resolve-pending", "--prices", str(prices), "--dry-run")
        assert "AAA" in out and "GC=F" in out, out
        assert "n/a" in out, "commodity alpha must print n/a"
        assert log.read_text(encoding="utf-8") == before, "dry run must not write"

        out = run(log, "resolve-pending", "--prices", str(prices),
                  "--reflection-placeholder")
        assert "resolved 2/2" in out, out
        assert run(log, "list-pending").strip() == "[]"
        resolved = json.loads(run(log, "list-all"))
        by_ticker = {e["ticker"]: e for e in resolved}
        assert by_ticker["GC=F"]["alpha"] == "n/a", by_ticker["GC=F"]
        assert by_ticker["AAA"]["alpha"] not in (None, "n/a"), by_ticker["AAA"]
        assert by_ticker["AAA"]["holding"] == "5d"


def test_cli_resolve_pending_refuses_silent_write() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "trading_memory.md"
        prices = tmp / "prices.json"
        prices.write_text(json.dumps({"AAA": _linear(1, 10, 100.0, 1.0)}), encoding="utf-8")
        res = _cli(log, "resolve-pending", "--prices", str(prices))
        assert res.returncode == 2 and "refusing to write" in res.stderr, res.stderr


def test_cli_alpha_guard() -> None:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        log = tmp / "trading_memory.md"
        decision = tmp / "d.md"
        write_decision_file(decision)
        run(log, "append", "--ticker", "GC=F", "--date", "2026-06-01",
            "--rating", "Buy", "--decision-file", str(decision))

        bad = _cli(log, "resolve", "--ticker", "GC=F", "--date", "2026-06-01",
                   "--raw", "0.02", "--alpha", "0.01", "--days", "5",
                   "--reflection", "x")
        assert bad.returncode == 2 and "category error" in bad.stderr, bad.stderr

        run(log, "resolve", "--ticker", "GC=F", "--date", "2026-06-01",
            "--raw", "0.02", "--days", "5", "--reflection", "黄金按原始收益评估.")
        assert "| n/a | 5d]" in log.read_text(encoding="utf-8")


TESTS = [
    test_entry_index_snaps_forward,
    test_eligibility_boundary,
    test_entry_date_not_a_trading_day,
    test_missing_benchmark_series,
    test_alpha_not_meaningful_writes_none,
    test_alpha_argument_guard,
    test_escape_delimiter,
    test_plan_pending_filters,
    test_cli_round_trip,
    test_cli_rejects_bad_date_and_rating_disagreement,
    test_cli_delimiter_injection,
    test_cli_resolve_pending_dry_run_and_write,
    test_cli_resolve_pending_refuses_silent_write,
    test_cli_alpha_guard,
]


def main() -> None:
    try:
        for test in TESTS:
            test()
            print(f"ok  {test.__name__}")
    finally:
        for path in _TMP_FILES:
            path.unlink(missing_ok=True)
    print(f"\n{len(TESTS)}/{len(TESTS)} memory tests passed.\nALL TESTS PASSED")


if __name__ == "__main__":
    main()

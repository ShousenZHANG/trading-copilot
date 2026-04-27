"""Quick smoke test for memory.py — run with `python scripts/_test_memory.py`."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MEMORY = ROOT / "data" / "memory" / "trading_memory.md"
TEST_DECISION = ROOT / "scripts" / "_test_decision.md"


def write_decision_file() -> None:
    TEST_DECISION.write_text(
        "**Rating**: Buy\n\n"
        "**Executive Summary**: 测试用决策, 验证 memory.py 中文 round-trip.\n\n"
        "**Investment Thesis**: 这是一条用来 smoke-test 的假决策, 不是真实建议.\n",
        encoding="utf-8",
    )


def run(*args: str) -> str:
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "memory.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"FAIL: {' '.join(args)}", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        sys.exit(1)
    return result.stdout


def main() -> None:
    # Reset
    MEMORY.write_text("", encoding="utf-8")
    write_decision_file()

    print("=== append ===")
    print(run("append", "--ticker", "TEST", "--date", "2026-04-27", "--rating", "Buy",
              "--decision-file", str(TEST_DECISION)))

    print("=== list-pending ===")
    pending = run("list-pending")
    print(pending)
    assert '"ticker": "TEST"' in pending, "pending entry not found"
    assert '"pending": true' in pending, "pending flag wrong"

    print("=== resolve ===")
    print(run("resolve", "--ticker", "TEST", "--date", "2026-04-27",
              "--raw", "0.052", "--alpha", "0.018", "--days", "5",
              "--reflection",
              "测试反思: 方向正确 (+5.2% raw, +1.8% alpha vs SPY). 论文核心立论成立. 下次类似setup可加大仓位."))

    print("=== list-pending (should be empty) ===")
    pending = run("list-pending")
    print(pending)
    assert pending.strip() == "[]", f"expected empty pending, got: {pending}"

    print("=== past-context (should show TEST resolved entry) ===")
    ctx = run("past-context", "--ticker", "TEST")
    print(ctx)
    assert "TEST" in ctx and "+5.2%" in ctx and "测试反思" in ctx, "past-context missing data"

    # Cleanup
    TEST_DECISION.unlink()
    MEMORY.write_text("", encoding="utf-8")
    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()

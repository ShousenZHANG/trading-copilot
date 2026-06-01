"""Regression tests for validation, ticker safety, and report assembly."""

from __future__ import annotations

import tempfile
from pathlib import Path

from assemble_report import assemble
from ticker import validate_ticker_component
from validate_outputs import (
    validate_portfolio_manager,
    validate_research_plan,
    validate_run_dir,
    validate_trader,
)


def test_ticker_validation() -> None:
    for ticker in ("NVDA", "BRK-B", "0700.HK", "BHP.AX", "GC=F", "XAUUSD=X", "^GSPC"):
        assert validate_ticker_component(ticker) == ticker
    for bad in ("", ".", "..", "../NVDA", "NV DA", "AAPL\x00", "CON", "NUL.T"):
        try:
            validate_ticker_component(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected unsafe ticker to fail: {bad!r}")


def test_output_contracts() -> None:
    assert validate_research_plan(
        "**Recommendation**: Overweight\n\n"
        "**Rationale**: Bull case carried.\n\n"
        "**Strategic Actions**: Build gradually."
    ).ok
    assert validate_trader(
        "**Action**: Buy\n\n"
        "**Reasoning**: Setup is constructive.\n\n"
        "**Entry Price**: 100\n\n"
        "**Stop Loss**: 94\n\n"
        "**Position Sizing**: 2%\n\n"
        "FINAL TRANSACTION PROPOSAL: **BUY**"
    ).ok
    assert validate_portfolio_manager(
        "**Rating**: Hold\n\n"
        "**Executive Summary**: No action.\n\n"
        "**Investment Thesis**: Evidence is balanced."
    ).ok
    assert not validate_trader("**Action**: Buy\n\n**Reasoning**: x").ok


def test_run_validation_and_assembly() -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        run_dir = root / "NVDA-2026-05-10"
        run_dir.mkdir()
        (run_dir / "01-market.md").write_text("# Market\nData timestamp: 2026-05-10", encoding="utf-8")
        (run_dir / "02-social.md").write_text("# Social", encoding="utf-8")
        (run_dir / "03-news.md").write_text("# News", encoding="utf-8")
        (run_dir / "04-fundamentals.md").write_text("# Fundamentals", encoding="utf-8")
        (run_dir / "00-past-context.md").write_text("(no past context)", encoding="utf-8")
        (run_dir / "debate_history.md").write_text("## Bull (round 1)\n...\n## Bear (round 1)\n...", encoding="utf-8")
        (run_dir / "risk_debate_history.md").write_text("## Aggressive (round 1)\n...", encoding="utf-8")
        (run_dir / "06-research-plan.md").write_text(
            "**Recommendation**: Hold\n\n"
            "**Rationale**: Evidence is balanced.\n\n"
            "**Strategic Actions**: Do nothing.",
            encoding="utf-8",
        )
        (run_dir / "07-trader-proposal.md").write_text(
            "**Action**: Hold\n\n"
            "**Reasoning**: No edge.\n\n"
            "FINAL TRANSACTION PROPOSAL: **HOLD**",
            encoding="utf-8",
        )
        (run_dir / "08-portfolio-decision.md").write_text(
            "**Rating**: Hold\n\n"
            "**Executive Summary**: No action.\n\n"
            "**Investment Thesis**: Inputs are balanced.\n\n"
            "**Time Horizon**: 3-6 months",
            encoding="utf-8",
        )

        result = validate_run_dir(run_dir)
        assert result.ok, result.errors
        out = root / "NVDA-2026-05-10.md"
        assemble("NVDA", "2026-05-10", run_dir, out)
        text = out.read_text(encoding="utf-8")
        assert "# NVDA 决策报告 — 2026-05-10" in text
        assert "Rating: Hold" in text
        assert "免责声明" in text


if __name__ == "__main__":
    test_ticker_validation()
    test_output_contracts()
    test_run_validation_and_assembly()
    print("ALL TESTS PASSED")


#!/usr/bin/env python3
"""Assemble a final /analyze report from run artifacts.

This keeps the final user-facing report deterministic: subagents write their
own section artifacts, validators check the decision contracts, and this script
performs the only Write to ``data/decisions/<TICKER>-<DATE>.md``.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from runtime import force_utf8_stdio
from ticker import validate_ticker_component
from validate_outputs import validate_run_dir

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent


def _read(path: Path, label: str, required: bool = True) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    if required:
        raise FileNotFoundError(f"required artifact missing: {path}")
    return f"_(missing: {label})_"


def _field(text: str, name: str) -> str | None:
    pattern = rf"^\s*\*{{0,2}}{re.escape(name)}\*{{0,2}}\s*[:\-]\s*(.+?)\s*$"
    m = re.search(pattern, text, flags=re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip().strip("*").strip() if m else None


def _headline(pm_text: str) -> str:
    rating = _field(pm_text, "Rating") or "Unknown"
    target = _field(pm_text, "Price Target")
    horizon = _field(pm_text, "Time Horizon")
    bits = [f"Rating: {rating}"]
    if target:
        bits.append(f"Target: {target}")
    if horizon:
        bits.append(f"Horizon: {horizon}")
    return " | ".join(bits)


def assemble(ticker: str, date: str, run_dir: Path, out_path: Path) -> str:
    validate_ticker_component(ticker)
    result = validate_run_dir(run_dir)
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if result.errors:
        for error in result.errors:
            print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)

    market = _read(run_dir / "01-market.md", "market report", required=False)
    social = _read(run_dir / "02-social.md", "social report", required=False)
    news = _read(run_dir / "03-news.md", "news report", required=False)
    fundamentals_path = run_dir / "04-fundamentals.md"
    macro_path = run_dir / "05-macro.md"
    fundamentals = _read(
        fundamentals_path if fundamentals_path.exists() else macro_path,
        "fundamentals or macro report",
        required=False,
    )
    fundamentals_label = "宏观面 (Macro)" if macro_path.exists() and not fundamentals_path.exists() else "基本面 (Fundamentals)"

    debate = _read(run_dir / "debate_history.md", "Bull/Bear debate", required=False)
    research_plan = _read(run_dir / "06-research-plan.md", "research plan")
    trader = _read(run_dir / "07-trader-proposal.md", "trader proposal")
    risk = _read(run_dir / "risk_debate_history.md", "risk debate", required=False)
    pm = _read(run_dir / "08-portfolio-decision.md", "portfolio decision")
    past_context = _read(run_dir / "00-past-context.md", "past context", required=False)

    report = f"""# {ticker} 决策报告 — {date}

> ⚠️ 教育与研究用途. 非投资建议. 详见 [DISCLAIMER.md](../../DISCLAIMER.md).

## 头条结论

{_headline(pm)}

## 最终结论 (Portfolio Manager)

{pm}

## 交易员方案 (Trader)

{trader}

## 研究主管摘要 (Research Manager)

{research_plan}

---

<details>
<summary>4位分析师报告 (展开查看)</summary>

### 技术面 (Market)

{market}

### 情绪面 (Social)

{social}

### 新闻面 (News)

{news}

### {fundamentals_label}

{fundamentals}

</details>

<details>
<summary>Bull/Bear 辩论纪要 (英文, 展开查看)</summary>

{debate}

</details>

<details>
<summary>风险辩论纪要 (Aggressive/Conservative/Neutral, 英文)</summary>

{risk}

</details>

---

## 过往决策上下文

{past_context}

---

⚠️ **免责声明**: 本报告由 trading-copilot 多 agent 生成. 仅供教育研究, 非投资建议. 模型可能产生幻觉/错误/遗漏. 你对所有投资决定负全责. 详见 [DISCLAIMER.md](../../DISCLAIMER.md).
"""

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    return str(out_path.resolve())


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a Trading Copilot final report")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    ticker = validate_ticker_component(args.ticker)
    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "data" / "runs" / f"{ticker}-{args.date}"
    out_path = Path(args.out) if args.out else ROOT / "data" / "decisions" / f"{ticker}-{args.date}.md"
    try:
        print(assemble(ticker, args.date, run_dir, out_path))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


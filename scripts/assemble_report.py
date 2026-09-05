#!/usr/bin/env python3
"""Assemble a final /analyze report from run artifacts.

This keeps the final user-facing report deterministic: subagents write their
own section artifacts, validators check the decision contracts, and this script
performs the only Write to ``data/decisions/<TICKER>-<DATE>.md``.

The 头条结论 section follows the 结论先行 style contract in
``.claude/config/output-language.md``: if the Portfolio Manager wrote a
``**结论卡**`` block, it is lifted verbatim to the top of the report so the
reader gets a plain-language answer before any evidence. Older runs that
predate the card still assemble — the extractor returns ``None`` and the
dense ``Rating | Target | Horizon`` one-liner stands alone.

CLI::

    python scripts/assemble_report.py --ticker NVDA --date 2026-06-25
    python scripts/assemble_report.py --self-test
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from parse_rating import first_rating_word, parse_rating
from runtime import force_utf8_stdio
from ticker import validate_date_component, validate_ticker_component
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


# --- 结论卡 (conclusion card) extraction ------------------------------------
# Contract: portfolio-manager.md emits a `**结论卡**` label line, then a bold
# plain-Chinese action line, then a 4-row table, then the machine-parsed
# `**Rating**:` field. Extraction stops at the first PM field label or the next
# markdown heading, so the card can never swallow the rest of the decision.

_CARD_LABEL_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?\*{0,2}\s*(?:结论卡|一句话结论)\s*\*{0,2}\s*[:：]?\s*(.*)$"
)
_PM_FIELD_RE = re.compile(
    r"^\s*\*{0,2}(?:Rating|Executive Summary|Investment Thesis|Price Target|Time Horizon)"
    r"\*{0,2}\s*[:\-]",
    re.IGNORECASE,
)
_HEADING_RE = re.compile(r"^\s*#{1,6}\s+\S")

# Defensive bound: a PM that never emits a field label must not drag the whole
# document into the headline block.
_CARD_MAX_LINES = 20


def _conclusion_card(pm_text: str) -> str | None:
    """Lift the PM's 结论卡 block, or return None when the run predates it."""
    lines = pm_text.splitlines()
    start: int | None = None
    inline = ""
    for index, line in enumerate(lines):
        match = _CARD_LABEL_RE.match(line)
        if match:
            start = index + 1
            inline = match.group(1).strip()
            break
    if start is None:
        return None

    block: list[str] = [inline] if inline else []
    for line in lines[start : start + _CARD_MAX_LINES]:
        if _PM_FIELD_RE.match(line) or _HEADING_RE.match(line):
            break
        block.append(line.rstrip())
    card = "\n".join(block).strip()
    return card or None


def _headline_block(pm_text: str) -> str:
    """头条结论 body: plain-language card first, dense one-liner underneath."""
    card = _conclusion_card(pm_text)
    headline = _headline(pm_text)
    if card is None:
        return headline
    return f"{card}\n\n{headline}"


def assemble(ticker: str, date: str, run_dir: Path, out_path: Path) -> str:
    # Both halves of the run path are validated. The date used to reach the
    # filesystem unchecked, so `--date ../../x` escaped data/decisions/.
    validate_ticker_component(ticker)
    validate_date_component(date)
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

{_headline_block(pm)}

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


_CARD_PM = (
    "**结论卡**\n"
    "\n"
    "**继续持有, 不动.** 三条预设触发线一条都没碰到.\n"
    "\n"
    "| 项 | 内容 |\n"
    "|----|------|\n"
    "| 现在做什么 | 不动 |\n"
    "| 什么时候再看 | 2026-08-14 财报后 |\n"
    "| 最大风险是什么 | 看穿浓度 8.7%, 超 5% 上限 |\n"
    "| 这次和上次比变了什么 | 浓度 8.1% -> 8.7% |\n"
    "\n"
    "**Rating**: Hold\n"
    "\n"
    "**Executive Summary**: 维持仓位.\n"
    "\n"
    "**Investment Thesis**: 证据均衡.\n"
    "\n"
    "**Time Horizon**: 3-6 months\n"
)
_LEGACY_PM = (
    "**Rating**: Hold\n\n"
    "**Executive Summary**: 维持仓位.\n\n"
    "**Investment Thesis**: 证据均衡.\n\n"
    "**Time Horizon**: 3-6 months\n"
)
# The hazard the parser-safety rule exists for: an English rating word inside
# the Chinese card. parse_rating's third pass is "first rating word anywhere",
# so if the anchored `**Rating**:` header were ever dropped or mangled, this
# document would be logged as Buy when the actual call is Underweight.
_POISONED_CARD_PM = (
    "**结论卡**\n"
    "\n"
    "**减仓一半.** 看穿浓度越线.\n"
    "\n"
    "| 项 | 内容 |\n"
    "|----|------|\n"
    "| 现在做什么 | 不要 Buy, 减仓 |\n"
    "\n"
    "**Rating**: Underweight\n"
)


def _date_ok(value: str) -> bool:
    """True when ``value`` survives the shared date-component guard."""
    try:
        validate_date_component(value)
    except ValueError:
        return False
    return True


def _self_test() -> int:
    """Built-in cases for 结论卡 extraction, parser safety, and --date validation."""
    runaway = "**结论卡**\n" + "\n".join(f"line {i}" for i in range(60))
    cases: list[tuple[str, str, bool]] = [
        # (label, pm_text, expect_card)
        ("new template with card", _CARD_PM, True),
        ("legacy run without card", _LEGACY_PM, False),
        ("heading-style card label", "## 结论卡\n不动.\n\n**Rating**: Hold\n", True),
        ("inline card label", "**一句话结论**: 不动.\n\n**Rating**: Hold\n", True),
        ("empty card falls back", "**结论卡**\n\n**Rating**: Hold\n", False),
        ("runaway card is bounded", runaway, True),
    ]
    failures = 0
    for label, text, expect_card in cases:
        card = _conclusion_card(text)
        got = card is not None
        ok = got == expect_card
        failures += not ok
        print(f"  {'ok ' if ok else 'XX '} {label}: card={'yes' if got else 'no'} "
              f"(expected {'yes' if expect_card else 'no'})")

    checks: list[tuple[str, bool]] = [
        ("card stops before **Rating**", "**Rating**" not in (_conclusion_card(_CARD_PM) or "")),
        ("card keeps the action line", "继续持有" in (_conclusion_card(_CARD_PM) or "")),
        ("card keeps all 4 table rows", (_conclusion_card(_CARD_PM) or "").count("| 现在做什么") == 1),
        ("headline survives with card", "Rating: Hold" in _headline_block(_CARD_PM)),
        ("headline survives without card", _headline_block(_LEGACY_PM) == _headline(_LEGACY_PM)),
        ("runaway bounded to 20 lines", len((_conclusion_card(runaway) or "").splitlines()) <= 20),
        ("extraction is idempotent", _conclusion_card(_CARD_PM) == _conclusion_card(_CARD_PM)),
        # --- parser safety: the card must not be able to change the logged rating.
        # parse_rating pass 3 is "first rating word ANYWHERE", so an English
        # rating word inside the card is a live hazard, not a style nit.
        ("shipped card template contains no English rating word",
         first_rating_word(_conclusion_card(_CARD_PM) or "") is None),
        ("anchored **Rating** beats an English word inside the card",
         parse_rating(_POISONED_CARD_PM) == "Underweight"),
        ("assembled report keeps the same rating parse_rating sees",
         parse_rating(_CARD_PM) == "Hold" and "Rating: Hold" in _headline_block(_CARD_PM)),
        ("legacy pre-card run parses identically",
         parse_rating(_LEGACY_PM) == "Hold"),
        ("card extraction never swallows the **Rating** line",
         first_rating_word(_conclusion_card(_POISONED_CARD_PM) or "") == "Buy"
         and "**Rating**" not in (_conclusion_card(_POISONED_CARD_PM) or "")),
        # --- --date is a path component and is validated like the ticker.
        ("--date rejects path traversal", not _date_ok("../../etc")),
        ("--date rejects an unpadded date", not _date_ok("2026-1-5")),
        ("--date rejects an impossible date", not _date_ok("2026-02-30")),
        ("--date accepts a real ISO date", _date_ok("2026-06-25")),
    ]
    for label, ok in checks:
        failures += not ok
        print(f"  {'ok ' if ok else 'XX '} {label}")

    total = len(cases) + len(checks)
    print(f"\n{total - failures}/{total} assemble_report unit tests passed.")
    return 1 if failures else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Assemble a Trading Copilot final report")
    parser.add_argument("--ticker")
    parser.add_argument("--date", help="YYYY-MM-DD")
    parser.add_argument("--run-dir", default=None)
    parser.add_argument("--out", default=None)
    parser.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    args = parser.parse_args()

    if args.self_test:
        return _self_test()
    if not args.ticker or not args.date:
        parser.error("--ticker and --date are required (or pass --self-test)")

    try:
        ticker = validate_ticker_component(args.ticker)
        run_date = validate_date_component(args.date)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    run_dir = Path(args.run_dir) if args.run_dir else ROOT / "data" / "runs" / f"{ticker}-{run_date}"
    out_path = Path(args.out) if args.out else ROOT / "data" / "decisions" / f"{ticker}-{run_date}.md"
    try:
        print(assemble(ticker, run_date, run_dir, out_path))
        return 0
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())


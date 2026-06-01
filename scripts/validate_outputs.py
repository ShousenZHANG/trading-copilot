#!/usr/bin/env python3
"""Validate Trading Copilot agent outputs.

The slash-command pipeline still exchanges markdown artifacts, but the three
decision-making agents must behave like structured-output agents. This module
checks those markdown contracts deterministically before memory logging or final
report assembly consumes them.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from runtime import force_utf8_stdio

force_utf8_stdio()

RATINGS_5_TIER = ("Buy", "Overweight", "Hold", "Underweight", "Sell")
TRADER_ACTIONS = ("Buy", "Hold", "Sell")
ADVISOR_RATINGS = ("Strong Buy", "Buy", "Hold", "Reduce", "Avoid", "Sell")

# Per-report soft cap on unsourced numeric claims. Inspired by financial-services
# `[UNSOURCED]` policy. A handful of `[UNSOURCED]` markers is acceptable when the
# author is explicit about provenance; many indicate the agent is fabricating.
UNSOURCED_SOFT_CAP = 3
_UNSOURCED_RE = re.compile(r"\[UNSOURCED\]")
_SUSPICIOUS_DIRECTIVE_RE = re.compile(r"\[suspicious directive content[^\]]*\]")


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]

    def extend(self, other: "ValidationResult", prefix: str = "") -> None:
        self.errors.extend(f"{prefix}{e}" for e in other.errors)
        self.warnings.extend(f"{prefix}{w}" for w in other.warnings)


def _field(text: str, name: str) -> str | None:
    pattern = rf"^\s*\*{{0,2}}{re.escape(name)}\*{{0,2}}\s*[:\-]\s*(.+?)\s*$"
    m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip("*").strip()


def _first_word(value: str | None) -> str | None:
    if not value:
        return None
    return value.strip().strip("*").split()[0].strip("*:.,")


def _nonempty_field(text: str, name: str, errors: list[str]) -> str | None:
    value = _field(text, name)
    if value is None:
        errors.append(f"missing required field: {name}")
        return None
    if not value.strip():
        errors.append(f"empty required field: {name}")
        return None
    return value


def validate_research_plan(text: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    rec = _nonempty_field(text, "Recommendation", errors)
    _nonempty_field(text, "Rationale", errors)
    _nonempty_field(text, "Strategic Actions", errors)
    if rec and _first_word(rec) not in RATINGS_5_TIER:
        errors.append(
            f"invalid Recommendation '{rec}'. Expected one of {list(RATINGS_5_TIER)}"
        )
    extra_headers = re.findall(r"^#{1,6}\s+", text, flags=re.MULTILINE)
    if extra_headers:
        warnings.append("research-manager output includes markdown headings despite strict contract")
    return ValidationResult(not errors, errors, warnings)


def validate_trader(text: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    action = _nonempty_field(text, "Action", errors)
    _nonempty_field(text, "Reasoning", errors)
    action_word = _first_word(action)
    if action_word and action_word not in TRADER_ACTIONS:
        errors.append(f"invalid Action '{action}'. Expected one of {list(TRADER_ACTIONS)}")
    final = re.search(r"FINAL TRANSACTION PROPOSAL:\s*\*\*(BUY|HOLD|SELL)\*\*", text)
    if not final:
        errors.append("missing trailing FINAL TRANSACTION PROPOSAL line")
    elif action_word and final.group(1).title() != action_word:
        errors.append(
            f"Action '{action_word}' conflicts with final proposal '{final.group(1)}'"
        )
    if action_word == "Buy":
        if _field(text, "Stop Loss") is None:
            warnings.append("Buy proposal has no Stop Loss; PM risk gate should downgrade or derive one")
        if _field(text, "Position Sizing") is None:
            warnings.append("Buy proposal has no Position Sizing; PM risk gate should downgrade or derive one")
    return ValidationResult(not errors, errors, warnings)


def validate_portfolio_manager(text: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    rating = _nonempty_field(text, "Rating", errors)
    _nonempty_field(text, "Executive Summary", errors)
    thesis = _nonempty_field(text, "Investment Thesis", errors)
    rating_word = _first_word(rating)
    if rating_word and rating_word not in RATINGS_5_TIER:
        errors.append(f"invalid Rating '{rating}'. Expected one of {list(RATINGS_5_TIER)}")
    if thesis and rating_word in {"Buy", "Overweight"}:
        lower = thesis.lower()
        required_terms = ("risk", "fresh", "stop")
        missing = [term for term in required_terms if term not in lower]
        if missing:
            warnings.append(
                "Buy/Overweight thesis may not explicitly document risk gate terms: "
                + ", ".join(missing)
            )
    return ValidationResult(not errors, errors, warnings)


def validate_advisor_report(text: str) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    if not re.search(r"^#\s+\S+\s+投资建议\s+—\s+\d{4}-\d{2}-\d{2}", text, re.MULTILINE):
        errors.append("missing advisor H1 title with ticker and date")
    rating_row = re.search(r"\|\s*\*\*评级\*\*\s*\|\s*([^|]+)\|", text)
    if not rating_row:
        errors.append("missing headline rating row")
    else:
        rating = rating_row.group(1).strip()
        if rating not in ADVISOR_RATINGS:
            errors.append(f"invalid advisor rating '{rating}'. Expected one of {list(ADVISOR_RATINGS)}")
    for marker in ("## ⚠️ 风险门检查", "## 📚 数据来源", "免责声明"):
        if marker not in text:
            errors.append(f"missing advisor section/marker: {marker}")
    if "as of" not in text and "数据新鲜" not in text:
        warnings.append("advisor report may not expose a clear data timestamp")
    return ValidationResult(not errors, errors, warnings)


def _unsourced_count(text: str) -> int:
    return len(_UNSOURCED_RE.findall(text))


def _suspicious_directive_count(text: str) -> int:
    return len(_SUSPICIOUS_DIRECTIVE_RE.findall(text))


def validate_run_dir(run_dir: Path) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    checks = {
        "06-research-plan.md": validate_research_plan,
        "07-trader-proposal.md": validate_trader,
        "08-portfolio-decision.md": validate_portfolio_manager,
    }
    for filename, validator in checks.items():
        path = run_dir / filename
        if not path.exists():
            errors.append(f"missing run artifact: {filename}")
            continue
        result = validator(path.read_text(encoding="utf-8"))
        errors.extend(f"{filename}: {e}" for e in result.errors)
        warnings.extend(f"{filename}: {w}" for w in result.warnings)

    analyst_candidates = [
        "01-market.md",
        "02-social.md",
        "03-news.md",
        "04-fundamentals.md",
        "05-macro.md",
    ]
    present_analysts = [name for name in analyst_candidates if (run_dir / name).exists()]
    if len(present_analysts) < 4:
        warnings.append(
            "fewer than four analyst reports present; final decision should disclose partial data"
        )

    # Aggregate unsourced/suspicious markers across analyst + decision reports.
    all_artifacts = present_analysts + [
        name for name in checks if (run_dir / name).exists()
    ]
    total_unsourced = 0
    total_suspicious = 0
    for name in all_artifacts:
        text = (run_dir / name).read_text(encoding="utf-8")
        unsourced = _unsourced_count(text)
        suspicious = _suspicious_directive_count(text)
        total_unsourced += unsourced
        total_suspicious += suspicious
        if unsourced > 0:
            warnings.append(f"{name}: {unsourced} [UNSOURCED] marker(s) — provenance gap")
        if suspicious > 0:
            warnings.append(
                f"{name}: {suspicious} suspicious directive content marker(s) — prompt injection attempt detected and ignored"
            )
    if total_unsourced > UNSOURCED_SOFT_CAP:
        warnings.append(
            f"total [UNSOURCED] markers across run = {total_unsourced} (soft cap {UNSOURCED_SOFT_CAP}); PM should consider downgrading on weak provenance"
        )
    return ValidationResult(not errors, errors, warnings)


def _read(path: Path) -> str:
    if not path.exists():
        raise FileNotFoundError(path)
    return path.read_text(encoding="utf-8")


def _print_result(result: ValidationResult) -> int:
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    for error in result.errors:
        print(f"error: {error}", file=sys.stderr)
    if result.ok:
        print("ok")
        return 0
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate Trading Copilot markdown outputs")
    parser.add_argument("kind", choices=("rm", "trader", "pm", "advisor", "run"))
    parser.add_argument("path")
    args = parser.parse_args()

    path = Path(args.path)
    try:
        if args.kind == "rm":
            return _print_result(validate_research_plan(_read(path)))
        if args.kind == "trader":
            return _print_result(validate_trader(_read(path)))
        if args.kind == "pm":
            return _print_result(validate_portfolio_manager(_read(path)))
        if args.kind == "advisor":
            return _print_result(validate_advisor_report(_read(path)))
        if args.kind == "run":
            return _print_result(validate_run_dir(path))
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    sys.exit(main())


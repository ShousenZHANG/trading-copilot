#!/usr/bin/env python3
"""Deterministic 5-tier rating parser.

Direct port of TradingAgents' ``agents/utils/rating.py``. Used by:
- ``scripts/memory.py`` when appending pending entries (defends against
  Portfolio Manager prompt drift that produces non-standard rating headers).
- Anywhere else trading-copilot needs to extract a rating from prose.

WHY THE PASSES ARE ORDERED THE WAY THEY ARE
-------------------------------------------
``scripts/validate_outputs.py`` reads the rating with a LINE-ANCHORED pattern
(``^\\s*\\*{0,2}Rating\\*{0,2}\\s*[:\\-]``). This module used to search each line
UNANCHORED, so a Chinese conclusion-card line such as ``- rating: Buy`` beat the
real ``**Rating**: Underweight`` header — the validator passed while the memory
log recorded the opposite call. ``/analyze`` and ``/gold`` invoke
``memory.py append`` WITHOUT ``--rating``, so this parser is the final authority
on what enters the append-only log; the two must not be able to disagree.

Pass order (first hit wins):
1. Line-anchored ``Rating: X`` label — same shape the validator accepts
   (leading whitespace and markdown bold only; a ``-``/``|`` list or table
   prefix does NOT qualify).
2. Unanchored ``rating ... : X`` on a line — LOWER priority, kept so decisions
   written before the conclusion-card era still parse.
3. First 5-tier rating word appearing anywhere in the text.

Returns a Title-cased rating string, or ``default`` if no rating word
appears. Default is "Hold" (the most defensive non-action stance).

CLI:
    python scripts/parse_rating.py path/to/decision.md
    cat path/to/decision.md | python scripts/parse_rating.py -
    python scripts/parse_rating.py --self-test
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Optional, Tuple

from runtime import force_utf8_stdio

force_utf8_stdio()


# Canonical, ordered 5-tier scale (most bullish to most bearish).
RATINGS_5_TIER: Tuple[str, ...] = (
    "Buy",
    "Overweight",
    "Hold",
    "Underweight",
    "Sell",
)

_RATING_SET = {r.lower() for r in RATINGS_5_TIER}

# Separators tolerated after the label. Full-width colon/hyphen/dash appear when
# the report body is Chinese and the author's IME stays in full-width mode.
_SEPARATORS = ":\\-：－—–"

# Pass 1 — anchored. Deliberately mirrors validate_outputs._field: only leading
# whitespace and markdown bold/italic markers may precede the label, so a card
# bullet ("- rating: Buy") or table cell ("| rating: Buy |") cannot win.
_ANCHORED_LABEL_RE = re.compile(
    rf"^[\s*_]*rating[\s*_]*[{_SEPARATORS}]\s*(.+)$", re.IGNORECASE
)

# Pass 2 — unanchored, lower priority. The historical behaviour, retained so
# pre-conclusion-card decision files keep parsing.
_LOOSE_LABEL_RE = re.compile(rf"rating.*?[{_SEPARATORS}]\s*(.+)$", re.IGNORECASE)

# Rating words are ASCII; extracting them word-wise makes the surrounding
# punctuation irrelevant — "**Sell**", "减持 (Underweight)" and "Buy/Hold" all
# resolve without a bespoke strip set.
_ASCII_WORD_RE = re.compile(r"[A-Za-z]+")


def first_rating_word(value: Optional[str]) -> Optional[str]:
    """Return the first 5-tier rating word inside ``value``, else None.

    Punctuation-insensitive: markdown bold, half/full-width parentheses and
    slashes are all ignored because only ASCII letter runs are considered.
    """
    if not value:
        return None
    for word in _ASCII_WORD_RE.findall(value):
        if word.lower() in _RATING_SET:
            return word.capitalize()
    return None


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Three passes, highest authority first: anchored label, unanchored label,
    then any rating word. Returns a Title-cased rating string, or ``default``
    if no rating word appears anywhere.
    """
    if not text:
        return default
    lines = text.splitlines()

    for pattern in (_ANCHORED_LABEL_RE, _LOOSE_LABEL_RE):
        for line in lines:
            match = pattern.search(line)
            if not match:
                continue
            rating = first_rating_word(match.group(1))
            if rating:
                return rating

    for line in lines:
        rating = first_rating_word(line)
        if rating:
            return rating

    return default


# --------------------------------------------------------------------------
# Built-in unit tests (deterministic). Run: python scripts/parse_rating.py --self-test
# --------------------------------------------------------------------------
_CARD_VS_HEADER = (
    "**结论卡**\n"
    "- 现在做什么: 减仓一半, rating: Buy 只是卡片里的口语措辞\n"
    "| 项目 | 说明 |\n"
    "| rating: Buy | 表格里的诱饵 |\n"
    "\n"
    "**Rating**: Underweight\n"
    "**Executive Summary**: 组合集中度过高.\n"
)

_PARSE_CASES: tuple[tuple[str, str, str], ...] = (
    (
        "card 'rating: Buy' loses to anchored **Rating**: Underweight",
        _CARD_VS_HEADER,
        "Underweight",
    ),
    ("Chinese gloss after the label", "**Rating**: 减持 (Underweight)\n", "Underweight"),
    ("bold value", "**Rating**: **Sell**\n", "Sell"),
    ("hyphen separator", "**Rating** - Sell\n", "Sell"),
    ("full-width colon", "**Rating**：Buy\n", "Buy"),
    ("plain anchored label", "Rating: Overweight\n", "Overweight"),
    ("indented anchored label", "   **Rating**: Hold\n", "Hold"),
    (
        "unanchored label still parses when no anchored one exists",
        "结论卡\n- final rating: Sell\n",
        "Sell",
    ),
    (
        "no label at all falls back to the first rating word",
        "The committee settled on Overweight after the debate.\n",
        "Overweight",
    ),
    ("empty text defaults to Hold", "", "Hold"),
    ("no rating word anywhere defaults to Hold", "No view expressed.\n", "Hold"),
    (
        "label line without a rating word defers to the next pass",
        "**Rating**: pending review\n\n**Recommendation**: Sell\n",
        "Sell",
    ),
    ("full-width parentheses gloss", "**Rating**：买入（Buy）\n", "Buy"),
)

_FIRST_WORD_CASES: tuple[tuple[Optional[str], Optional[str]], ...] = (
    ("**Sell**", "Sell"),
    ("减持 (Underweight)", "Underweight"),
    ("BUY", "Buy"),
    ("no rating here", None),
    ("", None),
    (None, None),
)


def _self_test() -> int:
    passed = 0
    for name, text, expected in _PARSE_CASES:
        actual = parse_rating(text)
        ok = actual == expected
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {name} -> {actual} (expected {expected})")
    for value, expected_word in _FIRST_WORD_CASES:
        actual_word = first_rating_word(value)
        ok = actual_word == expected_word
        passed += ok
        print(
            f"  {'ok ' if ok else 'XX '} first_rating_word({value!r}) -> {actual_word} "
            f"(expected {expected_word})"
        )
    total = len(_PARSE_CASES) + len(_FIRST_WORD_CASES)
    print(f"\n{passed}/{total} parse_rating unit tests passed.")
    return 0 if passed == total else 1


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_rating.py <path|-|--self-test>", file=sys.stderr)
        return 2
    arg = sys.argv[1]
    if arg == "--self-test":
        return _self_test()
    if arg == "-":
        text = sys.stdin.read()
    else:
        path = Path(arg)
        if not path.exists():
            print(f"file not found: {path}", file=sys.stderr)
            return 1
        text = path.read_text(encoding="utf-8")
    print(parse_rating(text))
    return 0


if __name__ == "__main__":
    sys.exit(main())

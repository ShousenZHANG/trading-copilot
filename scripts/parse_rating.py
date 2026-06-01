#!/usr/bin/env python3
"""Deterministic 5-tier rating parser.

Direct port of TradingAgents' ``agents/utils/rating.py``. Used by:
- ``scripts/memory.py`` when appending pending entries (defends against
  Portfolio Manager prompt drift that produces non-standard rating headers).
- Anywhere else trading-copilot needs to extract a rating from prose.

Two-pass strategy:
1. Look for an explicit "Rating: X" / "Rating - X" label (tolerant of
   markdown bold ``**X**``). Matches the ``render_pm_decision`` shape.
2. Fall back to the first 5-tier rating word found anywhere in the text.

Returns a Title-cased rating string, or ``default`` if no rating word
appears. Default is "Hold" (the most defensive non-action stance).

CLI:
    python scripts/parse_rating.py path/to/decision.md
    cat path/to/decision.md | python scripts/parse_rating.py -
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Tuple

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

# Matches "Rating: X" / "rating - X" / "Rating: **X**" — tolerates markdown
# bold wrappers and either a colon or hyphen separator.
_RATING_LABEL_RE = re.compile(r"rating.*?[:\-][\s*]*(\w+)", re.IGNORECASE)


def parse_rating(text: str, default: str = "Hold") -> str:
    """Heuristically extract a 5-tier rating from prose text.

    Two-pass strategy:
    1. Look for an explicit "Rating: X" label (tolerant of markdown bold).
    2. Fall back to the first 5-tier rating word found anywhere in the text.

    Returns a Title-cased rating string, or ``default`` if no rating word
    appears.
    """
    for line in text.splitlines():
        m = _RATING_LABEL_RE.search(line)
        if m and m.group(1).lower() in _RATING_SET:
            return m.group(1).capitalize()

    for line in text.splitlines():
        for word in line.lower().split():
            clean = word.strip("*:.,")
            if clean in _RATING_SET:
                return clean.capitalize()

    return default


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: parse_rating.py <path|->", file=sys.stderr)
        return 2
    arg = sys.argv[1]
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

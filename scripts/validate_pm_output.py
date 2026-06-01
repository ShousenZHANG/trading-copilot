#!/usr/bin/env python3
"""Validate Portfolio Manager output against the expected markdown schema.

Defends against prompt drift: if the PM forgets a required field, this
flags it before downstream consumers (memory log, assembled report) get
malformed input.

Required fields (per ``portfolio-manager.md``):
- ``**Rating**: <Buy|Overweight|Hold|Underweight|Sell>``
- ``**Executive Summary**: ...``
- ``**Investment Thesis**: ...``

Optional fields:
- ``**Price Target**: ...``
- ``**Time Horizon**: ...``

Exit codes:
  0 = valid
  1 = missing required field
  2 = malformed rating value
  3 = file not found / IO error

CLI:
    python scripts/validate_pm_output.py data/runs/NVDA-2026-04-27/08-portfolio-decision.md
"""

from __future__ import annotations

import sys
from pathlib import Path

from runtime import force_utf8_stdio
from validate_outputs import validate_portfolio_manager

force_utf8_stdio()


def validate(text: str) -> tuple[int, list[str]]:
    """Return (exit_code, messages). 0 = valid."""
    result = validate_portfolio_manager(text)
    messages = result.errors + [f"WARNING: {w}" for w in result.warnings]
    if result.ok:
        return 0, messages or ["VALID."]
    if any("invalid Rating" in message for message in messages):
        return 2, messages
    return 1, messages


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: validate_pm_output.py <path>", file=sys.stderr)
        return 3
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"file not found: {path}", file=sys.stderr)
        return 3
    text = path.read_text(encoding="utf-8")
    code, messages = validate(text)
    for msg in messages:
        prefix = "ok:" if code == 0 else "error:"
        print(f"{prefix} {msg}", file=sys.stderr if code else sys.stdout)
    return code


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Ticker and run-date validation helpers.

Ticker and date values enter this project from slash-command arguments and from
model outputs. They are later interpolated into paths (``data/runs/<TICKER>-<DATE>/``,
``data/decisions/<TICKER>-<DATE>.md``) and into memory-log tags, so they need a
small deterministic guard before any filesystem or persistence use.
"""

from __future__ import annotations

import re
from datetime import date as _date

_TICKER_RE = re.compile(r"^[A-Za-z0-9._\-=^]+$")
#: Strict ISO calendar date. Zero-padded, exactly ten characters, nothing else.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WINDOWS_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_ticker_component(value: str, *, max_len: int = 32) -> str:
    """Return ``value`` if it is safe as a ticker/path component.

    Accepted examples include ``NVDA``, ``BRK-B``, ``0700.HK``, ``BHP.AX``,
    ``GC=F``, ``XAUUSD=X``, and ``^GSPC``. The function rejects separators,
    whitespace, null bytes, all-dot values, and Windows reserved device names.
    """

    if not isinstance(value, str) or not value:
        raise ValueError(f"ticker must be a non-empty string, got {value!r}")
    if len(value) > max_len:
        raise ValueError(f"ticker exceeds {max_len} chars: {value!r}")
    if not _TICKER_RE.fullmatch(value):
        raise ValueError(f"ticker contains unsafe characters: {value!r}")
    if set(value) == {"."}:
        raise ValueError(f"ticker cannot consist solely of dots: {value!r}")
    if value.endswith((".", " ")):
        raise ValueError(f"ticker cannot end with a dot or space: {value!r}")
    stem = value.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED:
        raise ValueError(f"ticker uses a Windows reserved path name: {value!r}")
    return value


def validate_date_component(value: str) -> str:
    """Return ``value`` if it is a strict ``YYYY-MM-DD`` calendar date.

    The run date is the other half of every run path and of every memory-log
    tag, but unlike the ticker it used to reach the filesystem unchecked.
    Rejected: non-strings, unpadded forms (``2026-1-5``), path traversal
    (``../..``), separators, and anything ``date.fromisoformat`` normalises
    differently (``2026-02-30``, ``20260105``).
    """

    if not isinstance(value, str) or not value:
        raise ValueError(f"date must be a non-empty string, got {value!r}")
    if not _DATE_RE.fullmatch(value):
        raise ValueError(f"date must be strict YYYY-MM-DD, got {value!r}")
    try:
        parsed = _date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"date is not a real calendar date: {value!r}") from exc
    # Round-trip guard: fromisoformat accepts a few shapes the regex lets past
    # on future Python versions; an asymmetric round-trip is always a reject.
    if parsed.isoformat() != value:
        raise ValueError(f"date does not round-trip as ISO-8601: {value!r}")
    return value


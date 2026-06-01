#!/usr/bin/env python3
"""Ticker validation helpers.

Ticker values enter this project from slash-command arguments and from model
outputs. They are later interpolated into paths and memory-log tags, so they
need a small deterministic guard before any filesystem or persistence use.
"""

from __future__ import annotations

import re

_TICKER_RE = re.compile(r"^[A-Za-z0-9._\-=^]+$")
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


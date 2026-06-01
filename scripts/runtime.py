"""Shared runtime helpers for Trading Copilot CLI scripts."""

from __future__ import annotations

import sys
from typing import TextIO


def _reconfigure_utf8(stream: TextIO) -> None:
    """Force UTF-8 output without replacing stdio wrappers.

    Re-wrapping ``sys.stdout.buffer`` is unsafe when multiple modules do it in
    the same process: garbage-collecting the old wrapper can close the shared
    buffer and break later writes. ``reconfigure`` mutates the existing stream.
    """
    try:
        stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        # Older or already-closed streams cannot be reconfigured. Let the caller
        # proceed with the platform default rather than failing during import.
        return


def force_utf8_stdio() -> None:
    """Make stdout/stderr tolerate Chinese output on Windows terminals."""
    _reconfigure_utf8(sys.stdout)
    _reconfigure_utf8(sys.stderr)

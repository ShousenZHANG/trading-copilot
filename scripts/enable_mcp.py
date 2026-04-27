#!/usr/bin/env python3
"""Toggle MCP servers in .claude/settings.json.

Usage:
    python scripts/enable_mcp.py                    # list all MCPs and state
    python scripts/enable_mcp.py yahoo-finance      # enable
    python scripts/enable_mcp.py polygon --disable  # disable

All MCPs ship disabled (prefixed with `_`). This script flips the prefix.
Cross-platform — runs on Windows, macOS, Linux.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def settings_path() -> Path:
    """Project-level MCP config. Claude Code reads this from project root."""
    return Path(__file__).resolve().parent.parent / ".mcp.json"


def list_mcps(settings: dict) -> None:
    mcps = settings.get("mcpServers", {})
    if not mcps:
        print("(no MCPs configured)")
        return
    print("Available MCPs:")
    for k in sorted(mcps.keys()):
        state = "disabled" if k.startswith("_") else "ENABLED "
        name = k.lstrip("_")
        print(f"  [{state}] {name}")


def toggle(settings: dict, name: str, enable: bool) -> str:
    mcps = settings.setdefault("mcpServers", {})
    disabled = f"_{name}"
    enabled = name

    if enable:
        if disabled in mcps:
            mcps[enabled] = mcps.pop(disabled)
            return f"Enabled: {name}"
        if enabled in mcps:
            return f"Already enabled: {name}"
        raise KeyError(f"MCP not found: {name}")

    if enabled in mcps:
        mcps[disabled] = mcps.pop(enabled)
        return f"Disabled: {name}"
    if disabled in mcps:
        return f"Already disabled: {name}"
    raise KeyError(f"MCP not found: {name}")


def main() -> int:
    path = settings_path()
    if not path.exists():
        print(f"Error: settings.json not found at {path}", file=sys.stderr)
        return 1

    with path.open("r", encoding="utf-8") as f:
        settings = json.load(f)

    args = sys.argv[1:]
    if not args:
        list_mcps(settings)
        return 0

    name = args[0]
    enable = "--disable" not in args

    try:
        result = toggle(settings, name, enable)
    except KeyError as e:
        print(str(e), file=sys.stderr)
        return 1

    with path.open("w", encoding="utf-8") as f:
        json.dump(settings, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(result)
    print("Restart Claude Code for changes to take effect.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

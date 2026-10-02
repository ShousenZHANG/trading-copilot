# /// script
# requires-python = ">=3.11"
# ///
"""Resolve canonical stdio env mappings at runtime without storing their values."""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLACEHOLDER = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)\}")


def mapped_environment(spec: dict, inherited: dict[str, str]) -> dict[str, str]:
    """Apply destination-name mappings using only the process environment."""
    result = dict(inherited)
    for destination, reference in spec.get("env", {}).items():
        match = PLACEHOLDER.fullmatch(reference) if isinstance(reference, str) else None
        if not match:
            raise ValueError(f"MCP env mapping {destination} must reference exactly one variable")
        result[destination] = inherited.get(match.group(1), "")
    return result


def launch(spec: dict, *, inherited: dict[str, str], cwd: Path) -> int:
    """Use argv, never shell interpolation, and preserve the child exit status."""
    command = spec.get("command")
    args = spec.get("args", [])
    if not isinstance(command, str) or not command or not isinstance(args, list):
        raise ValueError("MCP stdio server requires a command and argument list")
    if not all(isinstance(argument, str) for argument in args):
        raise ValueError("MCP stdio arguments must be strings")
    directory = Path(spec.get("cwd", cwd))
    if not directory.is_absolute():
        directory = cwd / directory
    return subprocess.run([command, *args], cwd=directory, env=mapped_environment(spec, inherited),
                          check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("server")
    args = parser.parse_args()
    try:
        config = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        spec = config["mcpServers"][args.server]
        return launch(spec, inherited=dict(os.environ), cwd=ROOT)
    except (KeyError, ValueError, OSError):
        # Configuration/OS errors can contain credential values; expose no payload.
        print("MCP environment launcher could not start the configured server", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

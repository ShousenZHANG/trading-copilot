#!/bin/sh
# Trading Copilot launcher for macOS / Linux / WSL / Git Bash.
#
# POSIX sibling of scripts/start.ps1. INSTALL tells every user to create a
# `.env`, but MCP servers only see a key if it is already in the environment of
# the process that starts Claude Code -- `${VAR}` in .mcp.json is substituted
# from the parent environment, not read from a file. Without this script the
# non-Windows install path silently starts finnhub (and any other keyed server)
# with an empty key.
#
# Usage:
#     cd /path/to/trading-copilot
#     sh scripts/start.sh            # or: ./scripts/start.sh  [any claude args]
#
# Never prints a value. `set -a` exports everything the file defines, so the
# only observable output is the count of variables loaded.

set -e

# Project root = parent of the directory holding this script. Works when the
# script is invoked from anywhere, not just the repo root.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
ENV_FILE="$PROJECT_DIR/.env"

cd "$PROJECT_DIR"

if [ -f "$ENV_FILE" ]; then
    # `set -a` marks every subsequent assignment for export; sourcing the file
    # then hands each KEY=value to the environment claude inherits. `set +a`
    # restores normal scoping immediately after.
    set -a
    # shellcheck disable=SC1090  # path is computed, not a literal
    . "$ENV_FILE"
    set +a
    # Count assignments, not values. Nothing from the file is echoed.
    n=$(grep -c '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*=' "$ENV_FILE" || true)
    echo "[start.sh] loaded $n env var(s) from .env"
else
    echo "[start.sh] warning: no .env at $ENV_FILE - starting without it." >&2
    echo "[start.sh] MCP servers that need an API key (finnhub) will fail." >&2
    echo "[start.sh] Fix: cp .env.example .env, then fill in the keys." >&2
fi

echo "[start.sh] launching claude in $PROJECT_DIR"
exec claude "$@"

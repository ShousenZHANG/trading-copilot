#!/bin/sh
# Optional first argument: claude (default) or codex. Remaining args pass unchanged.
set -eu
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec python3 "$SCRIPT_DIR/start_client.py" "$@"

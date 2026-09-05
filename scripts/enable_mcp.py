#!/usr/bin/env python3
"""Add or remove MCP servers in .mcp.json, using .mcp.json.template as the catalog.

Why not the old underscore-prefix trick
---------------------------------------
This script used to "disable" a server by renaming its key from ``name`` to
``_name``. That never disabled anything: Claude Code has no such convention and
launches every key under ``mcpServers``, so the underscore-prefixed servers were
started on every session -- some connected and exposed tools under a
``mcp___name__*`` namespace, the rest reported connection errors. The prefix also
broke this very script, because the documented invocation used the prefixed name
(``enable_mcp.py _akshare``), which built ``__akshare`` and silently reported
"Already enabled".

So the model is now explicit: ``.mcp.json`` contains exactly the servers that
should run, and ``.mcp.json.template`` is the catalog they are copied from.
Absence is the off switch.

Usage:
    python scripts/enable_mcp.py                    # list catalog + state
    python scripts/enable_mcp.py fred               # copy from template into .mcp.json
    python scripts/enable_mcp.py fred --disable     # remove from .mcp.json
    python scripts/enable_mcp.py --self-test

Stdlib only. Cross-platform.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
ACTIVE_PATH = ROOT / ".mcp.json"
CATALOG_PATH = ROOT / ".mcp.json.template"


# ---------------------------------------------------------------------------
# Pure functions (covered by --self-test)
# ---------------------------------------------------------------------------

def servers_of(config: dict) -> dict[str, Any]:
    """Server mapping from a parsed config, tolerating a missing key."""
    servers = config.get("mcpServers")
    return dict(servers) if isinstance(servers, dict) else {}


def normalise_name(name: str) -> str:
    """Accept the legacy ``_name`` spelling that the old docs used everywhere."""
    return name[1:] if name.startswith("_") and len(name) > 1 else name


def enable(active: dict[str, Any], catalog: dict[str, Any], name: str) -> tuple[dict[str, Any], str]:
    """Return (new_active, message). Never mutates its arguments."""
    name = normalise_name(name)
    if name in active:
        return dict(active), f"Already active: {name}"
    if name not in catalog:
        raise KeyError(
            f"unknown MCP server: {name}. Known: {', '.join(sorted(catalog)) or '(catalog empty)'}")
    return {**active, name: catalog[name]}, f"Enabled: {name}"


def disable(active: dict[str, Any], name: str) -> tuple[dict[str, Any], str]:
    """Return (new_active, message). Never mutates its arguments.

    Removes both spellings. A config written by the old prefix-flipping version
    of this script can still hold a stale ``_name`` key, and that key is not
    inert -- Claude Code launches it. Disabling has to clear it too, or the
    migration silently leaves the server running.
    """
    plain = normalise_name(name)
    doomed = {name, plain, f"_{plain}"}
    if not doomed & set(active):
        return dict(active), f"Already inactive: {plain}"
    return {k: v for k, v in active.items() if k not in doomed}, f"Disabled: {plain}"


def describe(active: dict[str, Any], catalog: dict[str, Any]) -> list[str]:
    """One line per catalog entry, plus any active server missing from the catalog."""
    lines = []
    for name in sorted(set(catalog) | set(active)):
        state = "ACTIVE  " if name in active else "inactive"
        note = "" if name in catalog else "   (not in template)"
        lines.append(f"  [{state}] {name}{note}")
    return lines


def _dump(config: dict) -> str:
    return json.dumps(config, indent=2, ensure_ascii=False) + "\n"


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

def _self_test() -> int:
    passed = failed = 0

    def check(label: str, got: Any, want: Any) -> None:
        nonlocal passed, failed
        if got == want:
            passed += 1
            print(f"  ok  {label}")
        else:
            failed += 1
            print(f"  FAIL {label}: got {got!r}, want {want!r}")

    catalog = {"fred": {"command": "npx"}, "exa": {"command": "npx"}, "akshare": {"command": "uv"}}
    active = {"finnhub": {"command": "uv"}}

    new, msg = enable(active, catalog, "fred")
    check("enable copies the catalog entry", new.get("fred"), {"command": "npx"})
    check("enable keeps existing servers", "finnhub" in new, True)
    check("enable reports the action", msg, "Enabled: fred")
    check("enable does not mutate its input", sorted(active), ["finnhub"])

    _, msg = enable({**active, "fred": {}}, catalog, "fred")
    check("enable is idempotent", msg, "Already active: fred")

    new, msg = enable(active, catalog, "_akshare")
    check("enable accepts the legacy _name spelling", "akshare" in new, True)
    check("legacy spelling is not double-prefixed", "__akshare" in new, False)

    try:
        enable(active, catalog, "nope")
        check("enable rejects unknown names", "no raise", "KeyError")
    except KeyError as exc:
        check("enable rejects unknown names", "nope" in str(exc), True)
        check("the error lists the catalog", "akshare" in str(exc), True)

    new, msg = disable({**active, "fred": {}}, "fred")
    check("disable removes the entry", "fred" in new, False)
    check("disable keeps the others", sorted(new), ["finnhub"])
    check("disable reports the action", msg, "Disabled: fred")

    _, msg = disable(active, "fred")
    check("disable is idempotent", msg, "Already inactive: fred")

    new, _ = disable({**active, "_legacy": {}}, "_legacy")
    check("disable accepts the legacy _name spelling", "_legacy" in new, False)

    check("servers_of tolerates a missing key", servers_of({}), {})
    check("servers_of tolerates a wrong type", servers_of({"mcpServers": []}), {})
    check("normalise_name strips one underscore", normalise_name("_fred"), "fred")
    check("normalise_name leaves a bare name alone", normalise_name("fred"), "fred")
    check("normalise_name leaves a lone underscore alone", normalise_name("_"), "_")

    lines = describe({"finnhub": {}}, catalog)
    check("describe covers the union", len(lines), 4)
    check("describe flags servers outside the catalog",
          any("not in template" in line for line in lines), True)
    check("describe marks the active one",
          any(line.startswith("  [ACTIVE  ] finnhub") for line in lines), True)

    # Round-trip: the shipped files must actually parse and agree with each other.
    if ACTIVE_PATH.exists() and CATALOG_PATH.exists():
        live_active = servers_of(json.loads(ACTIVE_PATH.read_text(encoding="utf-8")))
        live_catalog = servers_of(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
        check("every active server exists in the template",
              sorted(set(live_active) - set(live_catalog)), [])
        check("no active server uses the legacy underscore spelling",
              [k for k in live_active if k.startswith("_")], [])

    print(f"\n{passed}/{passed + failed} enable_mcp unit tests passed.")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Add or remove MCP servers in .mcp.json from the .mcp.json.template catalog")
    ap.add_argument("name", nargs="?", help="server name (omit to list)")
    ap.add_argument("--disable", action="store_true", help="remove instead of add")
    ap.add_argument("--self-test", action="store_true",
                    help="run deterministic unit tests and exit")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    for path in (ACTIVE_PATH, CATALOG_PATH):
        if not path.exists():
            print(f"Error: {path.name} not found at {path}", file=sys.stderr)
            return 1

    active_config = json.loads(ACTIVE_PATH.read_text(encoding="utf-8"))
    catalog = servers_of(json.loads(CATALOG_PATH.read_text(encoding="utf-8")))
    active = servers_of(active_config)

    if not args.name:
        print("MCP servers (.mcp.json is the active set; .mcp.json.template is the catalog):")
        for line in describe(active, catalog):
            print(line)
        return 0

    try:
        updated, message = (disable(active, args.name) if args.disable
                            else enable(active, catalog, args.name))
    except KeyError as exc:
        print(str(exc).strip('"'), file=sys.stderr)
        return 1

    if updated != active:
        active_config["mcpServers"] = updated
        ACTIVE_PATH.write_text(_dump(active_config), encoding="utf-8")
        print(message)
        print("Restart Claude Code for changes to take effect.")
        print(f"Verify it starts: python scripts/mcp_handshake.py --server "
              f"{normalise_name(args.name)}")
    else:
        print(message)
    return 0


if __name__ == "__main__":
    sys.exit(main())

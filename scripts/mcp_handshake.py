#!/usr/bin/env python3
"""Start a shipped MCP server and require a real JSON-RPC ``initialize`` reply.

Why this exists
---------------
Every other check in this repo is a *shape* check: JSON parses, frontmatter has
a key, a string is present. All of them stayed green for weeks while the two
in-repo MCP servers and the default market-data server could not start at all --
``mcp[cli]`` had no upper bound, the SDK released 2.x, and 2.x deleted
``mcp.server.fastmcp``. A ``--self-test`` cannot catch that class of failure by
construction: it is designed to pass without the SDK, without a network, and
without a running process.

So this module does the one thing nothing else did: it spawns the server exactly
the way ``.mcp.json`` tells Claude Code to, speaks the handshake, and demands a
``serverInfo`` back. A dependency-resolution break, a crash on import, or a
protocol mismatch all surface here as a non-zero exit.

Stdlib only. The subprocess is killed on every path -- success, failure, timeout.

Usage
-----
    python scripts/mcp_handshake.py --all
    python scripts/mcp_handshake.py --server finnhub
    python scripts/mcp_handshake.py --self-test
    python scripts/mcp_handshake.py cmd /c uvx yahoo-finance-mcp

Exit codes: 0 all probed servers answered, 1 at least one did not, 2 bad input.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
MCP_CONFIG = ROOT / ".mcp.json"

# First-run `uv` provisioning of akshare+pandas genuinely takes minutes on a cold
# cache. The timeout is about "did it ever answer", not about latency.
DEFAULT_TIMEOUT = 180.0

INITIALIZE_REQUEST: dict[str, Any] = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "trading-copilot-handshake", "version": "1"},
    },
}


# ---------------------------------------------------------------------------
# Pure helpers (covered by --self-test -- no process, no network)
# ---------------------------------------------------------------------------

def stdio_servers(config: dict) -> dict[str, list[str]]:
    """Map server name -> argv for every stdio entry in an .mcp.json mapping.

    HTTP servers (``"type": "http"``) have no argv to spawn and are skipped.
    A leading ``_`` in a key is NOT a disable switch -- Claude Code has no such
    convention and launches those servers like any other. They are returned
    here too; the CLI decides which subset to probe.
    """
    out: dict[str, list[str]] = {}
    for name, entry in (config.get("mcpServers") or {}).items():
        if not isinstance(entry, dict) or entry.get("type") == "http":
            continue
        command = entry.get("command")
        if not command:
            continue
        args = [str(a) for a in entry.get("args") or []]
        out[name] = [str(command), *args]
    return out


def default_servers(servers: dict[str, list[str]]) -> dict[str, list[str]]:
    """The subset a clean install actually depends on.

    Optional servers need API keys that CI does not have, so probing them would
    report noise rather than regressions.
    """
    return {k: v for k, v in servers.items() if not k.startswith("_")}


def is_initialize_reply(line: str) -> bool:
    """True when a stdout line is a JSON-RPC result carrying ``serverInfo``."""
    try:
        message = json.loads(line)
    except (ValueError, TypeError):
        return False
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return False
    result = message.get("result")
    return isinstance(result, dict) and "serverInfo" in result


def server_version(line: str) -> str:
    """Best-effort ``name version`` from an initialize reply; '?' when absent."""
    try:
        info = json.loads(line)["result"]["serverInfo"]
        return f"{info.get('name', '?')} {info.get('version', '?')}"
    except Exception:
        return "?"


# ---------------------------------------------------------------------------
# The probe itself
# ---------------------------------------------------------------------------

def handshake(argv: Sequence[str], timeout: float = DEFAULT_TIMEOUT,
              env: Optional[dict[str, str]] = None) -> tuple[bool, float, str]:
    """Spawn ``argv``, send ``initialize``, wait for a well-formed reply.

    Returns ``(ok, elapsed_seconds, detail)``. Never raises for a server that
    simply fails -- a crash, a timeout and a protocol mismatch are all results.
    """
    started = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(argv),
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            cwd=str(ROOT), env={**os.environ, **(env or {})},
        )
    except OSError as exc:
        return False, time.monotonic() - started, f"spawn failed: {exc}"

    lines: list[str] = []

    def _drain() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            lines.append(line)

    threading.Thread(target=_drain, daemon=True).start()
    try:
        assert proc.stdin is not None
        proc.stdin.write(json.dumps(INITIALIZE_REQUEST) + "\n")
        proc.stdin.flush()
    except OSError:
        pass  # server already dead; the loop below reports the exit code

    reply = ""
    while time.monotonic() - started < timeout:
        for line in list(lines):
            if is_initialize_reply(line):
                reply = line
                break
        if reply or (proc.poll() is not None and not lines):
            break
        time.sleep(0.2)

    elapsed = time.monotonic() - started
    if reply:
        _kill(proc)
        return True, elapsed, server_version(reply)

    code = proc.poll()
    stderr_tail = _read_stderr(proc)
    _kill(proc)
    reason = f"exit={code}" if code is not None else f"no reply in {timeout:.0f}s"
    return False, elapsed, f"{reason}: {stderr_tail or '(no stderr)'}"


def _read_stderr(proc: subprocess.Popen) -> str:
    """Drain stderr without blocking forever on a server that is still alive."""
    if proc.stderr is None:
        return ""
    captured: list[str] = []

    def _drain() -> None:
        try:
            captured.append(proc.stderr.read())  # type: ignore[union-attr]
        except Exception:
            pass

    thread = threading.Thread(target=_drain, daemon=True)
    thread.start()
    thread.join(timeout=5)
    return "".join(captured)[-400:].strip().replace("\n", " | ")


def _kill(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except Exception:
            pass
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream is not None:
                stream.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Self-test -- deterministic, offline, no MCP SDK required
# ---------------------------------------------------------------------------

_ECHO_OK = (
    "import sys,json;sys.stdin.readline();"
    "print(json.dumps({'jsonrpc':'2.0','id':1,'result':"
    "{'protocolVersion':'2024-11-05','serverInfo':{'name':'fixture','version':'9.9'}}}),flush=True);"
    "sys.stdin.readline()"
)
_ECHO_JUNK = "import sys;sys.stdin.readline();print('not json',flush=True);sys.stdin.readline()"
_DIE = "import sys;sys.stderr.write('boom');sys.exit(3)"


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

    # --- stdio_servers / default_servers ---
    cfg = {"mcpServers": {
        "a": {"command": "cmd", "args": ["/c", "x"]},
        "_optional": {"command": "uvx", "args": ["thing"]},
        "http": {"type": "http", "url": "https://example.invalid"},
        "noargs": {"command": "solo"},
        "broken": {"args": ["no command"]},
        "notadict": "nope",
    }}
    servers = stdio_servers(cfg)
    check("stdio_servers keeps stdio entries", servers.get("a"), ["cmd", "/c", "x"])
    check("stdio_servers skips http", "http" in servers, False)
    check("stdio_servers tolerates missing args", servers.get("noargs"), ["solo"])
    check("stdio_servers skips entries with no command", "broken" in servers, False)
    check("stdio_servers skips non-dict entries", "notadict" in servers, False)
    check("stdio_servers on empty config", stdio_servers({}), {})
    check("stdio_servers keeps underscore entries", "_optional" in servers, True)
    check("default_servers drops underscore entries",
          sorted(default_servers(servers)), ["a", "noargs"])

    # --- is_initialize_reply / server_version ---
    good = json.dumps({"jsonrpc": "2.0", "id": 1,
                       "result": {"serverInfo": {"name": "n", "version": "1"}}})
    check("accepts a real initialize reply", is_initialize_reply(good), True)
    check("rejects non-JSON", is_initialize_reply("hello"), False)
    check("rejects a JSON-RPC error", is_initialize_reply(
        json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32000}})), False)
    check("rejects a result without serverInfo", is_initialize_reply(
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {}})), False)
    check("rejects a JSON scalar", is_initialize_reply("42"), False)
    check("rejects an empty line", is_initialize_reply(""), False)
    check("server_version extracts name and version", server_version(good), "n 1")
    check("server_version degrades to ?", server_version("junk"), "?")

    # --- handshake against fixture processes (no SDK, no network) ---
    ok, _, detail = handshake([sys.executable, "-c", _ECHO_OK], timeout=30)
    check("handshake succeeds against a well-behaved fixture", ok, True)
    check("handshake reports the fixture version", detail, "fixture 9.9")

    ok, _, _ = handshake([sys.executable, "-c", _ECHO_JUNK], timeout=8)
    check("handshake fails on a non-JSON-RPC server", ok, False)

    ok, _, detail = handshake([sys.executable, "-c", _DIE], timeout=30)
    check("handshake fails on a server that exits", ok, False)
    check("handshake surfaces the server stderr", "boom" in detail, True)

    ok, _, detail = handshake(["definitely-not-a-real-binary-xyz"], timeout=5)
    check("handshake reports a spawn failure", ok, False)
    check("spawn failure is labelled", detail.startswith("spawn failed"), True)

    print(f"\n{passed}/{passed + failed} mcp_handshake unit tests passed.")
    return 1 if failed else 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(
        description="Prove every shipped MCP server can complete a JSON-RPC handshake")
    ap.add_argument("--self-test", action="store_true",
                    help="run deterministic offline unit tests and exit")
    ap.add_argument("--all", action="store_true",
                    help="probe the default (non-underscore) stdio servers in .mcp.json")
    ap.add_argument("--include-optional", action="store_true",
                    help="also probe underscore-prefixed optional servers")
    ap.add_argument("--server", action="append", default=[],
                    help="probe one named server from .mcp.json (repeatable)")
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT,
                    help=f"seconds to wait for a reply (default {DEFAULT_TIMEOUT:.0f})")
    ap.add_argument("argv", nargs="*", help="explicit argv to probe instead of .mcp.json")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    if args.argv:
        ok, elapsed, detail = handshake(args.argv, timeout=args.timeout)
        print(f"{'PASS' if ok else 'FAIL'}  {elapsed:6.1f}s  {' '.join(args.argv)}  {detail}")
        return 0 if ok else 1

    if not MCP_CONFIG.exists():
        print(f"no {MCP_CONFIG}", file=sys.stderr)
        return 2
    all_servers = stdio_servers(json.loads(MCP_CONFIG.read_text(encoding="utf-8")))
    if args.server:
        missing = [s for s in args.server if s not in all_servers]
        if missing:
            print(f"unknown stdio server(s): {', '.join(missing)}. "
                  f"Known: {', '.join(sorted(all_servers))}", file=sys.stderr)
            return 2
        servers = {k: v for k, v in all_servers.items() if k in args.server}
    elif args.all:
        servers = all_servers if args.include_optional else default_servers(all_servers)
    else:
        ap.print_help()
        return 2

    failures = 0
    for name, argv in sorted(servers.items()):
        ok, elapsed, detail = handshake(argv, timeout=args.timeout)
        print(f"{'PASS' if ok else 'FAIL'}  {elapsed:6.1f}s  {name:16} {detail}")
        failures += 0 if ok else 1
    print(f"\n{len(servers) - failures}/{len(servers)} server(s) completed the handshake.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())

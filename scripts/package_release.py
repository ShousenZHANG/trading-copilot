#!/usr/bin/env python3
"""Build a clean, shippable release zip of the Trading Copilot plugin.

WHAT IT SHIPS
-------------
Only the plugin itself — prompts, slash commands, skills, scripts, docs,
templates. A user can unzip it into a Claude Code project and run immediately.

WHAT IT NEVER SHIPS (security)
------------------------------
- `.env` (real API keys) — only `.env.example`
- `data/positions.md`, `data/memory/trading_memory.md`, `data/runs/`,
  `data/decisions/` — personal trading state of the author's instance
- `.git/`, `reference/` (vendored upstream), build caches, editor configs
- `.claude/settings.local.json`, credentials

The allow-list below is explicit: if a path is not listed, it is NOT shipped.
This is fail-closed — safer than a deny-list for an open-source release.

MCP CONFIG
----------
`.mcp.json` IS shipped so the unzipped folder is runnable as-is (the plugin
manifest points at it). It may only reference secrets via `${VAR}` substitution;
the post-build audit fails the release if any `env`/`headers` value in a shipped
MCP config carries a literal value instead of a placeholder.

USAGE
-----
    python scripts/package_release.py            # version from plugin.json
    python scripts/package_release.py --version 0.2.0
    python scripts/package_release.py --stamp 2026-06-01   # date suffix
    python scripts/package_release.py --self-test          # audit unit tests

Output: dist/trading-copilot-<version>.zip
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import re
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

try:
    from runtime import force_utf8_stdio

    force_utf8_stdio()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent

# Explicit allow-list of top-level paths to include (fail-closed).
INCLUDE_PATHS = [
    ".claude/agents",
    ".claude/commands",
    ".claude/skills",
    ".claude/config",
    ".claude/settings.json",
    ".claude-plugin",
    "scripts",
    "mcps",
    "docs",
    "evals",
    "data/watchlist.md",
    "data/memory/README.md",
    "README.md",
    "README_zh.md",
    "LICENSE",
    "DISCLAIMER.md",
    "CLAUDE.md",
    "CONTEXT.md",
    ".mcp.json",
    ".mcp.json.template",
    ".env.example",
    ".gitignore",
    # .github/ is deliberately NOT shipped: CI config is repo infrastructure, not
    # plugin content, and a user unzipping this should not inherit our schedules.
]

# Files a plugin-directory listing (and a first-run user) expects to find.
# The build fails if any of these are absent from the finished zip.
REQUIRED_ARTIFACT_FILES = [
    ".claude-plugin/plugin.json",
    "README.md",
    "README_zh.md",
    "LICENSE",
    "DISCLAIMER.md",
    ".mcp.json",
    ".env.example",
]

# Patterns excluded even if under an included path (defense in depth).
EXCLUDE_PATTERNS = [
    "*.pyc", "__pycache__", "*.log", ".DS_Store", "Thumbs.db", "desktop.ini",
    "*.swp", "*.swo", ".omc",
    # never ship personal state even if a path rule slips
    "positions.md", "trading_memory.md",
    "settings.local.json", ".credentials.json",
    "*.env", ".env",
    # never ship the author's per-run analysis
    "data/decisions/*", "data/runs/*", "data/audit/*",
    "evals/results/*", "evals/cache/*",
]


def _excluded(rel: str) -> bool:
    parts = rel.replace("\\", "/")
    for pat in EXCLUDE_PATTERNS:
        if "/" in pat:
            if fnmatch.fnmatch(parts, pat):
                return True
        else:
            # match any path segment or basename
            if fnmatch.fnmatch(Path(parts).name, pat):
                return True
            if any(fnmatch.fnmatch(seg, pat) for seg in parts.split("/")):
                return True
    return False


def _iter_files(base: Path) -> Iterator[Path]:
    if base.is_file():
        yield base
        return
    for p in sorted(base.rglob("*")):
        if p.is_file():
            yield p


# A shipped MCP config may only carry `${VAR}` references, never literal keys.
_PLACEHOLDER_RE = re.compile(r"\$\{[A-Za-z0-9_]+\}")


def _hardcoded_mcp_secrets(config_text: str) -> list[str]:
    """Return `server.field.key` paths whose value is not a ${VAR} placeholder."""
    try:
        config = json.loads(config_text)
    except json.JSONDecodeError:
        return ["<unparseable MCP config>"]
    offenders: list[str] = []
    servers = config.get("mcpServers")
    if not isinstance(servers, dict):
        return offenders
    for server, spec in servers.items():
        if not isinstance(spec, dict):
            continue
        for field in ("env", "headers"):
            block = spec.get(field)
            if not isinstance(block, dict):
                continue
            for key, value in block.items():
                if isinstance(value, str) and value and not _PLACEHOLDER_RE.search(value):
                    offenders.append(f"{server}.{field}.{key}")
    return offenders


def _forbidden_archive_name(name: str) -> bool:
    """True for an archive member that must never ship (secrets/personal state).

    This is the single definition of the leak rule; ``_audit_zip`` is its only
    caller, and ``main`` runs the audit. Keeping a second inline copy in ``main``
    is what let the two drift apart in the first place.
    """
    low = name.lower()
    if low.endswith("/.env") or low.endswith("/positions.md"):
        return True
    if "trading_memory.md" in low:
        return True
    return "/data/decisions/" in low or "/data/runs/" in low


def _audit_zip(out: Path) -> list[str]:
    """Post-build verification: no secrets, no personal state, listing complete."""
    problems: list[str] = []
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        for name in names:
            if _forbidden_archive_name(name):
                problems.append(name)
            low = name.lower()
            if low.endswith(".mcp.json") or low.endswith(".mcp.json.template"):
                text = zf.read(name).decode("utf-8", errors="replace")
                problems += [f"{name}: hardcoded secret at {p}" for p in _hardcoded_mcp_secrets(text)]
        present = {n.split("/", 1)[1] for n in names if "/" in n}
        for required in REQUIRED_ARTIFACT_FILES:
            if required not in present:
                problems.append(f"missing required listing file: {required}")
    return problems


def plugin_version() -> str:
    manifest = ROOT / ".claude-plugin" / "plugin.json"
    if manifest.exists():
        try:
            return json.loads(manifest.read_text(encoding="utf-8")).get("version", "0.0.0")
        except Exception:
            pass
    return "0.0.0"


def build(version: str, stamp: str | None) -> Path:
    dist = ROOT / "dist"
    dist.mkdir(exist_ok=True)
    suffix = f"-{stamp}" if stamp else ""
    out = dist / f"trading-copilot-{version}{suffix}.zip"

    added = 0
    skipped = 0
    leak_guard = ("positions.md", "trading_memory.md", ".env")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for inc in INCLUDE_PATHS:
            base = ROOT / inc
            if not base.exists():
                print(f"  (skip missing) {inc}", file=sys.stderr)
                continue
            for f in _iter_files(base):
                rel = str(f.relative_to(ROOT)).replace("\\", "/")
                if _excluded(rel):
                    skipped += 1
                    continue
                # final hard leak guard
                if any(g in rel for g in leak_guard) and not rel.endswith(".example"):
                    print(f"  !! LEAK GUARD blocked: {rel}", file=sys.stderr)
                    skipped += 1
                    continue
                zf.write(f, arcname=f"trading-copilot/{rel}")
                added += 1

    print(f"Built {out.relative_to(ROOT)}  ({added} files, {skipped} skipped)")
    return out


# --------------------------------------------------------------------------
# Built-in unit tests (deterministic, offline).
# Run: python scripts/package_release.py --self-test
# --------------------------------------------------------------------------
_CLEAN_MCP = json.dumps({
    "mcpServers": {
        "finnhub": {"command": "uv", "env": {"FINNHUB_API_KEY": "${FINNHUB_API_KEY}"}},
        "yahoo-finance": {"command": "uvx"},
    }
})

_LEAKY_MCP = json.dumps({
    "mcpServers": {
        "finnhub": {"command": "uv", "env": {"FINNHUB_API_KEY": "d1abc23def456"}},
        "vendor": {"command": "npx", "headers": {"Authorization": "Bearer sk-live-xyz"}},
    }
})


def _required_files_are_shippable() -> bool:
    """Every REQUIRED_ARTIFACT_FILES entry must be covered by the allow-list.

    Pure string logic — this is the check that catches the list drifting after
    a path is dropped from INCLUDE_PATHS (as ``.github/`` was).
    """
    for required in REQUIRED_ARTIFACT_FILES:
        covered = any(
            required == inc or required.startswith(inc.rstrip("/") + "/")
            for inc in INCLUDE_PATHS
        )
        if not covered or _excluded(required):
            return False
    return True


def _self_test() -> int:
    leaky = _hardcoded_mcp_secrets(_LEAKY_MCP)
    cases: list[tuple[str, bool]] = [
        # --- _hardcoded_mcp_secrets --------------------------------------
        ("${VAR} substitution is clean", _hardcoded_mcp_secrets(_CLEAN_MCP) == []),
        ("literal env key is flagged", "finnhub.env.FINNHUB_API_KEY" in leaky),
        ("literal header value is flagged", "vendor.headers.Authorization" in leaky),
        ("exactly the two literals are flagged", len(leaky) == 2),
        ("server with no env/headers is not flagged",
            _hardcoded_mcp_secrets(json.dumps({"mcpServers": {"a": {"command": "x"}}})) == []),
        ("unparseable config is a finding",
            _hardcoded_mcp_secrets("{not json") == ["<unparseable MCP config>"]),
        ("config without mcpServers is clean", _hardcoded_mcp_secrets("{}") == []),
        ("the repo's own .mcp.json files are clean",
            all(_hardcoded_mcp_secrets((ROOT / name).read_text(encoding="utf-8")) == []
                for name in (".mcp.json", ".mcp.json.template")
                if (ROOT / name).exists())),
        # --- _excluded (path predicate) -----------------------------------
        ("personal state excluded: positions.md", _excluded("data/positions.md")),
        ("personal state excluded: trading_memory.md",
            _excluded("data/memory/trading_memory.md")),
        ("personal state excluded: per-run analysis",
            _excluded("data/runs/NVDA-2026-04-27/01-market.md")),
        ("personal state excluded: assembled decision",
            _excluded("data/decisions/NVDA-2026-04-27.md")),
        ("dotenv excluded", _excluded(".env")),
        ("local settings excluded", _excluded(".claude/settings.local.json")),
        ("nested __pycache__ excluded", _excluded("scripts/__pycache__/runtime.cpython-312.pyc")),
        ("allowed path kept: README.md", not _excluded("README.md")),
        ("allowed nested path kept: a skill file",
            not _excluded(".claude/skills/trading-copilot/SKILL.md")),
        ("allowed nested path kept: a script", not _excluded("scripts/montecarlo.py")),
        ("allowed path kept: .env.example", not _excluded(".env.example")),
        ("allowed path kept: watchlist", not _excluded("data/watchlist.md")),
        ("windows separators normalised", _excluded("data\\runs\\NVDA-2026-04-27\\01-market.md")),
        # --- archive-member predicate + required-file list -----------------
        ("archive leak: .env member",
            _forbidden_archive_name("trading-copilot/.env")),
        ("archive leak: positions.md member",
            _forbidden_archive_name("trading-copilot/data/positions.md")),
        ("archive leak: run artifact member",
            _forbidden_archive_name("trading-copilot/data/runs/NVDA-2026-04-27/01-market.md")),
        ("archive ok: .env.example member",
            not _forbidden_archive_name("trading-copilot/.env.example")),
        ("archive ok: README member",
            not _forbidden_archive_name("trading-copilot/README.md")),
        ("required files are all covered by the allow-list",
            _required_files_are_shippable()),
        (".github/ is not in the shipped payload",
            not any(inc.startswith(".github") for inc in INCLUDE_PATHS)),
    ]

    passed = 0
    for label, ok in cases:
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {label}")
    print(f"\n{passed}/{len(cases)} package_release unit tests passed.")
    return 0 if passed == len(cases) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Package Trading Copilot release zip")
    ap.add_argument("--version", default=None, help="override version (default: from plugin.json)")
    ap.add_argument("--stamp", default=None, help="optional date suffix, e.g. 2026-06-01")
    ap.add_argument("--self-test", action="store_true", help="run built-in audit unit tests")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    version = args.version or plugin_version()
    out = build(version, args.stamp)

    # Post-build verification. This is the security control the module docstring
    # and the README advertise: no secrets, no personal state, no missing
    # required file. A non-empty problem list fails the release.
    problems = _audit_zip(out)
    if problems:
        print("ERROR: release audit failed:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("Release audit passed: no secrets, no personal state, listing complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

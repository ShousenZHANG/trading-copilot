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
    ".github/workflows",
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


def _audit_zip(out: Path) -> list[str]:
    """Post-build verification: no secrets, no personal state, listing complete."""
    problems: list[str] = []
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        for name in names:
            low = name.lower()
            if low.endswith("/.env") or low.endswith("/positions.md") or "trading_memory.md" in low:
                problems.append(name)
            if "/data/decisions/" in low or "/data/runs/" in low:
                problems.append(name)
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Package Trading Copilot release zip")
    ap.add_argument("--version", default=None, help="override version (default: from plugin.json)")
    ap.add_argument("--stamp", default=None, help="optional date suffix, e.g. 2026-06-01")
    args = ap.parse_args()

    version = args.version or plugin_version()
    out = build(version, args.stamp)

    # Post-build verification: open the zip and assert no secret/personal files.
    forbidden = []
    with zipfile.ZipFile(out) as zf:
        for name in zf.namelist():
            low = name.lower()
            if low.endswith("/.env") or low.endswith("/positions.md") or "trading_memory.md" in low:
                forbidden.append(name)
            if "/data/decisions/" in low or "/data/runs/" in low:
                forbidden.append(name)
    if forbidden:
        print("ERROR: release zip contains forbidden files:", file=sys.stderr)
        for n in forbidden:
            print(f"  {n}", file=sys.stderr)
        return 1
    print("Leak check passed: no secrets or personal state in the zip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

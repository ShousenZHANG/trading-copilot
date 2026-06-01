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
import sys
import zipfile
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
    "LICENSE",
    "DISCLAIMER.md",
    "CLAUDE.md",
    "CONTEXT.md",
    ".mcp.json.template",
    ".env.example",
    ".github/workflows",
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


def _iter_files(base: Path):
    if base.is_file():
        yield base
        return
    for p in sorted(base.rglob("*")):
        if p.is_file():
            yield p


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

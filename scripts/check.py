#!/usr/bin/env python3
"""Repository health checks for Trading Copilot.

Inspired by Anthropic's financial-services plugin checks, but scoped to this
repo's file-based Claude/Codex plugin layout. The goal is to catch prompt drift,
broken manifests, unsafe model-tier changes, and stale docs before a trading run
depends on them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from runtime import force_utf8_stdio

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
errors: list[str] = []
warnings: list[str] = []
checked = 0

EXPECTED_COMMANDS = {
    "advise.md",
    "analyze.md",
    "debate.md",
    "earnings.md",
    "gold.md",
    "scan.md",
    "screen.md",
    "watchlist.md",
    "weekly-review.md",
}
OPUS_AGENTS = {"research-manager", "portfolio-manager", "investment-advisor"}
INTERNAL_DEBATE_AGENTS = {
    "bull-researcher",
    "bear-researcher",
    "aggressive-debator",
    "conservative-debator",
    "neutral-debator",
}


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT)).replace("\\", "/")


def err(message: str) -> None:
    errors.append(message)


def warn(message: str) -> None:
    warnings.append(message)


def read(path: Path) -> str:
    global checked
    checked += 1
    return path.read_text(encoding="utf-8")


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = read(path)
    if not text.startswith("---"):
        err(f"{rel(path)}: missing frontmatter")
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        err(f"{rel(path)}: malformed frontmatter")
        return {}
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"')
    return meta


def check_json(path: Path) -> None:
    try:
        json.loads(read(path))
    except json.JSONDecodeError as exc:
        err(f"{rel(path)}: JSON parse failed: {exc}")


def check_commands() -> None:
    command_dir = ROOT / ".claude" / "commands"
    found = {p.name for p in command_dir.glob("*.md")}
    missing = EXPECTED_COMMANDS - found
    extra = found - EXPECTED_COMMANDS
    if missing:
        err(f".claude/commands: missing commands {sorted(missing)}")
    if extra:
        warn(f".claude/commands: unexpected command files {sorted(extra)}")
    for command in command_dir.glob("*.md"):
        parse_frontmatter(command)

    analyze = read(command_dir / "analyze.md")
    required = [
        "Step 1: Analysts (PARALLEL",
        "scripts/assemble_report.py",
        "scripts/validate_outputs.py run",
    ]
    for marker in required:
        if marker not in analyze:
            err(f".claude/commands/analyze.md: missing pipeline guardrail '{marker}'")


def check_agents() -> None:
    agent_files = sorted((ROOT / ".claude" / "agents").rglob("*.md"))
    if len(agent_files) < 14:
        err(f".claude/agents: expected at least 14 agent prompts, found {len(agent_files)}")
    for path in agent_files:
        meta = parse_frontmatter(path)
        name = meta.get("name")
        model = meta.get("model")
        tools = meta.get("tools")
        for key in ("name", "description", "tools", "model"):
            if key not in meta:
                err(f"{rel(path)}: missing frontmatter key '{key}'")
        if not name:
            continue
        if name in OPUS_AGENTS and model != "opus":
            err(f"{rel(path)}: {name} must remain Opus-tier")
        if name not in OPUS_AGENTS and model not in {"sonnet"}:
            err(f"{rel(path)}: non-decider agent should remain Sonnet-tier, got {model!r}")
        if not tools:
            err(f"{rel(path)}: tools list is empty")
        text = read(path)
        if name in INTERNAL_DEBATE_AGENTS:
            if "Output language**: English" not in text:
                err(f"{rel(path)}: internal debate agent must output English")
        elif "Output language" in text and "Chinese" not in text:
            warn(f"{rel(path)}: user-facing agent mentions output language but not Chinese")


def check_skill_mirror() -> None:
    # Cross-runtime mirror (.agents/) is optional. Validate only if present.
    source = ROOT / ".claude" / "skills" / "trading-copilot" / "SKILL.md"
    mirror = ROOT / ".agents" / "skills" / "trading-copilot" / "SKILL.md"
    if not mirror.exists():
        return
    if read(source) != read(mirror):
        err(".agents/skills/trading-copilot/SKILL.md drifted from .claude source")


def check_docs_and_workflows() -> None:
    methodology = read(ROOT / "docs" / "methodology.md")
    stale_markers = [
        "ANALYSTS (sequential)",
        "Why sequential analysts (not parallel)",
    ]
    for marker in stale_markers:
        if marker in methodology:
            err(f"docs/methodology.md: stale sequential-analyst marker '{marker}'")
    if "ANALYSTS (parallel fan-out)" not in methodology:
        err("docs/methodology.md: missing current parallel analyst description")

    weekly = read(ROOT / ".github" / "workflows" / "weekly-review.yml")
    if "Install uv" not in weekly:
        err(".github/workflows/weekly-review.yml: missing uv install for MCP scripts")
    if "FINNHUB_API_KEY" not in weekly:
        warn(".github/workflows/weekly-review.yml: limited MCP env; weekly resolution may lack fallbacks")


def check_private_state_not_tracked() -> None:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "data/memory/trading_memory.md",
                "data/runs",
                "data/audit",
                ".env",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError as exc:
        warn(f"git unavailable for private-state check: {exc}")
        return
    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    if tracked:
        err(f"private state is tracked by git: {tracked}")


def main() -> int:
    check_json(ROOT / ".claude-plugin" / "plugin.json")
    check_json(ROOT / ".mcp.json")
    check_json(ROOT / ".claude" / "settings.json")
    check_commands()
    check_agents()
    check_skill_mirror()
    check_docs_and_workflows()
    check_private_state_not_tracked()

    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    if errors:
        print(f"FAIL - {len(errors)} issue(s) across {checked} checked file(s):", file=sys.stderr)
        for message in errors:
            print(f"  - {message}", file=sys.stderr)
        return 1
    print(f"OK - {checked} file(s) checked, 0 errors, {len(warnings)} warning(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

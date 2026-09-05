#!/usr/bin/env python3
"""Repository health checks for Trading Copilot.

Inspired by Anthropic's financial-services plugin checks, but scoped to this
repo's file-based Claude/Codex plugin layout. The goal is to catch prompt drift,
broken manifests, unsafe model-tier changes, and stale docs before a trading run
depends on them.
"""

from __future__ import annotations

import json
import re
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
    "portfolio.md",
    "scan.md",
    "screen.md",
    "watchlist.md",
    "weekly-review.md",
}

# Fields the directory listing renders. `category` is deliberately NOT here:
# `claude plugin validate` reports it as belonging in marketplace.json and
# ignored at load time. This checker used to require it, which is how the repo
# stayed green while the real validator rejected the manifest outright.
REQUIRED_PLUGIN_FIELDS = (
    "name",
    "version",
    "description",
    "displayName",
    "license",
    "homepage",
)

# Fields whose shape the official validator pins. Getting these wrong fails
# `claude plugin validate` — which CI now runs — so mirror them here to fail
# faster and with a message that says what to do.
PLUGIN_STRING_FIELDS = ("repository", "commands", "skills", "mcpServers")

# A fully-qualified MCP tool reference: mcp__<server>__<tool>.
_MCP_TOOL_RE = r"mcp__[A-Za-z0-9-]+__[A-Za-z0-9_]+"

# A workflow_dispatch input or event payload spliced straight into a run: block.
# The job that did this held every API secret in the repo.
_WORKFLOW_INTERPOLATION_RE = r"\$\{\{\s*(inputs\.[A-Za-z0-9_]+|github\.event[A-Za-z0-9_.]*)"

# Tools whose version silently changed under the scheduled workflows.
_WORKFLOW_PINNED_INSTALLS = (
    ("npm i -g @anthropic-ai/claude-code", "@anthropic-ai/claude-code@"),
    ("pip install ruff", "ruff=="),
)

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


def check_plugin_manifest(path: Path) -> None:
    """Validate plugin.json against the official directory's listing schema.

    Supersedes a plain JSON parse check for this file: it still errors on a
    parse failure, and additionally enforces the metadata the directory renders.
    """
    try:
        manifest = json.loads(read(path))
    except json.JSONDecodeError as exc:
        err(f"{rel(path)}: JSON parse failed: {exc}")
        return
    if not isinstance(manifest, dict):
        err(f"{rel(path)}: manifest must be a JSON object")
        return

    for field in REQUIRED_PLUGIN_FIELDS:
        value = manifest.get(field)
        if not isinstance(value, str) or not value.strip():
            err(f"{rel(path)}: missing or empty required field '{field}'")

    if "category" in manifest:
        err(f"{rel(path)}: drop 'category' — `claude plugin validate` reports it "
            f"belongs in marketplace.json and is ignored at load time")
    if "settings" in manifest:
        err(f"{rel(path)}: drop 'settings' — the field wants an inline record, not "
            f"a path, and plugin-supplied permissions/env are not applied anyway")

    for field in PLUGIN_STRING_FIELDS:
        if field in manifest and not isinstance(manifest[field], str):
            err(f"{rel(path)}: '{field}' must be a string, got "
                f"{type(manifest[field]).__name__}")

    # `agents` must enumerate the real files: a directory string is rejected by
    # the validator, and a stale list ships an agent the pipeline cannot dispatch.
    declared = manifest.get("agents")
    if not isinstance(declared, list):
        err(f"{rel(path)}: 'agents' must be a list of file paths")
    else:
        on_disk = sorted("./" + q.relative_to(ROOT).as_posix()
                         for q in (ROOT / ".claude/agents").rglob("*.md"))
        if sorted(declared) != on_disk:
            missing = sorted(set(on_disk) - set(declared))
            extra = sorted(set(declared) - set(on_disk))
            err(f"{rel(path)}: 'agents' is out of sync with .claude/agents "
                f"(missing: {missing or 'none'}; stale: {extra or 'none'})")

    keywords = manifest.get("keywords")
    if not isinstance(keywords, list) or not keywords:
        warn(f"{rel(path)}: keywords are empty; the directory listing will show no tags")


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
        check_agent_mcp_grants(path, name, tools or "", text)


def known_mcp_servers() -> set[str]:
    """Every server name the repo knows about: active set plus the catalog."""
    names: set[str] = set()
    for config in (ROOT / ".mcp.json", ROOT / ".mcp.json.template"):
        try:
            servers = json.loads(read(config)).get("mcpServers")
        except json.JSONDecodeError:
            continue
        if isinstance(servers, dict):
            names |= {k.lstrip("_") for k in servers}
    return names


def check_agent_mcp_grants(path: Path, name: str, tools: str, text: str) -> None:
    """An agent's `tools:` allowlist must cover every MCP server it calls.

    `tools:` is an allowlist, not an addition: an agent listing only
    `Read, Write, WebFetch` has NO MCP tools, launches without error, and
    silently degrades to WebFetch or invention. Every analyst prompt in this
    repo was in that state while telling the model to call
    `mcp__yahoo-finance__get_stock_info`.

    Only the two documented server-level spellings are accepted --
    `mcp__<server>` and `mcp__<server>__*` -- because a bare `mcp__*` grant and
    a partial-name glob are not documented for this field.
    """
    granted = {
        entry.strip().removeprefix("mcp__").removesuffix("__*").split("__")[0]
        for entry in tools.split(",")
        if entry.strip().startswith("mcp__")
    }
    known = known_mcp_servers()
    for server in sorted(granted - known):
        err(f"{rel(path)}: grants mcp__{server}, which is in neither .mcp.json "
            f"nor .mcp.json.template — a typo here fails silently")

    # mcp__<server>__<tool> -> parts[1] is the server. Tool names contain single
    # underscores (get_stock_info), so splitting on the double underscore is safe.
    called = {
        parts[1]
        for parts in (m.split("__") for m in re.findall(_MCP_TOOL_RE, text))
        if len(parts) >= 3 and parts[1]
    }
    for server in sorted(called - granted):
        err(f"{rel(path)}: prompt calls mcp__{server}__* but tools: does not grant "
            f"mcp__{server} — the call is unreachable")


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



def _is_ignored(candidate: str) -> bool:
    """True when .gitignore covers the path. Fails open if git is unavailable."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", candidate],
            cwd=ROOT, capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def check_workflows() -> None:
    """Lint every GitHub workflow for the defects that actually shipped here.

    Two scheduled workflows failed every run for weeks while this checker stayed
    green, because the only thing it inspected was whether two strings appeared
    in one file. These are the mechanical halves of what went wrong: a dispatch
    input interpolated into a shell script in a job holding every API secret,
    unpinned tool installs that let the CLI change under us, duplicate "DST"
    crons that doubled the bill, and paths .gitignore covers so the step either
    did nothing or published private state.
    """
    workflow_dir = ROOT / ".github" / "workflows"
    if not workflow_dir.is_dir():
        return
    for path in sorted(workflow_dir.glob("*.yml")) + sorted(workflow_dir.glob("*.yaml")):
        text = read(path)

        for match in re.findall(_WORKFLOW_INTERPOLATION_RE, text):
            err(f"{rel(path)}: interpolates '{match}' into the script — pass it "
                f"through env: and reference it as a quoted variable instead")

        for tool, pin in _WORKFLOW_PINNED_INSTALLS:
            for line in text.splitlines():
                if tool in line and pin not in line:
                    err(f"{rel(path)}: unpinned install '{tool}' — a silent upstream "
                        f"change is exactly how the scheduled workflows broke")

        crons = re.findall(r"^\s*-\s*cron:", text, re.M)
        if len(crons) > 1:
            err(f"{rel(path)}: {len(crons)} cron entries — GitHub cron has no DST "
                f"awareness, so 'winter' and 'summer' lines both fire every day")

        for token in re.findall(r"git add ([^\n&|;]+)", text):
            for candidate in token.split():
                if _looks_like_path(candidate) and _is_ignored(candidate):
                    err(f"{rel(path)}: 'git add {candidate}' targets a gitignored "
                        f"path — the step can never stage anything")

        for block in re.findall(r"^\s*path:\s*\|?\s*$((?:\n\s+\S[^\n]*)+)", text, re.M):
            for candidate in block.split():
                if _looks_like_path(candidate) and _is_ignored(candidate):
                    err(f"{rel(path)}: uploads gitignored path '{candidate}' as an "
                        f"artifact — that publishes private state")


def _looks_like_path(token: str) -> bool:
    token = token.strip("-\"' ")
    return bool(token) and not token.startswith(("-", "$", "#", "{"))


def check_private_state_not_tracked() -> None:
    try:
        result = subprocess.run(
            [
                "git",
                "ls-files",
                "data/memory/trading_memory.md",
                "data/runs",
                "data/audit",
                "data/state",
                "data/decisions",
                "data/positions.md",
                "docs/strategy.md",
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
    check_plugin_manifest(ROOT / ".claude-plugin" / "plugin.json")
    check_json(ROOT / ".mcp.json")
    check_json(ROOT / ".claude" / "settings.json")
    check_commands()
    check_agents()
    check_skill_mirror()
    check_docs_and_workflows()
    check_workflows()
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

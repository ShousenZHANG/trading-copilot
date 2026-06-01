# ADR-0006: Validate Agent Outputs Before Memory and Final Report Assembly

## Status

Accepted, 2026-05-10.

## Context

Trading Copilot uses markdown artifacts between Claude Code subagents. This keeps the plugin simple, but it also means a prompt drift in Research Manager, Trader, or Portfolio Manager can leak into the final report or the append-only memory log.

Upstream TradingAgents mitigates this with structured-output schemas for its decision agents. Anthropic's financial-services repo mitigates plugin drift with repository checks, schema validation, and clearer read/write responsibility between workers.

## Decision

Portfolio Manager writes only `data/runs/<TICKER>-<DATE>/08-portfolio-decision.md`.

The orchestrator then performs deterministic persistence:

1. `scripts/validate_outputs.py run data/runs/<TICKER>-<DATE>`
2. `scripts/memory.py append --ticker <TICKER> --date <DATE> --decision-file .../08-portfolio-decision.md`
3. `scripts/assemble_report.py --ticker <TICKER> --date <DATE>`

`scripts/check.py` validates repo-level invariants such as command guardrails, model tier assignments, skill mirror drift, workflow MCP setup, and private-state tracking.

## Consequences

- Memory writes now pass through one CLI and a ticker validator.
- Final `/analyze` report layout is deterministic instead of depending on Portfolio Manager free-form output.
- Prompt drift fails early before a user sees a rating or the memory log learns from malformed output.
- The project has one health-check command to run before commits or scheduled automation changes.


# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## What this project is

A **Codex plugin**, not a standalone app. No backend, no build step. The "code" is markdown subagent prompts + slash commands + a thin Python MCP wrapper. The pipeline executes when the user runs a slash command in Codex.

Direct port of [TradingAgents](https://github.com/TauricResearch/TradingAgents) (53k+ stars). Reference source vendored at `reference/TradingAgents/` (gitignored locally but used as design source).

## Two entry points (different cost/depth tradeoff)

| Command | Agents | Cost | Use |
|---------|--------|------|-----|
| `/advise <TICKER>` | 1 Opus super-agent | $0.20-0.50, 5-10 min | Daily decisions, watchlist sanity-checks |
| `/analyze <TICKER>` | 12-agent staged pipeline | $1-3, 30-60 min | High-conviction positions only |

Other commands (`/gold`, `/scan`, `/debate`, `/weekly-review`, `/watchlist`) all build on `/analyze`. See [README.md](README.md) and [docs/methodology.md](docs/methodology.md).

## Pipeline architecture (`/analyze`)

```
Step 0: Setup + RESUME DETECTION (skip completed steps if files >50 bytes exist)
Step 1: 4 analysts PARALLEL (single message, 4 Agent calls) → 01-market.md ... 04-fundamentals.md
Step 2: Bull/Bear debate (alternating, default 1 round) → debate_history.md
Step 3: research-manager (Opus) → 06-research-plan.md
Step 4: trader → 07-trader-proposal.md
Step 5: 3-way risk debate (Aggressive → Conservative → Neutral, fixed order) → risk_debate_history.md
Step 6: portfolio-manager (Opus) → 08-portfolio-decision.md
Step 7: Validate run → append memory via scripts/memory.py → assemble report via scripts/assemble_report.py
```

Critical invariants when editing the pipeline:
- **Resume contract** — Step 0 in [analyze.md](.claude/commands/analyze.md) defines the file→skip table. Adding a step requires updating the resume table.
- **Step 1 is parallel by design** — analysts have no inter-dependencies. Don't serialize. On rate-limit fall back to serial, log to `_errors.md`.
- **Steps 2-6 are strictly sequential** — each reads prior output.
- **For `/gold`**: replace `fundamentals-analyst` with `macro-analyst` (writes `05-macro.md` instead of `04-fundamentals.md`). Everything else identical.

## Model tier assignment (cost)

- **Opus** (`model: opus` in agent frontmatter): only `research-manager`, `portfolio-manager`, `investment-advisor`. The 2 deciders + the single-shot advisor.
- **Sonnet**: 9 analysts/researchers/trader/risk-debators (default).
- **Haiku**: reflection summarization in `/weekly-review` only.

Don't promote a Sonnet agent to Opus without considering 2-3× cost increase per run.

## Rating scale discipline (DO NOT MIX)

- Research Manager + Portfolio Manager: **5-tier** (`Buy / Overweight / Hold / Underweight / Sell`).
- Trader: **3-tier** (`Buy / Hold / Sell`).
- `investment-advisor` (`/advise`): own scale (`Strong Buy / Buy / Hold / Reduce / Avoid`).

Mapping in [SKILL.md](.claude/skills/trading-copilot/SKILL.md): 5-tier `Buy`/`Overweight` → 3-tier `Buy`; etc.

## Language protocol

Single source of truth: [.claude/config/output-language.md](.claude/config/output-language.md). Edit that file to switch project-wide language.

- **Internal debates** (Bull/Bear/Risk debators): **English** — TradingAgents found reasoning quality degrades in non-English. Non-negotiable.
- **User-facing reports** (analyst reports, RM rationale, Trader reasoning, PM thesis, final decision): follows `output-language.md` (current: **Chinese 中文**).
- **Always preserve in English regardless of report language**: ticker symbols (incl. exchange suffixes `.HK` `.T` `.AX` `=F` `=X`), indicator names (RSI/MACD/ATR), price numbers, FRED series IDs.

## Memory log — append-only, never edit by hand

`data/memory/trading_memory.md` is gitignored, append-only, managed by `scripts/memory.py` and the slash commands.

Two-phase resolution:
- **Phase A**: Portfolio Manager appends `[date | ticker | rating | pending]` block per run.
- **Phase B (T+5 days)**: `/weekly-review` pulls actual returns from yfinance, computes raw + alpha vs SPY, runs Haiku reflection prompt, atomically replaces pending tag with `[date | ticker | rating | raw% | alpha% | Nd]` and appends `REFLECTION:` section.

Hard delimiter: `<!-- ENTRY_END -->` HTML comment. LLM output cannot accidentally produce this.

`scripts/memory.py` CLI:
```bash
python scripts/memory.py list-pending
python scripts/memory.py append --ticker NVDA --date 2026-04-27 --rating Buy --decision-file path/to/decision.md
python scripts/memory.py past-context --ticker NVDA [--n-same 5] [--n-cross 3]
python scripts/memory.py resolve --ticker NVDA --date 2026-04-27 --raw 0.052 --alpha 0.018 --days 5 --reflection "..."
```

Only `scripts/memory.py` should mutate `data/memory/trading_memory.md`. Portfolio Manager writes `08-portfolio-decision.md`; the orchestrator validates the run with `scripts/validate_outputs.py` before appending memory or assembling `data/decisions/<TICKER>-<DATE>.md`.

## MCP servers

Configured in [.mcp.json](.mcp.json). **Only servers without `_` prefix are active.** All others ship disabled.

Currently active: `yahoo-finance`, `finnhub`. Disabled-by-default: `_polygon`, `_alpha-vantage`, `_fred`, `_gold`, `_exa`, `_claude-mem`.

Toggle:
```bash
python scripts/enable_mcp.py                  # list state
python scripts/enable_mcp.py fred             # enable (strips _ prefix)
python scripts/enable_mcp.py polygon --disable
```

**Custom Finnhub MCP** at [mcps/finnhub_mcp.py](mcps/finnhub_mcp.py) — replaces the broken npm `finnhub-mcp` (Windows path bug). Run via `uv run --no-project --quiet --script`. Don't replace with the npm version.

API keys live in `.env` (gitignored). On Windows, launch via [scripts/start.ps1](scripts/start.ps1) so it loads `.env` into the PowerShell session before invoking `Codex` (MCPs read keys via `${VAR}` substitution in `.mcp.json`).

## Pre-trade risk gate (Portfolio Manager enforces)

Before issuing **Buy** or **Overweight**, all checks must pass — failure → automatic downgrade with explicit reason:

| Check | Threshold |
|-------|-----------|
| Single-name concentration | ≤ 5% portfolio |
| Sector concentration | ≤ 25% |
| Correlation to existing book | < 0.7 |
| Position size vs ADV | ≤ 1% |
| Data freshness | ≤ 24h for daily horizon |
| Stop-loss is set | required |
| Portfolio max-drawdown | not in > 15% DD |

`investment-advisor` has its own simpler gate (data freshness, analyst spread, catalyst window, 52W-high zone, insider net selling).

## Data freshness — anti-stale rule

`market-analyst` (and `investment-advisor`) MUST verify the last bar's date against today and downgrade to "data unreliable" if gap > 7 days. Never use `period='1y'` or `period='max'` on `get_historical_stock_prices` — they may return slow datasets ending at outdated dates. Use `period='3mo'` for tactical view.

## Per-run filesystem layout

```
data/runs/<TICKER>-<YYYY-MM-DD>/
├── 00-past-context.md           # extracted memory log slice
├── 01-market.md
├── 02-social.md
├── 03-news.md
├── 04-fundamentals.md           # OR 05-macro.md for /gold
├── debate_history.md            # Bull/Bear transcript
├── 06-research-plan.md
├── 07-trader-proposal.md
├── risk_debate_history.md       # 3-way transcript
└── 08-portfolio-decision.md

data/decisions/<TICKER>-<YYYY-MM-DD>.md   ← user-facing assembled report
data/memory/trading_memory.md             ← appended pending entry
```

`data/runs/`, `data/positions.md`, `data/memory/trading_memory.md` all gitignored (personal trading state).

## Personal trading state — never commit

`.gitignore` blocks `data/positions.md`, `data/runs/`, `data/memory/trading_memory.md`, and `data/audit/`. If you ever stage these accidentally, abort the commit. Same applies to `.env`, `evals/results/`, `.codex/settings.local.json`.

## Common operations

```bash
# Toggle MCPs
python scripts/enable_mcp.py [name] [--disable]

# Memory log inspection
python scripts/memory.py list-pending

# Append PM decision (rating auto-parsed if --rating omitted)
python scripts/memory.py append --ticker NVDA --date 2026-04-27 --decision-file data/runs/NVDA-2026-04-27/08-portfolio-decision.md

# Parse rating from any markdown file (defensive against PM prompt drift)
python scripts/parse_rating.py data/runs/NVDA-2026-04-27/08-portfolio-decision.md

# Validate PM output against required schema (Rating + Executive Summary + Investment Thesis)
python scripts/validate_pm_output.py data/runs/NVDA-2026-04-27/08-portfolio-decision.md

# Watch a /analyze run progress (live tailing)
.\scripts\monitor.ps1 NVDA           # PowerShell on Windows

# Launch Codex with .env loaded (Windows)
.\scripts\start.ps1
```

## Resilience scripts

- [scripts/parse_rating.py](scripts/parse_rating.py) — deterministic 5-tier rating parser. Two-pass: explicit `Rating: X` label, then any 5-tier word. Default `Hold`. Used by `scripts/memory.py append` to validate ratings. Direct port of upstream `agents/utils/rating.py`.
- [scripts/validate_pm_output.py](scripts/validate_pm_output.py) — verify PM markdown has required fields (`**Rating**`, `**Executive Summary**`, `**Investment Thesis**`) before downstream consumers parse it. Catches prompt drift early.
- [scripts/validate_outputs.py](scripts/validate_outputs.py) — validates Research Manager, Trader, Portfolio Manager, `/advise`, and full run artifacts before final report assembly.
- [scripts/assemble_report.py](scripts/assemble_report.py) — deterministic `/analyze` report assembler; keeps final report layout out of free-form PM output.
- [scripts/check.py](scripts/check.py) — repository health check for manifests, command/agent prompt drift, model tiers, private-state tracking, and workflow guardrails.
- [docs/mcp-fallback.md](docs/mcp-fallback.md) — fallback playbook when MCP servers fail (yahoo-finance → finnhub → WebFetch chain, etc.). Agents (especially `investment-advisor`) should follow this.

## When NOT to use the pipeline

The skill description in [.claude/skills/trading-copilot/SKILL.md](.claude/skills/trading-copilot/SKILL.md) is explicit:

> Do **not** invoke for general market commentary or news questions — those don't need the full pipeline. Just the slash commands trigger the pipeline.

If the user asks a one-off "what's NVDA doing?" question, answer directly from MCP tools — don't run `/analyze`.

## Disclaimer footer (always append to user-facing reports)

> ⚠️ Educational and informational use only. Not investment advice. See [DISCLAIMER.md](./DISCLAIMER.md) for full terms.

## Reference

- [README.md](README.md) — quick start, pipeline diagram, MCP table
- [docs/methodology.md](docs/methodology.md) — core beliefs, why-we-do-this rationale, anti-patterns
- [docs/tradingagents-deep-dive.md](docs/tradingagents-deep-dive.md) — upstream architecture analysis
- [.claude/skills/trading-copilot/SKILL.md](.claude/skills/trading-copilot/SKILL.md) — methodology reference loaded into agent context
- [evals/README.md](evals/README.md) — eval harness scaffold (FinanceBench + StockBench)
- `docs/strategy.md` — your personal strategy (gitignored; create your own, not shipped). Read before `/advise` / `/scan` if present.
- [docs/adr/](docs/adr/) — software architecture decisions

## Agent skills

### Issue tracker

GitHub Issues at `ShousenZHANG/trading-copilot` via `gh` CLI. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary (`needs-triage` / `needs-info` / `ready-for-agent` / `ready-for-human` / `wontfix`). See `docs/agents/triage-labels.md`.

### Domain docs

Single-context: `CONTEXT.md` + `docs/adr/` at repo root. See `docs/agents/domain.md`.

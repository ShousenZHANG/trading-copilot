---
description: Quarterly earnings update for a single ticker. Pulls latest 10-Q + transcript + consensus, computes beat/miss vs estimates, updates thesis. Use after a covered name reports. Direct port of financial-services /earnings pattern, adapted for free-tier MCPs.
argument-hint: <TICKER> [--quarter=Q1-FY27]
---

# /earnings — Quarterly Earnings Update

Run a focused earnings update on `$ARGUMENTS`. Faster + narrower than `/analyze` — only relevant when a quarterly result has just dropped.

> ⚠️ **Status: graduation-phase command.** Until graduation gates pass (see [docs/strategy.md](../../docs/strategy.md), capital ≥$30k AUD), the user holds 100% ETFs. This command only matters when buying individual stocks. Stub kept for future activation.

## When to use

- Company reported within the last 7 days, you have or are considering a position
- The Q result was a meaningful beat or miss vs consensus
- Updated forward guidance shifts your thesis

## Difference vs `/analyze`

| `/analyze` | `/earnings` |
|------------|-------------|
| 13-agent full debate (Bull/Bear + 3-way risk + PM) | 1 specialist agent (earnings-reviewer), no debate |
| 30-60 min, $1-3 | 8-15 min, $0.30-0.80 |
| Generates new investment thesis | Updates existing thesis with new quarterly data |
| Produces full decision report | Produces earnings note with delta-from-prior |

## Args

- **`$ARGUMENTS`**: first token is ticker (preserve exchange suffix)
- `--quarter=Q1-FY27` (optional): override which quarter to analyze. If omitted, agent infers most-recent reported quarter.

## Execution

### Step 1: Resolve + freshness check

1. Parse ticker + quarter
2. Today's date from system clock
3. Verify `mcp__finnhub__get_earnings_surprise` confirms the quarter exists
4. If most recent earnings >7 days old, ask user "do you really want a stale earnings update? Run `/analyze` instead"

### Step 2: Single dispatch — `earnings-reviewer` subagent

Run brief:

```
Ticker: <TICKER>
Date: <YYYY-MM-DD>
Quarter: <Q1-FY27 or whatever was inferred>
Output path: data/runs/<TICKER>-<DATE>/09-earnings-update.md
User context: AU retail investor in graduation phase, satellite position size 5% of portfolio max.
```

The agent:
1. Fetches `mcp__finnhub__get_earnings_surprise` → consensus EPS / Rev vs actual
2. Fetches `mcp__finnhub__get_company_news` (last 7 days) → call commentary, segment color
3. Fetches `mcp__yahoo-finance__get_yahoo_finance_news` → analyst reaction notes
4. Fetches `mcp__yahoo-finance__get_recommendations` → post-earnings target / rating revisions
5. Computes: beat/miss magnitudes, segment surprises, guidance delta vs prior, margin direction
6. Writes earnings update report (3-6 sections, 1-2 KB) — NOT a full /analyze report

### Step 3: Update memory + assemble

```bash
python scripts/validate_outputs.py earnings data/runs/<TICKER>-<DATE>/09-earnings-update.md
python scripts/memory.py append --ticker <TICKER> --date <DATE> --decision-file data/runs/<TICKER>-<DATE>/09-earnings-update.md
```

(Note: validator extension for `kind=earnings` is **TBD** — see [TODO](#TODO).)

### Step 4: Reply to user

- Headline: beat/miss + magnitude + thesis-impact verdict (e.g. "thesis intact" / "thesis weakening" / "rerate up")
- 1 sentence on most consequential item
- Path to full update

## TODO before activation

- [ ] Build `.claude/agents/specialists/earnings-reviewer.md` prompt (modeled on financial-services `earnings-reviewer.md`)
- [ ] Add `validate_earnings_update(text)` to `scripts/validate_outputs.py`
- [ ] Add `09-earnings-update.md` to assembler if user wants to surface in main report
- [ ] Decide: does `/earnings` after a `/analyze` supersede the prior PM rating? Probably yes, with explicit "supersedes 2026-04-27 Buy → updated to Hold post-Q1 miss" annotation in memory log.

## Source

Direct port of [`anthropic/financial-services/plugins/vertical-plugins/equity-research/commands/earnings.md`](https://github.com/anthropics/financial-services/blob/main/plugins/vertical-plugins/equity-research/commands/earnings.md). Adapted: removed DOCX output (markdown only), replaced Bloomberg/FactSet with Finnhub free tier.

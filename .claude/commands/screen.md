---
description: Run a quantitative screen against the watchlist before dispatching `/analyze`. Saves cost when watchlist is large (>10 tickers) by pre-filtering on value/growth/quality criteria. Direct port of financial-services /screen + idea-generation skill, adapted for free-tier MCPs.
argument-hint: <value|growth|quality|short|special> [--top=5]
---

# /screen — Pre-filter watchlist by quantitative criteria

> ⚠️ **Status: stub.** Activate when watchlist grows beyond ~10 tickers OR when the user wants to surface ideas outside the current watchlist. For 6-ticker mega-cap watchlist (current state), `/scan` is fine.

## When to use

- Watchlist has >10 tickers and `/scan` cost would exceed $5
- User wants thematic idea generation ("find me cheap quality") without specifying tickers
- Pre-position research before a `/analyze` run on a single name

## Args

- **`$ARGUMENTS`** first token = screen template:
  - `value` — P/E < sector median, EV/EBITDA < 5y avg, FCF yield > 5%, P/B < 1.5x
  - `growth` — Revenue YoY > 15%, EPS YoY > 20%, expanding margins, ROIC > 15%
  - `quality` — 5y revenue stability, ROE > 15%, low debt/equity, high FCF conversion, insider ownership > 5%
  - `short` — declining revenue, margin compression, rising AR/inventory, insider selling
  - `special` — recent IPOs near lockup, spin-offs <12mo, activist involvement, management changes
- `--top=N` (default 5) — number of names to surface

## Execution

### Step 1: Define universe

Use `data/watchlist.md` as default universe. Optionally override with explicit ticker list passed inline.

### Step 2: Quantitative filter (cheap first pass)

For each ticker in universe, fetch via Finnhub `get_basic_financials` + Yahoo `get_stock_info`:
- P/E TTM, Forward P/E, EV/EBITDA, P/B
- Revenue / EPS YoY growth
- ROE, ROIC, debt/equity, FCF margin
- Insider trades last 90 days (Finnhub `get_insider_transactions`)

Apply selected screen template. Drop tickers that fail.

**Cost**: 4-6 MCP calls per ticker × N tickers. ~$0.05-0.10 per ticker (data calls free, LLM token cost minimal).

### Step 3: Rank survivors

Sort by:
- **value**: composite z-score across P/E, EV/EBITDA, FCF yield (lower = better rank)
- **growth**: composite revenue + EPS growth, weighted by margin trend
- **quality**: composite ROE + FCF conversion + revenue stability
- **short**: inverse — composite "deterioration score"
- **special**: chronological by event date (most recent first)

Take top N (default 5).

### Step 4: Output

Write `data/decisions/_screen-<KIND>-<DATE>.md`:

```markdown
# Watchlist Screen: <KIND> — <DATE>

> Universe: <N> tickers from data/watchlist.md
> Survivors: <K> after filter, top <top> below

| Rank | Ticker | Composite | Key Metric | Trigger |
|------|--------|-----------|-----------|---------|
| 1 | NVDA | -1.8z | Forward P/E 25x vs peers 32x | value screen |
| 2 | ... | ... | ... | ... |

## Suggested next actions
- Top 1 (NVDA): consider `/analyze NVDA` for deep dive
- Top 2-3: consider `/advise <TICKER>` for fast read
- Bottom of list: deferred / re-run screen next month
```

### Step 5: Reply

- Top-N table inline (5 rows max)
- Path to screen file
- 1-line suggestion: "ready to `/analyze` top <ticker>?"

## TODO before activation

- [ ] Build `idea-generation` skill at `.claude/skills/idea-generation/SKILL.md` with screen templates above
- [ ] Build `screen-runner` agent (Sonnet, with finnhub + yahoo tools)
- [ ] Add `kind=screen` to `validate_outputs.py`
- [ ] Decide: does `/screen` write to memory log? Probably no — paper-trade is `/scan`'s job.

## Source

Pattern from [`anthropic/financial-services/plugins/vertical-plugins/equity-research/commands/screen.md`](https://github.com/anthropics/financial-services/blob/main/plugins/vertical-plugins/equity-research/commands/screen.md) + `idea-generation/SKILL.md`. Adapted: free-tier Finnhub instead of CapIQ, simplified composite scoring (z-score not full quant model).

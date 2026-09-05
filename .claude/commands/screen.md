---
description: "[inactive · 未激活] Quantitative pre-filter over the watchlist — NOT IMPLEMENTED. The screen agent, the templates skill, and the validator kind it needs do not exist yet; invoking it does nothing. Kept as the activation spec. Use /scan to run the watchlist today."
argument-hint: <value|growth|quality|short|special> [--top=5]
---

# /screen — Pre-filter watchlist by quantitative criteria (not implemented)

> ⛔ **This command is a specification, not a feature.** There is no
> `screen-runner` agent, no `idea-generation` skill holding the templates below,
> and `validate_outputs.py` has no `screen` kind. Running it would fail on its
> first scripted step. Nothing below is executed.
>
> **What to run instead, today**: `/scan` runs the pipeline across the whole
> watchlist; `/advise <TICKER>` is the cheap single-name read.

> **Note on the activation threshold**: the old text here claimed a "6-ticker
> watchlist (current state)". The shipped `data/watchlist.md` has **11 active
> tickers** (6 US equities, 2 commodities, 3 macro proxies; the ASX and A-share
> blocks are commented out). The ">10 tickers" trigger this command was going to
> wait for is therefore already met — implementation, not demand, is what is
> missing.

## What it would do (design intent)

Rank the watchlist against one of five quantitative templates before spending
`/analyze` money on any of it, and surface the top N names with the metric that
put them there.

## When it would be worth using

- Watchlist is large enough that a full `/scan` costs more than the screen saves
- Thematic idea generation ("find me cheap quality") without naming tickers first
- Pre-positioning research before a single-name `/analyze`

## Args (design intent)

- **`$ARGUMENTS`** first token = screen template:
  - `value` — P/E < sector median, EV/EBITDA < 5y avg, FCF yield > 5%, P/B < 1.5x
  - `growth` — Revenue YoY > 15%, EPS YoY > 20%, expanding margins, ROIC > 15%
  - `quality` — 5y revenue stability, ROE > 15%, low debt/equity, high FCF conversion, insider ownership > 5%
  - `short` — declining revenue, margin compression, rising AR/inventory, insider selling
  - `special` — recent IPOs near lockup, spin-offs <12mo, activist involvement, management changes
- `--top=N` (default 5) — number of names to surface

## Intended ranking

- **value**: composite z-score across P/E, EV/EBITDA, FCF yield (lower = better rank)
- **growth**: composite revenue + EPS growth, weighted by margin trend
- **quality**: composite ROE + FCF conversion + revenue stability
- **short**: inverse — composite "deterioration score"
- **special**: chronological by event date (most recent first)

## Intended output

A screen file under `data/decisions/` holding the universe size, the survivor
count, a top-N table (rank, ticker, composite, key metric, trigger) and a
suggested-next-action line per survivor. A screen is **evidence, not a decision**:
it never writes to the memory log. See
[ADR-0002](../../docs/adr/0002-local-scheduling-and-evidence-only-stubs.md).

## TODO before activation

- [ ] Build `idea-generation` skill at `.claude/skills/idea-generation/SKILL.md` with the screen templates above
- [ ] Build `screen-runner` agent (Sonnet), granting the finnhub + yahoo-finance servers in its `tools:` allowlist
- [ ] Add `kind=screen` to `validate_outputs.py`
- [ ] Confirm the no-memory-write rule stays: paper-trading the watchlist is `/scan`'s job
- [ ] Drop the `[inactive · 未激活]` prefix from `description:` and restore an Execution section only once all of the above exist

## Output language

User-facing output follows [.claude/config/output-language.md](../config/output-language.md)
(currently Chinese 中文). Ticker symbols, metric names and numbers stay in English.

## Source

Pattern from [`anthropic/financial-services/plugins/vertical-plugins/equity-research/commands/screen.md`](https://github.com/anthropics/financial-services/blob/main/plugins/vertical-plugins/equity-research/commands/screen.md) + `idea-generation/SKILL.md`. Adapted: free-tier Finnhub instead of CapIQ, simplified composite scoring (z-score not a full quant model).

> ⚠️ Educational use only. Not investment advice. See [DISCLAIMER.md](../../DISCLAIMER.md).

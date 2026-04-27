---
description: Sunday deep portfolio review with Opus. Resolves pending memory entries (T+5d outcomes), summarizes performance, surfaces lessons, and proposes adjustments.
argument-hint: [--no-resolve]
---

Run the weekly portfolio review.

## Steps

### 1. Resolve pending memory entries (skipped if `--no-resolve` in `$ARGUMENTS`)

For each `pending` entry in `data/memory/trading_memory.md`:
1. Check if `entry.date + 5 calendar days >= today`. If not, leave as pending (price data not yet meaningful).
2. Use `mcp__yahoo-finance` (or any market-data MCP) to fetch:
   - Close price of `entry.ticker` on `entry.date` and on `entry.date + 5 trading days`
   - Same for SPY (alpha calculation)
3. Compute `raw_return` and `alpha = stock_return - spy_return`.
4. Dispatch a Haiku-tier reflection: prompt is in `docs/prompts/reflection.md` (or inline below):

> You are a trading analyst reviewing your own past decision now that the outcome is known.
> Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).
>
> Cover in order:
> 1. Was the directional call correct? (cite the alpha figure)
> 2. Which part of the investment thesis held or failed?
> 3. One concrete lesson to apply to the next similar analysis.
>
> Be specific and terse. Your output will be stored verbatim in a decision log
> and re-read by future analysts, so every word must earn its place.
>
> Inputs:
> Raw return: <X.X%>
> Alpha vs SPY: <X.X%>
> Final Decision: <decision text from log>

5. Atomically replace the `[date | ticker | rating | pending]` tag with `[date | ticker | rating | raw% | alpha% | Nd]` and append `REFLECTION:\n<reflection text>` before the `<!-- ENTRY_END -->` marker.

### 2. Performance summary

Read all resolved entries from `data/memory/trading_memory.md` for the past 30 days. Compute:

- Total decisions
- Hit rate (% of decisions where alpha > 0)
- Mean alpha
- Best call, worst call (cite ticker + alpha)
- Cumulative raw + alpha (assume equal-weight 5-day holds)

### 3. Lessons synthesis

Use **Opus** to read the past 30 days of resolved entries (DECISION + REFLECTION) and synthesize:
- Top 3 patterns of success
- Top 3 patterns of failure  
- 1-2 concrete protocol changes to consider

### 4. Position review

Read `data/positions.md`. For each open position:
- Pull current price
- Compute unrealized P&L
- Flag positions where the current state diverges from the original thesis (use Opus to read past decisions on this ticker)

### 5. Output

Write `data/decisions/_weekly-<YYYY-MM-DD>.md`:

```markdown
# Weekly Review — week of <YYYY-MM-DD>

## Resolved this week
<N entries resolved, list ticker | rating | alpha>

## Performance (trailing 30d)
- Decisions: <N>
- Hit rate: <X%>
- Mean alpha: <+/-X.X%>
- Best: <TICKER> (+X.X%)
- Worst: <TICKER> (-X.X%)

## Lessons (Opus synthesis)
### Patterns of success
1. ...

### Patterns of failure
1. ...

### Protocol changes to consider
1. ...

## Position Review
| Ticker | Entry | Current | P&L | Thesis status | Action? |
|--------|-------|---------|-----|---------------|---------|

⚠️ 教育用途, 非投资建议.
```

### 6. Reply to user

Show:
- Performance one-liner
- Top 1-2 lessons
- Any flagged positions
- Path to the full review

If the schedule skill is available, **offer to /schedule** this command to run every Sunday 6pm.

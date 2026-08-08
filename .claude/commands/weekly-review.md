---
description: Sunday deep portfolio review with Opus. Resolves pending memory entries (T+5d outcomes), summarizes performance, surfaces lessons, and proposes adjustments.
argument-hint: [--no-resolve]
---

Run the weekly portfolio review.

## Steps

### 1. Resolve pending memory entries (skipped if `--no-resolve` in `$ARGUMENTS`)

For each `pending` entry in `data/memory/trading_memory.md`:
1. Check if `entry.date + 5 calendar days >= today`. If not, leave as pending (price data not yet meaningful).
2. **Derive the benchmark from the listing venue — never assume SPY.** Alpha vs SPY is only correct for US listings. An ASX or HKEX name measured against the S&P 500 is not alpha at all: it is alpha plus an unhedged FX move plus a market mismatch. Resolve it deterministically:

   ```bash
   python scripts/benchmarks.py --ticker NDQ.AX     # -> ^AXJO
   python scripts/benchmarks.py --ticker 0700.HK    # -> ^HSI
   python scripts/benchmarks.py --ticker NVDA       # -> SPY
   ```

   Mapping lives in [scripts/benchmarks.py](../../scripts/benchmarks.py): `.AX`→`^AXJO`, `.HK`→`^HSI`, `.T`→`^N225`, `.L`→`^FTSE`, `.TO`→`^GSPTSE`, `.NS`→`^NSEI`, `.BO`→`^BSESN`, `.SS`→`000001.SS`, `.SZ`→`399001.SZ`, `.SI`→`^STI`, `.KS`→`^KS11`, `.NZ`→`^NZ50`, `.DE`→`^GDAXI`, `.PA`→`^FCHI`, `.SW`→`^SSMI`; anything else falls back to `SPY`.

   **Commodities / FX / indices (`GC=F`, `XAUUSD=X`, `^GSPC`) have no natural equity benchmark.** `alpha_meaningful=False` for these — gold's "alpha vs the S&P 500" is a category error. Report the **raw return** as the number that matters and record alpha as `n/a`; do not let it drive the hit-rate stat.

3. Use `mcp__yahoo-finance` (or any market-data MCP) to fetch:
   - Close price of `entry.ticker` on `entry.date` and on `entry.date + 5 trading days`
   - Same two closes for the **derived benchmark** from step 2
4. Compute `raw_return` and `alpha = stock_return - benchmark_return`.
5. Dispatch a Haiku-tier reflection: prompt is in `docs/prompts/reflection.md` (or inline below):

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
> Alpha vs <BENCHMARK>: <X.X%>     (name the actual benchmark, e.g. "Alpha vs ^AXJO"; for commodities/FX write "Alpha: n/a — no equity benchmark" and reason from the raw return)
> Final Decision: <decision text from log>

6. Write the outcome through `scripts/memory.py` — never hand-edit the log:

   ```bash
   python scripts/memory.py resolve --ticker NDQ.AX --date 2026-08-01 \
     --raw 0.052 --alpha 0.018 --days 5 --reflection "..."
   # --benchmark is optional; omitted -> derived from the exchange suffix.
   # Pass --benchmark explicitly only to override (e.g. a sector-ETF comparison).
   ```

   This atomically replaces the `[date | ticker | rating | pending]` tag with `[date | ticker | rating | raw% | alpha% | Nd]`, records a `BENCHMARK: <ticker> (<reason>)` line in the entry body so the alpha figure stays auditable, and appends `REFLECTION:\n<reflection text>` before the `<!-- ENTRY_END -->` marker. The 6-field header shape is unchanged, so legacy entries keep parsing.

   **Entries with no `BENCHMARK:` line are pre-fix and were all resolved against SPY** regardless of listing venue. Treat their alpha as unreliable for any non-US ticker, and say so if you cite one.

### 2. Performance summary

Read all resolved entries from `data/memory/trading_memory.md` for the past 30 days. Compute:

- Total decisions
- Hit rate (% of decisions where alpha > 0) — exclude commodity/FX entries, whose alpha is not meaningful
- Mean alpha
- Best call, worst call (cite ticker + alpha + the benchmark it was measured against)
- Cumulative raw + alpha (assume equal-weight 5-day holds)

Alpha figures are relative to **each entry's own benchmark**, not a single index. Always name the benchmark next to the number; a bare "+1.8% alpha" is ambiguous across a mixed US/ASX/HK book. Legacy entries without a `BENCHMARK:` line were measured against SPY — flag non-US ones as unreliable rather than averaging them in silently.

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

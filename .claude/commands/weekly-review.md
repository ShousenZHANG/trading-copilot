---
description: Sunday deep portfolio review with Opus. Resolves pending memory entries (T+5d outcomes), summarizes performance, surfaces lessons, and proposes adjustments.
argument-hint: [--no-resolve]
---

Run the weekly portfolio review.

## Steps

### 1. Resolve pending memory entries (skipped if `--no-resolve` in `$ARGUMENTS`)

**Do not eyeball eligibility or do the arithmetic by hand.** Everything except the reflection prose is deterministic and lives in `scripts/memory.py resolve-pending`. Eligibility is a *bar count*, not a calendar comparison: an entry resolves once its ticker has at least `--holding-days` price bars after the entry bar. (The old prose here tested `entry.date + 5 calendar days >= today`, which is true exactly when T+5 has **not** arrived yet — it resolved everything too early.)

**1a. Build the price map** (skip if you already have a fresh one):

```bash
python scripts/prices.py --tickers NDQ.AX,^AXJO,GC=F,NVDA,SPY \
  --start 2026-07-01 --end 2026-09-05 --out evals/prices/prices.json
```

Include each pending ticker **and** its benchmark. Resolve benchmarks with `python scripts/benchmarks.py --ticker <TICKER>` — the mapping is `.AX`→`^AXJO`, `.HK`→`^HSI`, `.T`→`^N225`, `.L`→`^FTSE`, `.TO`→`^GSPTSE`, `.NS`→`^NSEI`, `.BO`→`^BSESN`, `.SS`→`000001.SS`, `.SZ`→`399001.SZ`, `.SI`→`^STI`, `.KS`→`^KS11`, `.NZ`→`^NZ50`, `.DE`→`^GDAXI`, `.PA`→`^FCHI`, `.SW`→`^SSMI`; anything else falls back to `SPY`. **Never assume SPY** — an ASX or HKEX name measured against the S&P 500 is not alpha at all, it is alpha plus an unhedged FX move plus a market mismatch.

The same JSON file feeds `evals/stockbench/runner.py --replay`, so one fetch serves both the review and the backtest.

**1b. Dry-run first — always:**

```bash
python scripts/memory.py resolve-pending --prices evals/prices/prices.json --dry-run
```

It prints one row per eligible entry (`TICKER · DATE · RATING · WINDOW · RAW · ALPHA · BENCHMARK`), sends every ineligible entry to stderr with the reason (insufficient tail bars, benchmark series missing, no bar on/after the decision date), writes **nothing**, and then prints a ready-to-run `resolve` command per entry. Add `--as-of YYYY-MM-DD` to replay a past review date, `--holding-days N` for a non-5-day window, or `--ticker`/`--date` to scope to one entry.

Alpha prints as `n/a` for commodities / FX / indices (`GC=F`, `XAUUSD=X`, `^GSPC`) — gold's "alpha vs the S&P 500" is a category error. For those, the **raw return** is the number that matters, and they must not drive the hit-rate stat.

**1c. Generate the reflection.** For each row, dispatch a Haiku-tier reflection using the prompt in [docs/prompts/reflection.md](../../docs/prompts/reflection.md). That file is the single copy — do not restate the prompt here. Feed it the raw return, the alpha **with the benchmark named** (or the commodity/FX branch when alpha is `n/a`), and the `DECISION:` text from the log.

**1d. Write the outcome** with the command the dry run printed — never hand-edit the log:

```bash
python scripts/memory.py resolve --ticker NDQ.AX --date 2026-08-01 \
  --raw 0.0520 --alpha 0.0180 --days 5 --reflection "<Haiku reflection>"

# Commodity / FX / index: omit --alpha entirely; the tag records n/a.
python scripts/memory.py resolve --ticker GC=F --date 2026-08-01 \
  --raw 0.0310 --days 5 --reflection "<Haiku reflection>"
```

`--alpha` is now optional and guarded: passing it for `GC=F` exits 2 (override with `--force-alpha`), and omitting it for an equity exits 2. `--benchmark` is optional too — omitted, it is derived from the exchange suffix; pass it only to override (e.g. a sector-ETF comparison).

This atomically replaces the `[date | ticker | rating | pending]` tag with `[date | ticker | rating | raw% | alpha% | Nd]` (`alpha%` may be `n/a`), records a `BENCHMARK: <ticker> (<reason>)` line in the entry body so the alpha figure stays auditable, and appends `REFLECTION:\n<reflection text>` before the `<!-- ENTRY_END -->` marker. The 6-field header shape is unchanged, so legacy entries keep parsing.

**Entries with no `BENCHMARK:` line are pre-fix and were all resolved against SPY** regardless of listing venue. Treat their alpha as unreliable for any non-US ticker, and say so if you cite one.

If you genuinely cannot produce reflections this session, `resolve-pending --reflection-placeholder` closes the arithmetic and stores a greppable stub instead. Without either `--dry-run` or `--reflection-placeholder` the command refuses to write — it will not invent a retrospective.

### 2. Performance summary

Read all resolved entries from `data/memory/trading_memory.md` for the past 30 days. Compute:

- Total decisions
- Hit rate (% of decisions where alpha > 0) — exclude every entry whose alpha field reads `n/a` (commodities / FX / indices), whose alpha is not meaningful
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

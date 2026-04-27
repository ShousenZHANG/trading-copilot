---
description: Run the full pipeline on every ticker in data/watchlist.md. Skip tickers analyzed in the last 24 hours. Output a summary table + push high-conviction calls.
argument-hint: [--force] [--filter=tag1,tag2]
---

Scan the entire watchlist by running `/analyze` on each ticker.

## Steps

1. **Read watchlist**: parse `data/watchlist.md`. Each non-comment line has format `TICKER | tag(s) | note`. Extract tickers.

2. **Filter**:
   - If `--filter=tagX,tagY` is in `$ARGUMENTS`, keep only tickers with at least one matching tag.
   - Otherwise process all.

3. **Skip-recent rule** (unless `--force`):
   - For each ticker, check `data/decisions/<TICKER>-<TODAY>.md` — if exists, skip (already analyzed today).
   - Also check the most recent `data/decisions/<TICKER>-*.md` — if its mtime < 24h ago, skip.

4. **Run pipeline per ticker** (sequential, not parallel — to respect MCP rate limits):
   - For each ticker not skipped, run the same logic as `/analyze TICKER`.
   - For gold tickers (`GC=F`, `XAUUSD=X`), use the `/gold` variant (macro-analyst instead of fundamentals).

5. **Aggregate output**: write `data/decisions/_scan-<DATE>.md`:

```markdown
# Watchlist Scan — <DATE>

> Scanned <N> tickers. Skipped <M> (recent). New decisions <K>.

| Ticker | Rating | Conviction | Price target | Time horizon | Report |
|--------|--------|-----------|--------------|--------------|--------|
| NVDA   | Buy    | high      | $1100        | 3-6m         | [link](NVDA-2026-04-27.md) |
| ...    | ...    | ...       | ...          | ...          | ...    |

## Highest-conviction calls (rating=Buy or Sell, ranked by Portfolio Manager confidence)

1. <TICKER> - <one-line thesis> -> [full](TICKER-DATE.md)
2. ...

## All Decisions
<table of every result row>

---
⚠️ 教育用途, 非投资建议.
```

6. **Reply to user**:
   - Show the highest-conviction calls (max 5 lines)
   - Show the path to `_scan-<DATE>.md`
   - If the schedule skill is available, **offer to schedule** a daily 8:30am ET run via GitHub Actions (Phase 6).

## Notes

- Estimated runtime: ~2-5 min per ticker depending on MCP latency. For a 10-ticker watchlist, expect 20-50 min.
- For long scans, write progress to `data/runs/_scan-<DATE>/progress.md` so the user can monitor.
- **Cost guardrail**: if estimated cost > $5 (rough estimate: ~$0.50/ticker with prompt caching), prompt the user before proceeding.

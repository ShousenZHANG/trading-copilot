# MCP Fallback Playbook

What to do when an MCP server fails mid-pipeline. Ordered by primary → fallback for each data type. Agents (especially `investment-advisor` and analysts) should follow this when a primary MCP returns an error or empty payload.

## Failure modes

| Symptom | Likely cause |
|---|---|
| `MCP server disconnected` system reminder | Local MCP process crashed or was unreachable |
| Empty payload (`{"result": "[]"}`) | Ticker not covered by vendor (e.g. ASX on Finnhub) |
| Rate-limit error (HTTP 429) | Free-tier quota exceeded |
| HTTP 503 / 403 | Vendor incident (Yahoo Finance has periodic outages) |

## Data-type fallback chains

### Stock quote / current price

1. **Primary**: `mcp__yahoo-finance__get_stock_info`
2. **Fallback 1**: `mcp__finnhub__get_quote` (US tickers only — no ASX)
3. **Fallback 2**: `WebFetch` `https://finance.yahoo.com/quote/<TICKER>`
4. **Fallback 3**: `WebFetch` `https://www.google.com/finance/quote/<TICKER>`
5. **All fail**: report `data not available — current price uncertain`. Portfolio Manager downgrades rating per data-freshness gate.

### Historical OHLCV (3mo)

1. **Primary**: `mcp__yahoo-finance__get_historical_stock_prices` with `period='3mo'`
2. **Fallback 1**: `mcp__finnhub__get_basic_financials` (limited)
3. **Fallback 2**: `WebFetch` Yahoo Finance historical chart page (parse table)
4. **All fail**: market-analyst MUST set `data unreliable` banner per agent prompt. Skip technicals, defer to fundamentals/news/macro.

### Company news (past 7d)

1. **Primary**: `mcp__finnhub__get_company_news`
2. **Fallback 1**: `mcp__yahoo-finance__get_yahoo_finance_news`
3. **Fallback 2**: `mcp__exa__web_search_exa` query `<ticker> news last 7 days`
4. **Fallback 3**: `WebFetch` Reuters / Bloomberg / Financial Times search results
5. **All fail**: news-analyst writes `news flow sparse — no actionable headlines` and continues.

### Insider transactions

1. **Primary**: `mcp__finnhub__get_insider_transactions`
2. **Fallback 1**: `mcp__yahoo-finance__get_holder_info` with `holder_type="insider_transactions"`
3. **All fail**: skip insider section (do not fabricate).

### Analyst recommendations / price targets

1. **Primary**: `mcp__yahoo-finance__get_recommendations`
2. **Fallback 1**: `mcp__finnhub__get_price_target`
3. **Fallback 2**: `mcp__finnhub__get_recommendation_trends`
4. **All fail**: omit the consensus line (do not invent a target).

### Sentiment scores

1. **Primary**: `mcp__finnhub__get_news_sentiment`
2. **Fallback 1**: `mcp__exa__web_search_exa` query `<ticker> reddit wallstreetbets stocktwits last 7 days`
3. **All fail**: social-analyst marks sentiment as `unmeasured` and uses qualitative headlines only.

### Macro / FRED series

1. **Primary**: `mcp__fred__*`
2. **Fallback 1**: `WebFetch` `https://fred.stlouisfed.org/series/<SERIES_ID>`
3. **Fallback 2**: `mcp__exa__web_search_exa` query `current <metric> rate fed`
4. **All fail**: macro-analyst flags `<series> data unavailable` and reasons from last-known reading + commentary.

### Gold spot price

1. **Primary**: `mcp__yahoo-finance__get_stock_info` ticker `GC=F` (futures) or `XAUUSD=X` (spot)
2. **Fallback 1**: `mcp___gold__*` (metal-price MCP if enabled)
3. **Fallback 2**: `WebFetch` `https://www.kitco.com/charts/livegold.html`
4. **All fail**: report `gold spot unavailable`, defer to FRED `GOLDAMGBD228NLBM` for daily fix.

## Cross-cutting rules

### Anchor every cited number

Every price, ratio, or indicator value must trace to a tool result this run. If the value is from a fallback, note the source explicitly: `(via WebFetch finance.yahoo.com)`. Never paste numbers from memory or training data.

### Stale-data downgrade

If the primary source fails AND fallback succeeds but the data is older than 7 days, treat the analysis as `data stale`. Banner the report. Portfolio Manager risk gate downgrades automatically (data-freshness check, ≤ 24h for daily horizon).

### Empty payload != no data

If a vendor returns `[]` for a thinly-covered ticker (e.g. ASX on Finnhub), this is `not covered`, not `nothing happened`. Try the next fallback before concluding silence.

### Fast-fail to mark unreliable

If primary + 2 fallbacks all fail in a row, do NOT keep retrying. Mark the data category as `unreliable` and proceed with what's available. The pipeline tolerates partial reports — better to ship a flagged-uncertain analysis than to time out.

### Log to `_errors.md`

Per `analyze.md` Step 0 contract, any MCP failure during a pipeline run logs a one-line note to `data/runs/<TICKER>-<DATE>/_errors.md`. Format:

```
2026-05-08T12:34:56Z | yahoo-finance | get_stock_info | HTTP 503 | fallback to finnhub get_quote OK
```

This makes failure modes visible at run-end without burying them in agent prose.

### When to abort vs continue

| Situation | Action |
|---|---|
| 1 of 4 analysts fully fails | Continue. Bull/Bear can debate from 3 reports |
| 2+ of 4 analysts fully fail | Abort with `data insufficient`. Tell user to re-run later |
| Yahoo + Finnhub both down | Abort. No reliable price source = no rating |
| FRED down (gold/macro pipelines only) | Continue with `WebFetch` fallback or mark macro as unreliable |
| Exa down | Continue. Sentiment + ad-hoc search are nice-to-have, not core |

## Configuration

Active MCPs are tracked in `.mcp.json`. Prefix `_` = disabled. Toggle with:

```bash
python scripts/enable_mcp.py [name] [--disable]
```

API keys live in `.env` and are substituted into `.mcp.json` via `${VAR}`. On Windows, launch via `scripts/start.ps1` to load `.env` before invoking `claude`.

## Verifying a fallback chain works

Quick smoke test for any ticker:

```bash
# In Claude Code main thread:
mcp__yahoo-finance__get_stock_info ticker=NVDA
mcp__finnhub__get_quote symbol=NVDA
WebFetch url=https://finance.yahoo.com/quote/NVDA
```

If all three return reasonable data, the fallback chain is healthy.

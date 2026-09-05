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
3. **Fallback 2 — Reddit keyless RSS** (pattern from [mvanhorn/last30days-skill](https://github.com/mvanhorn/last30days-skill), MIT): Reddit's `.json` endpoints return HTTP 403 (shreddit anti-bot), but **RSS feeds still serve 200 with no key**. WebFetch these directly:
   - `https://www.reddit.com/search.rss?q=<ticker>&sort=top&t=month`
   - `https://www.reddit.com/r/wallstreetbets/search.rss?q=<ticker>&restrict_sr=on&sort=top&t=month`
   - `https://www.reddit.com/r/stocks/top.rss?t=week` (listing sweep)
   RSS entries carry no upvote counts — treat as qualitative discovery, not scored sentiment.
4. **All fail**: social-analyst marks sentiment as `unmeasured` and uses qualitative headlines only.

### Event probabilities (Fed / CPI / recession odds)

1. **Primary**: `python scripts/polymarket_odds.py "<event query>"` — real-money market-implied odds via Polymarket Gamma API (keyless, free). E.g. `"fed decision june"`, `"CPI inflation"`, `"recession 2026"`.
2. Cite as `market-implied P(X) = Y% (Polymarket, $<volume>)` — these are tool-sourced, NOT `[UNSOURCED]`. Always cite volume; thin markets are weak signals.
3. Agents must PREFER these over their own subjective probabilities for any event Polymarket prices.

### Macro / FRED series

1. **Primary**: `mcp__fred__*`
2. **Fallback 1**: `WebFetch` `https://fred.stlouisfed.org/series/<SERIES_ID>`
3. **Fallback 2**: `mcp__exa__web_search_exa` query `current <metric> rate fed`
4. **All fail**: macro-analyst flags `<series> data unavailable` and reasons from last-known reading + commentary.

### A-share / HK / index data (`.SS` `.SZ` `.BJ` `.HK`)

Finnhub and Yahoo Finance coverage of mainland-China listings is **thin-to-absent**. A `[]` or `{}` from those vendors for `600519.SS` means *not covered*, not *nothing happened* — do not read it as "no data exists". Fall through the chain below before concluding anything.

1. **Primary — AkShare (keyless)**: `mcp__akshare__get_cn_quote` / `get_cn_history` / `get_hk_quote` / `get_index_quote`. **No API key, no registration, no token** — that is why it outranks Tushare. Ships disabled (extra deps + slow first import); enable with `python scripts/enable_mcp.py akshare`. Source: [mcps/akshare_mcp.py](../mcps/akshare_mcp.py). Symbols: `600519.SS`, `000001.SZ`, `430047.BJ`, `00700.HK`, `000001.SH` (index).
2. **Fallback 1 — Tushare** (`mcp__tushare__*`): hosted streamable-HTTP MCP, config-only in `.mcp.json`. Requires `TUSHARE_TOKEN` and burns a points quota, so it sits *below* AkShare. Enable with `python scripts/enable_mcp.py tushare`.
3. **Fallback 2 — BaoStock**: keyless Python library (`pip install baostock`), A-share daily/weekly bars back to 1990. Not wired as an MCP here — reach for it only if both of the above are down and the run genuinely needs deep CN history.
4. **Fallback 3 — WebFetch**: `https://quote.eastmoney.com/<sh600519|sz000001>.html`, or `https://finance.yahoo.com/quote/600519.SS` (often stale/partial for CN names — treat as last resort and label the source).
5. **All fail**: report `A-share data not available — <symbol> uncertain`. Per the stale-data rule below, the Portfolio Manager data-freshness gate downgrades automatically. Do NOT substitute a US-listed ADR price for the local line and present it as the same instrument.

Notes for analysts:
- Every AkShare tool returns `{"error": "..."}` on upstream failure instead of raising — that is your signal to step down the chain, not to abort the run.
- `get_cn_history` returns `last_bar_date` + `bar_count`. Run the >7-day staleness check against `last_bar_date` exactly as `market-analyst` does for Yahoo. Default period is `3mo` (tactical); never request `1y`/`2y` for a tactical view.
- Prices are CNY (A-share) / HKD (HK). Never mix them into a USD portfolio total without an explicit FX conversion, and cite the FX source.

### Gold spot price

1. **Primary**: `mcp__yahoo-finance__get_stock_info` ticker `GC=F` (futures) or `XAUUSD=X` (spot)
2. **Fallback 1**: `mcp__gold__*` (metal-price MCP if enabled)
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

`.mcp.json` holds exactly the servers that run — **absence is the off switch**, there is no disable prefix. `.mcp.json.template` is the catalog they are copied from. Toggle with:

```bash
python scripts/enable_mcp.py                 # list catalog + what is active
python scripts/enable_mcp.py [name]          # copy from catalog into .mcp.json
python scripts/enable_mcp.py [name] --disable
```

Default active set: `yahoo-finance` + `finnhub`. Every other server named on this page must be enabled before its fallback step is reachable — a chain step pointing at a server that is not in `.mcp.json` is a dead step, not a fallback.

API keys live in `.env` and are substituted into `.mcp.json` via `${VAR}`. Launch via `scripts/start.ps1` (Windows) or `scripts/start.sh` (macOS / Linux / WSL) to load `.env` before invoking `claude`.

## Verifying a fallback chain works

Quick smoke test for any ticker:

```bash
# In Claude Code main thread:
mcp__yahoo-finance__get_stock_info ticker=NVDA
mcp__finnhub__get_quote symbol=NVDA
WebFetch url=https://finance.yahoo.com/quote/NVDA
```

If all three return reasonable data, the fallback chain is healthy.

Before that, prove the servers can even start — a server that dies on import
reports the same "Connection closed" as one that is misconfigured:

```bash
python scripts/mcp_handshake.py --all
```

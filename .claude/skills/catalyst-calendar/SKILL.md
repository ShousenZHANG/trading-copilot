---
name: catalyst-calendar
description: Maintain a forward-looking calendar of catalysts (earnings, FOMC, CPI, RBA, regulatory deadlines, M&A close dates, ex-div) for any ticker, ETF, or macro instrument. Lets the pipeline avoid blind entries 2 days before binary events. Triggers on "catalyst calendar", "what's coming up", "upcoming events", "earnings calendar", "next catalyst", or when an analyst needs to flag the next 90 days of events for a name.
---

# Catalyst Calendar — Methodology

> Direct port of [anthropic/financial-services equity-research/skills/catalyst-calendar](https://github.com/anthropics/financial-services/blob/main/plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md), adapted for retail Trading Copilot use (free MCPs, no Bloomberg).

## When to use

- A `news-analyst` or `fundamentals-analyst` needs to flag the next 90 days of events for a single name.
- A `/scan` run wants to skip tickers with binary catalyst inside 7 days (DCA pause rule).
- The user asks "what catalysts does NVDA have in May?" or "is there a CPI/FOMC this week?".
- Weekly review wants a **forward** calendar (current `/weekly-review` is backward-looking only).

## Source priority

1. **`mcp__finnhub__get_earnings_calendar`** — confirmed earnings dates, primary source.
2. **`mcp__finnhub__get_economic_calendar`** — Fed / CPI / NFP / GDP / CB rate decisions (US, EU, UK, AU, JP).
3. **`mcp__yahoo-finance__get_stock_actions`** — ex-dividend dates, splits, buybacks for a single ticker.
4. **WebFetch** — issuer IR pages for product launches, capital markets day, conference attendance.
5. **Exa MCP** (when enabled) — recent press around upcoming dates that aren't yet in structured calendars.

## Categories to track

**Single-name corporate**
- Earnings date + time (pre / post market) + consensus EPS / Revenue
- Ex-dividend + payment date
- Investor / analyst day, capital markets day
- M&A close target date, regulatory approval deadlines
- Lockup expiration (recent IPOs)
- Insider trading window opens (typically 2 days post-earnings)

**Sector / industry**
- FDA decisions (biotech)
- Conference calendar (e.g. JPM Healthcare, Goldman Tech, RBC Capital Markets)
- Industry data prints (auto monthly sales, weekly EIA, monthly housing)

**Macro**
- FOMC dates + minutes release
- CPI / PCE / PPI release (US monthly)
- NFP (US monthly first Friday)
- RBA cash rate decision (AU first Tuesday of month except Jan)
- ECB / BOE / BOJ decisions
- Treasury auctions (relevant for TLT / bonds)

## Output structure (markdown)

```markdown
# Catalyst Calendar — <TICKER or theme> (next 90 days)

## High-impact (within 7 days) ⚠️
| Date | Event | Type | Why it matters |
|------|-------|------|----------------|
| 2026-05-13 | US April CPI | Macro | If ≥3.3%, SPY/VGS rate-cut path resets |
| 2026-05-20 | NVDA Q1 FY27 earnings | Single-name | Data Center growth re-rating risk |

## Medium-impact (8-30 days) 🟡
| Date | Event | Type | Notes |
|------|-------|------|-------|
| ... | ... | ... | ... |

## Routine / scheduled (31-90 days) 🟢
| Date | Event | Type | Notes |
|------|-------|------|-------|
| ... | ... | ... | ... |

## Pre-positioning notes
- <ticker A>: catalyst within 7 days → DCA pause / size down / hedge consideration
- <ticker B>: post-event re-rate window opens → eligible for fresh entry

## Data freshness
| Source | Last fetched | Status |
|--------|--------------|--------|
| Finnhub earnings calendar | 2026-05-10 | ✅ |
| Finnhub economic calendar | 2026-05-10 | ✅ |
| Issuer IR page | <ticker> | ⚠️ stale (>30 days) |
```

## File-system layout

```
data/catalysts/
├── _macro-2026-05.md         # macro calendar for May 2026 (shared across all tickers)
├── _macro-2026-06.md
├── NVDA-2026-05-10.md         # ticker-specific calendar fetched on this date
└── VAS.AX-2026-05-10.md
```

The macro file is **shared** — built once per month, reused by every `/advise` and `/scan` run that month.

The ticker-specific file is **rebuilt every fetch** (earnings dates can shift; lockups can move).

## Integration points

### `/advise <TICKER>` integration

The `investment-advisor` agent calls catalyst-calendar **after** baseline data fetch:
1. Fetch ticker price + technicals + fundamentals + news (existing flow)
2. Invoke `catalyst-calendar` → append "Key Dates (next 90 days)" table
3. Risk gate check #3 ("Major catalyst within 7 days") **uses the calendar output**, not heuristics

### `/analyze` Step 3 (news-analyst) integration

The news-analyst's "Upcoming Catalysts" section is now fed by catalyst-calendar:
- news-analyst fetches news, adds: "See catalyst calendar at `data/catalysts/<TICKER>-<DATE>.md`"
- Portfolio Manager risk gate #3 reads the calendar to enforce 24h freshness

### `/scan` integration

Before dispatching full pipeline on a watchlist ticker, scan checks:
1. If ticker has high-impact catalyst within 7 days → flag, **but still scan** (don't skip — high-conviction calls before binary events are valid; the call adjusts sizing)
2. If macro week (CPI / FOMC / NFP) → bump all conviction labels down one tier (cautious posture into binary)

## Output rules

- Cite source for every event (Finnhub `get_earnings_calendar`, FRED `<series>`, issuer IR URL, etc.)
- Time-stamp the calendar generation moment (calendars age fast — earnings move, conferences cancel)
- Flag stale entries (>30 days since last refresh from a primary source)
- For ASX-listed names: also include RBA + AU CPI + AU GDP dates (not just US macro)
- For gold (`GC=F`): focus on FOMC + DXY + CPI; insider/earnings irrelevant

## What this skill does NOT do

- It does not predict catalyst outcomes (that's the analysts' job)
- It does not score catalyst "importance" beyond the H/M/L bucketing rule
- It does not generate a watchlist — it operates on a given universe

## Source

Pattern from [`anthropic/financial-services/plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md`](https://github.com/anthropics/financial-services/blob/main/plugins/vertical-plugins/equity-research/skills/catalyst-calendar/SKILL.md). Adapted for free-tier MCPs (Finnhub + Yahoo + FRED) instead of Bloomberg / FactSet.

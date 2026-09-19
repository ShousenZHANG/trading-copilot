# ADR-0005: Three data sources named in ADR-0004 do not exist on the terms it assumed

Status: accepted, 2026-09-19. Amends ADR-0004 clauses 3 and 6.

## Context

ADR-0004 was written before its external data sources were verified. A
verification pass on 2026-09-19 fetched the primary sources and found that
three of them cannot supply what the decision assumed.

**Macro surprise.** Clause 3 lets the model emit a brake derived from "the
day's macro-calendar surprise". A surprise needs an actual value and a market
consensus for the same release. Finnhub's economic calendar returns HTTP 403
for this repository's key while the same key returns 200 on `/quote`, and its
documentation marks the endpoint premium. Trading Economics' guest credential
returns HTTP 410, discontinued. The one free feed that carries a forecast
publishes a single rolling week with no `actual` field, and its previous- and
next-week URLs return 404, so the history cannot be backfilled. Finnhub's own
documentation states historical surprises are available to Enterprise clients,
so a paid tier is not known to permit a backtest either. A signal that cannot
be backtested cannot pass the admission gate in ADR-0004 clause 4.

**Gold history.** The rule admission gate requires at least fifteen years of
history. The Shanghai Gold Exchange adapter reaches about four. The obvious
extension, reconstructing a yuan gold price from a dollar benchmark and the
exchange rate, has no licence-clean source: both LBMA series were deleted from
FRED on 2022-01-31, FRED search returns no LBMA series at all, every surviving
FRED gold series is an index rather than a price level, and LBMA's own endpoint
carries a statement that an IBA licence is required to obtain or use historical
benchmark data. The exchange-rate leg alone is unaffected.

**IB Gateway.** Clause 6 offers an IB Gateway session as a third price source.
IBKR documents that the API requires a Level 1 streaming subscription to return
historical data and that delayed market data does not lift that requirement.
Separately, `ib_async` constrains `tzdata` below 2026.0 while every entry point
in this repository pins 2026.3, which makes the dependency unresolvable rather
than merely degraded.

## Decision

1. The macro-surprise input to the brake is removed. The brake keeps its
   `{none, reduce_50, skip}` shape and its one-way constraint, and is derived
   from news evidence alone. It remains outside the admission gate, is marked
   unbacktested wherever it appears, and can only reduce or cancel.
2. The gold sleeve is deferred. `GOLD.CNY` stays a conversational signal on the
   existing Shanghai Gold Exchange adapter. No gold rule enters the rule library
   until a source exists that is free, licence-clean, long enough for the
   admission gate, and expressed as a price level rather than an index.
3. IB Gateway is not integrated. Prices remain on the Yahoo and Nasdaq
   two-source cross-check. The IBKR Flex Web Service stays the intended
   account-data path; its client is not built until a live token allows its
   response shape to be verified, because IBKR publishes no schema and the
   attribute set is defined by the user's own query template.
4. The pipeline defects these sources exposed are fixed now, because they are
   live defects independent of any of them: an empty injected environment
   variable masking `.env`, `safe_url` missing a single-character secret
   parameter, a redaction list that had drifted from the loader's list, and a
   Finnhub MCP server returning HTTP 401 from every tool.

## Consequences

- The worked example that motivated the macro leg — a rate rise the market had
  already priced, so the release itself is not news — is not something this tool
  will detect automatically. That limitation is documented rather than papered
  over with a model's guess.
- Position sizing still waits on the account-data path, which now waits on a
  live Flex token rather than on code.
- Anyone reading ADR-0004 alone would plan against sources that are not
  reachable. It now carries a pointer here.

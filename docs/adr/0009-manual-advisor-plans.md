# ADR-0009: Research signals and local manual trade cards

Status: accepted, 2026-10-03. Extends ADR-0004 clauses 1 and 6; preserves its
strategy admission gate and manual execution boundary.

## Decision

- One advisor covers long-term and swing research. Explicit common-stock
  research requires saved Nasdaq ordinary-share identity in a supported US
  venue; the ETF registry and adopted ETF universe remain unchanged.
- `signals.py` computes evidence-bound technical conditions and reference
  levels. Their adjusted/raw basis remains visible. They are not calibrated
  probabilities, executable prices or strategy admission evidence.
- `broker.py` optionally queries a local TWS/IB Gateway through the official
  `ibapi` SDK. Its allowlist contains account/data queries and subscription
  cleanup only. Client zero, order binding and place/modify/cancel APIs are
  excluded. Disabled, absent SDK, incomplete coverage, delayed data and
  unknown order visibility return named blocked states.
- `trade_plan.py` computes a funded manual card from immutable research,
  current admitted ETF rules or separately adopted advisor templates, fresh
  actual raw quotes and local configuration.
  Whole shares, tiered ticks, fees, settled USD cash, order reservations,
  omitted holdings, NAV FX, direct sector attribution and risk limits are
  calculated in code. Unfilled sales never finance buys.
- `advisor_strategy.py` defines inspectable long-term trend and swing breakout
  hypotheses. Adoption recomputes raw historical inputs with frozen code/data,
  nonzero costs, 15-year/stress coverage, a two-year holdout and reported
  sensitivity. Historical source flags/hashes are assertions; an independent
  actual user source-review statement is required at the CLI-only adoption
  boundary. The report never claims network-authenticated history. A separate
  actual strategy-adoption statement and explicit private config pointer select
  the live hypothesis. No user strategy is silently selected by this upgrade.
- `plan_store.py` persists observations, immutable inputs, Review versions and
  cash-flow receipts in the existing local SQLite database. Review requires an
  actual user statement; preflight observes the broker again. Changes or partial
  fills suppress reusable orders. Broker observations never create journal fills.
- Default public context contains necessary sanitized holdings and calculated
  facts. Raw transaction statements, full account identifiers and keys stay
  local. A bounded operation lookup supports corrections without raw history.

## Limits and acceptance

Stock/swing research signals remain separate from funded template orders.
The existing historical/cost/OOS admission gate remains mandatory; passing
coverage/calculation checks proves neither superiority nor future profit.
TWS execution coverage is recent, so clock strategies also need verified cadence
history before a funded card. Swing holdings require confirmed mode lots,
entry sessions and fixed entry loss references; a Review cannot create them.

The first trustworthy USD-valued broker observation establishes a prospective
drawdown baseline. Earlier drawdown is unknown. Subsequent intervals need
explicitly confirmed external flows; absent flow coverage pauses new risk.
Static ETF sector attribution does not measure constituents or overlap.

Offline temporary fixtures validate money arithmetic and state transitions.
Official-SDK doubles validate allowed query methods. Real IBKR account coverage,
fees, entitlements and refresh-to-manual timing require a separate live read-only
acceptance after the user starts TWS/Gateway. Neither test passage nor a Review
receipt establishes future profitability or cancels an existing broker order.

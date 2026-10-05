# Manual trade cards

For a concrete adopted-strategy USD stock/ETF order, run the steps below.
An explicitly user-directed finite ETF allocation instead enters through
[one-off allocation](one-off-allocation.md), then rejoins Review and preflight.
Run `get_execution_readiness` to collect independent missing requirements.

1. Fresh research and `analyze_market_signals`/`assess_investment_proposal`.
2. `collect_broker_snapshot` for the candidate/adopted universe. Inspect every
   coverage flag, account version, settled currency cash, working orders,
   recent executions, actual received data type, spread, hours and tick rules.
   For held shares belonging to different modes, obtain the user's explicit
   split and call `confirm_holding_modes` with this snapshot/account version.
   Quantity, actual-fill or ownership changes invalidate the split, including a
   same-quantity sell/buy round trip. Unassigned shares remain conservatively
   counted; a mode cannot sell shares assigned to another mode.
3. `prepare_manual_trade_plan(research_snapshot_id, execution_snapshot_id, mode)`.
   The mode is `long_term` or `swing`; local configuration selects the rule and
   risk parameters. The compiler alone supplies prices, fees and whole shares.
4. Present the returned card. For `ready_for_review`, show its exact instrument,
   side, quantity, USD limit, fees, funds after buys, risk scope and expiry.
   `blocked` or `no_trade` retains its named reasons without an invented order.
5. Only after the user's actual approval, call `confirm_manual_plan_review` with
   their statement and the current `expected_version`. Save the committed receipt.
   To reject or abandon a local card, use outcome `reject` or `cancel` with the
   user's actual statement. This never cancels a broker order. Already reviewed
   active cards reserve their proposed budget against competing local cards.
6. Immediately before the user's manual action, call
   `revalidate_manual_trade_plan` with the reviewed version. This collects again.
   Changed cash/holdings/orders/fills/quotes/policy require a new card and Review.
   After a passing preflight, the user submits the displayed order in IBKR.
7. Refresh broker observations to reconcile working/partial/cancel-pending
   orders. Record journal fills only from the user's explicit actual trade
   report. A completed broker snapshot is not historical cost reconstruction.

The broker component is local read-only TWS/IB Gateway, disabled by default.
For setup and live acceptance, read `docs/IBKR.md` in the repository checkout.
No shipped tool places, modifies or cancels an order. A plan expiry never
cancels a DAY/GTC order. Pending cancellation can still fill; partial fills
and remaining orders retain their shares/cash reservations.

Funding uses settled USD cash minus open-buy principal, estimated open-order
fees and the stricter configured/adopted reserve. Unfilled sales and AUD NAV do
not create USD buying cash. Fresh live FX converts NAV for risk budgets only.
The calculation preserves held names omitted from the strategy universe,
counts their account exposure, and shares one physical account across modes.
Sector attribution for registry ETFs is static direct attribution; fund
constituents/overlap are not measured. Unknown ordinary-stock sector blocks
new funded plans. Fee assumptions need explicit local confirmation.

Drawdown starts at the first trustworthy complete USD-valued broker baseline;
earlier drawdown remains unknown. Each later interval needs explicitly confirmed
net deposits/withdrawals through `confirm_account_cash_flow`, including zero.
Unknown flows pause new risk. Cash-flow declarations affect return accounting,
not broker balances, holdings or permission to trade.

The funded compiler accepts current admitted legacy ETF rules and separately
adopted `long_term_trend`/`swing_breakout` stock/ETF templates. Read
[template admission](advisor-templates.md) before selecting either new rule.
Clock-based legacy rules require verified complete execution history; TWS's
recent executions alone do not establish cadence. For a new advisor rule,
`confirm_strategy_execution_state` records the user's actual first activation
with no prior strategy executions, or last actual completed execution session.
Use the configured rule/mode and current account version. Do not infer first
activation from empty recent history; changed account activity requires renewed
confirmation. Review does not advance a rebalance clock.

For actual swing shares, `confirm_holding_modes` also takes user-confirmed
`entry_sessions` and `entry_plan_ids`, each keyed by symbol then mode. A frozen
entry card supplies the fixed loss reference; current ATR cannot move that
reference. A planned/reviewed buy alone never creates a lot. Missing actual
entry or split-adjustment evidence pauses the affected plan.
Unknown cadence/entry risk pauses new swing buys. An independently verified
fixed-reference, time or technical exit can still reduce confirmed swing-owned
shares; another mode's shares remain held. Holdings used to bind an actual entry
must be observed after that entry card's approval, never retrofitted to an earlier
position observation. Refresh after the actual trade before confirming lots.

Accepted startup swing caps are configuration guardrails. They are not
backtested optimal parameters. Overnight loss can exceed
any planned stop; reference invalidation is not an acknowledged protective order.

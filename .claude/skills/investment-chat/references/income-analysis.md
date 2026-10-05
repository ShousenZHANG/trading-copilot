# Income ETF evidence and comparison

Use `collect_income_evidence(["QQQI","JEPQ"])` to save current distribution
evidence. Read each source role and gap: issuer and Nasdaq exchange fallbacks
are different provenance, not independent corroboration. A blocked fetch is not
zero distribution. Historical/current issuer pages do not authenticate what an
investor knew years ago; do not use them to manufacture point-in-time backtests.

`analyze_income_evidence(snapshot_id, parameters)` computes trailing payout,
stability and, when raw NAV history is explicitly supplied, price-plus-payout
total return. Raw NAV and reinvestment-adjusted prices must not be mixed.
Announced actual, estimated, scheduled and unknown future amounts stay separate.
Ex-date eligibility requires actual pre-ex holdings; a pay date does not prove
the user's broker credited cash. ROC/19a estimates are not final personal tax.

Parameters may supply `prices` (USD research references by symbol), `nav_history`,
`withholding_scenarios` such as a named map of symbol->fraction under `rates`,
and `fx_scenarios` with `name`, `base_currency` and `usd_to_base`. Explain all
assumptions and their dates/sources in the answer. Unknown personal withholding
needs scenarios, not a guessed applicable rate. AUD cash flow is a scenario,
not a completed conversion or spendable USD balance.

For an allocation comparison, `compare_income_portfolios` takes stored evidence
and `prices`, `budget_usd`, `cost_model` (`per_share_usd`, `minimum_usd`,
`other_cost_bps`), `constraints` (`cash_floor_pct`, `weight_bounds` with `min`
and `max` fractions per symbol). Optional `existing_holdings` counts cost of
changes. Choose `gross_cashflow`, `net_cashflow` or `total_return` explicitly;
net results need a withholding scenario and total return needs raw NAV history.

Show the highest-scoring feasible historical scenario alongside concentration,
costs and assumptions. It is not a unique future optimum, guaranteed monthly
payment, tax advice or a trade card. Actual payments vary and payouts can coexist
with NAV losses. Do not confuse annualized last payment with trailing yield.
Only an actual user choice of hypothetical holdings can move to
[one-off allocation](one-off-allocation.md), with fresh funded execution data.

## Actual credited distributions

Only after the user reports an actual completed cash credit, call
`record_distribution_receipt(receipt, idempotency_key)`. Preserve their
`statement` and `source_message_id`; bind `opening_balance_id`, `instrument_id`,
`payment_date`, `currency` and decimal-string `net_amount`. Unknown `gross_amount`
and `withholding_amount` stay null. A source pay date or expected monthly amount
cannot supply this confirmation. Never infer withholding by assuming a tax rate.

Use `get_distribution_receipts` for existing identity/version before correcting
or reversing with `event_type`, `operation_id`, `expected_version` and the user's
new statement. Distinguish cash distributions from external deposits/withdrawals.
This receipt does not edit broker cash or create shares. A separately reported
actual reinvestment purchase follows the ordinary fill workflow, with no inferred
DRIP quantity or price. Only a committed receipt permits “已记录到账”.

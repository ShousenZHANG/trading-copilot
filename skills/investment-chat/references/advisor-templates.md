# Stock and swing template adoption

The account-free templates are `long_term_trend` and `swing_breakout`, for a
declared US stock/ETF universe. Long-term trend uses a completed-session moving
average and a rebalance cadence. Swing breakout uses the previous-window high,
trend filters, an ATR-based entry reference and a maximum holding period.
These are inspectable hypotheses. Do not call either the best strategy, give a
win rate without evidence, or activate it because the current chart looks good.

For a user's requested adoption, use the local CLI. There is no model-facing
validate/adopt tool. `validate-strategy` consumes a raw history bundle, the exact
template spec, a frozen manifest, calendar counts and a complete cost model;
`adopt-strategy` recomputes that same history in the adoption call. Serialized
performance reports cannot replace the calculation.

Read `docs/STRATEGY_VALIDATION.md` in the checkout for the input contract.
Admission requires at least 15 years after warmup, 2008/2020/2022 coverage,
costs, at most three parameters, at least two years of holdout, and reported
timing/cost/parameter sensitivity. No padding of pre-listing history or hidden
waiver is allowed. A recent ETF may have valid research and insufficient history
for this adoption gate at the same time.

Source URLs, hashes and flags in an input JSON are assertions. They do not
authenticate market history or prove that a holdout was unseen. The independent
history-review statement must come from the user's actual review of source,
calendar, corporate actions, universe selection and holdout evidence. Keep it
in a private local file. A second actual statement approves strategy adoption
after the user sees the net results, benchmarks and sensitivity. Never author
either statement on the user's behalf or reuse approval of a trade card.

Successful adoption writes an immutable local record. The user explicitly sets
`[advisor].long_term_rule_id` or `swing_rule_id` in private `config/user.toml`.
No personal pointer is changed by validation/adoption. Current code, template,
cost and sizing fingerprints are checked again before compilation; changes
require revalidation. Startup caps are constraints, not optimized parameters.

For actual orders, continue with [manual trade cards](manual-trade-plans.md).
Broker quotes, cash, positions, fees, mode ownership and actual user Review
remain prerequisites. Historical close replay is an execution assumption; it
does not predict the price at which the user will manually submit or fill.

# Adopted-rule execution

1. Read `get_investment_context(sleeve="etf"|"gold")` for the requested book.
   Its `recommendations` are historical research views, including rows whose
   holdings are still current. They are not fresh executable orders: collect
   new evidence and call `evaluate_adopted_rule` before reporting an action.
   `requires_reevaluation` is true and `execution_scope` is `research_only`;
   quantities in `historical_order` are audit details, never today's instructions.
   `recorded_bindings_current` does not validate current config, cash or quotas.
   Read the user's configured rule and capital through the validated config
   CLI when needed. Rule adoption comes from a real admitted
   `backtest_cli.py --adopt`; the user points `config/user.toml` at its receipt
   (ADR-0007). For setup, read `docs/INSTALL.md` from the repository root.
   Schema 1/2 adoption records remain readable for audit. The current schema 3
   requires a new backtest and adoption under the current engine before the user
   updates the pointer. Only actual completed trades advance execution cadence;
   an evaluation, recommendation or zero-trade attempt does not.
2. Coverage is the user's explicit assertion that the recorded holdings for
   this book are complete. Call `declare_holdings_coverage` only in direct
   response to that assertion, including an explicit confirmation of no
   holdings. Supply **both** parameters: `sleeve="etf", base_currency="USD"`
   or `sleeve="gold", base_currency="CNY"`. Any later completed trade
   invalidates the declaration. Read the context again before sizing.
3. Collect one fresh snapshot covering every configured ETF universe member,
   or `GOLD.CNY` for gold. Check its quality, issues and expiry.
4. For gold, the user must supply the merchant/product and a current
   **all-in CNY ask per fine gram**, with an explicit timezone timestamp.
   Unknown purity/fees or a benchmark alone does not provide that quote.
   Call `record_retail_gold_quote` only for the user's observed quote. Check
   its returned receipt and use the **new returned snapshot_id** for evaluation;
   the original snapshot remains immutable. Quote validation checks shape
   and freshness, not the truth of the merchant's price.
   To update a quote, attach the new report to the last returned snapshot and
   use the next returned ID; it selects that report, retaining the earlier one.
   Supply optional `account_id` only when the user explicitly identifies the
   account, with the same local identifier as its recorded trades. A merchant
   name is not account identity. Omit an unknown account; the daily quota then
   conservatively includes all accounts.
5. Call `evaluate_adopted_rule(snapshot_id, sleeve, brake_level,
   brake_reason, brake_evidence_ids)`. The engine computes quantity, price
   and weight. Choose only `none`, `reduce_50` or `skip` for the brake. A
   non-`none` brake needs a stated reason and at least one **news** evidence
   ID from this snapshot. It can reduce/cancel a buy, never enlarge it or
   change a sale, and is reported as unbacktested.
6. Report the returned orders, execution scope, refusals and disclosures.
   Gold's contribution cadence has not been backtested: its historical curve
   measures holding gold, not the benefit of a contribution schedule. Keep
   that disclosure beside any CNY/gram amount. No broker order is sent.

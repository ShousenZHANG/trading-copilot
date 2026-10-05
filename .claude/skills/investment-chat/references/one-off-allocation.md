# User-directed one-off ETF allocation

Use when the user explicitly wants a finite purchase budget or a holding
rebalance, including QQQI/JEPQ whose history may not satisfy strategy admission.
This route calculates a user decision; do not describe it as validated alpha,
the best future entry or a guaranteed return. Strategy recommendations continue
through historical admission. Do not fabricate adoption to unlock this route.

1. Collect current research, analyze signals and assess the proposal as usual.
   Collect a fresh read-only broker snapshot. Run `get_execution_readiness` with
   route `user_directed` and both snapshot IDs to expose all missing prerequisites.
   Follow named checks and their `stage`, not a blanket requirement that the
   summary already say `ready`: the intent still needs human confirmation,
   drawdown is checked by the compiler, and income research is optional.
   Historical strategy adoption is not a prerequisite on this explicit route.
2. Establish the user's actual instruction: `kind` `buy_budget_usd` or
   `target_shares`; `targets` maps registered ETF symbols to decimal USD budgets
   or whole target shares. `allowed_sells` explicitly lists symbols that may be
   reduced. Unmentioned holdings remain preserved. AUD money is not USD cash;
   only actual settled USD funds purchases, after fees and reservations.
3. Present the four `risk_limits` together: `cash_floor_pct`,
   `max_single_name_pct`, `max_trade_notional_pct`, `long_term_nav_cap_pct`.
   Fractions range 0–1. Use the user's already stated preferences; ask only for
   missing or changed material choices. Never silently default to 100% exposure.
   A change to cash buffer or concentration must appear in this immutable intent.
4. After an actual affirmative user statement about those choices, call
   `confirm_manual_allocation_intent(execution_snapshot_id, research_snapshot_id,
   intent, statement, expected_account_version)`. Copy the user's own statement;
   quoted, negated, conditional, third-party or inferred approval is insufficient.
   A denial is not permission to rewrite their words into an accepted template.
5. Call `prepare_directed_trade_plan(intent_id)`. Only its current
   `ready_for_review` orders can supply limit, shares, fees and expiry. Describe
   the raw ask/bid basis as a current calculation, never an optimal future price.
6. Continue the same explicit Review, fresh preflight and manual submission
   steps in [manual trade cards](manual-trade-plans.md). Intent confirmation and
   Review of calculated prices are distinct. No tool places an order.

If funding fits only part of the target, show actual funded whole shares and
unmet intent; do not say the target allocation is complete. A sell can appear
without a funded buy. Wait for actual settlement, refresh account/research,
and confirm the remaining instruction before another card. Unknown flows,
expired/delayed quotes and pending target orders block the dependent calculation.
An existing reviewed card must be reconciled or explicitly abandoned before a
competing card can use the same physical account.

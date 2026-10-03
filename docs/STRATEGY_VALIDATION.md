# Stock and swing validation

This workbench validates a declared hypothesis; it does not choose a strategy
for the account or change a live configuration pointer. The templates are:

| Family | Mode | Parameters |
|---|---|---|
| `long_term_trend` | `long_term` | `trend_sessions=200`, `rebalance_sessions=21` |
| `swing_breakout` | `swing` | `breakout_sessions=20`, `atr_stop_multiple=2`, `max_holding_sessions=20` |

Both accept 1–24 registered ETFs or provider-verified ordinary US stocks. The
declared universe stays fixed through a validation. The sizing profile binds
whole shares, separate long-term/swing caps, stock/ETF caps, fees and swing risk.
This fixed startup profile has not been optimized for a personal account.

## Local inputs

Keep market data exports and review statements outside version control. The
`--input` JSON object contains:

```json
{
  "spec": {
    "family": "long_term_trend",
    "universe": ["SPY", "QQQ"],
    "parameters": {"trend_sessions": 200, "rebalance_sessions": 21}
  },
  "history_bundle": {
    "schema_version": 1,
    "bars": {"SPY": [], "QQQ": []},
    "dividend_events": {"SPY": [], "QQQ": []},
    "provenance": {
      "data_role": "market",
      "point_in_time_verified": true,
      "calendar_verified": true,
      "corporate_actions_complete": true,
      "survivorship_verified": true,
      "universe_method": "predeclared_fixed_universe",
      "dividend_events_complete": true,
      "dividend_event_basis": "pre_ex_entitlement_with_explicit_cash_availability",
      "execution_price_basis": "raw_unadjusted_usd",
      "indicator_price_basis": "total_return_adjusted",
      "universe_evidence": "Reference to the independently reviewed universe evidence",
      "source_evidence": []
    }
  },
  "freeze_manifest": {},
  "expected_sessions": {}
}
```

This is a shape example with empty history, not admissible evidence. Each bar
declares `session`, raw `open/high/low/close`, `adjusted_close`, `split_ratio`
and a timezone-aware `available_at`. Adjusted prices
form signals; raw prices, shares, splits, cash distributions and fees form the
portfolio ledger. Missing IPO/delisting periods cannot be padded. Symbols must
have aligned valid sessions and corporate-action coverage.
Every universe symbol needs an explicit `dividend_events` list, including `[]`
when there are no distributions. Each event declares a unique `event_id`,
`ex_session`, `payment_session`, timezone-aware `known_at` and
`cash_available_at`, positive `amount_per_pre_ex_share`, `currency="USD"`,
`unit="USD_per_pre_ex_raw_share"`, `distribution_type="ordinary_cash"` and
`source_evidence_id`. The event source must reference a hashed source-evidence
entry. `known_at` must precede the ex-session's local midnight;
`cash_available_at` must belong to the declared payment session. Both dates
must be verified US trading sessions. This conservative contract refuses
same-day announcements rather than assuming they were known before trading.
The old nonzero bar-level `dividend_cash_per_share` is refused because it does
not identify who owns the payment. Zero values remain compatible.

The replay locks the previous session's raw shares into a fixed USD receivable
before ex-session splits or trades. Receivables count toward portfolio NAV but
cannot fund buys. Cash becomes available only at the first modeled execution
close at or after its declared availability; an after-close payment cannot
fund that day's order. Moving a receivable into cash adds no second return.
Sale proceeds are modeled as receivables for a fixed
conservative five subsequent published sessions; this is an explicit replay
assumption, not a claim to implement the historical statutory settlement calendar.
Ordinary cash-distribution entitlement belongs to the holder before the supplied
ex-date; selling afterward does not transfer that payment to a new buyer. The
replay separates that receivable from payment-date settled cash. Special
distributions of at least 25% of the preceding raw close, stock distributions
and due-bill rules require a different contract and are refused here.
`ordinary_cash` identifies the entitlement mechanics, not a tax classification.
The replay uses gross cash amounts before tax; it does not model withholding,
return-of-capital tax treatment or the account's actual net credit. See the primary
[Investor.gov ex-dividend rules](https://www.investor.gov/introduction-investing/investing-basics/glossary/ex-dividend-dates-when-are-you-entitled-stock-and).

`source_evidence` entries contain `id`, an HTTPS `url` and the lowercase SHA-256
of the source artifact. `expected_sessions` maps calendar years to verified
session counts, including all complete years and the three stress years. These
declared fields need source review; writing `true` is not verification.

`freeze_manifest` contains `kind`, `spec_hash`, `code_hash`, `data_hash`, `cost_hash`,
`locked_at`, `training_end`, `oos_start`, `oos_end`, `holdout_locked=true` and
`holdout_tuned=false`. Use `TemplateSpec`, `spec_hash`, `runtime_fingerprint` and
`history_hash`/`cost_hash` from `scripts/copilot/advisor_strategy.py` for the exact hashes.
`fingerprint-strategy --input <history-input.json>` returns these current
bindings without asserting a historical lock or source authenticity.
`current_frozen_replay` explicitly means a present-day historical replay.
`prospective` additionally requires a lock before the holdout starts; local
metadata alone cannot prove that historical lock or an unseen holdout. Keep
independent dated evidence and disclose its limits. Future availability or
uncompleted session prices cannot enter a historical signal.

An optional `cost_model` must declare every field of the shared `CostModel`,
with nonzero per-share commission, minimum commission and spread assumptions;
the advisor contract uses uncapped explicit commissions. See the class in
`scripts/copilot/backtest/engine.py`. Use actual applicable fee assumptions when
reviewing a strategy. Live card fees need separate local confirmation.

## Validate, review, adopt

From the project root, with paths to private files:

```powershell
uv run --no-project --quiet --script scripts/copilot_cli.py validate-strategy --input <history-input.json>
uv run --no-project --quiet --script scripts/copilot_cli.py validate-strategy --input <history-input.json> --history-attestation-file <actual-user-history-review.txt>
uv run --no-project --quiet --script scripts/copilot_cli.py adopt-strategy --input <history-input.json> --history-attestation-file <actual-user-history-review.txt> --confirmation-file <actual-user-adoption.txt>
```

Replace the angle-bracket paths; they are placeholders, not literal PowerShell
arguments. The first calculation can run without an attestation and cannot be
adopted. The history statement records the user's actual independent review;
the adoption statement records the user's subsequent actual strategy choice.
Model-written, quoted or hypothetical approvals do not grant authority.

The accepted history-review statement is exactly
`我已审核历史数据来源、交易日历、分红拆股、时点标的与冻结样本外区间`.
The accepted adoption statement is exactly `我明确采用此顾问策略`.
The equivalent English statements are declared in `HISTORY_ATTESTATIONS` and
`ADOPTION_CONFIRMATIONS` in the module. These forms describe completed human
actions; do not create a statement file until that person actually says it.

Inspect net total return, fees, turnover, drawdown and recovery, holdout and
stress periods, benchmark comparison and parameter/timing/cost sensitivity.
The replay starts with USD 100,000. Integer shares and minimum fees scale
nonlinearly, so that curve does not validate returns for a smaller personal
account. The report lists excluded account overlays; live settled cash, actual
other-mode exposure, fees, sector limits and preflight remain separate checks.
At least 15 years after warmup and two years of holdout are required. Recent
funds must not acquire synthetic pre-inception history to pass. Admission means
the declared coverage and calculation checks passed; it proves neither future
profit nor superiority. `market_validated` and `source_authenticated` remain
false: the program does not fetch/authenticate the historical artifacts.
`adoption_eligible` also requires real-market role and the independent actual
history-review statement. Test fixtures cannot become personal adoptions.

Adoption persists an immutable local receipt. Set the returned `rule_id` as
`[advisor].long_term_rule_id` or `swing_rule_id` in private `config/user.toml`
only after the user's explicit choice. The CLI does not set it automatically.
Changes to code/template/data/cost/sizing invalidate the binding. No strategy
has been adopted for the personal account by this repository upgrade.
The fingerprint also binds the installed exchange-calendar and timezone-data
versions; changing those dependencies requires revalidation.

Then follow [IBKR read-only acceptance](IBKR.md) and the Skill's manual-card
workflow. Analysis cannot substitute for live account/quote checks. The user
Reviews, refreshes the card and submits each order manually; no order API is
exposed.

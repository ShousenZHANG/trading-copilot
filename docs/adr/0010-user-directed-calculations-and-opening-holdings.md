# ADR-0010: User-directed calculations and opening holdings

Status: accepted, 2026-10-05. Extends ADR-0004 and ADR-0009.

## Problem

Existing holdings cannot be represented by invented historic executions. A user
may also want to allocate a finite budget to a young income ETF whose actual
history cannot meet the strategy-admission horizon. A live quote cannot supply
that missing strategy validation.

## Decision

Keep two typed calculation paths. Adopted strategies retain historical,
cost, out-of-sample and explicit adoption requirements. `user_directed` cards
instead calculate the user's explicitly confirmed one-off ETF instruction:
USD buy budgets or target whole shares, named permitted sales and four explicit
cash/concentration/notional/exposure limits. They claim no validated alpha.

The content-addressed intent binds the physical account/version, research and
execution snapshots, actual confirmation, creation and expiry. It is neither a
standing authorization nor a model-created strategy. Changing the intent makes
a new immutable identity; changed configuration still invalidates the card.
One-off risk choices do not rewrite strategy adoption or the user's config.

The shared plan store enforces mutual exclusion, Review/version history and
preflight for both paths. Prices come from fresh raw live bid/ask and tick rules;
research reference levels are never presented as executable or optimal prices.
Buying power is settled USD less reservations, fees and the confirmed reserve.
AUD NAV and unfilled sales do not fund buys. A two-leg switch requires a new
observation after actual sale settlement before buying with its proceeds.
Unknown cash flows pause new risk; a known prospective drawdown ceiling remains
effective. Existing swing-assigned shares cannot be altered through the one-off
long-term route. Unknown instrument identity or unsupported positions fail closed.

An `opening_balance` is a separate user-confirmed journal event bound to saved
broker positions and observation time. It initializes known quantities without
fabricating fill dates, prices, fees, realized profit or strategy cadence. Broker
average cost is observational evidence, not certified tax basis. Unknown basis
stays unknown. Corrections/reversals append versions. Subsequent user-reported
fills reference `opening_balance_id`; fills already included in the opening
cutoff cannot be counted twice.

Distribution research stores issuer/exchange provenance, ex/record/pay dates,
announced versus estimated amounts and retrieval/publication precision. It does
not certify ownership entitlement, actual cash receipt or personal tax. Income
comparisons are hypothetical holdings under explicit costs and bounds, and must
enter an actual user-confirmed intent before execution arithmetic.

## Consequences and acceptance

- Young ETFs can be calculated without weakening historical strategy admission.
- No shipped code submits, modifies or cancels broker orders.
- Readiness diagnosis reports independent missing prerequisites together and
  never grants authorization. Real broker acceptance remains environment-specific.
- Regression contracts cover denial, fee amendments, opening cutoffs, unknown
  USD cash, delayed quotes, sale settlement, concurrent cards and changed state.
- Offline/model workflow evidence is reported separately from market efficacy.

Limits: one-off cards currently support registered USD ETFs and whole shares.
They do not implement automatic FX, fractional orders, tax-lot optimization,
fund-constituent risk look-through or an atomic broker account lock. Preflight is
a fresh observation; any material change before manual submission needs refresh.

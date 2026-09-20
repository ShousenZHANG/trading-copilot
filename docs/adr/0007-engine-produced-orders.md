# ADR-0007: Engine-produced orders — the exchange whitelist, per-sleeve limits, declared coverage, and the disabled correlation check

Status: accepted, 2026-09-20.

## Context

Verification on 2026-09-19/20 established six findings that bound how the
engine can turn an adopted rule into an order.

1. **The exchange whitelist blocked most of the registry.** `providers.py:503`
   required the Nasdaq-reported exchange to start with `NASDAQ`, `NYSE` or
   `AMEX`. Probed live across all 39 registry symbols on 2026-09-19, Nasdaq
   reports exactly two labels: `PSE` for 29 symbols and `NASDAQ-GM` for 9,
   plus SPLG, which Nasdaq does not recognise at all (29 + 9 + 1 = 39). `PSE`
   is the legacy Pacific Exchange code for what is now NYSE Arca, a registered
   US national securities exchange and the primary listing venue for most
   ETFs. Rejecting it left Yahoo as the only upstream for 29 of 39 symbols, and
   `market_data.py:352` requires `authoritative or (len(upstreams) >= 2 and
   any(c["status"] == "pass" for c in comparisons))` — `authoritative` is only
   ever true for SGE and the Nasdaq index publisher, never for a US ETF. The
   item got `independent_price_confirmation_missing`.
   **Denominator correction:** an earlier draft of this ADR reported this as
   "19 of the 22 admissible equity ETFs," borrowing ADR-0006's *backtest*-
   admissibility pool — 22 symbols selected by 2008 historical-bar
   availability, which excludes VT, SPLG and six short-history funds for
   reasons that have nothing to do with live price confirmation. Live price
   confirmation and backtest admissibility are different pools, gated by
   different concerns. The pool this defect actually affects is the registry's
   33 non-defensive equity ETFs (39 minus the 6 defensive bond/gold ETFs,
   which the ETF sleeve excludes by config regardless of data quality): of
   those 33, 3 (QQQ, SMH, SOXX) were already Nasdaq-listed and passing, and
   SPLG is unhelped because Nasdaq does not recognise it at all, leaving
   **29 of 33** that returned `data_insufficient` before this fix and are
   helped by it — worse than the `research_only` ADR-0004 anticipates.

2. **ADR-0004 clause 2 is violated today.** `policy.py:190` validates four
   model-supplied numeric keys (`price`, `quantity`, `target_weight`,
   `stop_loss`) and `policy.py:366-373` echoes them back into the decision,
   with no comparison against the snapshot's verified `item["price"]`. The
   direction is model-proposes / engine-vetoes, not engine-computes as ADR-0004
   clause 1 requires. Observed case: a proposal claiming `price: 4242.0`
   against a snapshot price of `100.0` returned `buy` with `execution_scope:
   "actionable"`, and that price was written to the immutable
   `recommendations` table. The first draft of this plan had ADR-0007 state
   that `price` is accepted only on the gold path and verified against a
   merchant quote — that claim is false; it was checked against the running
   code and disproved before being written here.

3. **`_LIMITS["single_name"] = 0.05` makes any ETF sleeve unexecutable.** A
   diversified ETF sleeve typically runs 8-12% per holding, or up to 20% for a
   concentrated top-five rotation. A 5% single-name cap cannot admit any of
   those without holding at most 60% invested at full compliance, which is not
   what an ETF sleeve is for.

4. **`config.py` cannot write TOML.** `config/user.toml` does not exist in
   this repository. `load_config()` returns `present=False`, an empty
   universe, `investable_total_usd=0.0` and `adopted_rule_id=""`. `config.py`
   exposes only `load_config` and `as_dict`, uses read-only `tomllib`, and the
   offline CI matrix forbids adding a TOML writer dependency.

5. **The gate's `complete` condition is unreachable in production.**
   `complete` requires `context["portfolio_complete"] is True`, a non-empty
   `portfolio_version`, a truthy `base_currency`, and per-proposal
   `snapshot_id`/`risk_proposal_fingerprint` matches. `journal.get_context`
   returns `portfolio_complete: False` as a literal and `base_currency: None`,
   and none of `snapshot_id`, `risk_proposal_fingerprint` or
   `verified_risk_inputs` appear in its keys at all — confirmed by running
   `service.context()` directly. Without a remedy the engine can never produce
   a quantity, regardless of how correct its arithmetic is.

6. **Only four of the five measured risk limits are computable for the ETF
   sleeve, and correlation has no informative threshold.** `single_name`,
   `sector`, `liquidity` and `drawdown` all have a real, configured source once
   coverage is declared. Daily-return correlation among broad equity ETFs is
   structurally 0.85-0.95 by construction — QQQ against SPY runs about 0.9. The
   existing 0.7 strict cap rejects every possible basket; a cap loose enough to
   admit one (0.98) rejects nothing and is therefore not a check. The
   informative measure — look-through holdings overlap — has no configured
   source.

## Decision

1. **NYSE Arca is a supported venue.** The whitelist (`SUPPORTED_US_EXCHANGES`
   in `scripts/copilot/providers.py`) accepts `PSE` alongside the pre-existing
   `NASDAQ`, `NYSE` and `AMEX`, plus `ARCA` and `NYSE ARCA` in case Nasdaq
   relabels. **Correction: only `PSE` and `NASDAQ-GM` were ever observed** —
   `NASDAQ`, `NYSE` and `AMEX` predate this ADR's probe, and `ARCA`/`NYSE ARCA`
   are speculative additions for a label Nasdaq could plausibly use but has
   not. An earlier draft of this clause claimed "only labels observed across
   the whole registry were added," which is false on its face for `ARCA` and
   `NYSE ARCA`; the code comment above `SUPPORTED_US_EXCHANGES` already said
   so, and this clause is corrected to match it. Matching is **exact, not a
   prefix**: an initial prefix match (`label.startswith(...)`) let real,
   currently-operating foreign venues collide by name — `PSE.PHILIPPINES`
   (Philippine Stock Exchange), `NASDAQ DUBAI` and `NYSE EURONEXT PARIS` all
   satisfy a prefix test against an accepted label without being one. The
   refusal (`unsupported_exchange_detail`) names the label it saw, so a future
   relabelling is diagnosable from the error alone rather than requiring a
   re-probe.

2. **Concentration limits are per sleeve**: 5% for an individual stock, 25%
   for an ETF. 25% is conventional and unmeasured — it is not derived from a
   backtested drawdown study. The registry contains no `stock` asset class
   today (`instruments.get_instrument` produces only `etf`, `index` and
   `physical_gold`), so in practice this raises the single-holding ceiling to
   25% for every tradable instrument the registry currently allows.

3. **Investable capital means cash.** `investable_total_usd` becomes
   `investable_cash_usd`, matching the backtest engine's own
   `engine.run(start_cash=...)` parameter name, so the live sizing input and
   the backtest input are the same concept under the same name.

4. **Coverage is declared by the user, and expires when holdings move.** A
   declaration states that the recorded holdings for a sleeve are complete and
   names the base currency. It is bound to the `portfolio_version` current at
   declaration time, so recording any trade invalidates it (`journal.py:271-273`
   hashes the active executed operations into that version) and a fresh
   declaration is required before the engine will size again. This keeps
   "only an explicit user action changes what the engine will act on" — the
   principle already in force for operations — while giving the gate the
   `portfolio_complete` and `base_currency` values it needs and has never had.

5. **The engine produces orders; the gate still checks them.**
   `policy.evaluate_rule` computes target weights, current holdings, share
   deltas and the four computable measured risk inputs, then runs one internal
   proposal per symbol through `assess_proposal` with a keyword-only
   `source="engine"`, supplying that proposal's own `snapshot_id` and
   `proposal_fingerprint`. That is legitimate for the engine and not for a
   model, because the engine computed both the trade and the risk from the
   same holdings and prices, so they genuinely correspond. `assess_proposal`
   with the default `source="model"` rejects `quantity`, `target_weight` and
   `stop_loss` outright, and requires any model-supplied `price` to equal the
   snapshot's verified `item["price"]` — which is what closes the ADR-0004
   clause 2 violation recorded in Context finding 2. The MCP surface never
   exposes `source="engine"`; only the engine's own call sites can select it.

6. **A blocked symbol blocks the whole evaluation.** Weights are computed
   across the declared universe, so one symbol with failed, missing or stale
   data makes every other symbol's weight unreliable — weight is not a
   per-symbol quantity, it is a ratio against the whole sleeve. The evaluation
   returns `research_only` with no quantities and names the blocked symbols,
   rather than inventing a partial-basket policy that silently omits one
   holding from the denominator. This is CLAUDE.md's "unknown, failed or
   conflicting data pauses the affected direction" applied to the whole
   sleeve, not just the symbol that failed.

7. **Correlation is not applicable to the ETF sleeve.** Daily-return
   correlation among broad equity ETFs is structurally 0.85-0.95; a 0.7 cap
   rejects every basket and a cap loose enough to admit one rejects nothing.
   The informative measure is look-through holdings overlap, which no
   configured source provides. The check is marked `not_applicable` with that
   reason recorded, rather than given an invented threshold that cannot fail.
   **Consequence, stated plainly: beyond single-name and sector concentration
   the sleeve has no diversification check, and QQQ plus SPY at 25% each will
   pass despite sharing most of their largest holdings.**

8. **Adoption is recorded in the journal, not written to TOML**, because
   `config.py` has no TOML writer and the offline CI matrix forbids adding
   one (Context finding 4). The news brake stays one-way, `{none, reduce_50,
   skip}`, un-backtested per ADR-0005, with its evidence recorded in
   `brake_evidence_ids` because news records carry
   `critical_evidence_eligible: False` and can never be cited as a policy
   reason directly (`policy.py:273`). Both the pre-brake and post-brake
   quantities, the level, the reason and the evidence ids are recorded on
   every decision, and every rendering marks the brake un-backtested.

## Consequences

- The whitelist change moves a safety boundary deliberately: it was rejecting
  the primary listing venue for most US ETFs, not a foreign or unverified one.
  The fix is not scoped to only the labels observed across the registry —
  `ARCA` and `NYSE ARCA` are speculative, kept for plausible relabelling — but
  matching is exact rather than a prefix, so it cannot be widened by a foreign
  venue whose name happens to start with an accepted label.
- **The cross-provider comparison verifies parser and symbol integrity, not
  source independence, and this is a narrower guarantee than the whitelist
  relaxation is often described against.** Live-checked on SPY and QQQ: the
  Yahoo/Nasdaq aligned-close relative differences were `3.2e-09` (SPY) and
  `1.69e-08` (QQQ). One cent of real, independent disagreement on a $550
  instrument would show up as a relative difference roughly 1,200-5,700x
  larger than what was observed; float64 rounding noise on a single
  arithmetic operation would be roughly 1e7x smaller than what was observed. Differences this size are consistent with two vendors
  redistributing the *same* canonical closing print through a chained
  adjustment-factor computation, not two observations formed independently of
  each other. The repository has no field identifying an ultimate data
  vendor — `upstream` is a self-reported HTTP endpoint label, supplied by the
  provider's own code, not a verified vendor identity — so this cannot
  currently be distinguished from genuine independence by inspecting a
  snapshot. What the comparison does establish, and it is real: neither
  provider's parser corrupted the value, and neither fetched the wrong
  symbol. The whitelist relaxation in Decision clause 1 rests on that
  narrower guarantee, not on "two independent sources agree."
- 25% single-ETF exposure with no correlation check is a real concentration
  the user accepted with eyes open (Decision clause 7); this tool will not
  flag a QQQ-plus-SPY basket as concentrated even though it effectively is.
- A stale coverage declaration cannot silently persist, because any trade
  invalidates it (Decision clause 4). But a *wrong* declaration — holdings
  the user mis-reports as complete — will size against a wrong total, and that
  responsibility is the user's; the engine has no independent way to verify
  completeness.
- One bad symbol pauses the whole sleeve's evaluation (Decision clause 6),
  which is conservative and visible rather than silently reweighting around
  the gap.
- Rule adoption requires a manual config edit by design (Decision clause 8);
  there is no adoption UI and none is planned by this ADR.
- The brake has no channel to increase an order: news arguing for buying more
  cannot act, only news arguing for buying less or not at all.
- Position sizing still depends on Task 2 (declared coverage) and Task 8
  (per-proposal context) actually landing; this ADR records the decisions,
  it does not by itself make the gate reachable.

# Rule Engine and News Brake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

> **This is the second draft.** The first was reviewed by five independent critics before any code was written; they found 8 blockers, including one that invalidated its central mechanism. Everything below is the corrected design. The section "What the first draft got wrong" records the failures so nobody reintroduces them.

**Goal:** Turn an adopted, backtested rule plus declared holdings coverage plus a fresh snapshot into `{action, quantity, limit_price, rule_id}` computed entirely by the engine, with a bounded one-way news brake and cost-aware whole-share sizing.

**Architecture:** The engine feeds the existing evidence gate rather than bypassing it. `policy.evaluate_rule` computes target weights, current holdings, share deltas and the five measured risk inputs, then runs one internal proposal per symbol through `assess_proposal` — supplying, per proposal, the `snapshot_id` and `proposal_fingerprint` that gate requires. That is legitimate because the engine computed both the trade and the risk from the same holdings and prices, so they genuinely correspond; it would not be legitimate for a model, which is why a keyword-only `source="engine"` separates the two callers and the MCP surface never exposes it.

The gate's `complete` condition also demands `portfolio_complete` and `base_currency`, which no code can currently produce. Task 2 adds them as an explicit user declaration bound to the current `portfolio_version`, so it expires the moment a trade is recorded.

**Tech Stack:** Python 3.11+ stdlib for the new modules. `scripts/copilot/backtest/engine.CostModel` and `scripts/copilot/backtest/rules` are reused so the live cost basis and the live signal are the ones the backtest was admitted under.

---

## What the first draft got wrong

Recorded because each of these is a mistake a reader would make again.

1. **The gate could never return a quantity.** `policy.py:370` echoes `quantity` only when `executable`, which needs `complete`, which needs `context["risk_proposal_fingerprint"] == proposal_fingerprint(proposal)` — a per-proposal value. The draft built N proposals against one context, so at most one symbol could ever pass. And in production none could: `service.context()` returns `portfolio_complete: False` as a literal, `base_currency: None`, and no `snapshot_id`, `risk_proposal_fingerprint` or `verified_risk_inputs` keys at all. Verified by running it. Fixed by Task 2 (declared coverage) and Task 8 (per-proposal context).

2. **Quantity and direction meant different things.** `plan_orders` computed "how many shares to end up holding" from cash alone, while `_order_action` compared target weight against *holdings market value* weight. Rendered together as `QQQ reduce 17 shares`, which a person reads as "sell 17" and the engine meant "hold 17 at the end". Fixed by Task 6: orders are deltas against held shares, and every weight uses one denominator (holdings market value + investable cash).

3. **Bars were read from a key that does not exist.** The draft read `snapshot["instruments"][sym]["bars"]`. Bars live on the *evidence* records (`market_data._public_evidence` copies the whole source dict); the instrument item has only `quality_status/latest_session/expected_session/price/indicators/evidence_ids/issues/sources`. Fixed by Task 8's `_primary_bars`, which selects the primary record by the same rule `market_data.py:298-300` uses.

4. **The strongest brake setting crashed the evaluation.** The draft wrote the post-brake quantity into a proposal while gating on the pre-brake quantity, so `skip` (which returns 0) hit `policy.py:190`'s positive-number check and raised. Its own test used `skip`. Fixed by Task 8: the brake is applied before the proposal is built, and a zero result becomes `hold` with no quantity.

5. **A pause became a crash.** Weights were computed before the blocked-symbol check, so a symbol missing from the snapshot raised instead of returning `blocked_symbols`. That breaks CLAUDE.md's "unknown, failed or conflicting data pauses the affected direction" — pausing is not raising.

6. **Every new test would have failed on its first line.** The real helper is `complete_context(snapshot, p=None)` with a required positional argument, and every policy test must pass `now=NOW` or the fixture's `valid_until` of 2026-09-06T12:00 has already expired against wall-clock time. The draft called `complete_context()` and omitted `now=`.

7. **A per-sleeve limit change turns an existing test red.** `_test_policy.py:189-195` sets QQQ's `post_trade_weight` to 0.2 and asserts `hold`. Under a 25% ETF limit that becomes `pass` and the action stays `buy`. Task 3 names the test and fixes its fixture rather than letting an implementer weaken it.

8. **An ADR clause said the opposite of the code.** The draft had ADR-0007 state that `price` is accepted only on the gold path and verified against a merchant quote. Verified false: `policy.py:366-369` echoes a model-supplied `price` for any non-gold instrument with no comparison against `item["price"]` — a proposal claiming 4242.0 against a snapshot price of 100.0 returns `actionable`. Writing a false decision record is worse than leaving a known defect, because it tells a future reader ADR-0004 clause 2 is closed. Task 3 fixes the code; Task 1 records what is actually true.

---

## Verified facts this plan is built on

Observed on 2026-09-19/20 by reading the code or running it. Do not re-derive; do not "improve" away.

### The data layer blocks most of the whitelist today

`providers.py:503` requires the Nasdaq-reported exchange to start with `NASDAQ`, `NYSE` or `AMEX`. Probed live across **all 39 registry symbols**, exactly two labels exist:

| Label | Count | Meaning |
|---|---|---|
| `PSE` | 29 | NYSE Arca — the legacy Pacific Exchange code Nasdaq still reports |
| `NASDAQ-GM` | 9 | Nasdaq Global Market |
| symbol mismatch | 1 | SPLG — Nasdaq does not know it either |

So 29 of 39 get `ProviderError("not_covered", ...)`, leaving Yahoo as the only upstream. `market_data.py:352` then requires `authoritative or (len(upstreams) >= 2 and any(c["status"] == "pass" ...))`, and `authoritative` is only ever true for SGE and the Nasdaq index publisher. The item gets `independent_price_confirmation_missing`, `policy` emits `"required instrument data has not passed quality checks"`, and `action` becomes `"data_insufficient"` — worse than the `research_only` ADR-0004 anticipates. Of the 22 admissible equity ETFs only QQQ, SMH and SOXX are Nasdaq-listed.

**SPLG failing on a second independent provider confirms Plan 3's `UNFETCHABLE` tier.** Yahoo returns HTTP 404; Nasdaq returns a body whose `data` does not carry the requested symbol.

### The bar and price shapes

- Yahoo bar row: `{"session", "open", "high", "low", "close", "volume", "adjusted_close"}`, with `indicator_basis: "total_return_adjusted"` (`providers.py:342-353`).
- Nasdaq equity record: **one** bar, `{"session", "close"}`, `indicator_basis: "single_current_session"` (`providers.py:538`).
- `primary` is chosen by `max(successes, key=(not missing_sessions, len(bars) >= minimum_history, indicator_basis == "total_return_adjusted", len(bars)))` (`market_data.py:298-300`). Nasdaq's one-bar record loses on the history test, so primary is effectively always Yahoo.
- `item["price"] = primary["bars"][-1]["close"]` — the **split-adjusted, not dividend-adjusted** close. `compute_indicators` uses `adjusted_close` when the basis is `total_return_adjusted` (`market_data.py:50`). Price and indicators therefore sit on different bases; ADR-0006 clause 3 already names this and forbids calling either one "raw".
- Bars reach a snapshot through `_public_evidence` (`market_data.py:186-191`), which copies the source dict and adds `history_sha256`. `service.snapshot_view` strips them unless `full=True` (`service.py:107-118`).

### The policy gate

- `assess_proposal(proposal, snapshot, context=None, *, now=None)` at `policy.py:164`. `ACTIONS = {"buy","hold","reduce","sell","avoid"}` — no `skip`, no `rebalance`.
- `complete` requires all of: `context["portfolio_complete"] is True`, a non-empty `portfolio_version` string, a truthy `base_currency`, `context["snapshot_id"] == snapshot["snapshot_id"]`, and `context["risk_proposal_fingerprint"] == proposal_fingerprint(proposal)` (`policy.py:316-318`).
- `proposal_fingerprint` hashes exactly `("instrument_id","action","mode","horizon","price","quantity","target_weight","stop_loss")` (`policy.py:53-55`).
- `executable` needs no blockers, a non-index asset class, `tradable is True` or a verified retail quote, and every one of `[*_LIMITS, "stop"]` in `{"pass","not_applicable"}` (`policy.py:352`).
- `_LIMITS` (`policy.py:17-23`): `single_name`/`post_trade_weight` ≤ 0.05; `sector`/`post_trade_sector_weight` ≤ 0.25; `correlation`/`max_correlation` < 0.7 strict; `liquidity`/`position_adv_fraction` ≤ 0.01; `drawdown`/`drawdown` ≤ 0.15. A value outside [0,1] or non-numeric becomes `"unknown"`, not `"fail"`.
- `checks["stop"]` is `not_applicable` when `mode == "accumulation"` or the action is not `buy` (`policy.py:329`). An accumulation proposal therefore needs no stop.
- `policy.py:134-160` strips `_IDENTITY_OR_DATE` tokens from `reasons`/`conditions` and requires every remaining literal number to match a verified claim. `_IDENTITY_OR_DATE` (`policy.py:87-93`) knows `snap_`, `ev_`, `research-`, `decision-` — not a rule prefix.
- News evidence can never be cited: `research_data.company_news` passes `critical=False` (`research_data.py:351` into `:97`) and `policy.py:273` blocks a proposal citing such a record.

### Journal

- Seven tables. `_SCHEMA` re-runs on every connection open and is all `CREATE ... IF NOT EXISTS` (`journal.py:159`), so adding a table is safe for existing databases.
- `_portfolio_version` hashes the active executed operations (`journal.py:271-273`), so it changes whenever a trade is recorded. Binding anything to it makes that thing expire on a holdings change.
- `get_context`'s completeness fields are literals (`journal.py:476`): `portfolio_complete: False`, `completeness: "unknown"`, `base_currency: None`, `fx_status: "unknown"`, `portfolio_value: None`. Confirmed by running `service.context()`: the returned keys are `as_of, base_currency, completeness, fx_status, holdings, holdings_by_currency, intents, operations, pending_operations, portfolio_complete, portfolio_value, portfolio_version, recommendations` — no `snapshot_id`, no `risk_proposal_fingerprint`, no `verified_risk_inputs`.
- `record_recommendation` requires non-empty `snapshot_id`, `portfolio_version`, `instrument_id`, `action` (`journal.py:507-509`), an already-stored snapshot, and an unchanged portfolio version. One row per instrument, each in its own transaction (`journal.py:513-521`).
- `recommendations` has immutability triggers, so its shape cannot be migrated later. `get_context` reads it unbounded and filters by instrument in Python (`journal.py:468-475`).
- Decimal-string enforcement (`journal.py:61-72`) runs only on the operation path's `quantity/price/fees/purity/weight_grams`. The recommendation path does no numeric validation.
- `decision_id` prefixes disagree: `journal.py:511` generates `"decision_"`, `policy.py:376` and `service.py:131` use `"decision-"`. Always pass an explicit id.

### Configuration and facades

- `config/user.toml` does not exist. `load_config()` returns `present=False`, `universe=()`, `investable_total_usd=0.0`, `adopted_rule_id=""`. `config.py` has only `load_config` and `as_dict` and uses read-only `tomllib`; there is no TOML writer in the repository and the offline CI matrix forbids adding one.
- `load_config` checks `schema_version`, then that all three of `[etf]`, `[gold]`, `[notify]` are present, and only then validates fields. A fragment missing `schema_version` raises `"schema_version must be 1, got None"`, not a field error.
- `_test_config.py` has `self.write(text)` and a module-level `VALID` template at line 19.
- Exactly 6 MCP tools, each a one-line delegation with no validation and no try/except. A 7th needs four coordinated edits with **no automatic cross-check**: `scripts/sync_runtimes.py`'s hardcoded tools list, `.claude/settings.json`'s allow array, `scripts/copilot_probe.py`'s required set, and `.claude/skills/investment-chat/SKILL.md`.
- `scripts/check.py` holds a closed set of 5 expected command files.
- A snapshot carrying research is capped at 30 minutes: `valid_until = min(expiry, now + 30min)` (`service.py:89-90`).
- The generated Codex config sets `tool_timeout_sec = 180`.
- `instruments.get_instrument` produces only `etf`, `index` and `physical_gold` — there is no `stock` in the registry (`instruments.py:75,84`).

### Test fixture shapes

- `_test_policy.py`: `NOW = datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc)`; `fixture(symbol="QQQ")` seals a snapshot whose `valid_until` is `2026-09-06T12:00:00+00:00`. Every test must pass `now=NOW`.
- `fixture()`'s single evidence record has **no** `instrument_id` and **no** `bars`. The JEPI test adds identity fields explicitly. Any fixture used by `evaluate_rule` must add both.
- `complete_context(snapshot, p=None)` takes a required positional snapshot and hardcodes `verified_risk_inputs`.
- `proposal(symbol="QQQ", action="buy")` takes no numeric keyword arguments.

### The decisions the user made

1. **Relax the exchange whitelist** to accept NYSE Arca (2026-09-19).
2. **Per-sleeve concentration**: 5% for a stock, **25% for an ETF** (2026-09-19).
3. **`investable_total_usd` means investable cash** and is renamed `investable_cash_usd` (2026-09-19).
4. **Add an explicit user coverage declaration** so quantities can be produced at all, rather than shipping a permanently `research_only` engine or dropping the risk checks (2026-09-20).

---

## Which risk checks the ETF sleeve can actually compute

`executable` requires every one of the five `_LIMITS` plus `stop` to be `pass` or `not_applicable`. This is the arithmetic, and it is why Task 3 and Task 7 look the way they do.

| Check | Computable? | Decision |
|---|---|---|
| `single_name` | Yes, once coverage is declared there is a denominator | Compute. Limit 25% for ETFs (user decision 2). |
| `sector` | Yes, with a static sector map — the nine `XL*` funds are single-sector by construction, broad funds are not | Compute. Task 3 adds the map. Limit stays 25%. |
| `liquidity` | Yes — Yahoo bars carry `volume`, so order notional over average dollar volume is real | Compute. Limit stays 1%. |
| `drawdown` | Yes, from the portfolio value recorded on each past evaluation. One observation means the drawdown genuinely is zero. | Compute, and report the sample count so a short history is visible. Limit stays 15%. |
| `correlation` | Computable, but **no threshold is informative** | `not_applicable` for the ETF sleeve, with the reason recorded. |

The correlation decision needs stating plainly because it disables a risk check. Daily-return correlation among broad equity ETFs is structurally 0.85–0.95 by construction — QQQ against SPY is about 0.9. A 0.7 cap rejects every possible basket; a cap loose enough to admit one (0.98) rejects nothing. The measure that would be informative is look-through holdings overlap, and no configured source provides fund constituents. Inventing 0.98 would be a check that cannot fail dressed as one that can.

**The consequence the user accepted:** beyond single-name 25% and sector 25%, the ETF sleeve has no diversification check. QQQ and SPY at 25% each will pass, despite sharing most of their largest holdings. ADR-0007 clause 7 records this.

---

## File Structure

| Path | Responsibility |
|---|---|
| `docs/adr/0007-engine-produced-orders.md` | The four user decisions, the ADR-0004 clause-2 remedy, the all-or-nothing universe gate, and the disabled correlation check |
| `scripts/copilot/ruleset.py` | `rule_id` derivation and the adoption record. Pure. |
| `scripts/copilot/sizing.py` | Share deltas against held shares, cost-aware. Pure. |
| `scripts/copilot/brake.py` | One-way news brake. Pure. |
| `scripts/copilot/riskinputs.py` | The four computable measured risk inputs. Pure. |
| `scripts/copilot/policy.py` | Modified: per-sleeve limits, `rule-` token, `source=` discipline, price binding, `evaluate_rule` |
| `scripts/copilot/journal.py` | Modified: `adopted_rules` and `coverage_declarations` tables, bounded recommendations |
| `scripts/copilot/instruments.py` | Modified: static sector map |
| `scripts/copilot/providers.py` | Modified: exchange whitelist accepts NYSE Arca |
| `scripts/copilot/config.py` | Modified: field rename, pointer format check |
| `scripts/copilot/service.py` | Modified: `declare_coverage`, `adopt`, `evaluate` |
| `mcps/copilot_mcp.py`, `scripts/copilot_cli.py` | Modified: two new tools, three new subcommands |
| `scripts/_test_ruleset.py` | New offline suite for ruleset, sizing, brake and riskinputs |
| `scripts/_test_policy.py`, `_test_journal.py`, `_test_config.py`, `_test_market_data.py`, `_test_copilot_service.py` | Extended |

---

### Task 1: ADR-0007 and the exchange whitelist

The decision record lands first, with the smallest defect fix it authorises.

**Files:**
- Create: `docs/adr/0007-engine-produced-orders.md`
- Modify: `scripts/copilot/providers.py:502-504`
- Test: `scripts/_test_market_data.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_market_data.py`, following the file's existing class style and its already-imported `HttpClient`/`NasdaqEquityProvider`/`ProviderError`/`mock.patch`:

```python
class ExchangeWhitelist(unittest.TestCase):
    def test_nyse_arca_is_accepted(self):
        # Probed live across all 39 registry symbols on 2026-09-19: Nasdaq
        # reports exactly two labels, PSE (29) and NASDAQ-GM (9). PSE is the
        # legacy Pacific Exchange code for NYSE Arca, the primary listing venue
        # for most ETFs. Rejecting it left Yahoo as the only upstream, so
        # cross-provider confirmation was impossible and 19 of the 22
        # admissible ETFs returned data_insufficient.
        from copilot.providers import SUPPORTED_US_EXCHANGE_PREFIXES, is_supported_us_exchange
        self.assertTrue(is_supported_us_exchange("PSE"))
        self.assertTrue(is_supported_us_exchange("NASDAQ-GM"))
        self.assertTrue(is_supported_us_exchange("NYSE ARCA"))
        self.assertIn("PSE", SUPPORTED_US_EXCHANGE_PREFIXES)

    def test_an_unknown_venue_is_still_refused(self):
        from copilot.providers import is_supported_us_exchange
        for label in ("", "LSE", "TSX", "XETRA", "HKEX", "UNKNOWN"):
            self.assertFalse(is_supported_us_exchange(label), label)

    def test_the_refusal_names_the_label_it_saw(self):
        from copilot.providers import unsupported_exchange_detail
        self.assertIn("XETRA", unsupported_exchange_detail("XETRA"))

    def test_the_provider_itself_accepts_a_pse_identity(self):
        # The three tests above would pass if the helpers existed and
        # providers.py:503 were never changed. This one drives the real code
        # path with a mocked HTTP layer.
        from copilot.providers import NasdaqEquityProvider, ProviderError
        client = mock.Mock()
        client.get.return_value = {"body": json.dumps({"data": {
            "symbol": "SPY", "assetClass": "ETF", "exchange": "PSE",
            "primaryData": {"lastSalePrice": "$500.00"}}})}
        provider = NasdaqEquityProvider(client)
        # The identity gate must not be what stops us. Anything raised here must
        # come from the later historical fetch, not from the exchange check.
        with self.assertRaises(Exception) as caught:
            provider.fetch("SPY", "2026-09-04", "2026-09-04")
        self.assertNotIn("supported US exchange", str(caught.exception))

    def test_the_provider_still_refuses_a_foreign_identity(self):
        from copilot.providers import NasdaqEquityProvider, ProviderError
        client = mock.Mock()
        client.get.return_value = {"body": json.dumps({"data": {
            "symbol": "SPY", "assetClass": "ETF", "exchange": "XETRA",
            "primaryData": {"lastSalePrice": "$500.00"}}})}
        with self.assertRaises(ProviderError) as caught:
            NasdaqEquityProvider(client).fetch("SPY", "2026-09-04", "2026-09-04")
        self.assertIn("XETRA", str(caught.exception))
```

Read `NasdaqEquityProvider`'s real constructor and `fetch` signature before writing these two provider-level tests and match them; the shapes above are the intent, not a promise about the signature. Add `import json` and `from unittest import mock` if the file lacks them.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_market_data.py -v
```

Expected: `ImportError: cannot import name 'SUPPORTED_US_EXCHANGE_PREFIXES'`.

- [ ] **Step 3: Fix the whitelist**

In `scripts/copilot/providers.py`, above the provider classes, add:

```python
#: Every exchange label Nasdaq reported across all 39 registry symbols when
#: probed on 2026-09-19: PSE for 29, NASDAQ-GM for 9, and one symbol (SPLG)
#: Nasdaq does not know at all. PSE is the legacy Pacific Exchange code for
#: NYSE Arca, a registered US national securities exchange and the primary
#: listing venue for most ETFs; omitting it left Yahoo as the only upstream, so
#: cross-provider confirmation was impossible for 29 of 39 symbols. "ARCA" and
#: "NYSE ARCA" are here because Nasdaq could modernise the label. Nothing else
#: speculative was added: a whitelist that guesses is not one.
SUPPORTED_US_EXCHANGE_PREFIXES = ("NASDAQ", "NYSE", "AMEX", "ARCA", "PSE")


def is_supported_us_exchange(label: str) -> bool:
    return str(label or "").upper().startswith(SUPPORTED_US_EXCHANGE_PREFIXES)


def unsupported_exchange_detail(label: str) -> str:
    """Name the label so a future relabelling is diagnosable from the error alone."""
    return (f"Nasdaq reported exchange {label!r}, which is not one of the supported "
            f"US venues {SUPPORTED_US_EXCHANGE_PREFIXES}")
```

Then replace `providers.py:502-504`:

```python
            exchange = str(identity.get("exchange", "")).upper()
            if not exchange.startswith(("NASDAQ", "NYSE", "AMEX")):
                raise ProviderError("not_covered", "Nasdaq did not identify a supported US exchange")
```

with:

```python
            exchange = str(identity.get("exchange", "")).upper()
            if not is_supported_us_exchange(exchange):
                raise ProviderError("not_covered", unsupported_exchange_detail(exchange))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_market_data.py -v
```

Expected: 5 new tests, OK.

- [ ] **Step 5: Confirm live that a blocked symbol now passes quality**

This is the point of the task. Do not skip it.

```bash
python scripts/copilot_cli.py snapshot SPY QQQ --horizon daily
```

Expected: SPY's instrument entry now reports `"quality_status": "pass"` with two upstreams and a passing comparison, where it previously carried `independent_price_confirmation_missing`. Report the actual `quality_status`, the upstream count and the comparison for both symbols, and note SPY's `price` so Task 8's fixtures can be built against a realistic magnitude. **If SPY still fails, stop and report** — nothing downstream has an acceptance path without it.

- [ ] **Step 6: Write ADR-0007**

Create `docs/adr/0007-engine-produced-orders.md` in the house format (`# ADR-NNNN: Title` / `Status: accepted, YYYY-MM-DD.` / `## Context` / `## Decision` / `## Consequences`).

**Context** — six findings, each with its evidence:
1. The exchange-whitelist blockage, with the 29/9/1 label distribution and the 19-of-22 consequence.
2. That ADR-0004 clause 2 is violated today: `policy.py:190` validates four model-supplied numeric keys and `policy.py:366-373` echoes them, and the direction is model-proposes / engine-vetoes. Include the observed case — a proposal claiming `price: 4242.0` against a snapshot price of `100.0` returned `buy` with `execution_scope: "actionable"` and the price was written to an immutable table.
3. That `_LIMITS["single_name"] = 0.05` makes any ETF sleeve unexecutable (8–12% per holding, 20% for a top-5 rotation, at most 60% invested at full compliance).
4. That `config.py` cannot write TOML and `config/user.toml` does not exist.
5. That the gate's `complete` condition is unreachable in production: `get_context` returns `portfolio_complete: False` as a literal, `base_currency: None`, and none of `snapshot_id`/`risk_proposal_fingerprint`/`verified_risk_inputs` — verified by running `service.context()`. Without a remedy the engine can never produce a quantity.
6. That of the five measured risk limits, only four are computable from configured sources, and correlation has no informative threshold for an equity-only sleeve.

**Decision** — eight clauses:
1. **NYSE Arca is a supported venue.** The whitelist accepts `PSE`/`ARCA` alongside `NASDAQ`/`NYSE`/`AMEX`. Only labels observed across the whole registry were added. The refusal names the label it saw.
2. **Concentration limits are per sleeve**: 5% for an individual stock, 25% for an ETF. Record that 25% is conventional and unmeasured, and that the registry contains no `stock` asset class today, so in practice this raises the single-holding ceiling to 25% for every tradable instrument the registry allows.
3. **Investable capital means cash.** `investable_total_usd` becomes `investable_cash_usd`, matching `engine.run(start_cash=...)`.
4. **Coverage is declared by the user, and expires when holdings move.** A declaration states that the recorded holdings for a sleeve are complete and names the base currency. It is bound to the `portfolio_version` current at declaration time, so recording any trade invalidates it and a fresh declaration is required. This keeps "only an explicit user action changes what the engine will act on" while giving the gate the `portfolio_complete` and `base_currency` it needs.
5. **The engine produces orders; the gate still checks them.** `policy.evaluate_rule` computes weights, share deltas and the measured risk inputs, then runs one internal proposal per symbol through `assess_proposal` with `source="engine"`, supplying per-proposal `snapshot_id` and `proposal_fingerprint`. That is legitimate for the engine and not for a model, because the engine computed both the trade and the risk from the same holdings and prices. `assess_proposal` with the default `source="model"` rejects `quantity`, `target_weight` and `stop_loss`, and requires any model-supplied `price` to equal the snapshot's `item["price"]` — which is what closes ADR-0004 clause 2.
6. **A blocked symbol blocks the whole evaluation.** Weights are computed across the universe, so one symbol with failed, missing or stale data makes every weight unreliable. The evaluation returns `research_only` with no quantities and names the blocked symbols, rather than inventing a partial-basket policy.
7. **Correlation is not applicable to the ETF sleeve.** Daily-return correlation among broad equity ETFs is structurally 0.85–0.95; a 0.7 cap rejects every basket and a cap loose enough to admit one rejects nothing. The informative measure is look-through holdings overlap, which no configured source provides. The check is marked `not_applicable` with that reason rather than given an invented threshold. **Consequence, stated plainly: beyond single-name and sector concentration the sleeve has no diversification check, and QQQ plus SPY at 25% each will pass despite sharing most of their largest holdings.**
8. **Adoption is recorded in the journal, not written to TOML**, and the news brake is one-way, `{none, reduce_50, skip}`, un-backtested, with its evidence in `brake_evidence_ids` because news records carry `critical_evidence_eligible: False`. Both quantities, the level, the reason and the evidence ids are recorded on every decision, and every rendering marks it un-backtested per ADR-0005.

**Consequences** — at least: the whitelist change moves a safety boundary deliberately; 25% single-ETF exposure with no correlation check is a real concentration the user accepted; a stale coverage declaration cannot silently persist because any trade invalidates it, but a *wrong* declaration will size against a wrong total and that responsibility is the user's; one bad symbol pauses the sleeve, which is conservative and visible; adoption requires a manual config edit by design; and the brake has no channel to increase an order, so news arguing for buying more cannot act.

- [ ] **Step 7: Run the gates**

```bash
python scripts/check.py && python -m unittest discover -s scripts -p "_test_*.py"
```

- [ ] **Step 8: Commit**

```bash
git add docs/adr/0007-engine-produced-orders.md scripts/copilot/providers.py scripts/_test_market_data.py
git commit -m "fix(providers): accept NYSE Arca as a US venue, record ADR-0007"
```

---

### Task 2: Declared holdings coverage

Without this the engine can never produce a quantity, because `policy`'s `complete` gate needs `portfolio_complete` and `base_currency` and nothing can currently produce either. A declaration is the user asserting that what is recorded is everything; it is bound to the `portfolio_version` current at the time, so any subsequent trade invalidates it.

**Files:**
- Modify: `scripts/copilot/journal.py` (`_SCHEMA`, `record_coverage_declaration`, `get_context`)
- Modify: `scripts/copilot/service.py` (`declare_coverage`)
- Modify: `scripts/_test_journal.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_journal.py`, reusing the file's existing temporary-database fixture — read it first, and never touch `data/state/copilot.sqlite`:

```python
class CoverageDeclarations(unittest.TestCase):
    def test_an_empty_journal_reports_unknown_coverage(self):
        with self.database() as db:
            context = get_context(db_path=db)
            self.assertFalse(context["portfolio_complete"])
            self.assertEqual(context["completeness"], "unknown")
            self.assertIsNone(context["base_currency"])

    def test_a_declaration_makes_coverage_complete(self):
        with self.database() as db:
            version = get_context(db_path=db)["portfolio_version"]
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=version, db_path=db)
            context = get_context(db_path=db)
            self.assertTrue(context["portfolio_complete"])
            self.assertEqual(context["completeness"], "declared")
            self.assertEqual(context["base_currency"], "USD")

    def test_recording_a_trade_invalidates_the_declaration(self):
        # This is the whole safety property: a declaration cannot go stale
        # silently, because _portfolio_version hashes the executed operations.
        with self.database() as db:
            version = get_context(db_path=db)["portfolio_version"]
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=version, db_path=db)
            self.assertTrue(get_context(db_path=db)["portfolio_complete"])
            record_operation(self.operation(), "key-after-declaration", db_path=db)
            context = get_context(db_path=db)
            self.assertFalse(context["portfolio_complete"])
            self.assertEqual(context["completeness"], "stale_declaration")
            self.assertIsNone(context["base_currency"])

    def test_declaring_against_a_version_that_is_not_current_is_refused(self):
        with self.database() as db:
            with self.assertRaisesRegex(JournalConflict, "portfolio_version"):
                record_coverage_declaration(sleeve="etf", base_currency="USD",
                                            portfolio_version="not-the-current-version",
                                            db_path=db)

    def test_redeclaring_after_a_trade_restores_coverage(self):
        with self.database() as db:
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=get_context(db_path=db)["portfolio_version"],
                                        db_path=db)
            record_operation(self.operation(), "key-1", db_path=db)
            self.assertFalse(get_context(db_path=db)["portfolio_complete"])
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=get_context(db_path=db)["portfolio_version"],
                                        db_path=db)
            self.assertTrue(get_context(db_path=db)["portfolio_complete"])

    def test_a_declaration_is_immutable(self):
        with self.database() as db:
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=get_context(db_path=db)["portfolio_version"],
                                        db_path=db)
            with _connection(db) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE coverage_declarations SET base_currency='CNY'")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM coverage_declarations")

    def test_the_history_of_declarations_is_readable(self):
        # An auditor must be able to see what was declared and when, including
        # declarations that later went stale.
        with self.database() as db:
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=get_context(db_path=db)["portfolio_version"],
                                        db_path=db)
            record_operation(self.operation(), "key-1", db_path=db)
            record_coverage_declaration(sleeve="etf", base_currency="USD",
                                        portfolio_version=get_context(db_path=db)["portfolio_version"],
                                        db_path=db)
            history = coverage_history(db_path=db)
            self.assertEqual(len(history), 2)
            self.assertTrue(history[0]["current"])
            self.assertFalse(history[1]["current"])

    def test_an_unsupported_base_currency_is_refused(self):
        with self.database() as db:
            for bad in ("", "usd", "EURO", "US$"):
                with self.assertRaisesRegex(ValueError, "base_currency"):
                    record_coverage_declaration(
                        sleeve="etf", base_currency=bad,
                        portfolio_version=get_context(db_path=db)["portfolio_version"],
                        db_path=db)

    def test_an_unsupported_sleeve_is_refused(self):
        with self.database() as db:
            with self.assertRaisesRegex(ValueError, "sleeve"):
                record_coverage_declaration(
                    sleeve="crypto", base_currency="USD",
                    portfolio_version=get_context(db_path=db)["portfolio_version"],
                    db_path=db)
```

`self.operation()` must be an executed operation on a registry symbol so that `_portfolio_version` actually changes; check the file's existing operation fixture and confirm it has `execution_status: "executed"` — a pending one does not move the version, because `_portfolio_version` filters on `had_execution`.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_journal.py -v
```

Expected: `ImportError: cannot import name 'record_coverage_declaration'`.

- [ ] **Step 3: Add the table**

In `scripts/copilot/journal.py`, inside `_SCHEMA` after the `recommendations` table:

```sql
CREATE TABLE IF NOT EXISTS coverage_declarations (
    declaration_id TEXT PRIMARY KEY,
    sleeve TEXT NOT NULL,
    base_currency TEXT NOT NULL,
    portfolio_version TEXT NOT NULL,
    declared_at TEXT NOT NULL
);
```

and after the recommendation triggers:

```sql
CREATE TRIGGER IF NOT EXISTS coverage_no_update BEFORE UPDATE ON coverage_declarations
BEGIN SELECT RAISE(ABORT, 'coverage declarations are immutable; declare again instead'); END;
CREATE TRIGGER IF NOT EXISTS coverage_no_delete BEFORE DELETE ON coverage_declarations
BEGIN SELECT RAISE(ABORT, 'coverage declarations are immutable; declare again instead'); END;
```

- [ ] **Step 4: Add the functions**

Add to `scripts/copilot/journal.py`:

```python
COVERAGE_SLEEVES = ("etf",)
#: One currency per declaration. A mixed-currency sleeve has no single total to
#: size against, and CLAUDE.md already forbids adding USD and CNY without dated
#: FX and a declared base currency.
COVERAGE_CURRENCIES = ("USD",)


def record_coverage_declaration(*, sleeve: str, base_currency: str,
                                portfolio_version: str, db_path: Any = None) -> dict:
    """Record the user's assertion that a sleeve's holdings are fully recorded.

    This does not change holdings, so it is not an operation: it states that
    what has already been recorded is everything. It is bound to the
    portfolio_version current at declaration time, and _portfolio_version hashes
    the executed operations, so recording any trade afterwards makes the
    declaration no longer current. A declaration therefore cannot go stale
    silently -- the failure mode is "coverage unknown again", not "sized against
    a book that moved".

    A declaration that does not match the current version is refused rather
    than stored, so the table never holds a claim that was wrong when made.
    """
    if sleeve not in COVERAGE_SLEEVES:
        raise ValueError(f"sleeve must be one of {COVERAGE_SLEEVES}, got {sleeve!r}")
    if base_currency not in COVERAGE_CURRENCIES:
        raise ValueError(f"base_currency must be one of {COVERAGE_CURRENCIES}, "
                         f"got {base_currency!r}")
    if not isinstance(portfolio_version, str) or not portfolio_version:
        raise ValueError("portfolio_version must be a nonempty string")
    with _connection(db_path) as connection:
        with _transaction(connection):
            current = _portfolio_version(_states(connection))
            if portfolio_version != current:
                raise JournalConflict(
                    "portfolio_version does not match the current holdings; read the "
                    "current context and declare against that version")
            declared_at = _stamp()
            declaration_id = "coverage-" + _hash(
                {"sleeve": sleeve, "base_currency": base_currency,
                 "portfolio_version": portfolio_version, "declared_at": declared_at})
            connection.execute("INSERT OR IGNORE INTO coverage_declarations VALUES (?,?,?,?,?)",
                               (declaration_id, sleeve, base_currency, portfolio_version,
                                declared_at))
        return {"declaration_id": declaration_id, "sleeve": sleeve,
                "base_currency": base_currency, "portfolio_version": portfolio_version,
                "declared_at": declared_at, "recorded": True}


def coverage_history(*, db_path: Any = None) -> list[dict]:
    """Every declaration, newest first, each flagged with whether it is current."""
    with _connection(db_path) as connection:
        current = _portfolio_version(_states(connection))
        rows = connection.execute(
            "SELECT declaration_id, sleeve, base_currency, portfolio_version, declared_at "
            "FROM coverage_declarations ORDER BY declared_at DESC, declaration_id").fetchall()
        return [{"declaration_id": row[0], "sleeve": row[1], "base_currency": row[2],
                 "portfolio_version": row[3], "declared_at": row[4],
                 "current": row[3] == current} for row in rows]
```

Add `_hash` to the imports only if it is not already module-local; it is used elsewhere in the file, so it should already be available.

- [ ] **Step 5: Teach `get_context` about declarations**

In `get_context`, replace the hardcoded completeness literals. The existing line is:

```python
            result = {"portfolio_version": portfolio_version, "portfolio_complete": False, "completeness": "unknown", "base_currency": None, "fx_status": "unknown", "portfolio_value": None, "holdings": holdings, ...
```

Compute the coverage state before building `result`:

```python
            # Coverage is a user declaration bound to a portfolio_version. Any
            # executed trade changes that version, so a declaration made before
            # it is reported as stale rather than silently honoured.
            declared = connection.execute(
                "SELECT base_currency, portfolio_version FROM coverage_declarations "
                "ORDER BY declared_at DESC, declaration_id LIMIT 1").fetchone()
            if declared and declared[1] == portfolio_version:
                complete, completeness, base_currency = True, "declared", declared[0]
            elif declared:
                complete, completeness, base_currency = False, "stale_declaration", None
            else:
                complete, completeness, base_currency = False, "unknown", None
```

and use `"portfolio_complete": complete, "completeness": completeness, "base_currency": base_currency` in the returned dict. Leave `fx_status` and `portfolio_value` as they are — a single-currency declaration does not establish an FX status, and `portfolio_value` is computed by the engine from a snapshot, not by the journal.

- [ ] **Step 6: Add `service.declare_coverage`**

```python
def declare_coverage(*, sleeve: str = "etf", base_currency: str = "USD",
                     portfolio_version: str | None = None, db_path=None) -> dict:
    """Declare that a sleeve's recorded holdings are complete.

    Omitting portfolio_version reads the current one, which is the normal path.
    Passing one explicitly lets a caller assert WHICH book it inspected, so a
    declaration written against a stale reading is refused rather than silently
    applied to a book that moved in between.
    """
    from .journal import coverage_history, record_coverage_declaration
    path = database_path(db_path)
    version = portfolio_version or context(db_path=db_path)["portfolio_version"]
    receipt = record_coverage_declaration(sleeve=sleeve, base_currency=base_currency,
                                          portfolio_version=version, db_path=path)
    receipt["history"] = coverage_history(db_path=path)
    receipt["message"] = (
        f"已记录 {sleeve} sleeve 的持仓覆盖声明（基准货币 {base_currency}）。"
        "任何新成交都会让这条声明失效，届时需要重新声明。")
    return receipt
```

- [ ] **Step 7: Run the tests**

```bash
python scripts/_test_journal.py -v && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: 9 new journal tests OK. **Existing tests that assert `portfolio_complete is False` or `completeness == "unknown"` still pass**, because an empty journal has no declaration — confirm that by name and report any that did not.

- [ ] **Step 8: Commit**

```bash
git add scripts/copilot/journal.py scripts/copilot/service.py scripts/_test_journal.py
git commit -m "feat(journal): user-declared holdings coverage that expires when holdings move"
```

---

### Task 3: Per-sleeve risk limits, the sector map, and the price binding

Three changes to `policy.py` that together make the ETF sleeve capable of passing `executable` at all, plus the fix that closes ADR-0004 clause 2.

**Files:**
- Modify: `scripts/copilot/instruments.py` (sector map)
- Modify: `scripts/copilot/policy.py` (`_LIMITS` per sleeve, correlation `not_applicable`, price binding, `source=`)
- Modify: `scripts/_test_policy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_policy.py`. Note every call passes `now=NOW` and a snapshot into `complete_context`:

```python
class SleeveLimits(unittest.TestCase):
    def test_etf_single_name_limit_is_twenty_five_percent(self):
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("single_name", "etf"), 0.25)

    def test_stock_single_name_limit_is_unchanged(self):
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("single_name", "stock"), 0.05)

    def test_only_single_name_differs_by_sleeve(self):
        from copilot.policy import _LIMITS, limit_for
        for name in _LIMITS:
            if name == "single_name":
                continue
            self.assertAlmostEqual(limit_for(name, "etf"), _LIMITS[name][1], msg=name)
            self.assertAlmostEqual(limit_for(name, "stock"), _LIMITS[name][1], msg=name)

    def test_an_etf_at_twenty_percent_passes(self):
        # MomentumTopN with top_n=5 equal-weighted is 20% a name. Under the old
        # flat 5% ceiling every ETF sleeve configuration failed.
        snapshot = fixture()
        p = proposal(action="buy")
        context = complete_context(snapshot, p)
        context["verified_risk_inputs"]["post_trade_weight"] = 0.20
        decision = assess_proposal(p, snapshot, context, now=NOW)
        self.assertEqual(decision["risk_checks"]["single_name"]["status"], "pass")
        self.assertAlmostEqual(decision["risk_checks"]["single_name"]["limit"], 0.25)

    def test_an_etf_above_twenty_five_percent_fails(self):
        snapshot = fixture()
        p = proposal(action="buy")
        context = complete_context(snapshot, p)
        context["verified_risk_inputs"]["post_trade_weight"] = 0.2500001
        decision = assess_proposal(p, snapshot, context, now=NOW)
        self.assertEqual(decision["risk_checks"]["single_name"]["status"], "fail")
        self.assertEqual(decision["action"], "hold")

    def test_the_five_percent_tier_is_still_reachable(self):
        # The only test that can kill a mutant which hardcodes 0.25 and never
        # reads the sleeve. GOLD.CNY is physical_gold, not etf.
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("single_name", "physical_gold"), 0.05)
        self.assertAlmostEqual(limit_for("single_name", "index"), 0.05)

    def test_correlation_is_not_applicable_for_an_etf(self):
        # Daily-return correlation among broad equity ETFs is structurally
        # 0.85-0.95, so no threshold is informative: 0.7 rejects every basket
        # and anything loose enough to admit one rejects nothing. ADR-0007
        # clause 7 records the decision and its consequence.
        snapshot = fixture()
        p = proposal(action="buy")
        context = complete_context(snapshot, p)
        context["verified_risk_inputs"]["max_correlation"] = 0.95
        decision = assess_proposal(p, snapshot, context, now=NOW)
        self.assertEqual(decision["risk_checks"]["correlation"]["status"], "not_applicable")
        self.assertIn("look-through", decision["risk_checks"]["correlation"]["detail"])

    def test_correlation_still_applies_outside_the_etf_sleeve(self):
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("correlation", "physical_gold"), 0.7)


class SectorMap(unittest.TestCase):
    def test_every_sector_etf_maps_to_one_sector(self):
        from copilot.instruments import sector_of
        for symbol, sector in (("XLK", "technology"), ("XLF", "financials"),
                               ("XLE", "energy"), ("XLV", "health_care"),
                               ("XLY", "consumer_discretionary"), ("XLP", "consumer_staples"),
                               ("XLI", "industrials"), ("XLB", "materials"),
                               ("XLU", "utilities"), ("XLRE", "real_estate"),
                               ("XLC", "communication_services")):
            self.assertEqual(sector_of(symbol), sector, symbol)

    def test_semiconductor_funds_are_a_sector(self):
        from copilot.instruments import sector_of
        self.assertEqual(sector_of("SMH"), "technology")
        self.assertEqual(sector_of("SOXX"), "technology")

    def test_broad_funds_are_diversified_not_a_sector(self):
        from copilot.instruments import sector_of
        for symbol in ("SPY", "VOO", "VTI", "IVV", "QQQ", "IWM", "VEA", "VWO", "IOO", "DIA"):
            self.assertEqual(sector_of(symbol), "diversified", symbol)

    def test_every_registry_symbol_has_a_sector(self):
        # A missing entry would silently make sector concentration uncomputable
        # for that symbol, which turns the check into "unknown" and blocks
        # execution for a reason nobody would find.
        from copilot.instruments import ETF_REGISTRY, sector_of
        for symbol in sorted(ETF_REGISTRY):
            self.assertIsInstance(sector_of(symbol), str, symbol)
            self.assertTrue(sector_of(symbol), symbol)

    def test_an_unregistered_symbol_raises(self):
        from copilot.instruments import sector_of
        with self.assertRaises(ValueError):
            sector_of("NVDA")


class ModelMayNotSupplyNumbers(unittest.TestCase):
    def test_quantity_target_weight_and_stop_are_refused_from_a_model(self):
        # ADR-0004 clause 2 stops being aspirational here.
        for key, value in (("quantity", 10), ("target_weight", 0.5), ("stop_loss", 90.0)):
            p = proposal(action="buy")
            p[key] = value
            with self.assertRaisesRegex(ValueError, "engine"):
                assess_proposal(p, fixture(), None, now=NOW)

    def test_a_model_price_must_equal_the_snapshot_price(self):
        # Observed before this change: a proposal claiming 4242.0 against a
        # snapshot price of 100.0 returned buy/actionable and the 4242.0 was
        # written to an immutable table.
        snapshot = fixture()
        p = proposal(action="buy")
        p["price"] = 4242.0
        with self.assertRaisesRegex(ValueError, "price"):
            assess_proposal(p, snapshot, None, now=NOW)

    def test_a_matching_model_price_is_accepted(self):
        snapshot = fixture()
        p = proposal(action="buy")
        p["price"] = snapshot["instruments"]["QQQ"]["price"]
        decision = assess_proposal(p, snapshot, None, now=NOW)
        self.assertEqual(decision["price"], snapshot["instruments"]["QQQ"]["price"])

    def test_the_engine_may_supply_a_quantity(self):
        p = proposal(action="buy")
        p["quantity"] = 10
        decision = assess_proposal(p, fixture(), None, now=NOW, source="engine")
        self.assertEqual(decision["requested_action"], "buy")

    def test_an_unknown_source_raises(self):
        with self.assertRaisesRegex(ValueError, "source"):
            assess_proposal(proposal(), fixture(), None, now=NOW, source="whatever")
```

**You must also fix an existing test.** `_test_policy.py:189-195` (`test_measured_risk_fail_downgrades_purchase`) sets QQQ's `post_trade_weight` to 0.2 and asserts `action == "hold"`. QQQ is an ETF, so 0.2 is now a pass and the action stays `buy`. Raise that fixture's weight above 0.25 — 0.30 — so it still exercises the downgrade path. **Do not delete or weaken the assertion**, and report the change.

Also extend `complete_context` so the new tests can vary one input without duplicating the fixture. Keep the existing positional signature:

```python
def complete_context(snapshot, p=None, **risk_inputs):
    inputs = {"post_trade_weight": .02, "post_trade_sector_weight": .2,
              "max_correlation": .5, "position_adv_fraction": .001, "drawdown": .01}
    inputs.update(risk_inputs)
    return {"portfolio_version": "v1", "portfolio_complete": True, "base_currency": "USD",
            "snapshot_id": snapshot["snapshot_id"],
            "risk_proposal_fingerprint": proposal_fingerprint(p or proposal()),
            "verified_risk_inputs": inputs}
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_policy.py -v
```

Expected: `ImportError: cannot import name 'limit_for'`.

- [ ] **Step 3: Add the sector map**

In `scripts/copilot/instruments.py`:

```python
#: Static sector attribution for the registry. The nine SPDR select-sector funds
#: and the two semiconductor funds are single-sector by construction; everything
#: else is broad and gets "diversified", which is not a sector and therefore
#: never concentrates. This exists so policy's sector limit is computable at all
#: -- no configured source provides fund constituents, so the alternative is a
#: permanently "unknown" check that blocks execution for an invisible reason.
#:
#: Defensive ETFs are included because the map must cover the whole registry;
#: the ETF sleeve itself still refuses them (config rejects DEFENSIVE_ETFS).
_SECTORS = {
    "XLK": "technology", "XLF": "financials", "XLE": "energy", "XLV": "health_care",
    "XLY": "consumer_discretionary", "XLP": "consumer_staples", "XLI": "industrials",
    "XLB": "materials", "XLU": "utilities", "XLRE": "real_estate",
    "XLC": "communication_services", "SMH": "technology", "SOXX": "technology",
    "TLT": "government_bonds", "BND": "aggregate_bonds",
    "GLD": "gold", "IAU": "gold", "SGOL": "gold", "GLDM": "gold",
}


def sector_of(value: str) -> str:
    """The sector a registry symbol concentrates in, or "diversified"."""
    symbol = normalize_instrument(value)
    if symbol not in ETF_REGISTRY:
        raise ValueError(f"{symbol} is not in the ETF registry")
    return _SECTORS.get(symbol, "diversified")
```

Confirm `normalize_instrument` raises for an unregistered US ticker before relying on the explicit membership check; if it already raises, keep the check anyway so the error names the registry.

- [ ] **Step 4: Per-sleeve limits and the correlation exemption**

In `scripts/copilot/policy.py`, after `_LIMITS`:

```python
#: Concentration is a different risk for a fund than for a single company. A
#: broad-market ETF already holds hundreds of names, so the 5% ceiling written
#: for individual stocks makes any ETF sleeve unexecutable: 8-12 holdings is
#: 8-12% each and a top-5 momentum rotation is 20%. 25% is conventional, not
#: measured -- see ADR-0007 clause 2.
_SLEEVE_LIMITS = {"etf": {"single_name": 0.25}}

#: Checks that cannot be made informative for a sleeve, with the reason. This is
#: deliberately not a threshold: daily-return correlation among broad equity
#: ETFs is structurally 0.85-0.95, so 0.7 rejects every basket and any cap loose
#: enough to admit one rejects nothing. The informative measure is look-through
#: holdings overlap and no configured source provides fund constituents.
#: ADR-0007 clause 7 records the consequence: beyond single-name and sector
#: concentration the ETF sleeve has no diversification check.
_SLEEVE_EXEMPT = {
    "etf": {"correlation": "equity ETF return correlation is structurally 0.85-0.95, so no "
                           "threshold separates a diversified basket from a concentrated "
                           "one; look-through holdings overlap is the informative measure "
                           "and no configured source provides fund constituents"},
}


def limit_for(name: str, asset_class: str) -> float:
    """The limit for a risk check, per sleeve. Defaults to the stock limit."""
    return _SLEEVE_LIMITS.get(asset_class, {}).get(name, _LIMITS[name][1])
```

Then replace the risk-check loop:

```python
    for name, (field, limit, strict) in _LIMITS.items():
        value = inputs.get(field)
        eligible = _number(value) and 0 <= value <= 1
        check = "unknown" if not eligible else "pass" if (value < limit if strict else value <= limit) else "fail"
        checks[name] = {"status": check, "value": value if eligible else None, "limit": limit}
```

with:

```python
    sleeve = identity["asset_class"]
    for name, (field, _default, strict) in _LIMITS.items():
        exempt = _SLEEVE_EXEMPT.get(sleeve, {}).get(name)
        if exempt:
            checks[name] = {"status": "not_applicable", "value": None, "limit": None,
                            "detail": exempt}
            continue
        limit = limit_for(name, sleeve)
        value = inputs.get(field)
        eligible = _number(value) and 0 <= value <= 1
        check = "unknown" if not eligible else "pass" if (value < limit if strict else value <= limit) else "fail"
        checks[name] = {"status": check, "value": value if eligible else None, "limit": limit}
```

`identity` is already in scope (`policy.py:204`). Read it from `identity`, **not** from `item.get("asset_class")`: the snapshot item's class can be overwritten by a provider (`market_data.py:271-276`) and `policy.py:222-225` only blocks the etf→non-etf direction, so reading the item would let a provider relabel its way into a different limit.

- [ ] **Step 5: Bind the model's price and refuse its quantities**

Change the signature at `policy.py:164`:

```python
def assess_proposal(proposal: dict, snapshot: dict, context: dict | None = None, *, now=None) -> dict:
```

to:

```python
def assess_proposal(proposal: dict, snapshot: dict, context: dict | None = None, *,
                    now=None, source: str = "model") -> dict:
```

and replace `policy.py:190-193`:

```python
    for key in ("price", "quantity", "target_weight", "stop_loss"):
        if key in proposal and not _number(proposal[key], positive=True):
            raise ValueError(f"{key} must be a finite positive number")
```

with:

```python
    if source not in ("model", "engine"):
        raise ValueError(f"source must be 'model' or 'engine', got {source!r}")
    for key in ("price", "quantity", "target_weight", "stop_loss"):
        if key in proposal and not _number(proposal[key], positive=True):
            raise ValueError(f"{key} must be a finite positive number")
```

The identity-dependent half has to run after `identity` exists. Immediately after the `identity` lookup (`policy.py:204-206`), add:

```python
    # ADR-0004 clause 2: the model has no interface through which it can alter
    # quantity, direction or rule identity. A model-supplied `price` is allowed
    # only where it can be checked: equal to the snapshot's own price for a
    # market instrument, or equal to a captured merchant quote for gold. Before
    # this, a proposal claiming 4242.0 against a snapshot price of 100.0
    # returned buy/actionable and the 4242.0 was written to an immutable table.
    if source == "model":
        for key in ("quantity", "target_weight", "stop_loss"):
            if key in proposal:
                raise ValueError(f"{key} is computed by the engine and must not be supplied "
                                 f"by a proposal; call evaluate_rule instead")
        if "price" in proposal and identity["asset_class"] != "physical_gold":
            if proposal["price"] != item.get("price"):
                raise ValueError(f"price must equal the snapshot price for {instrument_id}; "
                                 f"the proposal says {proposal['price']!r} and the snapshot "
                                 f"says {item.get('price')!r}")
```

- [ ] **Step 6: Run the tests**

```bash
python scripts/_test_policy.py -v && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: 18 new policy tests OK. Any pre-existing test that supplied a numeric key from a model proposal now raises — for each one decide whether it was exercising the engine path (add `source="engine"`) or the model path (drop the key), and **report every test you touched and which way you decided**. `test_measured_risk_fail_downgrades_purchase` must still assert the downgrade with a weight above 0.25.

- [ ] **Step 7: Commit**

```bash
git add scripts/copilot/instruments.py scripts/copilot/policy.py scripts/_test_policy.py
git commit -m "feat(policy): per-sleeve limits, a computable sector map, and a bound model price"
```

---

### Task 4: `ruleset.py` — rule identity bound to a real backtest

**Files:**
- Create: `scripts/copilot/ruleset.py`
- Create: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/_test_ruleset.py`:

```python
#!/usr/bin/env python3
"""Offline contracts for rule identity, sizing, the brake and risk inputs.

Stdlib only, no network, no clock: this runs in the CI matrix job that installs
zero third-party packages.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from copilot import ruleset
from copilot.backtest import engine as bt_engine
from copilot.backtest import frame as frame_mod
from copilot.backtest import rules as rule_families


def admitted_result(family="momentum_top_n", parameters=None, days=5200):
    """A real engine.Result from a real backtest, not a hand-written dict.

    ruleset must derive the admission verdict itself rather than trusting a
    caller's claim, so the tests have to hand it something a backtest actually
    produced.
    """
    parameters = parameters or {"top_n": 2.0, "lookback_days": 252.0, "skip_days": 21.0}
    symbols = ["IWM", "QQQ", "SPY"]
    start = date(2005, 1, 3)
    dates, closes, day = [], [], start
    i = 0
    while len(dates) < days:
        if day.weekday() < 5:
            dates.append(day)
            closes.append([100.0 + i * 0.01, 100.0 + i * 0.02, 100.0 + i * 0.015])
            i += 1
        day += timedelta(days=1)
    frame = frame_mod.build(dates=dates, symbols=symbols, closes=closes)
    rule = rule_families.MomentumTopN(
        tuple(symbols), top_n=int(parameters["top_n"]),
        lookback_days=int(parameters["lookback_days"]),
        skip_days=int(parameters["skip_days"]))
    return frame, rule, bt_engine.run(
        frame, rule=rule, start_cash=100000.0,
        cost_model=bt_engine.CostModel(), cash_floor_pct=0.15)


class RuleIdentity(unittest.TestCase):
    def material(self, **overrides):
        base = dict(
            family="momentum_top_n",
            parameters={"top_n": 2.0, "lookback_days": 252.0, "skip_days": 21.0},
            universe=("IWM", "QQQ", "SPY"),
            targets=None,
            cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                        "max_pct_of_notional": 0.01, "spread_bps": 2.0},
            cash_floor_pct=0.15,
            integer_shares=True,
            admission={"admitted": True, "waived": False},
        )
        base.update(overrides)
        return base

    def test_rule_id_is_stable_and_well_formed(self):
        self.assertEqual(ruleset.rule_id(**self.material()),
                         ruleset.rule_id(**self.material()))
        self.assertRegex(ruleset.rule_id(**self.material()), r"^rule-[0-9a-f]{16}$")

    def test_universe_order_does_not_change_the_id(self):
        self.assertEqual(ruleset.rule_id(**self.material(universe=("IWM", "QQQ", "SPY"))),
                         ruleset.rule_id(**self.material(universe=("SPY", "IWM", "QQQ"))))

    def test_every_identity_input_changes_the_id(self):
        # The first draft tested three of nine inputs. Two rules differing only
        # in cash_floor_pct would have collided: the second either fails to
        # store (JournalConflict) or runs live reserving 15% while its admitted
        # metrics were measured reserving 50%.
        base = ruleset.rule_id(**self.material())
        variants = {
            "family": {"family": "inverse_volatility"},
            "parameters": {"parameters": {"top_n": 3.0, "lookback_days": 252.0, "skip_days": 21.0}},
            "universe": {"universe": ("IWM", "QQQ")},
            "targets": {"targets": {"QQQ": 1.0}},
            "cost_model": {"cost_model": {"per_share_usd": 0.005, "minimum_usd": 1.0,
                                          "max_pct_of_notional": 0.01, "spread_bps": 2.0}},
            "cash_floor_pct": {"cash_floor_pct": 0.50},
            "integer_shares": {"integer_shares": False},
            "admitted": {"admission": {"admitted": False, "waived": False}},
            "waived": {"admission": {"admitted": True, "waived": True}},
        }
        for name, override in variants.items():
            self.assertNotEqual(base, ruleset.rule_id(**self.material(**override)),
                                f"{name} does not participate in identity")

    def test_an_uncapped_cost_model_differs_from_a_zero_cap(self):
        # engine.CostModel records this as a bug fixed once already: 0.0 used to
        # mean "no cap" and None now does, so the two must not hash alike.
        uncapped = self.material()
        uncapped["cost_model"] = {**uncapped["cost_model"], "max_pct_of_notional": None}
        zero = self.material()
        zero["cost_model"] = {**zero["cost_model"], "max_pct_of_notional": 0.0}
        self.assertNotEqual(ruleset.rule_id(**uncapped), ruleset.rule_id(**zero))

    def test_an_incomplete_cost_model_is_refused(self):
        # CostModel(**{}) silently constructs the default IBKR schedule, so an
        # empty mapping would mean the admitted metrics no longer describe the
        # rule that runs.
        with self.assertRaisesRegex(ValueError, "cost_model"):
            ruleset.rule_id(**self.material(cost_model={}))
        with self.assertRaisesRegex(ValueError, "cost_model"):
            ruleset.rule_id(**self.material(cost_model={"per_share_usd": 0.0035}))

    def test_a_rule_id_survives_the_prose_gate_whole(self):
        import re
        from copilot.policy import _IDENTITY_OR_DATE
        text = f"adopted rule {ruleset.rule_id(**self.material())} produced this order"
        stripped = re.sub(r"\s+", " ", _IDENTITY_OR_DATE.sub(" ", text)).strip()
        self.assertEqual(stripped, "adopted rule produced this order")


class AdoptionRecord(unittest.TestCase):
    def build(self, **overrides):
        frame, rule, result = admitted_result()
        kwargs = dict(sleeve="etf", family=rule.name, parameters=dict(rule.parameters),
                      universe=tuple(frame.symbols), targets=None, result=result,
                      cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                                  "max_pct_of_notional": 0.01, "spread_bps": 2.0},
                      cash_floor_pct=0.15, integer_shares=True)
        kwargs.update(overrides)
        return ruleset.build_adoption(**kwargs)

    def test_the_admission_verdict_is_derived_not_supplied(self):
        # build_adoption calls backtest.admission.assess itself. A caller cannot
        # hand it {"admitted": True} for a strategy that never ran.
        record = self.build()
        self.assertIn("admission", record)
        self.assertIn("metrics", record["admission"])
        self.assertIn("cagr", record["admission"]["metrics"])
        self.assertIsInstance(record["admission"]["admitted"], bool)

    def test_signature_has_no_admission_parameter(self):
        import inspect
        self.assertNotIn("admission", inspect.signature(ruleset.build_adoption).parameters)

    def test_a_rejected_backtest_cannot_be_adopted(self):
        # A short curve fails rule 1, so the gate refuses the adoption.
        _, rule, short = admitted_result(days=400)
        with self.assertRaisesRegex(ValueError, "admitted"):
            self.build(result=short)

    def test_the_record_carries_the_admitted_metrics(self):
        # ADR-0004's last consequence promises every order carries the backtest
        # statistics it was adopted under, so they must be stored here.
        record = self.build()
        for key in ("years", "cagr", "max_drawdown", "annual_turnover", "sharpe"):
            self.assertIn(key, record["admission"]["metrics"], key)

    def test_universe_is_stored_sorted(self):
        self.assertEqual(self.build()["universe"], ["IWM", "QQQ", "SPY"])

    def test_only_the_etf_sleeve_exists(self):
        # ADR-0005 clause 2 deferred the gold sleeve; a gold adoption would skip
        # every universe check and could then be pointed at by etf.adopted_rule_id.
        self.assertEqual(ruleset.SLEEVES, ("etf",))
        with self.assertRaisesRegex(ValueError, "sleeve"):
            self.build(sleeve="gold")

    def test_a_non_admissible_symbol_is_refused(self):
        # SCHD is registered but has zero 2008 bars, so it never passed Q29.
        with self.assertRaises(ValueError):
            self.build(universe=("QQQ", "SCHD"))

    def test_an_unregistered_symbol_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(universe=("QQQ", "NVDA"))

    def test_an_unknown_parameter_key_is_refused(self):
        # parameters={"topn": 3.0} would hash into the identity while
        # _rule_weights reads "top_n" and falls back to its default, so the
        # recorded identity would not describe the rule that runs.
        with self.assertRaisesRegex(ValueError, "topn"):
            self.build(parameters={"topn": 3.0, "lookback_days": 252.0, "skip_days": 21.0})

    def test_a_missing_parameter_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "skip_days"):
            self.build(parameters={"top_n": 2.0, "lookback_days": 252.0})

    def test_fixed_weight_targets_must_cover_the_universe(self):
        with self.assertRaisesRegex(ValueError, "targets"):
            self.build(family="fixed_weight_bands",
                       parameters={"relative_band": 0.25, "absolute_band": 0.05,
                                   "calendar_days": 365.0},
                       targets={"QQQ": 1.0})

    def test_fixed_weight_targets_must_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            self.build(family="fixed_weight_bands",
                       parameters={"relative_band": 0.25, "absolute_band": 0.05,
                                   "calendar_days": 365.0},
                       targets={"IWM": 0.3, "QQQ": 0.3, "SPY": 0.3})
```

Before implementing, run `admitted_result()` and report what `admission.assess` actually returns for that synthetic curve — whether it is admitted, and which rules fail if not. **If a monotonically rising synthetic series cannot be admitted, fix the fixture so it can** (it needs ≥15 years spanning 2008/2020/2022 with every month present, non-zero costs, ≤3 parameters and a ≥2-year out-of-sample segment) and say what you changed. Do not weaken `build_adoption` to accept a rejected result.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ModuleNotFoundError: No module named 'copilot.ruleset'`.

- [ ] **Step 3: Write `ruleset.py`**

```python
"""Rule identity and the adoption record. Pure, stdlib, no I/O.

WHY A NEW IDENTIFIER
A backtest Result carries `rule_name` (the family) and `parameters`. Neither
identifies a strategy: `parameters` deliberately excludes `universe` and
`targets` so the admission gate's three-parameter cap measures only fitted
knobs, which means two runs over completely different baskets report the same
name and parameters.

A rule_id is content-addressed over everything that changes what the strategy
does or what evidence admitted it: the family, its parameters, the universe, any
fixed targets, the cost model the backtest was charged under, the cash floor, the
share granularity, and the admission verdict. The cost model and cash floor are
part of identity on purpose -- a rule sized live under a different fee schedule
or a different reserve has admitted metrics that no longer describe it.

WHY THE VERDICT IS DERIVED, NOT SUPPLIED
`build_adoption` takes a backtest `Result` and calls the admission gate itself.
A caller cannot hand it `{"admitted": True}`: adoption is supposed to mean "this
passed the gate", and a self-asserted boolean would make a hand-written JSON
pipe enough to adopt a strategy that never ran.

FORMAT
`rule-` plus 16 lowercase hex characters. The prefix is tokenised by
policy._IDENTITY_OR_DATE so an engine-authored reason can cite the id without
tripping the numeric-provenance gate.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
#: Only the ETF sleeve exists. ADR-0005 clause 2 deferred gold, and a gold
#: adoption would skip every universe check here while still being reachable
#: through etf.adopted_rule_id, which is the only pointer that exists.
SLEEVES = ("etf",)
ID_PREFIX = "rule-"
ID_HEX_LENGTH = 16

_COST_FIELDS = ("per_share_usd", "minimum_usd", "max_pct_of_notional", "spread_bps")

#: Exactly the keys each family's `parameters` property returns. Validated so a
#: typo cannot hash into the identity while the evaluator reads the correct name
#: and silently falls back to its default.
_FAMILY_PARAMETERS = {
    "fixed_weight_bands": ("relative_band", "absolute_band", "calendar_days"),
    "inverse_volatility": ("lookback_days", "rebalance_days"),
    "momentum_top_n": ("top_n", "lookback_days", "skip_days"),
}
WEIGHT_TOLERANCE = 1e-6


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _clean_cost_model(cost_model: Mapping[str, Any]) -> dict:
    if not isinstance(cost_model, Mapping):
        raise ValueError("cost_model must be a mapping")
    missing = [key for key in _COST_FIELDS if key not in cost_model]
    if missing:
        raise ValueError(f"cost_model is missing {', '.join(missing)}; an incomplete mapping "
                         "would construct the default fee schedule silently and the admitted "
                         "metrics would stop describing this rule")
    cleaned: dict[str, Any] = {}
    for key in _COST_FIELDS:
        value = cost_model[key]
        if key == "max_pct_of_notional" and value is None:
            cleaned[key] = None     # None is uncapped; 0.0 is a real zero cap
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"cost_model.{key} must be a number")
        cleaned[key] = float(value)
    return cleaned


def _clean_universe(universe: Sequence[str]) -> list[str]:
    if not isinstance(universe, (list, tuple)) or not universe:
        raise ValueError("universe must be a nonempty list or tuple of symbols")
    symbols = sorted({str(s).upper() for s in universe})
    if len(symbols) != len(universe):
        raise ValueError("universe contains duplicate symbols")
    return symbols


def _clean_parameters(family: str, parameters: Mapping[str, float]) -> dict:
    expected = _FAMILY_PARAMETERS.get(family)
    if expected is None:
        raise ValueError(f"unknown rule family {family!r}; known families are "
                         f"{tuple(_FAMILY_PARAMETERS)}")
    supplied = dict(parameters or {})
    unknown = sorted(set(supplied) - set(expected))
    if unknown:
        raise ValueError(f"{family} has no parameter(s) {', '.join(unknown)}; expected "
                         f"exactly {expected}")
    missing = [key for key in expected if key not in supplied]
    if missing:
        raise ValueError(f"{family} requires parameter(s) {', '.join(missing)}")
    return {key: float(supplied[key]) for key in expected}


def rule_id(*, family: str, parameters: Mapping[str, float], universe: Sequence[str],
            targets: Mapping[str, float] | None, cost_model: Mapping[str, Any],
            cash_floor_pct: float, integer_shares: bool,
            admission: Mapping[str, Any]) -> str:
    """Content-addressed identity. Universe order does not matter."""
    material = {
        "schema_version": SCHEMA_VERSION,
        "family": str(family),
        "parameters": _clean_parameters(family, parameters),
        "universe": _clean_universe(universe),
        "targets": ({str(k): float(v) for k, v in dict(targets).items()} if targets else None),
        "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct),
        "integer_shares": bool(integer_shares),
        "admitted": bool(admission.get("admitted")),
        "waived": bool(admission.get("waived")),
    }
    return ID_PREFIX + hashlib.sha256(_canonical(material).encode()).hexdigest()[:ID_HEX_LENGTH]


def build_adoption(*, sleeve: str, family: str, parameters: Mapping[str, float],
                   universe: Sequence[str], targets: Mapping[str, float] | None,
                   result, cost_model: Mapping[str, Any], cash_floor_pct: float,
                   integer_shares: bool, waivers: Mapping[str, str] | None = None) -> dict:
    """Build the immutable record of adopting a rule. Raises rather than guesses.

    `result` is a backtest engine.Result. The admission verdict is computed here
    from it, never taken from a caller.
    """
    from .backtest import admission as admission_gate
    from .backtest import universe as universe_tiers

    if sleeve not in SLEEVES:
        raise ValueError(f"sleeve must be one of {SLEEVES}, got {sleeve!r}")
    symbols = _clean_universe(universe)
    cleaned_parameters = _clean_parameters(family, parameters)
    for symbol in symbols:
        classification = universe_tiers.classify(symbol)
        if not classification.admissible:
            raise ValueError(f"{symbol} is tier {classification.tier!r}: {classification.reason}")
    cleaned_targets = None
    if family == "fixed_weight_bands":
        if not targets:
            raise ValueError("fixed_weight_bands requires targets")
        cleaned_targets = {str(k).upper(): float(v) for k, v in dict(targets).items()}
        if set(cleaned_targets) != set(symbols):
            raise ValueError(f"targets must cover exactly the universe; targets name "
                             f"{sorted(cleaned_targets)} and the universe is {symbols}")
        total = sum(cleaned_targets.values())
        if abs(total - 1.0) > WEIGHT_TOLERANCE:
            raise ValueError(f"targets must sum to 1.0, got {total:.9f}")

    report = admission_gate.assess(result, sessions_by_year=admission_gate.STRESS_SESSIONS,
                                   waivers=dict(waivers or {}))
    if not report.admitted:
        raise ValueError("this backtest did not pass the admission gate, so the rule cannot "
                         "be adopted: " + "; ".join(report.failures))
    admission = {"admitted": True, "waived": bool(report.waived),
                 "waiver_reasons": list(report.waiver_reasons),
                 "failures": list(report.failures), "metrics": dict(report.metrics)}
    identity = rule_id(family=family, parameters=cleaned_parameters, universe=symbols,
                       targets=cleaned_targets, cost_model=cost_model,
                       cash_floor_pct=cash_floor_pct, integer_shares=integer_shares,
                       admission=admission)
    return {
        "schema_version": SCHEMA_VERSION, "rule_id": identity, "sleeve": sleeve,
        "family": str(family), "parameters": cleaned_parameters, "universe": symbols,
        "targets": cleaned_targets, "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct), "integer_shares": bool(integer_shares),
        "admission": admission, "waived": bool(report.waived),
    }
```

- [ ] **Step 4: Add the `rule-` token to the prose gate**

In `scripts/copilot/policy.py`, extend `_IDENTITY_OR_DATE`'s prefix alternation. Constrain the new token to the actual id shape rather than reusing the loose `[A-Za-z0-9_-]+` the other prefixes carry, so it cannot absorb an adjacent number:

```python
    r"(?<![A-Za-z])(?:RSI|SMA|EMA|ATR|MACD)\s*\d+(?!\d)|\b(?:snap_|ev_|research-|decision-)[A-Za-z0-9_-]+\b"
```

becomes

```python
    r"(?<![A-Za-z])(?:RSI|SMA|EMA|ATR|MACD)\s*\d+(?!\d)|\b(?:snap_|ev_|research-|decision-)[A-Za-z0-9_-]+\b"
    r"|\brule-[0-9a-f]{16}\b"
```

- [ ] **Step 5: Run the tests**

```bash
python scripts/_test_ruleset.py -v && python scripts/_test_policy.py -v
```

Expected: 18 ruleset tests OK; the policy suite still green.

- [ ] **Step 6: Commit**

```bash
git add scripts/copilot/ruleset.py scripts/_test_ruleset.py scripts/copilot/policy.py
git commit -m "feat(ruleset): rule identity derived from a real admitted backtest"
```

---

### Task 5: `sizing.py` — share deltas, one denominator

The first draft computed "how many shares to end up holding" from cash alone while the action was derived from holdings weights, so an order read as a sell and meant a target. Orders here are **deltas against shares actually held**, and every weight uses one denominator: holdings market value plus investable cash.

**Files:**
- Create: `scripts/copilot/sizing.py`
- Modify: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_ruleset.py` (add `from copilot import sizing`):

```python
class CostAwareSizing(unittest.TestCase):
    def model(self):
        return bt_engine.CostModel()

    def test_zero_cost_sizing_is_the_naive_floor(self):
        self.assertEqual(sizing.affordable_shares(
            budget=1000.0, price=333.0, cost_model=bt_engine.CostModel.free()), 3)

    def test_costs_reduce_the_count_when_the_naive_floor_does_not_fit(self):
        # Verify this arithmetic against the real CostModel defaults before
        # trusting the expected value; if 3 shares at 333.0 plus commission and
        # half-spread does NOT exceed 1000.0, change the fixture to a case that
        # does and say so.
        self.assertEqual(sizing.affordable_shares(
            budget=1000.0, price=333.0, cost_model=self.model()), 2)

    def test_the_result_always_fits_and_is_maximal(self):
        model = self.model()
        for budget in (100.0, 1000.0, 9999.99, 20000.0, 123456.78):
            for price in (1.0, 37.5, 333.0, 612.34):
                shares = sizing.affordable_shares(budget=budget, price=price, cost_model=model)
                if shares:
                    notional = shares * price
                    self.assertLessEqual(
                        notional + model.total(shares=shares, notional=notional),
                        budget + 1e-9, f"{budget}/{price}/{shares} did not fit")
                more = (shares + 1) * price
                self.assertGreater(more + model.total(shares=shares + 1, notional=more),
                                   budget, f"{budget}/{price}/{shares} was not maximal")

    def test_a_non_positive_price_raises_and_a_non_positive_budget_is_zero(self):
        with self.assertRaises(ValueError):
            sizing.affordable_shares(budget=100.0, price=0.0, cost_model=self.model())
        self.assertEqual(sizing.affordable_shares(budget=0.0, price=10.0, cost_model=self.model()), 0)
        self.assertEqual(sizing.affordable_shares(budget=-5.0, price=10.0, cost_model=self.model()), 0)


class OrderPlanning(unittest.TestCase):
    def plan(self, **overrides):
        kwargs = dict(weights={"AAA": 0.5, "BBB": 0.5},
                      prices={"AAA": 100.0, "BBB": 50.0},
                      held_shares={},
                      investable_cash=10000.0,
                      cash_floor_pct=0.0,
                      cost_model=bt_engine.CostModel.free())
        kwargs.update(overrides)
        return sizing.plan_orders(**kwargs)

    def test_an_empty_book_produces_pure_buys(self):
        plan = self.plan()
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["AAA"]["side"], "buy")
        self.assertEqual(by_symbol["AAA"]["delta_shares"], 50)
        self.assertEqual(by_symbol["AAA"]["target_shares"], 50)
        self.assertEqual(by_symbol["BBB"]["delta_shares"], 100)

    def test_an_overweight_holding_produces_a_sell_of_the_difference(self):
        # The failure the first draft would have shipped: 600 held against a
        # target of 17 rendered as "reduce 17 shares", which reads as sell 17.
        plan = self.plan(held_shares={"AAA": 200}, prices={"AAA": 100.0, "BBB": 50.0},
                         investable_cash=0.0)
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["AAA"]["side"], "sell")
        self.assertEqual(by_symbol["AAA"]["target_shares"], 100)
        self.assertEqual(by_symbol["AAA"]["delta_shares"], -100)

    def test_the_denominator_is_holdings_plus_cash(self):
        # 200 AAA at 100.0 is 20000 of holdings; 20000 of cash makes 40000. A
        # 50% target is 20000, which is the 200 shares already held, so the
        # delta is zero and the side is hold.
        plan = self.plan(held_shares={"AAA": 200}, investable_cash=20000.0,
                         weights={"AAA": 0.5, "BBB": 0.5})
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["AAA"]["delta_shares"], 0)
        self.assertEqual(by_symbol["AAA"]["side"], "hold")
        self.assertAlmostEqual(plan["total_value"], 40000.0)

    def test_a_drift_inside_the_tolerance_is_a_hold_with_no_delta(self):
        plan = self.plan(held_shares={"AAA": 50, "BBB": 100}, investable_cash=0.0)
        for order in plan["orders"]:
            self.assertEqual(order["delta_shares"], 0)
            self.assertEqual(order["side"], "hold")

    def test_the_cash_floor_is_withheld_from_the_total_not_from_cash(self):
        # engine.run reserves a fraction of cash + positions, so live sizing
        # must use the same base or the same admitted rule reserves a different
        # amount in production than it did in the backtest.
        plan = self.plan(held_shares={"AAA": 100}, investable_cash=10000.0,
                         cash_floor_pct=0.20, weights={"AAA": 1.0},
                         prices={"AAA": 100.0})
        self.assertAlmostEqual(plan["total_value"], 20000.0)
        self.assertAlmostEqual(plan["investable_value"], 16000.0)
        self.assertEqual(plan["orders"][0]["target_shares"], 160)
        self.assertEqual(plan["orders"][0]["delta_shares"], 60)

    def test_a_buy_that_cash_cannot_fund_is_reported_not_silently_shrunk(self):
        plan = self.plan(weights={"AAA": 0.5, "BBB": 0.5},
                         prices={"AAA": 100.0, "BBB": 100000.0},
                         investable_cash=1000.0)
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["BBB"]["delta_shares"], 0)
        self.assertIn("BBB", plan["unfunded"])

    def test_sells_are_not_limited_by_cash(self):
        # Selling raises cash; a sell must never be trimmed by the cash budget.
        plan = self.plan(held_shares={"AAA": 500}, investable_cash=0.0,
                         weights={"AAA": 1.0}, prices={"AAA": 100.0},
                         cash_floor_pct=0.50)
        self.assertEqual(plan["orders"][0]["side"], "sell")
        self.assertEqual(plan["orders"][0]["target_shares"], 250)
        self.assertEqual(plan["orders"][0]["delta_shares"], -250)

    def test_weights_that_do_not_sum_to_one_are_refused(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            self.plan(weights={"AAA": 0.9})

    def test_a_missing_price_is_a_keyerror(self):
        with self.assertRaises(KeyError):
            self.plan(prices={"AAA": 100.0})

    def test_a_held_symbol_outside_the_target_set_is_sold_to_zero(self):
        # A momentum rotation drops names. Leaving them held would silently
        # diverge from the weights the backtest measured.
        plan = self.plan(weights={"AAA": 1.0}, prices={"AAA": 100.0, "OLD": 20.0},
                         held_shares={"OLD": 300}, investable_cash=0.0)
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["OLD"]["target_shares"], 0)
        self.assertEqual(by_symbol["OLD"]["delta_shares"], -300)
        self.assertEqual(by_symbol["OLD"]["side"], "sell")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ImportError: cannot import name 'sizing'`.

- [ ] **Step 3: Write `sizing.py`**

```python
"""Whole-share order deltas that fit the budget after costs.

TWO THINGS THE FIRST DRAFT GOT WRONG, BOTH FIXED HERE

First, an order is a DELTA. The earlier version computed "how many shares to end
up holding" from cash alone while the direction came from comparing weights, so
`QQQ reduce 17` meant "hold 17 at the end" and read as "sell 17". Every order
below carries `target_shares`, `delta_shares` and an explicit `side`, and
`delta_shares` is what a broker would be told.

Second, there is ONE denominator: holdings market value plus investable cash.
Comparing a target weight measured against cash to a current weight measured
against holdings is meaningless once any position exists.

The cash floor is withheld from that same total, because `engine.run` reserves a
fraction of `cash + positions` (engine.py:167). Reserving a fraction of cash
instead would make a rule reserve a different amount live than it did in the
backtest it was admitted under.

PRECONDITION: `affordable_shares` assumes `price >= 1.0`. Its descent subtracts
the observed overshoot in whole shares, which is exact at ordinary prices; for
sub-dollar prices it can stop one or two shares short of maximal. No registry
symbol trades under a dollar, and a test pins maximality over the prices that
do occur.
"""
from __future__ import annotations

import math
from typing import Mapping

WEIGHT_TOLERANCE = 1e-6


def affordable_shares(*, budget: float, price: float, cost_model) -> int:
    """Largest whole share count whose notional plus costs fits `budget`."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"price must be a finite positive number, got {price!r}")
    if not math.isfinite(budget) or budget <= 0:
        return 0
    shares = int(budget // price)
    while shares > 0:
        notional = shares * price
        total = notional + cost_model.total(shares=shares, notional=notional)
        if total <= budget:
            return shares
        shares -= max(1, int((total - budget) // price))
    return 0


def plan_orders(*, weights: Mapping[str, float], prices: Mapping[str, float],
                held_shares: Mapping[str, float], investable_cash: float,
                cash_floor_pct: float, cost_model) -> dict:
    """Turn target weights plus what is held into whole-share deltas.

    Sells are computed first and are never limited by cash: a sell raises cash.
    Buys then draw from the remaining budget in a deterministic (sorted) order,
    so the total spend cannot exceed what is available even when rounding on one
    symbol frees change a later one could use.
    """
    if not 0.0 <= cash_floor_pct < 1.0:
        raise ValueError(f"cash_floor_pct must be in [0, 1), got {cash_floor_pct}")
    if not weights:
        raise ValueError("weights must be a nonempty mapping")
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"target weights must sum to 1.0, got {total_weight:.9f}")
    for symbol, weight in weights.items():
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"{symbol}: weight must be finite and non-negative, got {weight!r}")

    held = {str(s).upper(): float(q) for s, q in (held_shares or {}).items() if q}
    symbols = sorted(set(weights) | set(held))
    holdings_value = sum(held[s] * prices[s] for s in held)
    total_value = holdings_value + float(investable_cash)
    investable_value = total_value * (1.0 - cash_floor_pct)

    targets: dict[str, int] = {}
    for symbol in symbols:
        price = prices[symbol]      # KeyError is correct: a missing price is a defect
        if price <= 0:
            raise ValueError(f"{symbol}: price must be positive, got {price!r}")
        targets[symbol] = int((investable_value * weights.get(symbol, 0.0)) // price)

    orders, unfunded = [], []
    cash = float(investable_cash)
    # Sells first: they fund the buys.
    for symbol in symbols:
        delta = targets[symbol] - int(held.get(symbol, 0))
        if delta >= 0:
            continue
        notional = -delta * prices[symbol]
        cost = cost_model.total(shares=-delta, notional=notional)
        cash += notional - cost
    for symbol in symbols:
        price = prices[symbol]
        current = int(held.get(symbol, 0))
        delta = targets[symbol] - current
        cost = 0.0
        if delta > 0:
            affordable = affordable_shares(budget=cash, price=price, cost_model=cost_model)
            if affordable < delta:
                if affordable == 0:
                    unfunded.append(symbol)
                delta = affordable
            if delta > 0:
                notional = delta * price
                cost = cost_model.total(shares=delta, notional=notional)
                cash -= notional + cost
        elif delta < 0:
            notional = -delta * price
            cost = cost_model.total(shares=-delta, notional=notional)
        side = "hold" if delta == 0 else "buy" if delta > 0 else "sell"
        orders.append({
            "instrument_id": symbol,
            "target_weight": float(weights.get(symbol, 0.0)),
            "held_shares": current,
            "target_shares": current + delta if side != "hold" else targets[symbol],
            "delta_shares": delta,
            "side": side,
            "limit_price": float(price),
            "notional": round(abs(delta) * price, 2),
            "estimated_cost": round(cost, 2),
        })
    return {
        "orders": orders, "unfunded": unfunded,
        "total_value": round(total_value, 2),
        "holdings_value": round(holdings_value, 2),
        "investable_cash": round(float(investable_cash), 2),
        "investable_value": round(investable_value, 2),
        "cash_remaining": round(cash, 2),
    }
```

Note `target_shares` for a `hold` reports `targets[symbol]`, which equals the held count by construction; for a buy or sell it reports `current + delta`. Confirm those agree on every test case and simplify to one expression if they do — two expressions that must agree is a defect waiting to happen, and if they can disagree the tests above will catch it.

- [ ] **Step 4: Run the tests**

```bash
python scripts/_test_ruleset.py -v
```

Expected: 14 new tests, OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/sizing.py scripts/_test_ruleset.py
git commit -m "feat(sizing): whole-share deltas against held shares on one denominator"
```

---

### Task 6: `brake.py` — the one-way news brake

**Files:**
- Create: `scripts/copilot/brake.py`
- Modify: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_ruleset.py` (add `from copilot import brake`):

```python
class NewsBrake(unittest.TestCase):
    def test_levels_are_exactly_the_three_the_user_approved(self):
        self.assertEqual(brake.LEVELS, ("none", "reduce_50", "skip"))

    def test_apply_is_one_way_over_the_whole_level_set(self):
        for level in brake.LEVELS:
            for quantity in range(0, 101):
                self.assertLessEqual(brake.apply(level=level, quantity=quantity), quantity)

    def test_reduce_50_floors_and_zero_is_a_valid_result(self):
        # Returning 0 is the contract, not an error: 1 share halved is 0 shares,
        # and the caller turns that into a hold rather than an order.
        self.assertEqual(brake.apply(level="reduce_50", quantity=17), 8)
        self.assertEqual(brake.apply(level="reduce_50", quantity=3), 1)
        self.assertEqual(brake.apply(level="reduce_50", quantity=1), 0)
        self.assertEqual(brake.apply(level="skip", quantity=17), 0)
        self.assertEqual(brake.apply(level="none", quantity=17), 17)

    def test_rounding_is_down_not_nearest(self):
        # round(q/2) would keep 2 of 3 -- 67% -- which is not a 50% reduction.
        self.assertEqual(brake.apply(level="reduce_50", quantity=3), 1)
        self.assertEqual(brake.apply(level="reduce_50", quantity=5), 2)
        self.assertEqual(brake.apply(level="reduce_50", quantity=7), 3)

    def test_a_negative_or_non_integer_quantity_raises(self):
        for bad in (-7, -1, 1.5, True, "3", None):
            with self.assertRaises((ValueError, TypeError)):
                brake.apply(level="none", quantity=bad)

    def test_an_unknown_level_raises(self):
        for bad in ("increase", "reduce_25", "double", "", None):
            with self.assertRaisesRegex(ValueError, "brake level"):
                brake.apply(level=bad, quantity=10)

    def test_a_non_none_level_requires_a_reason(self):
        for blank in ("", "   ", None):
            with self.assertRaisesRegex(ValueError, "reason"):
                brake.record(level="skip", reason=blank, evidence_ids=["ev_1"])

    def test_none_needs_no_reason_and_carries_no_evidence_requirement(self):
        record = brake.record(level="none", reason="", evidence_ids=[])
        self.assertEqual(record["level"], "none")
        self.assertEqual(record["evidence_ids"], [])

    def test_a_bare_string_of_evidence_ids_is_refused(self):
        # [str(e) for e in "ev_news_1"] would store nine single characters, and
        # this is the only evidence field in the system with no validation
        # behind it -- its input comes from headlines the model read.
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            brake.record(level="skip", reason="halt", evidence_ids="ev_news_1")

    def test_a_non_none_level_requires_at_least_one_evidence_id(self):
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            brake.record(level="reduce_50", reason="halt", evidence_ids=[])

    def test_every_record_is_marked_unbacktested(self):
        # ADR-0005 clause 1 requires this wherever the brake appears. Finnhub's
        # company-news archive is one rolling year and returns HTTP 200 with an
        # empty array beyond it, so a replay harness would look green while
        # testing nothing.
        for level in brake.LEVELS:
            record = brake.record(level=level,
                                  reason="" if level == "none" else "issuer halt",
                                  evidence_ids=[] if level == "none" else ["ev_1"])
            self.assertFalse(record["backtested"])
            self.assertIn("未回测", record["disclosure"])

    def test_applied_reports_both_quantities(self):
        applied = brake.applied(
            record=brake.record(level="reduce_50", reason="halt", evidence_ids=["ev_1"]),
            quantity=17)
        self.assertEqual(applied["pre_brake_quantity"], 17)
        self.assertEqual(applied["post_brake_quantity"], 8)
        self.assertFalse(applied["backtested"])
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ImportError: cannot import name 'brake'`.

- [ ] **Step 3: Write `brake.py`**

```python
"""The bounded news brake. One-way by construction: it can only ever reduce.

WHY IT IS SHAPED THIS WAY
Q35 put macro surprise into the rules and news into a bounded adjustment.
ADR-0005 clause 1 then removed the macro leg, because Finnhub's economic
calendar is premium and returns HTTP 403 -- as does its news-sentiment endpoint,
which is not recorded anywhere else and was found by probing. What survives is
news alone, and news cannot be backtested here: the company-news archive is one
rolling year, and a request beyond it returns HTTP 200 with an empty array, so a
replay harness would report success while testing nothing. Coverage also spans
two orders of magnitude across the universe -- 240 items in seven days for SPY
against 2 for VWO -- so no count threshold could be calibrated.

The Q42 answer fixed the consequence: something with no evidence behind it gets
a veto, never an accelerator. `apply` cannot return more than it was given, and
a test asserts that across the whole level set.

The model selects the level and states a reason; the engine bounds it. That is
ADR-0004 clause 3: the model may apply the brake, and nothing else.

ZERO IS A RESULT, NOT AN ERROR
`skip` returns 0, and `reduce_50` returns 0 for a single share. Callers turn that
into a hold with no quantity. The first draft wrote the post-brake quantity into
a proposal while gating on the pre-brake one, so `skip` hit policy's
positive-number check and raised out of an MCP tool with no try/except.

BRAKE EVIDENCE IS NOT PROPOSAL EVIDENCE
News records carry `critical_evidence_eligible: False` (research_data.py:97,351).
Citing one in a proposal's `evidence_ids` makes policy emit
"evidence {eid} cannot support a critical recommendation claim" and the whole
decision becomes data_insufficient. Brake evidence travels in its own field and
is never merged. `evaluate_rule` verifies each id against the snapshot; this
module only enforces shape.
"""
from __future__ import annotations

from typing import Mapping, Sequence

#: Exactly the three the user approved. Widening this set is a design change,
#: not a refactor: every extra level is another unbacktested degree of freedom.
LEVELS = ("none", "reduce_50", "skip")

DISCLOSURE = "新闻刹车未回测：它只能减少或跳过，永远不能加仓"


def _quantity(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"quantity must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"quantity must not be negative, got {value}")
    return value


def apply(*, level: str, quantity: int) -> int:
    """Post-brake quantity. Never greater than `quantity`; 0 is a valid result."""
    if level not in LEVELS:
        raise ValueError(f"brake level must be one of {LEVELS}, got {level!r}")
    count = _quantity(quantity)
    if level == "skip":
        return 0
    if level == "reduce_50":
        return count // 2      # floor: halving must never round up
    return count


def record(*, level: str, reason: str, evidence_ids: Sequence[str]) -> dict:
    """Build the auditable brake record carried on every decision."""
    if level not in LEVELS:
        raise ValueError(f"brake level must be one of {LEVELS}, got {level!r}")
    if isinstance(evidence_ids, str) or (evidence_ids is not None
                                         and not isinstance(evidence_ids, (list, tuple))):
        raise ValueError("evidence_ids must be a list of evidence ids, not a bare string")
    ids = [str(e) for e in (evidence_ids or [])]
    text = str(reason or "").strip()
    if level != "none":
        if not text:
            raise ValueError("a brake that changes the order requires a stated reason")
        if not ids:
            raise ValueError("a brake that changes the order requires at least one "
                             "evidence_ids entry naming what it read")
    return {"level": level, "reason": text, "evidence_ids": ids,
            "backtested": False, "disclosure": DISCLOSURE}


def applied(*, record: Mapping[str, object], quantity: int) -> dict:
    """Apply a brake record to a quantity, reporting both sides."""
    level = str(record.get("level", "none"))
    post = apply(level=level, quantity=quantity)
    return {"level": level, "reason": record.get("reason", ""),
            "evidence_ids": list(record.get("evidence_ids") or []),
            "backtested": False, "disclosure": DISCLOSURE,
            "pre_brake_quantity": _quantity(quantity), "post_brake_quantity": post}
```

- [ ] **Step 4: Run the tests and commit**

```bash
python scripts/_test_ruleset.py -v
```

Expected: 12 new tests, OK.

```bash
git add scripts/copilot/brake.py scripts/_test_ruleset.py
git commit -m "feat(brake): one-way news brake whose evidence cannot reach evidence_ids"
```

---

### Task 7: `riskinputs.py` — the four computable measured inputs

`executable` needs every `_LIMITS` entry plus `stop` in `{pass, not_applicable}`. Task 3 made correlation `not_applicable` for the ETF sleeve; this task computes the other four honestly. If any cannot be computed it stays absent, which makes the check `unknown` and blocks execution — that is the correct outcome, not something to paper over.

**Files:**
- Create: `scripts/copilot/riskinputs.py`
- Modify: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_ruleset.py` (add `from copilot import riskinputs`):

```python
class RiskInputs(unittest.TestCase):
    def test_post_trade_weight_uses_the_post_trade_share_count(self):
        # The gate's fingerprint binds these numbers to THIS trade, so they must
        # describe the book after it, not before.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=100, price=100.0, total_value=40000.0,
            sector_values={"diversified": 10000.0}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[40000.0])
        self.assertAlmostEqual(inputs["post_trade_weight"], 0.25)

    def test_sector_weight_counts_the_whole_sector_after_the_trade(self):
        inputs = riskinputs.compute(
            symbol="XLK", target_shares=50, price=200.0, total_value=100000.0,
            sector_values={"technology": 5000.0}, sector_of="technology",
            average_dollar_volume=1e9, value_history=[100000.0])
        # 50 * 200 = 10000 for this holding, plus 5000 already in the sector
        # from other holdings, over 100000.
        self.assertAlmostEqual(inputs["post_trade_sector_weight"], 0.15)

    def test_liquidity_is_order_notional_over_average_dollar_volume(self):
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=10, price=500.0, total_value=100000.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1_000_000.0, value_history=[100000.0],
            delta_shares=10)
        self.assertAlmostEqual(inputs["position_adv_fraction"], 0.005)

    def test_liquidity_measures_the_traded_amount_not_the_held_amount(self):
        # A hold trades nothing, so it consumes no liquidity.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1000, price=500.0, total_value=1e9,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1_000_000.0, value_history=[1e9], delta_shares=0)
        self.assertEqual(inputs["position_adv_fraction"], 0.0)

    def test_a_single_observation_has_zero_drawdown(self):
        # Not a cheat: with one value the drawdown from peak genuinely is zero,
        # and it becomes meaningful as history accumulates. The sample count is
        # reported so a short history is visible rather than implied.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=100.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[100.0])
        self.assertEqual(inputs["drawdown"], 0.0)
        self.assertEqual(inputs["drawdown_sample_count"], 1)

    def test_drawdown_is_measured_from_the_peak(self):
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=80.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[100.0, 120.0, 90.0, 80.0])
        self.assertAlmostEqual(inputs["drawdown"], (120.0 - 80.0) / 120.0)
        self.assertEqual(inputs["drawdown_sample_count"], 4)

    def test_no_correlation_key_is_produced(self):
        # policy marks correlation not_applicable for the ETF sleeve, so
        # supplying a value here would be a number nobody reads pretending to
        # be a measurement.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=100.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[100.0])
        self.assertNotIn("max_correlation", inputs)

    def test_every_produced_value_is_in_the_unit_interval(self):
        # policy treats a value outside [0, 1] as "unknown", not "fail", so an
        # out-of-range number would silently block execution instead of failing
        # a limit.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1000, price=500.0, total_value=100000.0,
            sector_values={"diversified": 400000.0}, sector_of="diversified",
            average_dollar_volume=1000.0, value_history=[100000.0], delta_shares=1000)
        for key in ("post_trade_weight", "post_trade_sector_weight",
                    "position_adv_fraction", "drawdown"):
            self.assertGreaterEqual(inputs[key], 0.0, key)
            self.assertLessEqual(inputs[key], 1.0, key)

    def test_a_zero_total_value_raises_rather_than_dividing(self):
        with self.assertRaisesRegex(ValueError, "total_value"):
            riskinputs.compute(
                symbol="QQQ", target_shares=1, price=1.0, total_value=0.0,
                sector_values={}, sector_of="diversified",
                average_dollar_volume=1e9, value_history=[100.0])

    def test_a_missing_volume_omits_the_liquidity_key(self):
        # Absent, not zero: zero would read as "no liquidity risk measured as
        # pass", and policy turns an absent key into "unknown", which blocks.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=100.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=None, value_history=[100.0])
        self.assertNotIn("position_adv_fraction", inputs)


class AverageDollarVolume(unittest.TestCase):
    def test_it_averages_close_times_volume(self):
        bars = [{"session": "2026-09-01", "close": 100.0, "volume": 1000},
                {"session": "2026-09-02", "close": 110.0, "volume": 2000}]
        self.assertAlmostEqual(riskinputs.average_dollar_volume(bars, sessions=2),
                               (100.0 * 1000 + 110.0 * 2000) / 2)

    def test_it_uses_only_the_most_recent_sessions(self):
        bars = [{"session": f"2026-08-{d:02d}", "close": 1.0, "volume": 1} for d in range(1, 26)]
        bars += [{"session": "2026-09-01", "close": 100.0, "volume": 100}]
        self.assertAlmostEqual(riskinputs.average_dollar_volume(bars, sessions=1), 10000.0)

    def test_bars_without_volume_yield_none(self):
        # The Nasdaq equity record has no volume at all, so this must not be
        # mistaken for zero volume.
        bars = [{"session": "2026-09-01", "close": 100.0}]
        self.assertIsNone(riskinputs.average_dollar_volume(bars, sessions=1))

    def test_an_empty_series_yields_none(self):
        self.assertIsNone(riskinputs.average_dollar_volume([], sessions=20))
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ImportError: cannot import name 'riskinputs'`.

- [ ] **Step 3: Write `riskinputs.py`**

```python
"""The measured risk inputs policy needs, computed from real data or omitted.

WHY THIS MODULE EXISTS
`policy`'s `executable` requires every one of its five limits to be `pass` or
`not_applicable`. An absent input becomes `unknown`, which blocks execution, so
the engine cannot produce a quantity without supplying real measurements. Four
of the five are computable from what the repository already collects; the fifth,
correlation, has no informative threshold for an equity-only sleeve and is marked
`not_applicable` in policy itself (ADR-0007 clause 7).

WHAT IS DELIBERATELY ABSENT RATHER THAN ZERO
A value that cannot be measured is left out of the returned mapping. Returning
0.0 would read as a measured pass. Policy turns an absent key into `unknown`,
which blocks -- the conservative direction.

EVERY VALUE IS CLAMPED TO [0, 1]
Policy treats a value outside that range as `unknown` rather than `fail`
(policy.py:324), so an unclamped ratio above 1 would silently block instead of
failing the limit it breached. Clamping keeps the failure legible.
"""
from __future__ import annotations

import math
from typing import Mapping, Sequence

#: 20 sessions is the window `compute_indicators` already uses for average
#: volume, so the liquidity denominator matches the one the snapshot reports.
ADV_SESSIONS = 20


def _clamp(value: float) -> float:
    if not math.isfinite(value):
        raise ValueError(f"risk input must be finite, got {value!r}")
    return max(0.0, min(1.0, value))


def average_dollar_volume(bars: Sequence[Mapping], *, sessions: int = ADV_SESSIONS) -> float | None:
    """Mean close-times-volume over the most recent sessions, or None.

    None when no bar carries a volume: the Nasdaq equity record has none at all,
    and treating that as zero volume would make every order look illiquid.
    """
    if not bars:
        return None
    window = list(bars)[-max(1, int(sessions)):]
    values = []
    for bar in window:
        close, volume = bar.get("close"), bar.get("volume")
        if isinstance(close, bool) or not isinstance(close, (int, float)):
            continue
        if isinstance(volume, bool) or not isinstance(volume, (int, float)):
            continue
        values.append(float(close) * float(volume))
    if not values:
        return None
    return sum(values) / len(values)


def portfolio_drawdown(value_history: Sequence[float]) -> tuple[float, int]:
    """Drawdown from the running peak, and how many observations it rests on.

    With one observation the drawdown genuinely is zero. The count is returned so
    a caller can report that the measurement is young rather than implying depth
    the history cannot support.
    """
    values = [float(v) for v in value_history
              if not isinstance(v, bool) and isinstance(v, (int, float))
              and math.isfinite(v) and v > 0]
    if not values:
        raise ValueError("value_history must carry at least one positive value")
    peak, deepest = values[0], 0.0
    for value in values:
        peak = max(peak, value)
        deepest = max(deepest, (peak - value) / peak)
    return deepest, len(values)


def compute(*, symbol: str, target_shares: int, price: float, total_value: float,
            sector_values: Mapping[str, float], sector_of: str,
            average_dollar_volume: float | None, value_history: Sequence[float],
            delta_shares: int | None = None) -> dict:
    """The measured inputs for one post-trade position.

    `sector_values` holds the post-trade market value of OTHER holdings per
    sector, so this position's own value is added here rather than double
    counted. `delta_shares` defaults to `target_shares`, which is correct for an
    empty book; pass it explicitly so liquidity measures what is traded rather
    than what is held.
    """
    if not math.isfinite(total_value) or total_value <= 0:
        raise ValueError(f"total_value must be a finite positive number, got {total_value!r}")
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"price must be a finite positive number, got {price!r}")
    position_value = float(target_shares) * float(price)
    traded = float(target_shares if delta_shares is None else delta_shares)
    drawdown, samples = portfolio_drawdown(value_history)
    inputs = {
        "post_trade_weight": _clamp(position_value / total_value),
        "post_trade_sector_weight": _clamp(
            (position_value + float(sector_values.get(sector_of, 0.0))) / total_value),
        "drawdown": _clamp(drawdown),
        "drawdown_sample_count": samples,
        "sector": sector_of,
    }
    if average_dollar_volume is not None and average_dollar_volume > 0:
        inputs["position_adv_fraction"] = _clamp(
            abs(traded) * float(price) / float(average_dollar_volume))
    return inputs
```

Note `drawdown_sample_count` and `sector` are extra keys policy ignores — it reads only the five `_LIMITS` field names. They exist so a decision can report how thin the drawdown measurement is and which sector was attributed. Confirm policy ignores unknown keys in `verified_risk_inputs` rather than rejecting them, and report what you found.

- [ ] **Step 4: Run the tests and commit**

```bash
python scripts/_test_ruleset.py -v
```

Expected: 14 new tests, OK.

```bash
git add scripts/copilot/riskinputs.py scripts/_test_ruleset.py
git commit -m "feat(riskinputs): measured concentration, sector, liquidity and drawdown"
```

---

### Task 8: `policy.evaluate_rule` — the engine entry point

The task the plan exists for. The engine computes weights, deltas and measured risk, then feeds one proposal per symbol to the existing gate with the per-proposal context that gate requires.

**A safety subtlety discovered while designing this, which must be implemented and tested:** the brake applies **only to buy orders**. `reduce_50` on a sell would halve the sell, leaving *more* exposure than the rule asked for — that is an accelerator wearing a brake's name. On a sell or a hold the brake records its level and changes nothing.

**Files:**
- Modify: `scripts/copilot/policy.py`
- Modify: `scripts/_test_policy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_policy.py`. Note the fixture work this needs — `fixture()`'s evidence record has no `instrument_id` and no `bars`, so `evaluate_rule` cannot use it as-is.

```python
def bars_series(count=300, start=100.0, step=0.05):
    from datetime import date as _date, timedelta as _td
    rows, day, i = [], _date(2025, 1, 1), 0
    while len(rows) < count:
        if day.weekday() < 5:
            close = start + i * step
            rows.append({"session": day.isoformat(), "open": close, "high": close,
                         "low": close, "close": close, "volume": 5_000_000,
                         "adjusted_close": close})
            i += 1
        day += _td(days=1)
    return rows


def engine_fixture(symbols=("QQQ", "SPY"), count=300):
    """A snapshot evaluate_rule can actually consume.

    fixture() is not usable here: its single evidence record carries no
    instrument_id, so market-evidence selection finds nothing, and no bars, so a
    rule has no series to compute a weight from.
    """
    instruments, evidence = {}, []
    for offset, symbol in enumerate(symbols):
        eid = f"market_{symbol}"
        rows = bars_series(count=count, start=100.0 + offset * 10, step=0.05 + offset * 0.01)
        instruments[symbol] = {
            **get_instrument(symbol), "quality_status": "pass",
            "latest_session": rows[-1]["session"], "expected_session": rows[-1]["session"],
            "price": rows[-1]["close"],
            "indicators": {"sma200": rows[-1]["close"] * 0.95, "sample_count": count},
            "evidence_ids": [eid], "issues": [], "sources": ["synthetic"]}
        evidence.append({
            "evidence_id": eid, "provider": "yahoo", "upstream": "Yahoo Finance",
            "source_url": "https://example.test/prices", "status": "ok",
            "observed_at": "2026-09-04T20:00:00+00:00",
            "retrieved_at": "2026-09-06T00:59:00+00:00",
            "instrument_id": symbol, "asset_class": "etf", "currency": "USD",
            "unit": "share", "price_kind": "regular_session_close",
            "indicator_basis": "total_return_adjusted", "bars": rows,
            "latest_session": rows[-1]["session"], "missing_sessions": []})
    return seal({"schema_version": 1, "created_at": "2026-09-06T01:00:00+00:00",
                 "decision_at": "2026-09-06T01:00:00+00:00",
                 "valid_until": "2026-09-06T12:00:00+00:00", "status": "ready",
                 "instruments": instruments, "evidence": evidence, "issues": []})


def declared_context(**overrides):
    base = {"portfolio_version": "v1", "portfolio_complete": True, "base_currency": "USD",
            "completeness": "declared", "holdings": [], "recommendations": []}
    base.update(overrides)
    return base


def bands_adoption(**overrides):
    base = {"schema_version": 1, "rule_id": "rule-0123456789abcdef", "sleeve": "etf",
            "family": "fixed_weight_bands",
            "parameters": {"relative_band": 0.25, "absolute_band": 0.05, "calendar_days": 365.0},
            "universe": ["QQQ", "SPY"], "targets": {"QQQ": 0.5, "SPY": 0.5},
            "cost_model": {"per_share_usd": 0.0035, "minimum_usd": 1.0,
                           "max_pct_of_notional": 0.01, "spread_bps": 2.0},
            "cash_floor_pct": 0.15, "integer_shares": True,
            "admission": {"admitted": True, "waived": False, "metrics": {"cagr": 0.08}},
            "waived": False}
    base.update(overrides)
    return base


class EngineProducedOrders(unittest.TestCase):
    def run_rule(self, **overrides):
        from copilot.policy import evaluate_rule
        kwargs = dict(adoption=bands_adoption(), snapshot=engine_fixture(),
                      context=declared_context(), investable_cash=20000.0, now=NOW)
        kwargs.update(overrides)
        return evaluate_rule(**kwargs)

    def test_one_order_per_universe_symbol_with_the_engine_tuple(self):
        result = self.run_rule()
        self.assertEqual({o["instrument_id"] for o in result["orders"]}, {"QQQ", "SPY"})
        self.assertEqual(result["rule_id"], "rule-0123456789abcdef")
        self.assertRegex(result["evaluation_id"], r"^eval-[0-9a-f]{16}$")
        for order in result["orders"]:
            for key in ("action", "quantity", "limit_price", "rule_id"):
                self.assertIn(key, order, f"{order['instrument_id']} missing {key}")

    def test_the_orders_are_actionable_on_a_declared_book(self):
        # The whole point. If this is research_only the engine produced nothing
        # a person can act on and the plan has not delivered.
        result = self.run_rule()
        self.assertEqual(result["execution_scope"], "actionable", result["orders"][0]["reasons"])
        for order in result["orders"]:
            self.assertEqual(order["execution_scope"], "actionable")
            self.assertGreater(order["quantity"], 0)

    def test_limit_price_is_the_snapshot_price_and_says_which_basis(self):
        snapshot = engine_fixture()
        result = self.run_rule(snapshot=snapshot)
        for order in result["orders"]:
            self.assertEqual(order["limit_price"],
                             snapshot["instruments"][order["instrument_id"]]["price"])
            self.assertEqual(order["limit_price_basis"], "split_adjusted_close")

    def test_the_measured_risk_inputs_are_reported_and_passed(self):
        result = self.run_rule()
        for order in result["orders"]:
            checks = order["risk_checks"]
            self.assertEqual(checks["single_name"]["status"], "pass")
            self.assertEqual(checks["sector"]["status"], "pass")
            self.assertEqual(checks["liquidity"]["status"], "pass")
            self.assertEqual(checks["drawdown"]["status"], "pass")
            self.assertEqual(checks["correlation"]["status"], "not_applicable")

    def test_unknown_coverage_withholds_every_quantity(self):
        result = self.run_rule(context={"portfolio_complete": False,
                                        "portfolio_version": "v1", "holdings": []})
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertFalse(result["coverage_known"])
        for order in result["orders"]:
            self.assertNotIn("quantity", order)
            self.assertNotIn("limit_price", order)

    def test_one_blocked_symbol_pauses_the_whole_evaluation(self):
        snapshot = engine_fixture()
        snapshot["instruments"]["SPY"]["quality_status"] = "fail"
        result = self.run_rule(snapshot=seal(snapshot))
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertIn("SPY", result["blocked_symbols"])
        for order in result["orders"]:
            self.assertNotIn("quantity", order)

    def test_a_universe_symbol_missing_from_the_snapshot_pauses_rather_than_raises(self):
        # Pausing is not raising. The first draft computed weights before this
        # check and crashed on the missing symbol's absent bars.
        snapshot = engine_fixture()
        del snapshot["instruments"]["SPY"]
        snapshot["evidence"] = [e for e in snapshot["evidence"] if e["evidence_id"] != "market_SPY"]
        result = self.run_rule(snapshot=seal(snapshot))
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertIn("SPY", result["blocked_symbols"])
        self.assertEqual({o["instrument_id"] for o in result["orders"]}, {"QQQ", "SPY"})

    def test_an_expired_snapshot_is_refused_before_anything_is_priced(self):
        # A research-bearing snapshot lives 30 minutes. Sizing against an
        # expired one and writing the result to an immutable table is worse
        # than refusing.
        from datetime import timedelta
        with self.assertRaisesRegex(ValueError, "expired"):
            self.run_rule(now=NOW + timedelta(days=2))

    def test_engine_reasons_survive_the_numeric_prose_gate(self):
        # Engine-authored reasons must carry no bare numbers apart from the
        # tokenised rule id, or policy blocks its own output.
        result = self.run_rule()
        for order in result["orders"]:
            self.assertNotEqual(order["action"], "data_insufficient",
                                f"engine blocked its own reasons: {order['reasons']}")

    def test_an_overweight_holding_becomes_a_sell_not_a_target(self):
        context = declared_context(holdings=[
            {"instrument_id": "QQQ", "quantity": 400, "currency": "USD"}])
        result = self.run_rule(context=context, investable_cash=0.0)
        qqq = next(o for o in result["orders"] if o["instrument_id"] == "QQQ")
        self.assertIn(qqq["action"], ("reduce", "sell"))
        self.assertLess(qqq["delta_shares"], 0)
        self.assertEqual(qqq["quantity"], abs(qqq["delta_shares"]))

    def test_the_brake_halves_a_buy_and_records_both_quantities(self):
        plain = self.run_rule()
        braked = self.run_rule(brake={"level": "reduce_50", "reason": "issuer halt",
                                      "evidence_ids": ["news_QQQ"]})
        for before, after in zip(plain["orders"], braked["orders"]):
            self.assertEqual(after["brake"]["pre_brake_quantity"], before["quantity"])
            self.assertEqual(after["quantity"], before["quantity"] // 2)
            self.assertFalse(after["brake"]["backtested"])

    def test_skip_returns_a_decision_rather_than_raising(self):
        # The first draft wrote the post-brake quantity into a proposal while
        # gating on the pre-brake one, so skip hit policy's positive-number
        # check and raised out of an MCP tool with no try/except.
        result = self.run_rule(brake={"level": "skip", "reason": "halt",
                                      "evidence_ids": ["news_QQQ"]})
        for order in result["orders"]:
            self.assertEqual(order["action"], "hold")
            self.assertNotIn("quantity", order)
            self.assertEqual(order["brake"]["post_brake_quantity"], 0)

    def test_the_brake_never_touches_a_sell(self):
        # Halving a sell leaves MORE exposure than the rule asked for, which is
        # an accelerator wearing a brake's name.
        context = declared_context(holdings=[
            {"instrument_id": "QQQ", "quantity": 400, "currency": "USD"}])
        plain = self.run_rule(context=context, investable_cash=0.0)
        braked = self.run_rule(context=context, investable_cash=0.0,
                               brake={"level": "reduce_50", "reason": "halt",
                                      "evidence_ids": ["news_QQQ"]})
        plain_qqq = next(o for o in plain["orders"] if o["instrument_id"] == "QQQ")
        braked_qqq = next(o for o in braked["orders"] if o["instrument_id"] == "QQQ")
        self.assertEqual(braked_qqq["quantity"], plain_qqq["quantity"])
        self.assertEqual(braked_qqq["brake"]["applied_to_side"], "sell")
        self.assertFalse(braked_qqq["brake"]["changed"])

    def test_brake_evidence_never_enters_evidence_ids(self):
        result = self.run_rule(brake={"level": "skip", "reason": "halt",
                                      "evidence_ids": ["news_QQQ"]})
        for order in result["orders"]:
            self.assertNotIn("news_QQQ", order["evidence_ids"])
            self.assertIn("news_QQQ", order["brake"]["evidence_ids"])

    def test_an_unknown_brake_level_raises(self):
        with self.assertRaises(ValueError):
            self.run_rule(brake={"level": "double", "reason": "x", "evidence_ids": ["news_QQQ"]})

    def test_a_snapshot_short_of_the_warmup_raises_naming_both_counts(self):
        adoption = bands_adoption(
            family="momentum_top_n",
            parameters={"top_n": 1.0, "lookback_days": 252.0, "skip_days": 21.0},
            targets=None)
        with self.assertRaisesRegex(ValueError, "252"):
            self.run_rule(adoption=adoption, snapshot=engine_fixture(count=100))

    def test_the_rule_trigger_is_honoured_not_replaced(self):
        # FixedWeightBands does nothing in `weights`; its behaviour lives in
        # should_rebalance. Skipping it turns an annual rule into a daily one
        # and the cost profile the admission gate measured stops applying.
        result = self.run_rule()
        self.assertIn("rebalance_due", result)
        self.assertIsInstance(result["rebalance_due"], bool)

    def test_a_rule_not_due_produces_holds_with_no_quantities(self):
        context = declared_context(
            holdings=[{"instrument_id": "QQQ", "quantity": 96, "currency": "USD"},
                      {"instrument_id": "SPY", "quantity": 87, "currency": "USD"}],
            recommendations=[{"rule_id": "rule-0123456789abcdef",
                              "evaluation_session": "2026-09-03",
                              "rebalance_due": True}])
        result = self.run_rule(context=context, investable_cash=0.0)
        if not result["rebalance_due"]:
            for order in result["orders"]:
                self.assertEqual(order["action"], "hold")
                self.assertNotIn("quantity", order)

    def test_a_gold_sleeve_adoption_is_refused(self):
        with self.assertRaisesRegex(ValueError, "sleeve"):
            self.run_rule(adoption=bands_adoption(sleeve="gold"))
```

Run `engine_fixture()` and the whole class before implementing and report what the first failure actually is. Several of these assert numbers (`before["quantity"] // 2`, `96`/`87` holdings) that depend on the fixture's prices and the 15% cash floor — **compute the real values from the fixture and fix the expectations to match, saying what you changed.** Do not weaken an assertion to a range or an inequality.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_policy.py -v
```

Expected: `ImportError: cannot import name 'evaluate_rule'`.

- [ ] **Step 3: Write `evaluate_rule` and its helpers**

Add at the end of `scripts/copilot/policy.py`:

```python
#: How the engine's order side maps onto the five ACTIONS policy accepts. A full
#: exit is a sell; a partial trim is a reduce. There is no "rebalance" action.
def _order_action(side: str, target_shares: int) -> str:
    if side == "buy":
        return "buy"
    if side == "sell":
        return "sell" if target_shares == 0 else "reduce"
    return "hold"


def _primary_evidence(snapshot: dict, symbol: str) -> dict:
    """The market evidence record whose bars the indicators were computed from.

    Selected by the same rule market_data.py:298-300 uses for `primary`, so the
    live signal is computed on the series the snapshot's own indicators describe.
    After ADR-0007 clause 1 every symbol has two records with bars, and the
    Nasdaq one carries a single unadjusted bar -- picking naively would either
    intersect a 260-bar history down to one session or mix an unadjusted close
    into an adjusted series.
    """
    item = snapshot.get("instruments", {}).get(symbol) or {}
    owned = set(item.get("evidence_ids") or [])
    candidates = [record for record in snapshot.get("evidence", [])
                  if record.get("evidence_id") in owned
                  and isinstance(record.get("bars"), list) and record["bars"]
                  and record.get("critical_evidence_eligible") is not False]
    if not candidates:
        raise ValueError(f"{symbol}: no market evidence in this snapshot carries bars; pass the "
                         "stored snapshot rather than the summary view, which strips them")
    return max(candidates, key=lambda r: (
        not bool(r.get("missing_sessions")),
        r.get("indicator_basis") == "total_return_adjusted",
        len(r["bars"])))


def _bars_matrix(snapshot: dict, universe: tuple) -> tuple[list, list, dict]:
    """Align each symbol's primary bars onto the sessions they all share.

    Intersection, not union: a weight computed from a forward-filled price is one
    nobody could have traded on. Reads `adjusted_close` when the record's basis is
    total-return adjusted, because that is the basis the rule families were
    backtested on; `item["price"]` supplies the limit price separately and the two
    are never mixed (ADR-0006 clause 3).
    """
    from datetime import date as _date
    per_symbol, records = {}, {}
    for symbol in universe:
        record = _primary_evidence(snapshot, symbol)
        records[symbol] = record
        adjusted = record.get("indicator_basis") == "total_return_adjusted"
        series = {}
        for row in record["bars"]:
            stamp = row.get("session")
            close = row.get("adjusted_close") if adjusted else row.get("close")
            if not stamp or isinstance(close, bool) or not isinstance(close, (int, float)):
                continue
            series[_date.fromisoformat(str(stamp)[:10])] = float(close)
        if not series:
            raise ValueError(f"{symbol}: no bar carried both a session and a usable close")
        per_symbol[symbol] = series
    common = set(per_symbol[universe[0]])
    for symbol in universe[1:]:
        common &= set(per_symbol[symbol])
    if not common:
        raise ValueError("the adopted universe shares no common session in this snapshot")
    dates = sorted(common)
    closes = [[per_symbol[symbol][day] for symbol in universe] for day in dates]
    return dates, closes, records


def _build_rule(adoption: dict):
    """Rebuild the rule object from the stored adoption, never from a model payload."""
    from .backtest import rules as rule_families
    family = adoption["family"]
    universe = tuple(adoption["universe"])
    p = {k: float(v) for k, v in (adoption.get("parameters") or {}).items()}
    if family == "fixed_weight_bands":
        targets = adoption.get("targets")
        if not targets:
            raise ValueError("fixed_weight_bands requires stored targets")
        return rule_families.FixedWeightBands(
            {str(k): float(v) for k, v in targets.items()},
            relative_band=p["relative_band"], absolute_band=p["absolute_band"],
            calendar_days=int(p["calendar_days"]))
    if family == "inverse_volatility":
        return rule_families.InverseVolatility(
            universe, lookback_days=int(p["lookback_days"]),
            rebalance_days=int(p["rebalance_days"]))
    if family == "momentum_top_n":
        return rule_families.MomentumTopN(
            universe, top_n=int(p["top_n"]), lookback_days=int(p["lookback_days"]),
            skip_days=int(p["skip_days"]))
    raise ValueError(f"unknown rule family {family!r}")


def _last_rebalance_index(context: dict, adoption: dict, dates: list) -> int | None:
    """Index of the session this rule last rebalanced on, from prior decisions.

    The rule families decide WHETHER to trade in `should_rebalance`, not in
    `weights`: FixedWeightBands.weights returns the stored targets unconditionally
    and all of its behaviour -- the 25% relative band, the 5-point absolute band,
    the calendar leg -- lives in the trigger. Evaluating `weights` alone turns an
    annual rule into a daily one and the turnover the admission gate measured
    stops describing it.
    """
    from datetime import date as _date
    sessions = [r.get("evaluation_session") for r in (context.get("recommendations") or [])
                if r.get("rule_id") == adoption["rule_id"] and r.get("rebalance_due")]
    stamps = sorted({_date.fromisoformat(str(s)[:10]) for s in sessions if s})
    if not stamps:
        return None
    for index in range(len(dates) - 1, -1, -1):
        if dates[index] <= stamps[-1]:
            return index
    return None


def _current_weights(holdings: list, prices: dict, total_value: float) -> dict:
    if total_value <= 0:
        return {}
    weights = {}
    for holding in holdings or []:
        symbol = str(holding.get("instrument_id", "")).upper()
        price, quantity = prices.get(symbol), holding.get("quantity")
        if price is None or isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            continue
        weights[symbol] = weights.get(symbol, 0.0) + float(quantity) * float(price) / total_value
    return weights


def _held_shares(holdings: list) -> dict:
    held = {}
    for holding in holdings or []:
        symbol = str(holding.get("instrument_id", "")).upper()
        quantity = holding.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            continue
        held[symbol] = held.get(symbol, 0.0) + float(quantity)
    return held


def _absent_decision(snapshot: dict, symbol: str, reasons: list, version: str) -> dict:
    """A blocked decision for a universe symbol the snapshot does not carry."""
    return {
        "schema_version": 1, "policy_version": POLICY_VERSION,
        "snapshot_id": snapshot.get("snapshot_id"), "instrument_id": symbol,
        "action": "data_insufficient", "requested_action": "hold",
        "data_status": "blocked", "reasons": list(reasons), "conditions": [],
        "risk_checks": {"data": {"status": "fail",
                                 "detail": f"{symbol} is absent from the snapshot"}},
        "evidence_ids": [], "claims": [],
        "reasoning_verification": "instrument absent from the evaluated snapshot",
        "portfolio_version": version, "mode": "accumulation", "horizon": "long_term",
        "warnings": [f"{symbol} is in the adopted universe but not in this snapshot"],
        "execution_scope": "research_only",
    }


def evaluate_rule(*, adoption: dict, snapshot: dict, context: dict | None = None,
                  investable_cash: float, brake: dict | None = None, now=None) -> dict:
    """Compute orders from an adopted rule. The only producer of live numbers.

    The engine feeds the evidence gate rather than bypassing it: it computes the
    weights, the share deltas and the measured risk inputs, then runs one
    proposal per symbol through assess_proposal with source="engine" and the
    per-proposal context that gate requires -- snapshot_id plus this proposal's
    own fingerprint plus the risk it measured for this trade. That is legitimate
    for the engine and not for a model, because the engine computed the trade and
    the risk from the same holdings and prices, so they genuinely correspond.

    ADR-0007 clause 6: one blocked symbol pauses the whole evaluation. Weights
    are computed across the universe, so one unreliable symbol makes every weight
    unreliable, and a partial basket would be a policy nobody approved.
    """
    from . import brake as brake_module
    from . import riskinputs, sizing
    from .backtest.engine import CostModel
    from .instruments import sector_of

    if not isinstance(adoption, dict) or not adoption.get("rule_id"):
        raise ValueError("adoption must be a stored adoption record with a rule_id")
    if adoption.get("sleeve") != "etf":
        raise ValueError(f"sleeve must be 'etf'; this adoption says "
                         f"{adoption.get('sleeve')!r} and no other sleeve has an engine")
    context = dict(context or {})
    rule_id = str(adoption["rule_id"])
    universe = tuple(str(s).upper() for s in adoption.get("universe") or [])
    if not universe:
        raise ValueError("adoption has an empty universe")

    created = _time(now if now is not None else datetime.now(timezone.utc), "now")
    valid_until = _time(snapshot.get("valid_until"), "snapshot.valid_until")
    if created >= valid_until:
        raise ValueError(f"snapshot {snapshot.get('snapshot_id')} expired at "
                         f"{valid_until.isoformat()}; collect a fresh one before sizing")

    brake_record = brake_module.record(
        level=str((brake or {}).get("level", "none")),
        reason=str((brake or {}).get("reason", "")),
        evidence_ids=(brake or {}).get("evidence_ids") or [])

    instruments = snapshot.get("instruments", {})
    blocked = [s for s in universe
               if s not in instruments or instruments[s].get("quality_status") != "pass"]
    coverage_known = context.get("portfolio_complete") is True
    version = str(context.get("portfolio_version") or "")
    withhold = bool(blocked) or not coverage_known

    plan, measured, rebalance_due = None, {}, None
    if not withhold:
        dates, closes, records = _bars_matrix(snapshot, universe)
        rule = _build_rule(adoption)
        if len(dates) <= rule.warmup_bars:
            raise ValueError(f"{adoption['family']} needs more than {rule.warmup_bars} bars to "
                             f"produce a signal; this snapshot shares {len(dates)}")
        from .backtest.frame import build as build_frame
        frame = build_frame(dates=dates, symbols=list(universe), closes=closes)
        last_index = len(dates) - 1
        prices = {s: instruments[s]["price"] for s in universe}
        held = _held_shares(context.get("holdings"))
        holdings_value = sum(held.get(s, 0.0) * prices[s] for s in held if s in prices)
        total_value = holdings_value + float(investable_cash)
        current = _current_weights(context.get("holdings"), prices, total_value)
        rebalance_due = rule.should_rebalance(
            frame, last_index, current, _last_rebalance_index(context, adoption, dates))
        weights = rule.weights(frame, last_index)
        plan = sizing.plan_orders(
            weights=weights, prices=prices, held_shares=held if rebalance_due else weights and held,
            investable_cash=float(investable_cash),
            cash_floor_pct=float(adoption.get("cash_floor_pct", 0.0)),
            cost_model=CostModel(**adoption["cost_model"]))
        if not rebalance_due:
            for order in plan["orders"]:
                order.update(delta_shares=0, side="hold", notional=0.0, estimated_cost=0.0)
        history = [float(r["portfolio_total_value"]) for r in (context.get("recommendations") or [])
                   if isinstance(r.get("portfolio_total_value"), (int, float))]
        history.append(plan["total_value"])
        sector_values: dict[str, float] = {}
        for order in plan["orders"]:
            sector = sector_of(order["instrument_id"])
            sector_values[sector] = sector_values.get(sector, 0.0) + \
                order["target_shares"] * order["limit_price"]
        for order in plan["orders"]:
            symbol = order["instrument_id"]
            sector = sector_of(symbol)
            own = order["target_shares"] * order["limit_price"]
            measured[symbol] = riskinputs.compute(
                symbol=symbol, target_shares=order["target_shares"],
                price=order["limit_price"], total_value=plan["total_value"],
                sector_values={sector: sector_values.get(sector, 0.0) - own},
                sector_of=sector,
                average_dollar_volume=riskinputs.average_dollar_volume(
                    _primary_evidence(snapshot, symbol)["bars"]),
                value_history=history, delta_shares=order["delta_shares"])

    sized = {o["instrument_id"]: o for o in (plan["orders"] if plan else [])}
    orders = []
    for symbol in universe:
        if blocked:
            reasons = [f"adopted rule {rule_id} paused: {', '.join(blocked)} has no "
                       "validated market data in this snapshot"]
        elif not coverage_known:
            reasons = [f"adopted rule {rule_id} produced a research view only; holdings "
                       "coverage has not been declared"]
        elif rebalance_due is False:
            reasons = [f"adopted rule {rule_id} is within its rebalance band; no trade is due"]
        else:
            reasons = [f"adopted rule {rule_id} produced this order"]
        if symbol not in instruments:
            orders.append({**_absent_decision(snapshot, symbol, reasons, version),
                           "rule_id": rule_id, "brake": dict(brake_record)})
            continue
        order = sized.get(symbol)
        applied = None
        item = {"instrument_id": symbol, "action": "hold", "mode": "accumulation",
                "horizon": "long_term", "reasons": reasons, "conditions": [],
                "evidence_ids": [_primary_evidence(snapshot, symbol)["evidence_id"]]}
        if order is not None and order["delta_shares"] != 0:
            traded = abs(order["delta_shares"])
            # The brake only ever reduces EXPOSURE. Halving a sell would leave
            # more exposure than the rule asked for, so a sell is never braked.
            if order["side"] == "buy":
                applied = brake_module.applied(record=brake_record, quantity=traded)
                traded = applied["post_brake_quantity"]
            else:
                applied = {**brake_module.applied(record=brake_record, quantity=traded),
                           "post_brake_quantity": traded}
            applied["applied_to_side"] = order["side"]
            applied["changed"] = traded != abs(order["delta_shares"])
            if traded > 0:
                item["action"] = _order_action(order["side"], order["target_shares"])
                item["quantity"] = traded
                item["price"] = order["limit_price"]
        proposal_context = {**context, "snapshot_id": snapshot["snapshot_id"],
                            "risk_proposal_fingerprint": proposal_fingerprint(item),
                            "verified_risk_inputs": measured.get(symbol, {})}
        decision = assess_proposal(item, snapshot, proposal_context, now=now, source="engine")
        decision["rule_id"] = rule_id
        decision["brake"] = applied or dict(brake_record)
        if order is not None and decision["data_status"] == "ready" and "quantity" in decision:
            decision["limit_price"] = order["limit_price"]
            decision["limit_price_basis"] = "split_adjusted_close"
            decision["delta_shares"] = order["delta_shares"]
            decision["target_shares"] = order["target_shares"]
            decision["held_shares"] = order["held_shares"]
            decision["estimated_cost"] = order["estimated_cost"]
            decision["portfolio_total_value"] = plan["total_value"]
            decision["admitted_metrics"] = dict(adoption.get("admission", {}).get("metrics", {}))
        orders.append(decision)

    actionable = (not withhold and bool(rebalance_due)
                  and all(o.get("execution_scope") == "actionable" for o in orders))
    result = {
        "schema_version": 1, "rule_id": rule_id, "sleeve": "etf",
        "snapshot_id": snapshot.get("snapshot_id"), "orders": orders,
        "blocked_symbols": blocked, "coverage_known": coverage_known,
        "rebalance_due": bool(rebalance_due),
        "execution_scope": "actionable" if actionable else "research_only",
        "brake": brake_record, "waived": bool(adoption.get("waived")),
        "admitted_metrics": dict(adoption.get("admission", {}).get("metrics", {})),
        "evaluation_session": (snapshot.get("instruments", {}).get(universe[0], {})
                               .get("latest_session")),
    }
    if not withhold:
        result["cash_plan"] = plan
    result["evaluation_id"] = "eval-" + _digest(result)[:16]
    return result
```

Two things in that code need resolving during implementation rather than being copied blindly, and you must report what you did:

1. The `held_shares=held if rebalance_due else weights and held` expression is wrong — it was written reaching for "pass the held shares either way" and reads as a bug. Pass `held` unconditionally and rely on the `rebalance_due` loop below to zero the deltas, or restructure so a not-due evaluation never calls `plan_orders` at all. Pick one, say which, and make the tests prove it.
2. `_digest(result)` runs `json.dumps(..., allow_nan=False)` over a dict containing `cash_plan`. Confirm every value in there is JSON-serialisable and finite — `round()` returns floats, but a `None` `max_pct_of_notional` inside a nested structure or a `date` object would raise. If it raises, serialise `evaluation_session` and any date to a string before digesting.

- [ ] **Step 4: Run the tests**

```bash
python scripts/_test_policy.py -v && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: 19 new policy tests OK; the whole suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/policy.py scripts/_test_policy.py
git commit -m "feat(policy): evaluate_rule produces orders through the evidence gate"
```

---

### Task 9: Stored adoptions, bounded history, and the config pointer

**Files:**
- Modify: `scripts/copilot/journal.py` (`adopted_rules` table, `record_adoption`, `load_adoption`, bounded recommendations)
- Modify: `scripts/copilot/config.py`, `config/user.example.toml`
- Modify: `scripts/_test_journal.py`, `scripts/_test_config.py`

- [ ] **Step 1: Write the failing tests**

Append to `scripts/_test_journal.py`:

```python
class Adoptions(unittest.TestCase):
    def adoption(self, **overrides):
        base = {"schema_version": 1, "rule_id": "rule-0123456789abcdef", "sleeve": "etf",
                "family": "momentum_top_n",
                "parameters": {"top_n": 2.0, "lookback_days": 252.0, "skip_days": 21.0},
                "universe": ["IWM", "QQQ", "SPY"], "targets": None,
                "cost_model": {"per_share_usd": 0.0035, "minimum_usd": 1.0,
                               "max_pct_of_notional": 0.01, "spread_bps": 2.0},
                "cash_floor_pct": 0.15, "integer_shares": True,
                "admission": {"admitted": True, "waived": False, "metrics": {"cagr": 0.08}},
                "waived": False}
        base.update(overrides)
        return base

    def test_record_and_load_round_trip(self):
        with self.database() as db:
            receipt = record_adoption(self.adoption(), db_path=db)
            self.assertEqual(receipt["rule_id"], "rule-0123456789abcdef")
            self.assertEqual(load_adoption("rule-0123456789abcdef", db_path=db)["family"],
                             "momentum_top_n")

    def test_the_same_adoption_twice_is_a_replay(self):
        with self.database() as db:
            record_adoption(self.adoption(), db_path=db)
            self.assertTrue(record_adoption(self.adoption(), db_path=db)["replayed"])

    def test_the_same_id_with_different_content_conflicts(self):
        with self.database() as db:
            record_adoption(self.adoption(), db_path=db)
            with self.assertRaises(JournalConflict):
                record_adoption(self.adoption(family="fixed_weight_bands"), db_path=db)

    def test_an_adoption_is_immutable(self):
        with self.database() as db:
            record_adoption(self.adoption(), db_path=db)
            with _connection(db) as connection:
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("UPDATE adopted_rules SET sleeve='gold'")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("DELETE FROM adopted_rules")

    def test_loading_an_unknown_rule_raises_keyerror(self):
        with self.database() as db:
            with self.assertRaises(KeyError):
                load_adoption("rule-ffffffffffffffff", db_path=db)

    def test_a_malformed_rule_id_is_refused(self):
        with self.database() as db:
            for bad in ("momentum", "rule-XYZ", "rule-0123", "rule-0123456789ABCDEF"):
                with self.assertRaisesRegex(ValueError, "rule_id"):
                    record_adoption(self.adoption(rule_id=bad), db_path=db)

    def test_adding_the_table_is_safe_for_an_existing_database(self):
        with self.database() as db:
            record_operation(self.operation(), "key-1", db_path=db)
            record_adoption(self.adoption(), db_path=db)
            self.assertEqual(load_adoption("rule-0123456789abcdef", db_path=db)["sleeve"], "etf")


class BoundedRecommendations(unittest.TestCase):
    def test_the_history_is_bounded_and_says_so(self):
        # An evaluation over 12 symbols writes 12 rows. get_context parsed every
        # row ever written on every call.
        with self.database() as db:
            self.store_recommendations(db, count=25)
            result = get_context(db_path=db, recommendations_limit=10)
            self.assertEqual(len(result["recommendations"]), 10)
            self.assertTrue(result["recommendations_truncated"])

    def test_a_short_history_is_not_marked_truncated(self):
        with self.database() as db:
            self.store_recommendations(db, count=3)
            result = get_context(db_path=db, recommendations_limit=10)
            self.assertEqual(len(result["recommendations"]), 3)
            self.assertFalse(result["recommendations_truncated"])

    def test_the_newest_rows_survive_truncation(self):
        # An "oldest first" mutant must fail this. Assert the actual ids.
        with self.database() as db:
            ids = self.store_recommendations(db, count=25)
            result = get_context(db_path=db, recommendations_limit=5)
            returned = {item["decision_id"] for item in result["recommendations"]}
            self.assertEqual(returned, set(ids[-5:]))

    def test_filtering_by_instrument_is_not_starved_by_the_limit(self):
        # The limit must be applied after the instrument filter, or asking for
        # one symbol returns nothing while its rows sit in the table.
        with self.database() as db:
            self.store_recommendations(db, count=30, symbol="SPY")
            self.store_recommendations(db, count=2, symbol="QQQ")
            result = get_context(["QQQ"], db_path=db, recommendations_limit=5)
            self.assertEqual(len(result["recommendations"]), 2)
```

Write `store_recommendations(db, count, symbol="QQQ")` using the file's existing `save_snapshot`/`record_recommendation` helpers; it must return the decision ids it created, in creation order, and give each a distinct `recorded_at` ordering key. Read the file first.

Append to `scripts/_test_config.py`. **Build every document from the module-level `VALID` template by string replacement** — a bare `[etf]` fragment fails on `schema_version` first and the test would pass for the wrong reason:

```python
class CashSemantics(unittest.TestCase):
    def test_the_field_is_named_for_cash(self):
        config = load_config(self.write(VALID.replace(
            "investable_total_usd = 0", "investable_cash_usd = 5000.0")))
        self.assertAlmostEqual(config.etf.investable_cash_usd, 5000.0)

    def test_the_old_name_is_refused_and_names_the_new_one(self):
        with self.assertRaisesRegex(ValueError, "investable_cash_usd"):
            load_config(self.write(VALID))

    def test_the_error_explains_the_semantics_not_just_the_rename(self):
        try:
            load_config(self.write(VALID))
        except ValueError as exc:
            self.assertIn("cash", str(exc).lower())


class AdoptionPointer(unittest.TestCase):
    def config(self, pointer):
        return self.write(VALID
                          .replace("investable_total_usd = 0", "investable_cash_usd = 0")
                          .replace('adopted_rule_id = ""', f'adopted_rule_id = "{pointer}"'))

    def test_a_well_formed_pointer_is_accepted(self):
        self.assertEqual(load_config(self.config("rule-0123456789abcdef")).etf.adopted_rule_id,
                         "rule-0123456789abcdef")

    def test_empty_means_nothing_is_adopted(self):
        self.assertEqual(load_config(self.config("")).etf.adopted_rule_id, "")

    def test_a_malformed_pointer_is_refused(self):
        for bad in ("momentum", "rule-XYZ", "rule-0123", "rule-0123456789ABCDEF"):
            with self.assertRaisesRegex(ValueError, "adopted_rule_id"):
                load_config(self.config(bad))
```

`VALID` still contains the old field name, so `test_the_old_name_is_refused` uses it unchanged and every other test replaces it. **You must also update `VALID` itself and any existing config test that relies on the old name** — list them and report what you changed.

- [ ] **Step 2: Run both to verify they fail**

```bash
python scripts/_test_journal.py -v; python scripts/_test_config.py -v
```

- [ ] **Step 3: Add the adoptions table and functions**

In `_SCHEMA`:

```sql
CREATE TABLE IF NOT EXISTS adopted_rules (
    rule_id TEXT PRIMARY KEY,
    sleeve TEXT NOT NULL,
    adopted_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS adoptions_no_update BEFORE UPDATE ON adopted_rules
BEGIN SELECT RAISE(ABORT, 'adoptions are immutable; adopt a new rule instead'); END;
CREATE TRIGGER IF NOT EXISTS adoptions_no_delete BEFORE DELETE ON adopted_rules
BEGIN SELECT RAISE(ABORT, 'adoptions are immutable; adopt a new rule instead'); END;
```

and the functions (add `import re` if absent):

```python
_RULE_ID = re.compile(r"^rule-[0-9a-f]{16}$")


def record_adoption(adoption: dict, *, db_path: Any = None) -> dict:
    """Store an immutable record of adopting a backtested rule.

    Adoption is a historical fact, so the row cannot change: superseding a rule
    means adopting a different one, which gets its own content-addressed id.
    Which rule is CURRENTLY live is not stored here -- that is the pointer the
    user edits by hand in config/user.toml (ADR-0007 clause 8), so nothing but an
    explicit user action changes what the engine will trade.
    """
    if not isinstance(adoption, dict):
        raise ValueError("adoption must be a dict")
    rule_id = adoption.get("rule_id")
    if not isinstance(rule_id, str) or not _RULE_ID.match(rule_id):
        raise ValueError("rule_id must look like rule-<16 lowercase hex characters>")
    sleeve = adoption.get("sleeve")
    if not isinstance(sleeve, str) or not sleeve:
        raise ValueError("adoption requires sleeve")
    serialized = _json(dict(adoption))
    with _connection(db_path) as connection:
        with _transaction(connection):
            previous = connection.execute(
                "SELECT payload FROM adopted_rules WHERE rule_id=?", (rule_id,)).fetchone()
            if previous and previous[0] != serialized:
                raise JournalConflict("rule_id already records a different adoption")
            connection.execute("INSERT OR IGNORE INTO adopted_rules VALUES (?,?,?,?)",
                               (rule_id, sleeve, _stamp(), serialized))
        return {"rule_id": rule_id, "recorded": True, "replayed": bool(previous)}


def load_adoption(rule_id: str, *, db_path: Any = None) -> dict:
    """Read a stored adoption. KeyError when the pointer does not resolve."""
    return _load("adopted_rules", "rule_id", rule_id, db_path)
```

- [ ] **Step 4: Bound the recommendation history after filtering**

The existing code selects every row and filters by instrument in Python. Applying a SQL `LIMIT` before that filter would starve a single-symbol query, so the limit goes after. Replace:

```python
            query = "SELECT payload FROM recommendations"
            params = ()
            if as_of is not None:
                query += " WHERE recorded_at <= ?"
                params = (_stamp(as_of, end_of_day=True),)
            query += " ORDER BY recorded_at DESC, decision_id"
            decisions = [json.loads(row[0]) for row in connection.execute(query, params).fetchall()]
            decisions = [dict(item, portfolio_current=item.get("portfolio_version") == portfolio_version) for item in decisions if not instruments or item.get("instrument_id") in instruments]
```

with:

```python
            # Newest first, and the LIMIT is applied AFTER the instrument filter:
            # limiting in SQL first would return the newest rows overall and then
            # filter them away, so asking for one symbol could come back empty
            # while its rows sat in the table. An evaluation over a 12-symbol
            # universe writes 12 rows, so an unbounded read grows fast.
            query = "SELECT payload FROM recommendations"
            params: tuple = ()
            if as_of is not None:
                query += " WHERE recorded_at <= ?"
                params = (_stamp(as_of, end_of_day=True),)
            query += " ORDER BY recorded_at DESC, decision_id"
            matched = [json.loads(row[0]) for row in connection.execute(query, params).fetchall()]
            matched = [item for item in matched
                       if not instruments or item.get("instrument_id") in instruments]
            limit = max(1, int(recommendations_limit))
            truncated = len(matched) > limit
            decisions = [dict(item, portfolio_current=item.get("portfolio_version") == portfolio_version)
                         for item in matched[:limit]]
```

Add `recommendations_limit: int = 200` to `get_context`'s signature and `"recommendations_truncated": truncated,` to the returned dict. Check every caller of `get_context` and report what you found — `service.context` is one.

This still reads every row before slicing, which is a real cost at scale but keeps the filter correct. Note that in the docstring rather than pretending the query is cheap.

- [ ] **Step 5: Rename the config field and validate the pointer**

In `scripts/copilot/config.py`, rename `investable_total_usd` to `investable_cash_usd` on `EtfConfig` with a comment saying it is cash, matching `engine.run(start_cash=...)` and ADR-0007 clause 3; add `_RULE_ID_RE = re.compile(r"^rule-[0-9a-f]{16}$")` and `import re`; and in `_etf`:

```python
    if "investable_total_usd" in section:
        raise ValueError("investable_total_usd was renamed investable_cash_usd: the value is "
                         "investable CASH available to deploy, not the sleeve's market value "
                         "(ADR-0007 clause 3)")
    adopted = _string(section, "adopted_rule_id", "etf")
    if adopted and not _RULE_ID_RE.match(adopted):
        raise ValueError(f"etf.adopted_rule_id must be empty or look like "
                         f"rule-<16 lowercase hex characters>, got {adopted!r}")
```

and pass `investable_cash_usd=_number(section, "investable_cash_usd", "etf", low=0, high=1e9)` plus `adopted_rule_id=adopted` to the constructor.

In `config/user.example.toml` replace the two blocks:

```toml
# CASH you can deploy into this sleeve, not its market value. Same quantity the
# backtest engine calls start_cash. Set it by hand; the IBKR Flex sync that
# would maintain it is not built.
investable_cash_usd = 0
```

```toml
# Points at a rule recorded by `copilot_cli.py adopt`, which prints the id.
# Nothing writes this file: keeping the pointer manual is what makes adoption an
# explicit act of yours rather than something a daily scan can do. Empty means
# nothing is adopted and the engine produces no orders.
adopted_rule_id = ""
```

- [ ] **Step 6: Run everything**

```bash
python scripts/_test_journal.py -v && python scripts/_test_config.py -v \
  && python -m unittest discover -s scripts -p "_test_*.py"
```

Grep for `investable_total_usd` across the repository and fix every remaining reference; report what you found.

- [ ] **Step 7: Commit**

```bash
git add scripts/copilot/journal.py scripts/copilot/config.py config/user.example.toml scripts/_test_journal.py scripts/_test_config.py
git commit -m "feat(journal): immutable adoptions; config names investable cash and checks the pointer"
```

---

### Task 10: Facades — declare, adopt, evaluate

**Files:**
- Modify: `scripts/copilot/service.py`, `mcps/copilot_mcp.py`, `scripts/copilot_cli.py`
- Modify: `scripts/sync_runtimes.py`, `.claude/settings.json`, `scripts/copilot_probe.py`, `.claude/skills/investment-chat/SKILL.md`
- Modify: `scripts/_test_copilot_service.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_copilot_service.py`, following its temporary-database conventions:

```python
class EngineFacade(unittest.TestCase):
    def test_evaluate_refuses_when_nothing_is_adopted(self):
        with self.assertRaisesRegex(ValueError, "adopted_rule_id"):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer=""), db_path=self.db)

    def test_evaluate_refuses_a_pointer_that_does_not_resolve(self):
        with self.assertRaises(KeyError):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer="rule-ffffffffffffffff"),
                             db_path=self.db)

    def test_evaluate_refuses_a_non_etf_adoption(self):
        # etf.adopted_rule_id is the only pointer that exists, so a gold
        # adoption could otherwise be pointed at and executed.
        self.store_adoption(sleeve="gold")
        with self.assertRaisesRegex(ValueError, "sleeve"):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer=self.rule_id), db_path=self.db)

    def test_every_order_is_persisted_under_one_evaluation_id(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db, now=self.now)
        stored = service.context(db_path=self.db)["recommendations"]
        matching = [r for r in stored if r.get("evaluation_id") == result["evaluation_id"]]
        self.assertEqual(len(matching), len(result["orders"]))
        for order in result["orders"]:
            self.assertEqual(order["evaluation_id"], result["evaluation_id"])

    def test_nothing_is_persisted_when_one_order_cannot_be_stored(self):
        # record_recommendation opens its own transaction per row, so a mid-loop
        # failure would leave half a basket in a table with immutability
        # triggers, permanently, with nothing marking the rest as missing.
        result = self.evaluate_with_a_failing_row()
        stored = service.context(db_path=self.db)["recommendations"]
        self.assertEqual([r for r in stored if r.get("evaluation_id") == result], [])

    def test_the_rendered_message_marks_an_applied_brake_unbacktested(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db, now=self.now,
                                  brake={"level": "reduce_50", "reason": "halt",
                                         "evidence_ids": [self.news_evidence_id]})
        self.assertIn("未回测", result["message"])

    def test_no_brake_adds_no_disclosure(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db, now=self.now)
        self.assertNotIn("未回测", result["message"])

    def test_a_research_only_result_says_why_rather_than_printing_an_empty_list(self):
        # The first draft printed the heading "按已采纳规则计算的委托：" and then
        # skipped every line, leaving a promise with nothing under it.
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db_without_coverage, now=self.now)
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertNotIn("按已采纳规则计算的委托", result["message"])
        self.assertIn("覆盖", result["message"])

    def test_brake_evidence_must_exist_in_the_snapshot(self):
        # The only evidence field with no validation behind it, and its input is
        # a headline the model read.
        with self.assertRaisesRegex(ValueError, "brake"):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer=self.rule_id),
                             db_path=self.db, now=self.now,
                             brake={"level": "skip", "reason": "halt",
                                    "evidence_ids": ["ev_does_not_exist"]})

    def test_declare_coverage_reports_what_invalidates_it(self):
        receipt = service.declare_coverage(db_path=self.db)
        self.assertIn("失效", receipt["message"])
        self.assertTrue(receipt["history"][0]["current"])

    def test_adopt_records_and_points_at_the_next_step(self):
        receipt = service.adopt(self.adoption_inputs(), db_path=self.db)
        self.assertRegex(receipt["rule_id"], r"^rule-[0-9a-f]{16}$")
        self.assertIn("config/user.toml", receipt["next_step"])
        self.assertIn(receipt["rule_id"], receipt["next_step"])
```

Write the fixtures the class needs. `self.db_without_coverage` is a database with the same snapshot and adoption but no coverage declaration; `self.news_evidence_id` is an evidence record in the snapshot carrying `critical_evidence_eligible: False`; `evaluate_with_a_failing_row` must make exactly one order unstorable — the simplest way is a universe symbol absent from the snapshot, whose `_absent_decision` has no `quantity` and can still be stored, so you will need a different lever: report how you made a row fail and whether an all-or-nothing write was achievable with `record_recommendation` as it stands.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_copilot_service.py -v
```

- [ ] **Step 3: Add the three service entry points**

`declare_coverage` landed in Task 2. Add:

```python
def adopt(adoption_inputs: dict, *, db_path=None) -> dict:
    """Record an adoption and tell the user how to make it live.

    Recording is not activation. The engine trades whatever
    config/user.toml's etf.adopted_rule_id points at, and only the user edits
    that file (ADR-0007 clause 8).
    """
    from .journal import record_adoption
    from .ruleset import build_adoption
    record = build_adoption(**adoption_inputs)
    receipt = record_adoption(record, db_path=database_path(db_path))
    receipt["adoption"] = record
    receipt["next_step"] = (f'set etf.adopted_rule_id = "{record["rule_id"]}" in '
                            "config/user.toml to make this rule live")
    return receipt


def evaluate(*, snapshot_id: str, config_path=None, db_path=None,
             brake: dict | None = None, now=None) -> dict:
    """Run the adopted rule against a stored snapshot and persist every order.

    All orders are validated before any is written. record_recommendation opens
    its own transaction per row, so writing as we go would leave half a basket in
    a table with immutability triggers if a later row failed.
    """
    from .config import load_config
    from .journal import load_adoption, record_recommendation
    from .policy import evaluate_rule
    settings = load_config(config_path)
    pointer = settings.etf.adopted_rule_id
    if not pointer:
        raise ValueError("no rule is adopted; set etf.adopted_rule_id in config/user.toml to a "
                         "rule id printed by `copilot_cli.py adopt`")
    path = database_path(db_path)
    adoption = load_adoption(pointer, db_path=path)
    stored = snapshot(snapshot_id, db_path=db_path)
    _verify_brake_evidence(stored, brake)
    current = context(db_path=db_path)
    result = evaluate_rule(adoption=adoption, snapshot=stored, context=current,
                           investable_cash=settings.etf.investable_cash_usd,
                           brake=brake, now=now)
    prepared = []
    for order in result["orders"]:
        order["evaluation_id"] = result["evaluation_id"]
        order["evaluation_session"] = result["evaluation_session"]
        order["rebalance_due"] = result["rebalance_due"]
        order["decision_id"] = "decision-" + hashlib.sha256(
            strict_json(order).encode()).hexdigest()
        for field in ("snapshot_id", "portfolio_version", "instrument_id", "action"):
            if not isinstance(order.get(field), str) or not order[field]:
                raise ValueError(f"order for {order.get('instrument_id')!r} cannot be recorded: "
                                 f"{field} is missing")
        prepared.append(order)
    for order in prepared:
        record_recommendation(order, db_path=path)
    result["message"] = render_evaluation(result, stored)
    return result


def _verify_brake_evidence(stored: dict, brake: dict | None) -> None:
    """Every brake evidence id must name a record that exists and is news.

    The brake is the only evidence field in the system with no validation behind
    it, and its input is a headline the model read from an aggregator. An id that
    resolves to nothing would become the stated grounds for zeroing a basket.
    """
    ids = list((brake or {}).get("evidence_ids") or [])
    if not ids:
        return
    records = {r.get("evidence_id"): r for r in stored.get("evidence", [])}
    for eid in ids:
        record = records.get(eid)
        if record is None:
            raise ValueError(f"brake evidence {eid!r} is not in snapshot "
                             f"{stored.get('snapshot_id')}")
        if record.get("critical_evidence_eligible") is not False:
            raise ValueError(f"brake evidence {eid!r} is market evidence, not news; the brake "
                             "reads news and market evidence belongs in evidence_ids")


def render_evaluation(result: dict, stored: dict) -> str:
    """Render only what the engine produced. Never print a heading with nothing under it."""
    lines = []
    if result.get("execution_scope") != "actionable":
        blocked = result.get("blocked_symbols") or []
        if blocked:
            reason = "数据未通过校验：" + "、".join(blocked)
        elif not result.get("coverage_known"):
            reason = "持仓覆盖未声明"
        elif not result.get("rebalance_due"):
            reason = "规则未触发再平衡"
        else:
            reason = "风险检查未全部通过"
        lines.append(f"研究观点，未给出具体仓位（{reason}）")
    else:
        lines.append("按已采纳规则计算的委托：")
        for order in result["orders"]:
            if not order.get("quantity"):
                continue
            labels = {"buy": "买入", "reduce": "减持", "sell": "清仓"}
            lines.append(f"  {order['instrument_id']} "
                         f"{labels.get(order['action'], order['action'])} "
                         f"{order['quantity']} 股，限价 {order['limit_price']}")
    unfunded = (result.get("cash_plan") or {}).get("unfunded") or []
    if unfunded:
        lines.append("现金不足未下单：" + "、".join(unfunded))
    if (result.get("brake") or {}).get("level", "none") != "none":
        lines.append(result["brake"]["disclosure"])
    if result.get("waived"):
        lines.append("该规则的准入门槛带有书面豁免，见 ADR-0006")
    return "\n".join(lines)
```

- [ ] **Step 4: Wire the MCP tools**

Add two tools to `mcps/copilot_mcp.py`:

```python
@mcp.tool()
def declare_holdings_coverage(base_currency: str = "USD") -> dict:
    """Record that the user has confirmed their recorded holdings are complete.

    Call this only when the user has explicitly said so. It is their assertion,
    not an inference from what happens to be recorded. Any later trade
    invalidates it and it must be made again.
    """
    return service.declare_coverage(base_currency=base_currency)


@mcp.tool()
def evaluate_adopted_rule(snapshot_id: str, brake_level: str = "none",
                          brake_reason: str = "",
                          brake_evidence_ids: list[str] | None = None) -> dict:
    """Run the adopted rule against a stored snapshot. The engine computes every number.

    You cannot supply a quantity, price or rule id. The only thing you choose is
    the brake: "none", "reduce_50" or "skip", which may reduce or cancel a
    purchase and can never enlarge one or change a sale. A non-"none" level needs
    a stated reason and at least one news evidence id from the snapshot, and is
    reported as not backtested. Report the engine's orders, never your own
    figures.
    """
    return service.evaluate(snapshot_id=snapshot_id,
                            brake={"level": brake_level, "reason": brake_reason,
                                   "evidence_ids": brake_evidence_ids or []})
```

- [ ] **Step 5: Wire the CLI**

Add three subparsers and their dispatch branches:

```python
    sub.add_parser("declare-coverage", help="record that recorded holdings are complete")
    adopt_cmd = sub.add_parser("adopt", help="record a backtested rule and print its id")
    adopt_cmd.add_argument("--input", default="-", help="adoption JSON file or stdin")
    evaluate_cmd = sub.add_parser("evaluate", help="run the adopted rule against a snapshot")
    evaluate_cmd.add_argument("snapshot_id")
    evaluate_cmd.add_argument("--brake-level", choices=("none", "reduce_50", "skip"),
                              default="none")
    evaluate_cmd.add_argument("--brake-reason", default="")
    evaluate_cmd.add_argument("--brake-evidence-id", action="append", default=[])
```

```python
        elif args.command == "declare-coverage":
            result = service.declare_coverage(db_path=args.db)
        elif args.command == "adopt":
            result = service.adopt(read_object(args.input), db_path=args.db)
        elif args.command == "evaluate":
            result = service.evaluate(snapshot_id=args.snapshot_id, db_path=args.db,
                                      brake={"level": args.brake_level,
                                             "reason": args.brake_reason,
                                             "evidence_ids": args.brake_evidence_id})
```

`adopt` takes a JSON object whose keys are `build_adoption`'s parameters — but `result` is an `engine.Result`, not JSON. Decide how `adopt` receives a backtest result: either it takes a path to a `backtest_cli.py` report and reconstructs the `Result`, or `backtest_cli.py` gains an `--adopt` flag that calls `service.adopt` directly with the in-memory result. **Pick one, say which and why, and make the CLI help text say it** — a JSON adoption payload that lets a caller describe a backtest that never ran is exactly what Task 4 refuses.

- [ ] **Step 6: Complete the four-place sync**

No automatic cross-check exists for these; each is a separate edit.

1. `scripts/sync_runtimes.py` — add both new tool names to the hardcoded tools list.
2. `.claude/settings.json` — add both to the allow array, matching the existing format.
3. `scripts/copilot_probe.py` — add both to the required tool set.
4. `.claude/skills/investment-chat/SKILL.md` — document them, and state plainly that the model chooses only the brake level, reason and evidence ids, never a quantity, price or rule; and that a coverage declaration must come from the user saying so, not from the model deciding the records look complete.

Then regenerate:

```bash
python scripts/sync_runtimes.py && python scripts/sync_runtimes.py --check
```

- [ ] **Step 7: Run every gate**

```bash
python scripts/check.py
python scripts/sync_runtimes.py --check
python -m unittest discover -s scripts -p "_test_*.py"
python scripts/package_release.py --self-test
python scripts/package_release.py
ruff check --select E9,F63,F7,F82 scripts evals mcps
python scripts/mcp_handshake.py --all --timeout 300
```

- [ ] **Step 8: End-to-end proof**

The acceptance test for the whole plan. Run it and paste the real output.

```bash
python scripts/copilot_cli.py snapshot SPY QQQ --horizon daily
python scripts/copilot_cli.py declare-coverage
```

Adopt a rule over those two symbols from a real admitted backtest, point `config/user.toml` at the printed id, then:

```bash
python scripts/copilot_cli.py evaluate <snapshot_id>
```

Report the orders, the `execution_scope`, the `rebalance_due` flag, and the five `risk_checks` for one order. **This run must produce `actionable` with real quantities** — that is what the whole plan is for, and coverage is now declarable. If it comes back `research_only`, report which condition withheld it and why; a `research_only` result here is a finding, not a pass.

Then run it once more with `--brake-level reduce_50 --brake-reason "acceptance test" --brake-evidence-id <a news evidence id>` and paste the pre- and post-brake quantities.

- [ ] **Step 9: Commit**

```bash
git add scripts/copilot/service.py mcps/copilot_mcp.py scripts/copilot_cli.py scripts/sync_runtimes.py .claude/settings.json scripts/copilot_probe.py .claude/skills/investment-chat/SKILL.md .agents skills .codex scripts/_test_copilot_service.py
git commit -m "feat(facade): declare coverage, adopt a rule, and evaluate it"
```

---

## What this plan deliberately does not build

- **The IBKR Flex client.** Deferred by the user pending a real token. Until then `investable_cash_usd` and the holdings records are maintained by hand, and the coverage declaration is what makes that state usable rather than guessed at.
- **Notifications and scheduling.** Plan 5. The 30-minute cap on a research-bearing snapshot (`service.py:89-90`) is the binding constraint on a daily scan and belongs there; `evaluate` refuses an expired snapshot rather than silently sizing against one.
- **The gold sleeve.** Plan 6. `ruleset.SLEEVES` is `("etf",)` on purpose. Note the brake cannot serve gold either: `research_data.py:360-362` fetches news only for `stock` and `etf`.
- **The README strategy table (Q22=C).** Plan 7. It needs Plan 3's numbers and this plan's order shape.
- **Look-through concentration.** ADR-0007 clause 7 disables the correlation check and records why. The informative measure needs fund constituents, which no configured source provides. This is the largest known gap in the risk model and it is written down rather than papered over.
- **A TOML writer.** ADR-0007 clause 8 makes the adoption pointer a deliberate manual step.
- **Decimal monetary values on the recommendation path.** `journal.py`'s decimal enforcement runs only on the operation path. This plan writes float `limit_price`, `notional` and `estimated_cost` into `recommendations`, where `round(2.675, 2) == 2.67` is unrecoverable behind the immutability triggers. Acting on it is still safe because a limit price is a reference, not a fill — a fill is recorded through `record_investment_operation`, which does enforce decimal strings. If that reasoning ever stops holding, the fix is `Decimal` inside `sizing.plan_orders`, not a change to the journal.
- **Q13's three comfort parameters.** Still deferred to after launch: `max_drawdown_pct`, `min_cash_reserve_pct` and the single-month cap. Two of them are now live in different places under different names — `min_cash_reserve_pct` in config against `cash_floor_pct` in the adoption record, and `max_drawdown_pct` (0.20) against `policy._LIMITS["drawdown"]` (0.15). Reconciling them is a user decision, not a refactor, and until it happens the adoption record's value is the one that binds.

# Rule Engine and News Brake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn an adopted, backtested rule plus current holdings plus a fresh snapshot into `{action, quantity, limit_price, rule_id}` computed entirely by the engine, with a bounded one-way news brake and a cost-aware integer sizing step.

**Architecture:** The engine does not bypass the evidence gate — it becomes a *producer* of proposals that the gate still checks. `policy.evaluate_rule` computes target weights and share counts, constructs one internal proposal per symbol, and runs each through the existing `assess_proposal`. That reuses every blocker, claim and risk check unchanged, and satisfies ADR-0004 because the numbers originate in `policy.py`, never in the model. A keyword-only `source="engine"` distinguishes the two callers; the MCP surface never exposes it, so the model has no path to set it.

**Tech Stack:** Python 3.11+ stdlib only for the new modules. `scripts/copilot/backtest/engine.CostModel` is reused so live sizing and backtest sizing share one cost basis.

---

## Verified facts this plan is built on

Every number and quoted string below was observed on 2026-09-19 by reading the code or running it. Do not re-derive them; do not "improve" them away.

### The data layer blocks most of the whitelist today

`NasdaqEquityProvider` requires the Nasdaq-reported exchange to start with `NASDAQ`, `NYSE` or `AMEX` (`scripts/copilot/providers.py:503`). Probed live across **all 39 registry symbols**, exactly two labels exist:

| Label | Count | Meaning |
|---|---|---|
| `PSE` | 29 | NYSE Arca (the legacy Pacific Exchange code Nasdaq still reports) |
| `NASDAQ-GM` | 9 | Nasdaq Global Market |
| symbol mismatch | 1 | SPLG — Nasdaq does not know it either |

So 29 of 39 symbols get `ProviderError("not_covered", "Nasdaq did not identify a supported US exchange")`, leaving Yahoo as the only upstream. Quality then fails, because `market_data.py:352` requires `authoritative or (len(upstreams) >= 2 and any(c["status"] == "pass" for c in comparisons))` and `authoritative` is only ever true for SGE and the Nasdaq index publisher — never for a US ETF. The item gets `issues: ["independent_price_confirmation_missing"]`, and `policy` then emits the blocker `"required instrument data has not passed quality checks"`, so `action` becomes `"data_insufficient"` — worse than the `research_only` ADR-0004 anticipates.

Of the 22 admissible equity ETFs, only **QQQ, SMH and SOXX** are Nasdaq-listed. The other 19 are blocked.

**SPLG failing on a second independent provider confirms Plan 3's `UNFETCHABLE` tier.** Yahoo returns HTTP 404 and Nasdaq returns a body whose `data` does not carry the requested symbol. Two providers, same verdict.

### ADR-0004 clause 2 is violated today

The ADR says the model "has no interface through which it can alter quantity, price, direction or rule identity. Prompts, skills and MCP tool schemas must not expose such a parameter" (`docs/adr/0004-engine-computes-model-explains.md:25`). But `policy.py:190` validates four model-supplied numeric keys:

```python
    for key in ("price", "quantity", "target_weight", "stop_loss"):
        if key in proposal and not _number(proposal[key], positive=True):
            raise ValueError(f"{key} must be a finite positive number")
```

and `policy.py:366-373` echoes them into the decision. The engine computes nothing: `action = "data_insufficient" if blockers else requested` (`policy.py:348`). The current direction is model-proposes / engine-vetoes, the exact inverse of the ADR.

Observed: a proposal claiming `price: 4242.0` against a snapshot price of `100.0` still returned `buy` with `execution_scope: "actionable"`. Only the gold path cross-checks price, against `retail_quote.ask_per_fine_gram` (`policy.py:368`).

### The risk limits make the ETF sleeve unexecutable

`_LIMITS["single_name"] = ("post_trade_weight", 0.05, False)` (`policy.py:18`). An 8–12 ETF sleeve is 8–12% per holding; `MomentumTopN` with `top_n=5` equal-weighted is 20%. Every configuration fails. With `MAX_UNIVERSE = 12` a fully compliant book could be at most 60% invested.

### Prose numbers are blocked unless they match a verified claim

`policy.py:134-160` strips `_IDENTITY_OR_DATE` tokens from `reasons` and `conditions` and then requires every remaining literal number to match a verified claim, else `"unsupported numerical fact in reasons/conditions; provide a matching verified claim"`. `_IDENTITY_OR_DATE` (`policy.py:87-93`) recognises `snap_`, `ev_`, `research-` and `decision-` prefixes but **not** a rule prefix. Engine-authored reasons must therefore be number-free apart from a tokenised rule id.

### Evidence and journal facts

- News evidence can never be cited as critical support. `research_data.company_news` passes `critical=False` (`research_data.py:351` into `research_data.py:97`), and `policy.py:273` blocks any proposal whose `evidence_ids` include such a record: `"evidence {eid} cannot support a critical recommendation claim"`. The brake's evidence must live in a separate field.
- `record_recommendation` requires four non-empty strings — `snapshot_id`, `portfolio_version`, `instrument_id`, `action` (`journal.py:507-509`) — plus an already-stored snapshot and an unchanged portfolio version. One row per instrument.
- `recommendations` has immutability triggers (`journal.py:129-132`), so its shape cannot be migrated later. `get_context` reads it with an unbounded `SELECT payload FROM recommendations` (`journal.py:468-475`).
- `get_context`'s completeness fields are literals: `"portfolio_complete": False, "completeness": "unknown", "base_currency": None, "fx_status": "unknown", "portfolio_value": None` (`journal.py:476`). No code can currently make them anything else, so `policy.py:316-318`'s `complete` gate is always false in production and `quantity` is never echoed.
- `_SCHEMA` is re-executed on every connection open (`journal.py:159`) and is entirely `CREATE ... IF NOT EXISTS`, so adding a table is safe for existing databases.
- `decision_id` prefixes disagree: `journal.py:511` generates `"decision_"` while `policy.py:376` and `service.py:131` use `"decision-"`. Anything new must pass an explicit id.

### Configuration facts

- `config/user.toml` **does not exist**. `load_config()` returns `present=False` with `universe=()`, `investable_total_usd=0.0`, `adopted_rule_id=""`.
- `config.py` has only `load_config` and `as_dict` and uses read-only `tomllib`. There is no TOML writer anywhere in the repository, and the offline CI matrix forbids adding one.
- `adopted_rule_id` appears exactly twice in production code: its definition (`config.py:31`) and a `_string()` validation with no pattern (`config.py:105`). Nothing writes it, nothing reads it.
- A backtest `Result` carries `rule_name` (the family) and `parameters`, and `parameters` deliberately excludes `universe`/`targets` because `admission.py` caps parameter count at 3. There is no stable rule identity anywhere.

### Facade facts

- Exactly 6 MCP tools, each a one-line delegation to `service.*`, no validation, no try/except (`mcps/copilot_mcp.py`).
- Adding a 7th requires four coordinated edits with **no automatic cross-check**: the hardcoded tools list in `scripts/sync_runtimes.py`, the allow array in `.claude/settings.json`, the required set in `scripts/copilot_probe.py`, and `.claude/skills/investment-chat/SKILL.md`.
- `scripts/check.py` holds a closed set of 5 expected command files; a new slash command fails the check unless added there.
- `snapshot_view` truncates bars to `bar_count` + the last 5 unless `full=True` (`service.py:107-118`). A rule needing 252 bars must read the stored snapshot, not the conversational view.
- A snapshot carrying research is capped at 30 minutes: `valid_until = min(expiry, now + 30min)` (`service.py:89-90`).
- The generated Codex config sets `tool_timeout_sec = 180`.

### The decisions the user made on 2026-09-19

1. **Relax the exchange whitelist** to accept NYSE Arca. Chosen over configuring Alpaca (free-tier SIP entitlement unverified, no credentials on the machine) and over restricting the sleeve to Nasdaq-listed ETFs.
2. **Per-sleeve concentration limits**: 5% for individual stocks, **25% for ETFs**, with the look-through caveat recorded — QQQ and SPY overlap heavily and the repository cannot compute look-through concentration, so 25% is a conventional figure with no data behind it.
3. **`investable_total_usd` means investable cash**, not sleeve market value. It is renamed `investable_cash_usd`.

---

## File Structure

| Path | Responsibility |
|---|---|
| `docs/adr/0007-engine-produced-orders.md` | Records the three decisions above, the ADR-0004 clause-2 remedy, and the all-or-nothing universe gate |
| `scripts/copilot/ruleset.py` | `rule_id` derivation and the adoption record shape. Pure, stdlib. |
| `scripts/copilot/sizing.py` | Cost-aware integer share sizing over `backtest.engine.CostModel`. Pure. |
| `scripts/copilot/brake.py` | The one-way news brake: levels, validation, application. Pure. |
| `scripts/copilot/policy.py` | Modified: per-sleeve limits, `rule-` token, `source=` discipline, new `evaluate_rule` |
| `scripts/copilot/journal.py` | Modified: `adopted_rules` table, `record_adoption`/`load_adoption`, bounded recommendations |
| `scripts/copilot/providers.py` | Modified: exchange whitelist accepts NYSE Arca |
| `scripts/copilot/config.py` | Modified: field rename, `adopted_rule_id` format check |
| `scripts/copilot/service.py` | Modified: `adopt` and `evaluate` entry points |
| `mcps/copilot_mcp.py`, `scripts/copilot_cli.py` | Modified: one new tool, two new subcommands |
| `scripts/_test_ruleset.py` | New offline suite for ruleset, sizing and brake |
| `scripts/_test_policy.py`, `scripts/_test_journal.py`, `scripts/_test_config.py` | Extended |

---

### Task 1: ADR-0007 and the exchange whitelist

The decision record lands first, as in Plans 1–3, together with the smallest defect fix it authorises.

**Files:**
- Create: `docs/adr/0007-engine-produced-orders.md`
- Modify: `scripts/copilot/providers.py:502-504`
- Test: `scripts/_test_market_data.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_market_data.py` (match the file's existing class style; if it uses a different harness, follow that):

```python
class ExchangeWhitelist(unittest.TestCase):
    def test_nyse_arca_is_accepted(self):
        # Probed live across all 39 registry symbols on 2026-09-19: Nasdaq
        # reports exactly two labels, PSE (29 symbols) and NASDAQ-GM (9).
        # PSE is the legacy Pacific Exchange code for what is now NYSE Arca,
        # the primary listing venue for most ETFs. Rejecting it left Yahoo as
        # the only upstream, so cross-provider confirmation was impossible and
        # 19 of the 22 admissible ETFs returned data_insufficient.
        from copilot.providers import SUPPORTED_US_EXCHANGE_PREFIXES, is_supported_us_exchange
        self.assertTrue(is_supported_us_exchange("PSE"))
        self.assertTrue(is_supported_us_exchange("NASDAQ-GM"))
        self.assertTrue(is_supported_us_exchange("NYSE ARCA"))
        self.assertIn("PSE", SUPPORTED_US_EXCHANGE_PREFIXES)

    def test_an_unknown_venue_is_still_refused(self):
        # The whitelist stays a whitelist. Only labels actually observed across
        # the registry were added; nothing speculative.
        from copilot.providers import is_supported_us_exchange
        for label in ("", "LSE", "TSX", "XETRA", "HKEX", "UNKNOWN"):
            self.assertFalse(is_supported_us_exchange(label), label)

    def test_the_refusal_names_the_label_it_saw(self):
        # A future relabelling must be diagnosable from the error alone.
        from copilot.providers import unsupported_exchange_detail
        self.assertIn("XETRA", unsupported_exchange_detail("XETRA"))
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_market_data.py -v
```

Expected: `ImportError: cannot import name 'SUPPORTED_US_EXCHANGE_PREFIXES'`

- [ ] **Step 3: Fix the whitelist**

In `scripts/copilot/providers.py`, above the provider classes, add:

```python
#: Every exchange label Nasdaq reported across all 39 registry symbols when
#: probed on 2026-09-19: PSE for 29 symbols, NASDAQ-GM for 9, and one symbol
#: (SPLG) Nasdaq does not know at all. PSE is the legacy Pacific Exchange code
#: for NYSE Arca, a registered US national securities exchange and the primary
#: listing venue for most ETFs; omitting it left Yahoo as the only upstream, so
#: cross-provider confirmation was impossible for 29 of 39 symbols. "ARCA" and
#: "NYSE ARCA" are included because Nasdaq could modernise the label; nothing
#: else speculative was added, because a whitelist that guesses is not one.
SUPPORTED_US_EXCHANGE_PREFIXES = ("NASDAQ", "NYSE", "AMEX", "ARCA", "PSE")


def is_supported_us_exchange(label: str) -> bool:
    return str(label or "").upper().startswith(SUPPORTED_US_EXCHANGE_PREFIXES)


def unsupported_exchange_detail(label: str) -> str:
    """Name the label so a future relabelling is diagnosable from the error."""
    return (f"Nasdaq reported exchange {label!r}, which is not one of the supported "
            f"US venues {SUPPORTED_US_EXCHANGE_PREFIXES}")
```

Then replace the check at `providers.py:502-504`:

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

Expected: 3 new tests, OK.

- [ ] **Step 5: Confirm live that a blocked symbol now passes quality**

This step is the point of the task; do not skip it.

```bash
python scripts/copilot_cli.py snapshot SPY QQQ --horizon daily
```

Expected: SPY's instrument entry now shows `"quality_status": "pass"` with two upstreams and a passing comparison, where before it carried `"independent_price_confirmation_missing"`. Report the actual `quality_status`, the upstream count and the comparison for both symbols. If SPY still fails, **stop and report** — the rest of this plan has no acceptance path without it.

- [ ] **Step 6: Write ADR-0007**

Create `docs/adr/0007-engine-produced-orders.md` following the house format (`# ADR-NNNN: Title` / `Status: accepted, YYYY-MM-DD.` / `## Context` / `## Decision` / `## Consequences`). It must record, in this order:

**Context** — four findings, each with its evidence:
1. The exchange-whitelist blockage, with the 29/9/1 label distribution and the consequence that 19 of 22 admissible ETFs returned `data_insufficient`.
2. That ADR-0004 clause 2 is violated today by `policy.py:190`, and that the direction is currently model-proposes / engine-vetoes.
3. That `_LIMITS["single_name"] = 0.05` makes any ETF sleeve unexecutable, with the 8–12% and 20% arithmetic.
4. That `config.py` cannot write TOML and `config/user.toml` does not exist.

**Decision** — six clauses:
1. **NYSE Arca is a supported venue.** The whitelist accepts `PSE`/`ARCA` alongside `NASDAQ`/`NYSE`/`AMEX`. Only labels observed across the whole registry were added. The refusal message names the label it saw.
2. **Concentration limits are per sleeve**: 5% for an individual stock, 25% for an ETF. Record plainly that 25% is conventional and unmeasured — QQQ and SPY overlap heavily, and the repository cannot compute look-through concentration, so this limit bounds *name* concentration, not *exposure* concentration.
3. **The engine produces orders; the gate still checks them.** `policy.evaluate_rule` computes weights and share counts, then runs one internal proposal per symbol through the existing `assess_proposal` with `source="engine"`. Numbers originate in `policy.py` only. `assess_proposal` called with the default `source="model"` rejects `quantity`, `target_weight` and `stop_loss` outright, which is how clause 2 of ADR-0004 stops being aspirational. `price` remains accepted on the gold path, where it is verified against a merchant quote rather than invented.
4. **A blocked symbol blocks the whole evaluation.** Rebalance weights are computed across the universe, so a single symbol with failed or unknown data makes every weight unreliable. The evaluation returns `research_only` with no quantities and names the blocked symbols. This follows CLAUDE.md's "Unknown, failed or conflicting data pauses the affected direction" rather than inventing a partial-fill policy.
5. **Adoption is recorded in the journal, not written to TOML.** `config.py` is read-only by construction and the offline CI matrix forbids a TOML writer. An adoption is an immutable journal row; `config/user.toml`'s `etf.adopted_rule_id` stays a pointer the user edits by hand, and loading validates that the pointer resolves. This also keeps "only an explicit user action changes what is live".
6. **The news brake is one-way and un-backtested.** Levels are exactly `{none, reduce_50, skip}`. The model selects a level, the engine applies it, and the engine guarantees the post-brake quantity never exceeds the pre-brake quantity. Both quantities, the level, the reason and the brake's evidence ids are recorded on every decision. Brake evidence lives in `brake_evidence_ids`, never `evidence_ids`, because news evidence carries `critical_evidence_eligible: False` and citing it in `evidence_ids` triggers a blocker. Every rendering marks it un-backtested, per ADR-0005.

**Consequences** — at least: the whitelist change widens what counts as confirmable data and is a safety boundary moved deliberately; 25% single-ETF exposure is a real risk the user accepted with the look-through caveat unresolved; the all-or-nothing universe gate means one bad symbol pauses the sleeve, which is conservative and will be visible; adoption requires a manual config edit, which is friction by design; and the brake can only ever reduce, so a news event that argues for buying more has no channel — by construction.

- [ ] **Step 7: Run the gates**

```bash
python scripts/check.py && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: `check.py` 0 errors; the suite green.

- [ ] **Step 8: Commit**

```bash
git add docs/adr/0007-engine-produced-orders.md scripts/copilot/providers.py scripts/_test_market_data.py
git commit -m "fix(providers): accept NYSE Arca as a US venue, record ADR-0007"
```

---

### Task 2: Per-sleeve concentration limits

**Files:**
- Modify: `scripts/copilot/policy.py:17-23` and the risk-check loop at `policy.py:320-326`
- Test: `scripts/_test_policy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_policy.py`:

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
        for name, (_, limit, _) in _LIMITS.items():
            if name == "single_name":
                continue
            self.assertAlmostEqual(limit_for(name, "etf"), limit, msg=name)
            self.assertAlmostEqual(limit_for(name, "stock"), limit, msg=name)

    def test_an_etf_at_twenty_percent_now_passes(self):
        # MomentumTopN with top_n=5 equal-weighted is 20% a name. Under the old
        # flat 5% limit every configuration of the ETF sleeve failed.
        decision = assess_proposal(
            proposal(action="buy"), fixture(),
            complete_context(post_trade_weight=0.20))
        self.assertEqual(decision["risk_checks"]["single_name"]["status"], "pass")
        self.assertAlmostEqual(decision["risk_checks"]["single_name"]["limit"], 0.25)

    def test_an_etf_above_twenty_five_percent_still_fails(self):
        decision = assess_proposal(
            proposal(action="buy"), fixture(),
            complete_context(post_trade_weight=0.2500001))
        self.assertEqual(decision["risk_checks"]["single_name"]["status"], "fail")
        self.assertEqual(decision["action"], "hold")
```

The helpers `proposal`, `fixture` and `complete_context` already exist in `scripts/_test_policy.py` (the complete-context fixture is around line 37). Read them and use their real signatures; if `complete_context` does not take a `post_trade_weight` keyword, add one rather than duplicating the fixture.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_policy.py -v
```

Expected: `ImportError: cannot import name 'limit_for'`

- [ ] **Step 3: Implement per-sleeve limits**

In `scripts/copilot/policy.py`, after the `_LIMITS` definition, add:

```python
#: Concentration is a different risk for a fund than for a single company. A
#: broad-market ETF already holds hundreds of names, so the 5% ceiling written
#: for individual stocks makes any ETF sleeve unexecutable: 8-12 holdings is
#: 8-12% each, and a top-5 momentum rotation is 20%.
#:
#: 25% is conventional, not measured. It bounds NAME concentration, not
#: EXPOSURE concentration -- QQQ and SPY share most of their largest holdings,
#: and this repository cannot compute look-through overlap. See ADR-0007
#: clause 2; the caveat is unresolved, not forgotten.
_SLEEVE_LIMITS = {"etf": {"single_name": 0.25}}


def limit_for(name: str, asset_class: str) -> float:
    """The limit for a risk check, per sleeve. Defaults to the stock limit."""
    return _SLEEVE_LIMITS.get(asset_class, {}).get(name, _LIMITS[name][1])
```

Then in the risk-check loop, replace:

```python
    for name, (field, limit, strict) in _LIMITS.items():
        value = inputs.get(field)
        eligible = _number(value) and 0 <= value <= 1
        check = "unknown" if not eligible else "pass" if (value < limit if strict else value <= limit) else "fail"
        checks[name] = {"status": check, "value": value if eligible else None, "limit": limit}
```

with:

```python
    for name, (field, _default, strict) in _LIMITS.items():
        limit = limit_for(name, item.get("asset_class", ""))
        value = inputs.get(field)
        eligible = _number(value) and 0 <= value <= 1
        check = "unknown" if not eligible else "pass" if (value < limit if strict else value <= limit) else "fail"
        checks[name] = {"status": check, "value": value if eligible else None, "limit": limit}
```

The reported `limit` is now the one actually applied, so a reader of `risk_checks` sees the sleeve's real ceiling.

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_policy.py -v
```

Expected: 5 new tests, OK, and every pre-existing policy test still green.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/policy.py scripts/_test_policy.py
git commit -m "feat(policy): concentration limits per sleeve, 25% for an ETF"
```

---

### Task 3: `ruleset.py` — rule identity and the adoption record

**Files:**
- Create: `scripts/copilot/ruleset.py`
- Create: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Create `scripts/_test_ruleset.py`:

```python
#!/usr/bin/env python3
"""Offline contracts for rule identity, sizing and the news brake.

Stdlib only, no network, no clock: this runs in the CI matrix job that installs
zero third-party packages.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from copilot import ruleset


def adoption_kwargs(**overrides):
    base = dict(
        family="momentum_top_n",
        parameters={"top_n": 5.0, "lookback_days": 252.0, "skip_days": 21.0},
        universe=("QQQ", "SPY", "IWM", "VTV", "VUG"),
        targets=None,
        cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                    "max_pct_of_notional": 0.01, "spread_bps": 2.0},
        cash_floor_pct=0.15,
        integer_shares=True,
        admission={"admitted": True, "waived": False, "years": 18.15,
                   "cagr": 0.0853, "max_drawdown": 0.4511},
    )
    base.update(overrides)
    return base


class RuleIdentity(unittest.TestCase):
    def test_rule_id_is_content_addressed_and_stable(self):
        first = ruleset.rule_id(**adoption_kwargs())
        second = ruleset.rule_id(**adoption_kwargs())
        self.assertEqual(first, second)
        self.assertRegex(first, r"^rule-[0-9a-f]{16}$")

    def test_universe_order_does_not_change_the_id(self):
        a = ruleset.rule_id(**adoption_kwargs(universe=("QQQ", "SPY", "IWM", "VTV", "VUG")))
        b = ruleset.rule_id(**adoption_kwargs(universe=("VUG", "VTV", "IWM", "SPY", "QQQ")))
        self.assertEqual(a, b)

    def test_changing_the_universe_changes_the_id(self):
        # The backtest Result deliberately excludes universe from `parameters`
        # to stay under the three-parameter admission cap, so identity that
        # ignored it would call two different strategies the same rule.
        a = ruleset.rule_id(**adoption_kwargs())
        b = ruleset.rule_id(**adoption_kwargs(universe=("QQQ", "SPY", "IWM", "VTV")))
        self.assertNotEqual(a, b)

    def test_changing_a_parameter_changes_the_id(self):
        a = ruleset.rule_id(**adoption_kwargs())
        b = ruleset.rule_id(**adoption_kwargs(parameters={"top_n": 3.0, "lookback_days": 252.0, "skip_days": 21.0}))
        self.assertNotEqual(a, b)

    def test_changing_the_cost_model_changes_the_id(self):
        # Live sizing must use the same cost basis the backtest was admitted
        # under; a different one makes the admitted metrics meaningless.
        a = ruleset.rule_id(**adoption_kwargs())
        b = ruleset.rule_id(**adoption_kwargs(
            cost_model={"per_share_usd": 0.005, "minimum_usd": 1.0,
                        "max_pct_of_notional": 0.01, "spread_bps": 2.0}))
        self.assertNotEqual(a, b)

    def test_rule_id_contains_no_bare_number_that_the_prose_gate_would_flag(self):
        # policy._IDENTITY_OR_DATE must tokenise the whole id, or an
        # engine-authored reason citing it trips the numeric-provenance gate.
        import re
        from copilot.policy import _IDENTITY_OR_DATE
        text = f"adopted rule {ruleset.rule_id(**adoption_kwargs())} rebalance band breached"
        self.assertEqual(re.sub(r"\s+", " ", _IDENTITY_OR_DATE.sub(" ", text)).strip(),
                         "adopted rule rebalance band breached")


class AdoptionRecord(unittest.TestCase):
    def test_build_adoption_carries_every_identity_input(self):
        record = ruleset.build_adoption(sleeve="etf", **adoption_kwargs())
        self.assertEqual(record["sleeve"], "etf")
        self.assertEqual(record["rule_id"], ruleset.rule_id(**adoption_kwargs()))
        self.assertEqual(record["schema_version"], ruleset.SCHEMA_VERSION)
        for key in ("family", "parameters", "universe", "cost_model",
                    "cash_floor_pct", "integer_shares", "admission"):
            self.assertIn(key, record)

    def test_universe_is_stored_sorted(self):
        record = ruleset.build_adoption(sleeve="etf", **adoption_kwargs(
            universe=("VUG", "QQQ", "SPY", "IWM", "VTV")))
        self.assertEqual(record["universe"], ["IWM", "QQQ", "SPY", "VTV", "VUG"])

    def test_an_unadmitted_rule_cannot_be_adopted(self):
        with self.assertRaisesRegex(ValueError, "admitted"):
            ruleset.build_adoption(sleeve="etf", **adoption_kwargs(
                admission={"admitted": False, "waived": False, "years": 18.15,
                           "cagr": 0.0853, "max_drawdown": 0.4511}))

    def test_a_waived_adoption_is_allowed_but_marked(self):
        record = ruleset.build_adoption(sleeve="etf", **adoption_kwargs(
            admission={"admitted": True, "waived": True, "years": 17.0,
                       "cagr": 0.06, "max_drawdown": 0.30}))
        self.assertTrue(record["waived"])

    def test_an_unknown_sleeve_is_refused(self):
        with self.assertRaisesRegex(ValueError, "sleeve"):
            ruleset.build_adoption(sleeve="crypto", **adoption_kwargs())

    def test_a_symbol_outside_the_registry_is_refused(self):
        with self.assertRaises(ValueError):
            ruleset.build_adoption(sleeve="etf", **adoption_kwargs(
                universe=("QQQ", "NVDA")))

    def test_a_non_admissible_symbol_is_refused(self):
        # SCHD is registered but has zero 2008 bars, so it never passed the
        # Q29 gate; adopting a rule over it would claim evidence that does not
        # exist.
        with self.assertRaises(ValueError):
            ruleset.build_adoption(sleeve="etf", **adoption_kwargs(
                universe=("QQQ", "SCHD")))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ModuleNotFoundError: No module named 'copilot.ruleset'`

- [ ] **Step 3: Write `ruleset.py`**

```python
"""Rule identity and the adoption record. Pure, stdlib, no I/O.

WHY A NEW IDENTIFIER
A backtest Result carries `rule_name` (the family: fixed_weight_bands,
inverse_volatility, momentum_top_n) and `parameters`. Neither identifies a
strategy. `parameters` deliberately excludes `universe` and `targets` so the
admission gate's three-parameter cap measures only fitted knobs, which means two
runs over completely different baskets report the same name and parameters.

A rule_id is therefore content-addressed over everything that changes what the
strategy does or what evidence admitted it: the family, its parameters, the
universe, any fixed targets, the cost model the backtest was charged under, the
cash floor, the share granularity, and the admission verdict.

The cost model is part of identity on purpose. Live sizing must charge what the
backtest charged; a rule sized under a different fee schedule has admitted
metrics that no longer describe it.

FORMAT
`rule-` followed by 16 lowercase hex characters. The prefix is tokenised by
policy._IDENTITY_OR_DATE so an engine-authored reason can cite the id without
tripping the numeric-provenance gate, which otherwise requires every literal
number in prose to match a verified claim.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

SCHEMA_VERSION = 1
SLEEVES = ("etf", "gold")
ID_PREFIX = "rule-"
ID_HEX_LENGTH = 16

_COST_FIELDS = ("per_share_usd", "minimum_usd", "max_pct_of_notional", "spread_bps")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def _clean_cost_model(cost_model: Mapping[str, Any]) -> dict:
    if not isinstance(cost_model, Mapping):
        raise ValueError("cost_model must be a mapping")
    missing = [key for key in _COST_FIELDS if key not in cost_model]
    if missing:
        raise ValueError(f"cost_model is missing {', '.join(missing)}")
    cleaned = {}
    for key in _COST_FIELDS:
        value = cost_model[key]
        if value is None and key == "max_pct_of_notional":
            cleaned[key] = None       # None means uncapped; see engine.CostModel
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"cost_model.{key} must be a number")
        cleaned[key] = float(value)
    return cleaned


def _clean_universe(universe: Sequence[str]) -> list[str]:
    if not isinstance(universe, (list, tuple)) or not universe:
        raise ValueError("universe must be a nonempty sequence of symbols")
    symbols = sorted({str(s).upper() for s in universe})
    if len(symbols) != len(universe):
        raise ValueError("universe contains duplicate symbols")
    return symbols


def rule_id(*, family: str, parameters: Mapping[str, float], universe: Sequence[str],
            targets: Mapping[str, float] | None, cost_model: Mapping[str, Any],
            cash_floor_pct: float, integer_shares: bool,
            admission: Mapping[str, Any]) -> str:
    """Content-addressed identity. Order of the universe does not matter."""
    material = {
        "schema_version": SCHEMA_VERSION,
        "family": str(family),
        "parameters": {str(k): float(v) for k, v in dict(parameters).items()},
        "universe": _clean_universe(universe),
        "targets": ({str(k): float(v) for k, v in dict(targets).items()}
                    if targets else None),
        "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct),
        "integer_shares": bool(integer_shares),
        "admitted": bool(admission.get("admitted")),
        "waived": bool(admission.get("waived")),
    }
    digest = hashlib.sha256(_canonical(material).encode()).hexdigest()
    return ID_PREFIX + digest[:ID_HEX_LENGTH]


def build_adoption(*, sleeve: str, family: str, parameters: Mapping[str, float],
                   universe: Sequence[str], targets: Mapping[str, float] | None,
                   cost_model: Mapping[str, Any], cash_floor_pct: float,
                   integer_shares: bool, admission: Mapping[str, Any]) -> dict:
    """Build the immutable record of adopting a rule. Raises rather than guesses.

    An unadmitted rule cannot be adopted: the whole point of the Q29 gate is
    that adoption requires evidence. A WAIVED admission is allowed — the only
    waiver granted so far is ADR-0006 clause 5 for the BXN proxy's missing 2008
    — but the record carries the flag so nothing downstream can forget it.
    """
    if sleeve not in SLEEVES:
        raise ValueError(f"sleeve must be one of {SLEEVES}, got {sleeve!r}")
    if not admission.get("admitted"):
        raise ValueError("a rule that was not admitted cannot be adopted; "
                         "run the backtest and satisfy the Q29 gate first")
    symbols = _clean_universe(universe)
    if sleeve == "etf":
        from .backtest import universe as universe_tiers
        for symbol in symbols:
            classification = universe_tiers.classify(symbol)
            if not classification.admissible:
                raise ValueError(f"{symbol} is tier {classification.tier!r}: "
                                 f"{classification.reason}")
    identity = rule_id(family=family, parameters=parameters, universe=symbols,
                       targets=targets, cost_model=cost_model,
                       cash_floor_pct=cash_floor_pct,
                       integer_shares=integer_shares, admission=admission)
    return {
        "schema_version": SCHEMA_VERSION,
        "rule_id": identity,
        "sleeve": sleeve,
        "family": str(family),
        "parameters": {str(k): float(v) for k, v in dict(parameters).items()},
        "universe": symbols,
        "targets": ({str(k): float(v) for k, v in dict(targets).items()}
                    if targets else None),
        "cost_model": _clean_cost_model(cost_model),
        "cash_floor_pct": float(cash_floor_pct),
        "integer_shares": bool(integer_shares),
        "admission": dict(admission),
        "waived": bool(admission.get("waived")),
    }
```

`_clean_universe` raises on duplicates by comparing the deduplicated length against the input length — note this also rejects a universe passed as a set, which is deliberate: identity must not depend on an unordered input the caller cannot reproduce.

- [ ] **Step 4: Add the `rule-` token to the prose gate**

In `scripts/copilot/policy.py`, in `_IDENTITY_OR_DATE`, extend the prefix alternation:

```python
    r"(?<![A-Za-z])(?:RSI|SMA|EMA|ATR|MACD)\s*\d+(?!\d)|\b(?:snap_|ev_|research-|decision-)[A-Za-z0-9_-]+\b"
```

becomes

```python
    r"(?<![A-Za-z])(?:RSI|SMA|EMA|ATR|MACD)\s*\d+(?!\d)|\b(?:snap_|ev_|research-|decision-|rule-)[A-Za-z0-9_-]+\b"
```

- [ ] **Step 5: Run the test to verify it passes**

```bash
python scripts/_test_ruleset.py -v && python scripts/_test_policy.py -v
```

Expected: 13 ruleset tests OK; the policy suite still green.

- [ ] **Step 6: Commit**

```bash
git add scripts/copilot/ruleset.py scripts/_test_ruleset.py scripts/copilot/policy.py
git commit -m "feat(ruleset): content-addressed rule identity and the adoption record"
```

---

### Task 4: `sizing.py` — cost-aware integer shares

Plan 3's engine left a documented wart: it buys `floor(investable / price)` and then pays commission, so cash can finish a bar below the floor. Its comment names this as Plan 4's problem.

**Files:**
- Create: `scripts/copilot/sizing.py`
- Modify: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_ruleset.py` (add `from copilot import sizing` and `from copilot.backtest.engine import CostModel` to the imports):

```python
class CostAwareSizing(unittest.TestCase):
    def test_zero_cost_sizing_is_the_naive_floor(self):
        self.assertEqual(sizing.affordable_shares(
            budget=1000.0, price=333.0, cost_model=CostModel.free()), 3)

    def test_costs_reduce_the_count_when_the_naive_floor_does_not_fit(self):
        # 3 shares at 333.0 is 999.0, plus a 1.00 minimum commission and a 1bp
        # half-spread on 999.0 is 1000.0999 -- over a 1000.0 budget by 10 cents.
        model = CostModel()
        self.assertEqual(sizing.affordable_shares(
            budget=1000.0, price=333.0, cost_model=model), 2)

    def test_the_result_always_fits_the_budget(self):
        model = CostModel()
        for budget in (100.0, 1000.0, 9999.99, 20000.0, 123456.78):
            for price in (1.0, 37.5, 333.0, 612.34):
                shares = sizing.affordable_shares(
                    budget=budget, price=price, cost_model=model)
                notional = shares * price
                total = notional + (model.total(shares=shares, notional=notional)
                                    if shares else 0.0)
                self.assertLessEqual(total, budget + 1e-9,
                                     f"budget={budget} price={price} shares={shares}")

    def test_the_result_is_maximal(self):
        # One more share must not fit, or the sizing is leaving money idle.
        model = CostModel()
        for budget in (1000.0, 9999.99, 20000.0):
            for price in (37.5, 333.0):
                shares = sizing.affordable_shares(
                    budget=budget, price=price, cost_model=model)
                more = shares + 1
                notional = more * price
                self.assertGreater(
                    notional + model.total(shares=more, notional=notional), budget,
                    f"budget={budget} price={price} shares={shares} was not maximal")

    def test_a_budget_below_one_share_is_zero(self):
        self.assertEqual(sizing.affordable_shares(
            budget=10.0, price=333.0, cost_model=CostModel()), 0)

    def test_non_positive_inputs_are_zero_not_an_error(self):
        model = CostModel()
        self.assertEqual(sizing.affordable_shares(budget=0.0, price=10.0, cost_model=model), 0)
        self.assertEqual(sizing.affordable_shares(budget=-5.0, price=10.0, cost_model=model), 0)

    def test_a_non_positive_price_raises(self):
        # A zero or negative price is a data defect, not an empty order.
        with self.assertRaises(ValueError):
            sizing.affordable_shares(budget=100.0, price=0.0, cost_model=CostModel())

    def test_plan_for_weights_respects_the_cash_floor(self):
        plan = sizing.plan_orders(
            weights={"AAA": 0.5, "BBB": 0.5},
            prices={"AAA": 100.0, "BBB": 50.0},
            investable_cash=10000.0, cash_floor_pct=0.20,
            cost_model=CostModel.free())
        spent = sum(o["quantity"] * o["limit_price"] for o in plan["orders"])
        self.assertLessEqual(spent, 8000.0 + 1e-9)
        self.assertGreaterEqual(plan["cash_remaining"], 2000.0 - 1e-9)

    def test_plan_reports_a_symbol_it_could_not_afford(self):
        plan = sizing.plan_orders(
            weights={"AAA": 0.5, "BBB": 0.5},
            prices={"AAA": 100.0, "BBB": 100000.0},
            investable_cash=1000.0, cash_floor_pct=0.0,
            cost_model=CostModel.free())
        unaffordable = [o for o in plan["orders"] if o["instrument_id"] == "BBB"]
        self.assertEqual(unaffordable[0]["quantity"], 0)
        self.assertIn("BBB", plan["unfunded"])

    def test_plan_rejects_weights_that_do_not_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            sizing.plan_orders(weights={"AAA": 0.9}, prices={"AAA": 100.0},
                               investable_cash=1000.0, cash_floor_pct=0.0,
                               cost_model=CostModel.free())

    def test_plan_rejects_a_missing_price(self):
        with self.assertRaises(KeyError):
            sizing.plan_orders(weights={"AAA": 1.0}, prices={},
                               investable_cash=1000.0, cash_floor_pct=0.0,
                               cost_model=CostModel.free())
```

Verify the arithmetic in `test_costs_reduce_the_count_when_the_naive_floor_does_not_fit` yourself before implementing: with `CostModel()` defaults (`per_share_usd=0.0035`, `minimum_usd=1.00`, `max_pct_of_notional=0.01`, `spread_bps=2.0`), compute the commission and half-spread for 3 shares at 333.0 and confirm the total exceeds 1000.0. **If it does not, fix the test's numbers to a case that does and say so — do not weaken the assertion.**

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ImportError: cannot import name 'sizing'`

- [ ] **Step 3: Write `sizing.py`**

```python
"""Cost-aware integer share sizing. Pure, stdlib apart from the shared CostModel.

The backtest engine buys floor(investable / price) and pays commission
afterwards, so cash can finish a bar below the floor; its own comment defers the
fix here. Live sizing cannot do that -- a broker rejects the overdraft -- so this
module finds the largest whole-share count whose notional PLUS costs fits the
budget.

The cost model is the one the rule was admitted under, carried in the adoption
record. Sizing live under a different fee schedule would make the admitted
metrics describe a strategy nobody is running.
"""
from __future__ import annotations

import math
from typing import Mapping

WEIGHT_TOLERANCE = 1e-6


def affordable_shares(*, budget: float, price: float, cost_model) -> int:
    """Largest whole share count whose notional plus costs fits `budget`.

    Converges quickly rather than decrementing one share at a time: each pass
    subtracts the observed overshoot in whole shares. Costs are bounded above by
    the notional cap plus the spread, so the first estimate is already close.
    """
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
        overshoot = total - budget
        shares -= max(1, int(overshoot // price))
    return 0


def plan_orders(*, weights: Mapping[str, float], prices: Mapping[str, float],
                investable_cash: float, cash_floor_pct: float, cost_model) -> dict:
    """Turn target weights into whole-share orders that fit the cash available.

    `investable_cash` is cash, not sleeve market value -- see ADR-0007 clause 3
    and the `investable_cash_usd` config field. The floor is withheld before any
    symbol is sized, so the reserve cannot be eroded by rounding.

    Symbols are sized in a deterministic order (sorted), and each one draws from
    the remaining cash, so the total never exceeds the budget even when rounding
    frees up change that a later symbol can use.
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

    reserve = investable_cash * cash_floor_pct
    budget = investable_cash - reserve
    orders, unfunded, spent = [], [], 0.0
    for symbol in sorted(weights):
        price = prices[symbol]        # KeyError is correct: a missing price is a defect
        allocation = min(budget * weights[symbol], budget - spent)
        shares = affordable_shares(budget=allocation, price=price, cost_model=cost_model)
        notional = shares * price
        cost = cost_model.total(shares=shares, notional=notional) if shares else 0.0
        spent += notional + cost
        if shares == 0:
            unfunded.append(symbol)
        orders.append({
            "instrument_id": symbol,
            "target_weight": float(weights[symbol]),
            "quantity": shares,
            "limit_price": float(price),
            "notional": round(notional, 2),
            "estimated_cost": round(cost, 2),
        })
    return {
        "orders": orders,
        "unfunded": unfunded,
        "investable_cash": round(float(investable_cash), 2),
        "cash_reserved": round(reserve, 2),
        "cash_spent": round(spent, 2),
        "cash_remaining": round(investable_cash - spent, 2),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_ruleset.py -v
```

Expected: 11 new tests, OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/sizing.py scripts/_test_ruleset.py
git commit -m "feat(sizing): whole-share orders that fit the budget after costs"
```

---

### Task 5: `brake.py` — the one-way news brake

**Files:**
- Create: `scripts/copilot/brake.py`
- Modify: `scripts/_test_ruleset.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_ruleset.py` (add `from copilot import brake` to the imports):

```python
class NewsBrake(unittest.TestCase):
    def test_levels_are_exactly_the_three_the_user_approved(self):
        self.assertEqual(brake.LEVELS, ("none", "reduce_50", "skip"))

    def test_none_leaves_the_quantity_alone(self):
        self.assertEqual(brake.apply(level="none", quantity=17), 17)

    def test_reduce_50_halves_and_rounds_down(self):
        # Rounding down, never up: the brake may only reduce.
        self.assertEqual(brake.apply(level="reduce_50", quantity=17), 8)
        self.assertEqual(brake.apply(level="reduce_50", quantity=1), 0)

    def test_skip_is_zero(self):
        self.assertEqual(brake.apply(level="skip", quantity=17), 0)

    def test_the_brake_can_never_increase_a_quantity(self):
        for level in brake.LEVELS:
            for quantity in range(0, 101):
                self.assertLessEqual(brake.apply(level=level, quantity=quantity), quantity)

    def test_an_unknown_level_raises(self):
        with self.assertRaisesRegex(ValueError, "brake level"):
            brake.apply(level="increase", quantity=10)
        with self.assertRaisesRegex(ValueError, "brake level"):
            brake.apply(level="reduce_25", quantity=10)

    def test_a_non_none_level_requires_a_reason(self):
        with self.assertRaisesRegex(ValueError, "reason"):
            brake.record(level="skip", reason="", evidence_ids=["ev_1"])
        with self.assertRaisesRegex(ValueError, "reason"):
            brake.record(level="reduce_50", reason="   ", evidence_ids=["ev_1"])

    def test_none_needs_no_reason(self):
        record = brake.record(level="none", reason="", evidence_ids=[])
        self.assertEqual(record["level"], "none")
        self.assertEqual(record["reason"], "")

    def test_the_record_is_always_marked_unbacktested(self):
        # ADR-0005 clause 1 requires this wherever the brake appears. Finnhub's
        # company-news archive is one rolling year and returns HTTP 200 with an
        # empty array beyond it, so a naive replay would look green while
        # testing nothing.
        for level in brake.LEVELS:
            record = brake.record(level=level, reason="x" if level != "none" else "",
                                  evidence_ids=["ev_1"])
            self.assertFalse(record["backtested"])
            self.assertIn("未回测", record["disclosure"])

    def test_brake_evidence_stays_out_of_evidence_ids(self):
        # News evidence carries critical_evidence_eligible=False, so citing it
        # in proposal["evidence_ids"] triggers policy's
        # "evidence {eid} cannot support a critical recommendation claim".
        record = brake.record(level="skip", reason="issuer halt", evidence_ids=["ev_news_1"])
        self.assertEqual(record["evidence_ids"], ["ev_news_1"])
        self.assertNotIn("critical", record)

    def test_applied_reports_both_quantities(self):
        applied = brake.applied(record=brake.record(level="reduce_50", reason="r",
                                                    evidence_ids=["ev_1"]),
                                quantity=17)
        self.assertEqual(applied["pre_brake_quantity"], 17)
        self.assertEqual(applied["post_brake_quantity"], 8)
        self.assertEqual(applied["level"], "reduce_50")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_ruleset.py -v
```

Expected: `ImportError: cannot import name 'brake'`

- [ ] **Step 3: Write `brake.py`**

```python
"""The bounded news brake. One-way by construction: it can only ever reduce.

WHY IT IS SHAPED THIS WAY
The user's Q35 answer put macro surprise into the rules and news into a bounded
adjustment. ADR-0005 clause 1 then removed the macro leg, because Finnhub's
economic-calendar endpoint is premium and returns HTTP 403. What survives is
news alone, and news cannot be backtested here: the company-news archive is one
rolling year, and a request beyond it returns HTTP 200 with an empty array, so a
replay harness would report success while testing nothing.

The Q42 answer fixed the consequence: something with no evidence behind it gets
a veto, never an accelerator. `apply` is written so that no level can return
more than it was given, and a test asserts that over the whole level set.

The model selects the level and states a reason; the engine bounds it. That
division is ADR-0004 clause 3: the model may apply the brake, and nothing else
about the order.

BRAKE EVIDENCE IS NOT PROPOSAL EVIDENCE
News records carry `critical_evidence_eligible: False`
(research_data.py:97/351). Citing one in a proposal's `evidence_ids` makes
policy emit "evidence {eid} cannot support a critical recommendation claim" and
the whole decision becomes data_insufficient. Brake evidence therefore travels
in its own field and is never merged.
"""
from __future__ import annotations

from typing import Mapping, Sequence

#: Exactly the three the user approved. Widening this set is a design change,
#: not a refactor: every additional level is an unbacktested degree of freedom.
LEVELS = ("none", "reduce_50", "skip")

DISCLOSURE = "新闻刹车未回测：它只能减少或跳过，永远不能加仓"


def apply(*, level: str, quantity: int) -> int:
    """Return the post-brake quantity. Never greater than `quantity`."""
    if level not in LEVELS:
        raise ValueError(f"brake level must be one of {LEVELS}, got {level!r}")
    if level == "skip":
        return 0
    if level == "reduce_50":
        return int(quantity) // 2      # floor, so halving can only round down
    return int(quantity)


def record(*, level: str, reason: str, evidence_ids: Sequence[str]) -> dict:
    """Build the auditable brake record carried on every decision."""
    if level not in LEVELS:
        raise ValueError(f"brake level must be one of {LEVELS}, got {level!r}")
    text = str(reason or "").strip()
    if level != "none" and not text:
        raise ValueError("a brake that changes the order requires a stated reason")
    ids = [str(e) for e in (evidence_ids or [])]
    return {
        "level": level,
        "reason": text,
        "evidence_ids": ids,
        "backtested": False,
        "disclosure": DISCLOSURE,
    }


def applied(*, record: Mapping[str, object], quantity: int) -> dict:
    """Apply a brake record to a quantity, reporting both sides."""
    level = str(record.get("level", "none"))
    post = apply(level=level, quantity=quantity)
    return {
        "level": level,
        "reason": record.get("reason", ""),
        "evidence_ids": list(record.get("evidence_ids") or []),
        "backtested": False,
        "disclosure": DISCLOSURE,
        "pre_brake_quantity": int(quantity),
        "post_brake_quantity": int(post),
    }
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_ruleset.py -v
```

Expected: 12 new tests, OK.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/brake.py scripts/_test_ruleset.py
git commit -m "feat(brake): one-way news brake that can only reduce or skip"
```

---

### Task 6: Journal — adoptions, and bounded recommendations

**Files:**
- Modify: `scripts/copilot/journal.py` (`_SCHEMA`, new functions, `get_context`)
- Modify: `scripts/_test_journal.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_journal.py`, following the file's existing temporary-database fixture style (read it first — never touch `data/state/copilot.sqlite`):

```python
class Adoptions(unittest.TestCase):
    def adoption(self, **overrides):
        base = {
            "schema_version": 1,
            "rule_id": "rule-0123456789abcdef",
            "sleeve": "etf",
            "family": "momentum_top_n",
            "parameters": {"top_n": 5.0, "lookback_days": 252.0, "skip_days": 21.0},
            "universe": ["IWM", "QQQ", "SPY", "VTV", "VUG"],
            "targets": None,
            "cost_model": {"per_share_usd": 0.0035, "minimum_usd": 1.0,
                           "max_pct_of_notional": 0.01, "spread_bps": 2.0},
            "cash_floor_pct": 0.15,
            "integer_shares": True,
            "admission": {"admitted": True, "waived": False},
            "waived": False,
        }
        base.update(overrides)
        return base

    def test_record_and_load_round_trip(self):
        with self.database() as db:
            receipt = record_adoption(self.adoption(), db_path=db)
            self.assertEqual(receipt["rule_id"], "rule-0123456789abcdef")
            self.assertTrue(receipt["recorded"])
            loaded = load_adoption("rule-0123456789abcdef", db_path=db)
            self.assertEqual(loaded["family"], "momentum_top_n")

    def test_recording_the_same_adoption_twice_is_a_replay(self):
        with self.database() as db:
            record_adoption(self.adoption(), db_path=db)
            receipt = record_adoption(self.adoption(), db_path=db)
            self.assertTrue(receipt["replayed"])

    def test_the_same_id_with_different_content_conflicts(self):
        with self.database() as db:
            record_adoption(self.adoption(), db_path=db)
            with self.assertRaises(JournalConflict):
                record_adoption(self.adoption(family="fixed_weight_bands"), db_path=db)

    def test_an_adoption_cannot_be_updated_or_deleted(self):
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

    def test_rule_id_must_look_like_one(self):
        with self.database() as db:
            with self.assertRaisesRegex(ValueError, "rule_id"):
                record_adoption(self.adoption(rule_id="momentum"), db_path=db)

    def test_adding_the_table_is_safe_for_an_existing_database(self):
        # _SCHEMA re-runs on every connection open and is all CREATE IF NOT
        # EXISTS, so an older database gains the table without migration.
        with self.database() as db:
            record_operation(self.operation(), "key-1", db_path=db)
            record_adoption(self.adoption(), db_path=db)
            self.assertEqual(load_adoption("rule-0123456789abcdef", db_path=db)["sleeve"], "etf")


class BoundedRecommendations(unittest.TestCase):
    def test_context_bounds_the_recommendation_history(self):
        # A daily scan over 12 symbols writes 12 rows a day. get_context parsed
        # every row ever written on every call.
        with self.database() as db:
            self.store_recommendations(db, count=25)
            result = get_context(db_path=db, recommendations_limit=10)
            self.assertEqual(len(result["recommendations"]), 10)
            self.assertTrue(result["recommendations_truncated"])

    def test_an_unbounded_history_is_not_marked_truncated(self):
        with self.database() as db:
            self.store_recommendations(db, count=3)
            result = get_context(db_path=db, recommendations_limit=10)
            self.assertEqual(len(result["recommendations"]), 3)
            self.assertFalse(result["recommendations_truncated"])

    def test_the_newest_recommendations_survive_truncation(self):
        with self.database() as db:
            ids = self.store_recommendations(db, count=25)
            result = get_context(db_path=db, recommendations_limit=5)
            returned = {item["decision_id"] for item in result["recommendations"]}
            self.assertTrue(returned.issubset(set(ids[-5:]) | set(ids)))
            self.assertEqual(len(returned), 5)
```

Write the `store_recommendations` helper to create a snapshot and then insert `count` recommendations that differ only by `instrument_id`/`decision_id`, using the existing `save_snapshot` and `record_recommendation`. Read the file's existing helpers first and reuse them.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_journal.py -v
```

Expected: `ImportError: cannot import name 'record_adoption'`

- [ ] **Step 3: Add the table and the triggers**

In `scripts/copilot/journal.py`, inside `_SCHEMA`, after the `recommendations` table, add:

```sql
CREATE TABLE IF NOT EXISTS adopted_rules (
    rule_id TEXT PRIMARY KEY,
    sleeve TEXT NOT NULL,
    adopted_at TEXT NOT NULL,
    payload TEXT NOT NULL
);
```

and after the recommendation triggers:

```sql
CREATE TRIGGER IF NOT EXISTS adoptions_no_update BEFORE UPDATE ON adopted_rules
BEGIN SELECT RAISE(ABORT, 'adoptions are immutable; adopt a new rule instead'); END;
CREATE TRIGGER IF NOT EXISTS adoptions_no_delete BEFORE DELETE ON adopted_rules
BEGIN SELECT RAISE(ABORT, 'adoptions are immutable; adopt a new rule instead'); END;
```

- [ ] **Step 4: Add `record_adoption` and `load_adoption`**

Add after `record_recommendation` in `scripts/copilot/journal.py`:

```python
_RULE_ID = re.compile(r"^rule-[0-9a-f]{16}$")


def record_adoption(adoption: dict, *, db_path: Any = None) -> dict:
    """Store an immutable record of adopting a backtested rule.

    Adoption is a historical fact, so the row is immutable: superseding a rule
    means adopting a different one, which gets its own content-addressed id.
    Which rule is CURRENTLY live is not stored here -- that is the pointer the
    user sets by hand in config/user.toml, per ADR-0007 clause 5, so nothing but
    an explicit user action changes what the engine will trade.
    """
    if not isinstance(adoption, dict):
        raise ValueError("adoption must be a dict")
    rule_id = adoption.get("rule_id")
    if not isinstance(rule_id, str) or not _RULE_ID.match(rule_id):
        raise ValueError("rule_id must look like rule-<16 hex characters>")
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

Add `import re` to the module imports if it is not already there.

- [ ] **Step 5: Bound the recommendation history**

In `get_context`, change the signature to accept `recommendations_limit: int = 200` and replace the unbounded query. The existing code is:

```python
            query = "SELECT payload FROM recommendations"
            params = ()
            if as_of is not None:
                query += " WHERE recorded_at <= ?"
                params = (_stamp(as_of, end_of_day=True),)
            query += " ORDER BY recorded_at DESC, decision_id"
            decisions = [json.loads(row[0]) for row in connection.execute(query, params).fetchall()]
```

Replace with:

```python
            # Bounded: a daily scan over a 12-symbol universe writes 12 rows a
            # day, and this used to parse every row ever written on every call.
            # Newest first, so truncation drops the oldest.
            query = "SELECT payload FROM recommendations"
            params: tuple = ()
            if as_of is not None:
                query += " WHERE recorded_at <= ?"
                params = (_stamp(as_of, end_of_day=True),)
            query += " ORDER BY recorded_at DESC, decision_id LIMIT ?"
            limit = max(1, int(recommendations_limit))
            rows = connection.execute(query, (*params, limit + 1)).fetchall()
            truncated = len(rows) > limit
            decisions = [json.loads(row[0]) for row in rows[:limit]]
```

and add `"recommendations_truncated": truncated,` to the returned dict, next to `"recommendations": decisions`.

Check every caller of `get_context` before changing the signature and report what you found — `service.context` is one; there may be others.

- [ ] **Step 6: Run the tests to verify they pass**

```bash
python scripts/_test_journal.py -v && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: 10 new journal tests OK; the whole suite green.

- [ ] **Step 7: Commit**

```bash
git add scripts/copilot/journal.py scripts/_test_journal.py
git commit -m "feat(journal): immutable rule adoptions, bounded recommendation history"
```

---

### Task 7: `policy.evaluate_rule` — the engine entry point

This is the task the plan exists for. The engine computes; the existing gate still checks.

**Files:**
- Modify: `scripts/copilot/policy.py`
- Modify: `scripts/_test_policy.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_policy.py`:

```python
class EngineProducedOrders(unittest.TestCase):
    def adoption(self, **overrides):
        base = {
            "schema_version": 1, "rule_id": "rule-0123456789abcdef", "sleeve": "etf",
            "family": "fixed_weight_bands",
            "parameters": {"relative_band": 0.25, "absolute_band": 0.05, "calendar_days": 365.0},
            "universe": ["QQQ", "SPY"], "targets": {"QQQ": 0.5, "SPY": 0.5},
            "cost_model": {"per_share_usd": 0.0035, "minimum_usd": 1.0,
                           "max_pct_of_notional": 0.01, "spread_bps": 2.0},
            "cash_floor_pct": 0.15, "integer_shares": True,
            "admission": {"admitted": True, "waived": False}, "waived": False,
        }
        base.update(overrides)
        return base

    def test_the_model_cannot_supply_a_quantity(self):
        # ADR-0004 clause 2 stops being aspirational here.
        with self.assertRaisesRegex(ValueError, "engine"):
            assess_proposal(proposal(action="buy", quantity=10), fixture(), None)

    def test_the_model_cannot_supply_a_target_weight_or_stop(self):
        for key in ("target_weight", "stop_loss"):
            with self.assertRaisesRegex(ValueError, "engine"):
                assess_proposal(proposal(action="buy", **{key: 0.5}), fixture(), None)

    def test_the_engine_may_supply_them(self):
        decision = assess_proposal(proposal(action="buy", quantity=10), fixture(), None,
                                   source="engine")
        self.assertEqual(decision["requested_action"], "buy")

    def test_gold_price_is_still_accepted_from_the_model(self):
        # A merchant quote is verified against captured evidence, not invented.
        decision = assess_proposal(gold_proposal(), gold_fixture(), None)
        self.assertIn("price", decision)

    def test_evaluate_rule_returns_one_order_per_universe_symbol(self):
        result = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                               context=complete_context(), investable_cash=20000.0)
        self.assertEqual({o["instrument_id"] for o in result["orders"]}, {"QQQ", "SPY"})
        self.assertEqual(result["rule_id"], "rule-0123456789abcdef")
        self.assertRegex(result["evaluation_id"], r"^eval-[0-9a-f]{16}$")

    def test_every_order_carries_the_engine_tuple(self):
        result = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                               context=complete_context(), investable_cash=20000.0)
        for order in result["orders"]:
            for key in ("action", "quantity", "limit_price", "rule_id"):
                self.assertIn(key, order, key)

    def test_limit_price_is_the_raw_close_not_the_adjusted_series(self):
        # item["price"] is the primary's raw close; indicators use the
        # adjusted basis. Mixing the two would size against a different series
        # than the signal was computed on.
        snapshot = two_symbol_fixture()
        result = evaluate_rule(adoption=self.adoption(), snapshot=snapshot,
                               context=complete_context(), investable_cash=20000.0)
        for order in result["orders"]:
            self.assertEqual(order["limit_price"],
                             snapshot["instruments"][order["instrument_id"]]["price"])
            self.assertEqual(order["limit_price_basis"], "raw_close")

    def test_one_blocked_symbol_pauses_the_whole_evaluation(self):
        # ADR-0007 clause 4: weights are computed across the universe, so one
        # unreliable symbol makes every weight unreliable.
        snapshot = two_symbol_fixture()
        snapshot["instruments"]["SPY"]["quality_status"] = "fail"
        result = evaluate_rule(adoption=self.adoption(), snapshot=snapshot,
                               context=complete_context(), investable_cash=20000.0)
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertIn("SPY", result["blocked_symbols"])
        for order in result["orders"]:
            self.assertNotIn("quantity", order)

    def test_a_universe_symbol_missing_from_the_snapshot_pauses_it_too(self):
        snapshot = two_symbol_fixture()
        del snapshot["instruments"]["SPY"]
        result = evaluate_rule(adoption=self.adoption(), snapshot=snapshot,
                               context=complete_context(), investable_cash=20000.0)
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertIn("SPY", result["blocked_symbols"])

    def test_engine_reasons_survive_the_numeric_prose_gate(self):
        # Engine-authored reasons must be number-free apart from the tokenised
        # rule id, or policy blocks its own output.
        result = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                               context=complete_context(), investable_cash=20000.0)
        for order in result["orders"]:
            self.assertNotEqual(order["action"], "data_insufficient",
                                f"engine blocked its own reasons: {order['reasons']}")

    def test_the_brake_reduces_and_records_both_quantities(self):
        plain = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                              context=complete_context(), investable_cash=20000.0)
        braked = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                               context=complete_context(), investable_cash=20000.0,
                               brake={"level": "reduce_50", "reason": "issuer halt",
                                      "evidence_ids": ["ev_news_1"]})
        for before, after in zip(plain["orders"], braked["orders"]):
            self.assertEqual(after["brake"]["pre_brake_quantity"], before["quantity"])
            self.assertLessEqual(after["quantity"], before["quantity"])
            self.assertFalse(after["brake"]["backtested"])

    def test_brake_evidence_never_enters_evidence_ids(self):
        result = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                               context=complete_context(), investable_cash=20000.0,
                               brake={"level": "skip", "reason": "halt",
                                      "evidence_ids": ["ev_news_1"]})
        for order in result["orders"]:
            self.assertNotIn("ev_news_1", order["evidence_ids"])
            self.assertIn("ev_news_1", order["brake"]["evidence_ids"])

    def test_a_brake_cannot_raise_the_quantity(self):
        with self.assertRaises(ValueError):
            evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                          context=complete_context(), investable_cash=20000.0,
                          brake={"level": "double", "reason": "x", "evidence_ids": []})

    def test_unknown_coverage_withholds_quantities(self):
        # journal.get_context reports portfolio_complete False in production, so
        # this is the normal path, not an edge case.
        result = evaluate_rule(adoption=self.adoption(), snapshot=two_symbol_fixture(),
                               context={"portfolio_complete": False}, investable_cash=20000.0)
        self.assertEqual(result["execution_scope"], "research_only")
        for order in result["orders"]:
            self.assertNotIn("quantity", order)
```

You must add the fixtures `two_symbol_fixture`, `complete_context`, `gold_proposal` and `gold_fixture` if they do not already exist, and extend `proposal` to accept arbitrary numeric keys. Read `scripts/_test_policy.py` first and follow its conventions. `two_symbol_fixture` must produce a valid snapshot (one that passes `market_data.verify_snapshot`) carrying QQQ and SPY with `quality_status: "pass"`, a raw `price`, and a `latest_session` matching the expected session — copy the mechanism the existing `fixture()` uses rather than inventing one.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_policy.py -v
```

Expected: `NameError: name 'evaluate_rule' is not defined`

- [ ] **Step 3: Add the `source` discipline to `assess_proposal`**

Change the signature at `policy.py:164`:

```python
def assess_proposal(proposal: dict, snapshot: dict, context: dict | None = None, *, now=None) -> dict:
```

to:

```python
def assess_proposal(proposal: dict, snapshot: dict, context: dict | None = None, *,
                    now=None, source: str = "model") -> dict:
```

and replace the numeric-key validation at `policy.py:190-193`:

```python
    for key in ("price", "quantity", "target_weight", "stop_loss"):
        if key in proposal and not _number(proposal[key], positive=True):
            raise ValueError(f"{key} must be a finite positive number")
```

with:

```python
    if source not in ("model", "engine"):
        raise ValueError(f"source must be 'model' or 'engine', got {source!r}")
    # ADR-0004 clause 2: the model has no interface through which it can alter
    # quantity, direction or rule identity. `price` stays open because the gold
    # path verifies it against a captured merchant quote rather than trusting
    # it; the other three had no such check and were echoed straight through.
    if source == "model":
        for key in ("quantity", "target_weight", "stop_loss"):
            if key in proposal:
                raise ValueError(f"{key} is computed by the engine and must not be "
                                 f"supplied by a proposal; call evaluate_rule instead")
    for key in ("price", "quantity", "target_weight", "stop_loss"):
        if key in proposal and not _number(proposal[key], positive=True):
            raise ValueError(f"{key} must be a finite positive number")
```

- [ ] **Step 4: Write `evaluate_rule`**

Add at the end of `scripts/copilot/policy.py`:

```python
def evaluate_rule(*, adoption: dict, snapshot: dict, context: dict | None = None,
                  investable_cash: float, brake: dict | None = None, now=None) -> dict:
    """Compute orders from an adopted rule. The only producer of live numbers.

    The engine does not bypass the evidence gate -- it feeds it. Weights and
    share counts are computed here, then one internal proposal per symbol is run
    through assess_proposal with source="engine", so every blocker, claim check
    and risk limit applies unchanged.

    ADR-0007 clause 4: a single blocked symbol pauses the whole evaluation.
    Rebalance weights are computed across the universe, so one unreliable
    symbol makes every weight unreliable; returning a partial basket would be
    inventing a policy nobody approved.
    """
    from . import brake as brake_module
    from . import sizing
    from .backtest.engine import CostModel

    if not isinstance(adoption, dict) or not adoption.get("rule_id"):
        raise ValueError("adoption must be a stored adoption record with a rule_id")
    context = context or {}
    rule_id = str(adoption["rule_id"])
    universe = [str(s).upper() for s in adoption.get("universe") or []]
    if not universe:
        raise ValueError("adoption has an empty universe")
    brake_record = brake_module.record(
        level=str((brake or {}).get("level", "none")),
        reason=str((brake or {}).get("reason", "")),
        evidence_ids=list((brake or {}).get("evidence_ids") or []))

    instruments = snapshot.get("instruments", {})
    blocked = [s for s in universe
               if s not in instruments or instruments[s].get("quality_status") != "pass"]
    coverage_known = context.get("portfolio_complete") is True
    withhold = bool(blocked) or not coverage_known

    prices = {s: instruments[s].get("price") for s in universe if s in instruments}
    weights = _rule_weights(adoption, snapshot)
    current_weights = _current_weights(context, prices)
    plan = (None if withhold else sizing.plan_orders(
        weights=weights, prices=prices, investable_cash=float(investable_cash),
        cash_floor_pct=float(adoption.get("cash_floor_pct", 0.0)),
        cost_model=CostModel(**adoption["cost_model"])))
    sized = {o["instrument_id"]: o for o in (plan["orders"] if plan else [])}

    orders = []
    for symbol in universe:
        # Engine-authored prose carries no bare numbers: policy's own numeric
        # provenance gate would otherwise block the engine's output. The rule
        # id is tokenised by _IDENTITY_OR_DATE.
        reasons = [f"adopted rule {rule_id} produced this order"]
        if blocked:
            reasons = [f"adopted rule {rule_id} paused: "
                       f"{', '.join(blocked)} has no validated market data"]
        elif not coverage_known:
            reasons = [f"adopted rule {rule_id} produced a research view only; "
                       "portfolio coverage is unknown"]
        item = {"instrument_id": symbol,
                "action": "hold" if withhold else _order_action(
                    target=weights.get(symbol, 0.0), current=current_weights.get(symbol, 0.0)),
                "mode": "accumulation", "horizon": "long_term",
                "reasons": reasons, "conditions": [],
                "evidence_ids": _market_evidence_ids(snapshot, symbol)}
        order = sized.get(symbol)
        if order and order["quantity"] > 0:
            item["quantity"] = brake_module.apply(level=brake_record["level"],
                                                  quantity=order["quantity"])
            item["price"] = order["limit_price"]
        decision = (assess_proposal(item, snapshot, context, now=now, source="engine")
                    if symbol in instruments else _absent_decision(snapshot, symbol, reasons))
        decision["rule_id"] = rule_id
        decision["limit_price_basis"] = "raw_close"
        if order:
            decision["limit_price"] = order["limit_price"]
            decision["target_weight_engine"] = order["target_weight"]
            decision["estimated_cost"] = order["estimated_cost"]
            decision["brake"] = brake_module.applied(record=brake_record,
                                                     quantity=order["quantity"])
        else:
            decision["brake"] = dict(brake_record)
        orders.append(decision)

    result = {
        "schema_version": 1, "rule_id": rule_id, "sleeve": adoption.get("sleeve"),
        "snapshot_id": snapshot.get("snapshot_id"), "orders": orders,
        "blocked_symbols": blocked, "coverage_known": coverage_known,
        "execution_scope": "research_only" if withhold else "actionable",
        "brake": brake_record, "cash_plan": plan,
        "waived": bool(adoption.get("waived")),
    }
    result["evaluation_id"] = "eval-" + _digest(result)[:16]
    return result
```

And the six helpers it calls, in the same module:

```python
#: A drift smaller than this is not worth a commission. Deliberately NOT a rule
#: parameter: the rule decides target weights, the engine decides whether the
#: gap is worth trading, and folding it in would consume one of the three slots
#: the admission gate allows.
_ORDER_TOLERANCE = 0.005


def _order_action(*, target: float, current: float) -> str:
    """Direction implied by the gap between where the book is and where the rule wants it."""
    if abs(target - current) <= _ORDER_TOLERANCE:
        return "hold"
    return "buy" if target > current else "reduce"


def _current_weights(context: dict, prices: dict) -> dict:
    """Current sleeve weights from recorded holdings, priced at the snapshot.

    Empty when coverage is unknown, which is the production path today -- see
    journal.get_context's hardcoded portfolio_complete. An empty mapping makes
    every target look like a fresh purchase, which is exactly why quantities are
    withheld in that case rather than acted on.
    """
    if context.get("portfolio_complete") is not True:
        return {}
    values, total = {}, 0.0
    for holding in context.get("holdings") or []:
        symbol = str(holding.get("instrument_id", "")).upper()
        price = prices.get(symbol)
        quantity = holding.get("quantity")
        if price is None or isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
            continue
        value = float(quantity) * float(price)
        values[symbol] = values.get(symbol, 0.0) + value
        total += value
    if total <= 0:
        return {}
    return {symbol: value / total for symbol, value in values.items()}


def _rule_weights(adoption: dict, snapshot: dict) -> dict:
    """Target weights from the adopted rule, evaluated at the snapshot's last bar.

    Rebuilt from the stored adoption rather than from anything the model sent,
    and evaluated through the same rule classes the backtest used, so the live
    signal and the admitted signal are the same code.
    """
    from .backtest import rules as rule_families
    from .backtest.frame import build as build_frame

    family = adoption["family"]
    universe = tuple(adoption["universe"])
    parameters = {k: float(v) for k, v in (adoption.get("parameters") or {}).items()}
    if family == "fixed_weight_bands":
        targets = adoption.get("targets")
        if not targets:
            raise ValueError("fixed_weight_bands requires stored targets")
        rule = rule_families.FixedWeightBands(
            {str(k): float(v) for k, v in targets.items()},
            relative_band=parameters.get("relative_band", 0.25),
            absolute_band=parameters.get("absolute_band", 0.05),
            calendar_days=int(parameters.get("calendar_days", 365)))
    elif family == "inverse_volatility":
        rule = rule_families.InverseVolatility(
            universe,
            lookback_days=int(parameters.get("lookback_days", 63)),
            rebalance_days=int(parameters.get("rebalance_days", 21)))
    elif family == "momentum_top_n":
        rule = rule_families.MomentumTopN(
            universe,
            top_n=int(parameters.get("top_n", 5)),
            lookback_days=int(parameters.get("lookback_days", 252)),
            skip_days=int(parameters.get("skip_days", 21)))
    else:
        raise ValueError(f"unknown rule family {family!r}")

    dates, closes = _bars_matrix(snapshot, universe)
    if len(dates) <= rule.warmup_bars:
        raise ValueError(
            f"{family} needs more than {rule.warmup_bars} bars to produce a signal; "
            f"this snapshot carries {len(dates)}. Collect a longer history before "
            "evaluating this rule.")
    frame = build_frame(dates=dates, symbols=list(universe), closes=closes)
    return rule.weights(frame, len(dates) - 1)


def _bars_matrix(snapshot: dict, universe: tuple) -> tuple[list, list]:
    """Align the snapshot's stored bars onto the dates every symbol shares.

    Intersection, not union: a weight computed from a forward-filled price is a
    weight nobody could have traded on. Uses the ADJUSTED close, because that is
    the basis the rule families were backtested on. The raw close is used only
    for limit_price, and the two are deliberately never mixed.
    """
    from datetime import date as _date

    per_symbol = {}
    for symbol in universe:
        item = snapshot.get("instruments", {}).get(symbol) or {}
        rows = item.get("bars")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"{symbol}: this snapshot carries no bars. Pass the stored "
                             "snapshot, not the summary view that strips them.")
        series = {}
        for row in rows:
            stamp = row.get("date") or row.get("session")
            close = row.get("adjusted_close", row.get("close"))
            if stamp is None or isinstance(close, bool) or not isinstance(close, (int, float)):
                continue
            series[_date.fromisoformat(str(stamp)[:10])] = float(close)
        if not series:
            raise ValueError(f"{symbol}: no usable bar had both a date and a close")
        per_symbol[symbol] = series

    common = set(per_symbol[universe[0]])
    for symbol in universe[1:]:
        common &= set(per_symbol[symbol])
    if not common:
        raise ValueError("the adopted universe shares no common session in this snapshot")
    dates = sorted(common)
    closes = [[per_symbol[symbol][day] for symbol in universe] for day in dates]
    return dates, closes


def _market_evidence_ids(snapshot: dict, symbol: str) -> list[str]:
    """Market evidence for one symbol. News can never appear here.

    A record with critical_evidence_eligible False triggers policy's
    "evidence {eid} cannot support a critical recommendation claim" blocker, so
    filtering it out at the source is what lets the brake cite news at all.
    """
    ids = []
    for record in snapshot.get("evidence", []):
        if record.get("critical_evidence_eligible") is False:
            continue
        owners = record.get("instrument_ids") or [record.get("instrument_id")]
        if symbol in owners:
            eid = record.get("evidence_id")
            if isinstance(eid, str) and eid not in ids:
                ids.append(eid)
    return ids


def _absent_decision(snapshot: dict, symbol: str, reasons: list) -> dict:
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
        "portfolio_version": "", "mode": "accumulation", "horizon": "long_term",
        "warnings": [f"{symbol} is in the adopted universe but not in this snapshot"],
        "execution_scope": "research_only",
    }
```

Two things to check while implementing these rather than assume:

- **Bar field names.** `_bars_matrix` reads `date`/`session` and `adjusted_close`/`close`. Confirm against a real stored snapshot what the bar rows are actually keyed on, and fix the reader to match — do not leave a fallback chain guessing at a shape you can observe.
- **`_absent_decision`'s `portfolio_version`.** It is `""`, which `record_recommendation` rejects (`journal.py:507-509` requires a non-empty string). Either populate it from the context or have `service.evaluate` skip persisting absent-symbol rows; decide which, and say which you chose and why.

- [ ] **Step 5: Run the test to verify it passes**

```bash
python scripts/_test_policy.py -v && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: 15 new policy tests OK; the full suite green. Existing tests that passed `quantity` from a model proposal will now raise — **fix those fixtures to pass `source="engine"` where they were exercising the engine path, and delete the key where they were exercising the model path. Report each one you changed and which way you decided.**

- [ ] **Step 6: Commit**

```bash
git add scripts/copilot/policy.py scripts/_test_policy.py
git commit -m "feat(policy): evaluate_rule produces orders; the model may no longer supply numbers"
```

---

### Task 8: Config rename and the adoption pointer

**Files:**
- Modify: `scripts/copilot/config.py`, `config/user.example.toml`
- Modify: `scripts/_test_config.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_config.py`, following its existing fixture style:

```python
class CashSemantics(unittest.TestCase):
    def test_the_field_is_named_for_cash(self):
        # ADR-0007 clause 3: the number is investable CASH, matching
        # engine.run(start_cash=...), not sleeve market value.
        config = load_config(self.write_config(investable_cash_usd=5000.0))
        self.assertAlmostEqual(config.etf.investable_cash_usd, 5000.0)

    def test_the_old_name_is_rejected_with_a_pointer_to_the_new_one(self):
        with self.assertRaisesRegex(ValueError, "investable_cash_usd"):
            load_config(self.write_config_raw(
                "[etf]\ninvestable_total_usd = 5000.0\n"))


class AdoptionPointer(unittest.TestCase):
    def test_a_well_formed_rule_id_is_accepted(self):
        config = load_config(self.write_config(adopted_rule_id="rule-0123456789abcdef"))
        self.assertEqual(config.etf.adopted_rule_id, "rule-0123456789abcdef")

    def test_empty_means_nothing_is_adopted(self):
        config = load_config(self.write_config(adopted_rule_id=""))
        self.assertEqual(config.etf.adopted_rule_id, "")

    def test_a_malformed_pointer_is_refused(self):
        for bad in ("momentum", "rule-XYZ", "rule-0123", "rule-0123456789ABCDEF"):
            with self.assertRaisesRegex(ValueError, "adopted_rule_id"):
                load_config(self.write_config(adopted_rule_id=bad))
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_config.py -v
```

Expected: `AttributeError: 'EtfConfig' object has no attribute 'investable_cash_usd'`

- [ ] **Step 3: Rename the field and validate the pointer**

In `scripts/copilot/config.py`, change `EtfConfig`:

```python
@dataclass(frozen=True)
class EtfConfig:
    universe: tuple[str, ...] = ()
    investable_total_usd: float = 0.0
    min_cash_reserve_pct: float = 0.15
    max_drawdown_pct: float = 0.20
    adopted_rule_id: str = ""
```

to:

```python
@dataclass(frozen=True)
class EtfConfig:
    universe: tuple[str, ...] = ()
    #: CASH available to deploy into this sleeve, not the sleeve's market value.
    #: Matches backtest engine.run(start_cash=...). See ADR-0007 clause 3.
    investable_cash_usd: float = 0.0
    min_cash_reserve_pct: float = 0.15
    max_drawdown_pct: float = 0.20
    #: A pointer to a journal adoption, set by hand. Nothing writes this file:
    #: config.py is read-only by construction and the offline CI matrix forbids
    #: a TOML writer. Keeping the pointer manual also keeps "only an explicit
    #: user action changes what the engine will trade" (ADR-0007 clause 5).
    adopted_rule_id: str = ""
```

Add near the other module constants:

```python
_RULE_ID_RE = re.compile(r"^rule-[0-9a-f]{16}$")
```

and in `_etf`, replace the `investable_total_usd` line and add pointer validation:

```python
    if "investable_total_usd" in section:
        raise ValueError("investable_total_usd was renamed investable_cash_usd; the value "
                         "is investable CASH, not sleeve market value (ADR-0007 clause 3)")
    adopted = _string(section, "adopted_rule_id", "etf")
    if adopted and not _RULE_ID_RE.match(adopted):
        raise ValueError(f"etf.adopted_rule_id must be empty or look like "
                         f"rule-<16 hex characters>, got {adopted!r}")
    return EtfConfig(
        universe=symbols,
        investable_cash_usd=_number(section, "investable_cash_usd", "etf", low=0, high=1e9),
        min_cash_reserve_pct=_number(section, "min_cash_reserve_pct", "etf", low=0, high=0.9),
        max_drawdown_pct=_number(section, "max_drawdown_pct", "etf", low=0.01, high=0.9),
        adopted_rule_id=adopted,
    )
```

- [ ] **Step 4: Update the example config**

In `config/user.example.toml`, replace:

```toml
# Replaced by the IBKR Flex sync in a later task; until then set it by hand.
investable_total_usd = 0
```

with:

```toml
# CASH you can deploy into this sleeve, not the sleeve's market value. This is
# the same quantity the backtest engine calls start_cash. Set it by hand; the
# IBKR Flex sync that would maintain it is not built.
investable_cash_usd = 0
```

and replace:

```toml
# Set when you adopt a backtested rule set. Empty means nothing is adopted.
adopted_rule_id = ""
```

with:

```toml
# Points at a rule recorded by `copilot_cli.py adopt`, which prints the id.
# Nothing writes this file: keeping the pointer manual is what makes adoption
# an explicit act of yours rather than something a scan can do. Empty means
# nothing is adopted and the engine will not produce orders.
adopted_rule_id = ""
```

- [ ] **Step 5: Run the tests**

```bash
python scripts/_test_config.py -v && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: 6 new config tests OK; the suite green. Grep for `investable_total_usd` across the repository and fix every remaining reference — report what you found.

- [ ] **Step 6: Commit**

```bash
git add scripts/copilot/config.py config/user.example.toml scripts/_test_config.py
git commit -m "feat(config): investable_cash_usd names what it holds; validate the adoption pointer"
```

---

### Task 9: Facades — adopt and evaluate

**Files:**
- Modify: `scripts/copilot/service.py`, `mcps/copilot_mcp.py`, `scripts/copilot_cli.py`
- Modify: `scripts/sync_runtimes.py`, `.claude/settings.json`, `scripts/copilot_probe.py`, `.claude/skills/investment-chat/SKILL.md`
- Modify: `scripts/_test_copilot_service.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_copilot_service.py`:

```python
class EngineFacade(unittest.TestCase):
    def test_evaluate_refuses_when_nothing_is_adopted(self):
        with self.assertRaisesRegex(ValueError, "adopted_rule_id"):
            service.evaluate(snapshot_id="snap_x", config_path=self.empty_config(),
                             db_path=self.db)

    def test_evaluate_refuses_a_pointer_that_does_not_resolve(self):
        with self.assertRaises(KeyError):
            service.evaluate(snapshot_id="snap_x",
                             config_path=self.config(adopted_rule_id="rule-ffffffffffffffff"),
                             db_path=self.db)

    def test_evaluate_persists_one_recommendation_per_order(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(adopted_rule_id=self.rule_id),
                                  db_path=self.db)
        stored = service.context(db_path=self.db)["recommendations"]
        self.assertEqual(len([r for r in stored
                              if r.get("evaluation_id") == result["evaluation_id"]]),
                         len(result["orders"]))

    def test_every_persisted_order_carries_the_evaluation_id(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(adopted_rule_id=self.rule_id),
                                  db_path=self.db)
        for order in result["orders"]:
            self.assertEqual(order["evaluation_id"], result["evaluation_id"])

    def test_the_rendered_message_marks_an_applied_brake_unbacktested(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(adopted_rule_id=self.rule_id),
                                  db_path=self.db,
                                  brake={"level": "reduce_50", "reason": "halt",
                                         "evidence_ids": []})
        self.assertIn("未回测", result["message"])

    def test_no_brake_does_not_add_the_disclosure(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(adopted_rule_id=self.rule_id),
                                  db_path=self.db)
        self.assertNotIn("未回测", result["message"])

    def test_adopt_records_and_returns_the_rule_id(self):
        receipt = service.adopt(self.adoption_payload(), db_path=self.db)
        self.assertRegex(receipt["rule_id"], r"^rule-[0-9a-f]{16}$")
        self.assertIn("config/user.toml", receipt["next_step"])
```

Write the fixtures (`self.db`, `self.snapshot_id`, `self.rule_id`, `self.config`, `self.empty_config`, `self.adoption_payload`) following the file's existing temporary-database conventions.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_copilot_service.py -v
```

Expected: `AttributeError: module 'copilot.service' has no attribute 'evaluate'`

- [ ] **Step 3: Add `adopt` and `evaluate` to `service.py`**

```python
def adopt(adoption_inputs: dict, *, db_path=None) -> dict:
    """Record an adoption and tell the user how to make it live.

    Recording is not activation. The engine trades whatever
    config/user.toml's etf.adopted_rule_id points at, and only the user edits
    that file -- ADR-0007 clause 5.
    """
    from .journal import record_adoption
    from .ruleset import build_adoption
    record = build_adoption(**adoption_inputs)
    receipt = record_adoption(record, db_path=database_path(db_path))
    receipt["adoption"] = record
    receipt["next_step"] = (f"set etf.adopted_rule_id = \"{record['rule_id']}\" in "
                            "config/user.toml to make this rule live")
    return receipt


def evaluate(*, snapshot_id: str, config_path=None, db_path=None,
             brake: dict | None = None, now=None) -> dict:
    """Run the adopted rule against a stored snapshot and persist every order."""
    from .config import load_config
    from .journal import load_adoption, record_recommendation
    from .policy import evaluate_rule
    settings = load_config(config_path)
    pointer = settings.etf.adopted_rule_id
    if not pointer:
        raise ValueError("no rule is adopted; set etf.adopted_rule_id in config/user.toml "
                         "to a rule id printed by `copilot_cli.py adopt`")
    adoption = load_adoption(pointer, db_path=database_path(db_path))
    stored = snapshot(snapshot_id, db_path=db_path)
    current = context(db_path=db_path)
    result = evaluate_rule(adoption=adoption, snapshot=stored, context=current,
                           investable_cash=settings.etf.investable_cash_usd,
                           brake=brake, now=now)
    for order in result["orders"]:
        order["evaluation_id"] = result["evaluation_id"]
        order["decision_id"] = "decision-" + hashlib.sha256(
            strict_json(order).encode()).hexdigest()
        strict_json(order)
        record_recommendation(order, db_path=database_path(db_path))
    result["message"] = render_evaluation(result, stored)
    return result


def render_evaluation(result: dict, stored: dict) -> str:
    """Render the engine's orders. Never invent a number the engine did not produce."""
    lines = []
    if result.get("execution_scope") != "actionable":
        blocked = result.get("blocked_symbols") or []
        reason = ("数据未通过校验：" + "、".join(blocked)) if blocked else "持仓覆盖未知"
        lines.append(f"研究观点，未给出具体仓位（{reason}）")
    else:
        lines.append("按已采纳规则计算的委托：")
        for order in result["orders"]:
            if not order.get("quantity"):
                continue
            lines.append(f"  {order['instrument_id']} {order['action']} "
                         f"{order['quantity']} 股，限价 {order['limit_price']}")
    unfunded = (result.get("cash_plan") or {}).get("unfunded") or []
    if unfunded:
        lines.append("资金不足未下单：" + "、".join(unfunded))
    if (result.get("brake") or {}).get("level", "none") != "none":
        lines.append(result["brake"]["disclosure"])
    if result.get("waived"):
        lines.append("该规则的准入门槛带有书面豁免，见 ADR-0006")
    return "\n".join(lines)
```

Note `evaluate` uses `hashlib`, already imported at `service.py:4`.

- [ ] **Step 4: Wire the MCP tool**

Add to `mcps/copilot_mcp.py`, after `assess_investment_proposal`:

```python
@mcp.tool()
def evaluate_adopted_rule(snapshot_id: str, brake_level: str = "none",
                          brake_reason: str = "", brake_evidence_ids: list[str] | None = None) -> dict:
    """Run the adopted rule against a stored snapshot; the engine computes every number.

    You cannot supply a quantity, price or rule id — those come from the engine.
    The only thing you choose is the brake: "none", "reduce_50" or "skip", which
    can reduce or cancel an order and never enlarge one. A non-"none" level
    requires a reason and is reported as not backtested. Return the engine's
    orders, not your own figures.
    """
    return service.evaluate(snapshot_id=snapshot_id,
                            brake={"level": brake_level, "reason": brake_reason,
                                   "evidence_ids": brake_evidence_ids or []})
```

- [ ] **Step 5: Wire the CLI**

In `scripts/copilot_cli.py`, add two subparsers after the `review` one:

```python
    adopt = sub.add_parser("adopt", help="record a backtested rule and print its id")
    adopt.add_argument("--input", default="-", help="adoption JSON file or stdin")
    evaluate = sub.add_parser("evaluate", help="run the adopted rule against a snapshot")
    evaluate.add_argument("snapshot_id")
    evaluate.add_argument("--brake-level", choices=("none", "reduce_50", "skip"), default="none")
    evaluate.add_argument("--brake-reason", default="")
```

and two dispatch branches:

```python
        elif args.command == "adopt":
            result = service.adopt(read_object(args.input), db_path=args.db)
        elif args.command == "evaluate":
            result = service.evaluate(snapshot_id=args.snapshot_id, db_path=args.db,
                                      brake={"level": args.brake_level,
                                             "reason": args.brake_reason,
                                             "evidence_ids": []})
```

- [ ] **Step 6: Complete the four-place sync**

There is no automatic cross-check for these; each is a separate edit.

1. `scripts/sync_runtimes.py` — add `evaluate_adopted_rule` to the hardcoded tools list (around lines 31-32).
2. `.claude/settings.json` — add the tool to the allow array, matching the existing entries' format.
3. `scripts/copilot_probe.py` — add it to the required tool set.
4. `.claude/skills/investment-chat/SKILL.md` — document it, and state plainly that the model chooses only the brake level and reason, never a quantity, price or rule. This file is canonical; the mirrors under `.agents/skills/`, `skills/` and `.codex/config.toml` are generated.

Then:

```bash
python scripts/sync_runtimes.py && python scripts/sync_runtimes.py --check
```

Expected: the generator rewrites the mirrors, then reports `0 changed`.

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

Expected: `check.py` 0 errors; `sync_runtimes --check` 0 changed; the suite green; `package_release --self-test` all pass; a clean build with a passing audit; ruff clean; both MCP servers complete a handshake with the new tool present.

- [ ] **Step 8: End-to-end proof**

This is the acceptance test for the whole plan. Run it and paste the real output.

```bash
python scripts/copilot_cli.py snapshot SPY QQQ --horizon daily
```

Take the returned `snapshot_id`, adopt a rule over those two symbols with a real admitted backtest result, point `config/user.toml` at it, then:

```bash
python scripts/copilot_cli.py evaluate <snapshot_id>
```

Report the orders, the `execution_scope`, and whether quantities were produced or withheld. **A `research_only` result is a legitimate outcome** — `journal.get_context` reports `portfolio_complete: False` in production, so quantities are withheld until portfolio coverage is solved, which is not this plan's job. Say plainly which it was.

- [ ] **Step 9: Commit**

```bash
git add scripts/copilot/service.py mcps/copilot_mcp.py scripts/copilot_cli.py scripts/sync_runtimes.py .claude/settings.json scripts/copilot_probe.py .claude/skills/investment-chat/SKILL.md .agents skills .codex scripts/_test_copilot_service.py
git commit -m "feat(facade): adopt and evaluate an adopted rule from the CLI and MCP"
```

---

## What this plan deliberately does not build

- **Portfolio coverage.** `journal.get_context` reports `portfolio_complete: False` as a hardcoded literal (`journal.py:476`), so `policy`'s `complete` gate is always false and exact quantities are withheld in production. Solving that means reconciling holdings against a broker — the IBKR Flex sync, which has no token. Until then `evaluate` legitimately returns `research_only`, and that is visible rather than papered over.
- **The IBKR Flex client.** Deferred by the user pending a real token.
- **Notifications and scheduling.** Plan 5. The 30-minute cap on a research-bearing snapshot (`service.py:89-90`) is the binding constraint on a daily scan and belongs to that plan.
- **The gold sleeve.** Plan 6. Note the brake cannot serve it: `research_data.py:360-362` fetches news only for `stock` and `etf`, so `GOLD.CNY` has no brake input at all.
- **The README strategy table (Q22=C).** Plan 7, and it needs Plan 3's numbers plus this plan's order shape.
- **Look-through concentration.** ADR-0007 clause 2 accepts a 25% per-ETF limit while recording that it bounds name concentration, not exposure concentration. Computing real overlap needs holdings data for each fund, which no configured source provides.
- **A TOML writer.** ADR-0007 clause 5 makes the adoption pointer a deliberate manual step.
- **Q13's three comfort parameters.** Still deferred by the user to after launch: `max_drawdown_pct`, `min_cash_reserve_pct` and the single-month cap. Note `max_drawdown_pct` (0.20) and `policy._LIMITS["drawdown"]` (0.15) are two unreconciled numbers in two places; whichever way they are reconciled is a user decision, not a refactor.

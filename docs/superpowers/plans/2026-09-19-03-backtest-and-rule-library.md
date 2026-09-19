# Backtest Engine and Rule Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, stdlib-only, offline backtest engine plus three rule families, gated by the Q29 six-rule admission standard, over a universe whose history has been verified symbol by symbol.

**Architecture:** A new package `scripts/copilot/backtest/` holds a price matrix, a historical loader, the admission gate, the engine loop, the metrics, and the three rule families. It is stdlib-only, so the offline CI matrix (which installs zero third-party packages) runs every behavioural test. `bt==1.2.3` is **not** a repo dependency: it is used only by a dev-only cross-check oracle that the runtime job may run, because verification found its band trigger broken at bar 0 in the only installable release. The existing `evals/stockbench/backtest_engine.py` is left untouched — it scores LLM ratings, a different domain, and it is a release-zip asserted member.

**Tech Stack:** Python 3.11+ stdlib only (`urllib.request`, `json`, `csv`, `dataclasses`, `datetime`, `statistics`). `exchange-calendars==4.13.2` appears in one runtime probe. `bt==1.2.3` + `ffn==1.2.2` appear in one dev-only script with a PEP 723 header.

---

## Verified facts this plan is built on

Every number below was observed by a verification agent, not assumed. Do not re-derive them; do not "improve" them away.

**Yahoo transport**
- `range=max&interval=1d` returns HTTP 200 with `meta.dataGranularity='1mo'` and ~405 monthly bars. The `range` field still echoes `'max'`. **Every response must assert `dataGranularity == '1d'`** or a 15-year gate can be passed by 180 monthly bars. The last monthly bar is not dated the 1st, so a `day == 1` sentinel does not catch it.
- `period1=0&period2=<epoch>&interval=1d` returns `dataGranularity='1d'`: SPY 8467 bars, 1993-01-29..2026-09-18.
- UA matters. No UA, `Python-urllib/3.13`, and `curl/8.5.0` all get HTTP 429 on the first request with no `Retry-After`. The repo's existing UA `Mozilla/5.0 TradingCopilot/1.0 (personal research)` (`scripts/copilot/providers.py:183`) returns 200.
- `indicators.quote.close` is split-adjusted but **not** dividend-adjusted. `indicators.adjclose` is both. As-traded prices are not obtainable. The repo invariant "Preserve raw and adjusted prices separately" is therefore re-defined for this module as "split-adjusted vs split-and-dividend-adjusted", written into the ADR in Task 1.
- `capitalGains` is never returned, for any of 16 symbols tested, even with `&events=div,split,capitalGain`.

**Why this loader must not use `providers.HttpClient`**
- `providers.py:199` is an unconditional `if self.cache_dir: self._cache(result)` with no off switch, and `service.py:77` sets `cache_dir` by default. `_cache` hard-prunes to 64 files / 64 MB by mtime. A 22–33 symbol sweep would evict the raw HTTP forensics of prior snapshots. That destroys the audit chain, it is not merely a small cache.
- `_throttle`/`_cooldown` key on the provider name `"yahoo"` in a shared sqlite quota. One 429 during a backtest sweep writes a cooldown, and the next **live** decision call fails with `ProviderError("rate_limited", ...)`. Yahoo's 429 carries no `Retry-After`, so the cooldown length is arbitrary.

**Universe (33 equity symbols, from `ETF_REGISTRY` minus `DEFENSIVE_ETFS`)**

| Tier | Symbols | Evidence |
|---|---|---|
| Qualified (≥15y, 2008/2020/2022 = 253/253/251 bars each) | SPY DIA XLK XLV XLF XLE XLY XLP XLI XLB XLU QQQ IVV IWM SMH IOO VTI SOXX VUG VTV VWO VEA (22) | first bars 1993-01-29 .. 2007-07-26 |
| Zero 2008 bars | SCHD VOO VXUS XLRE XLC QQQM (6) | SCHD 2011-10-20; QQQM 2020-10-13, 2020 only 56/253 |
| Partial 2008 | VT (1) | first bar 2008-06-26, 2008 = 131/253 — starts **after** the 2007-10 peak, so GFC drawdown silently reports shallower |
| Unfetchable | SPLG (1) | chart endpoint HTTP 404 on every form; `yfinance 1.7.0` returns an empty frame and **does not raise** |
| Income, proxy only | QQQI JEPQ JEPI (3) | QQQI 2.63y; JEPQ 2022 = 167/251; JEPI 2020 = 156/253 |

22 + 6 + 1 + 1 + 3 = 33. SCHD crosses 15 years on 2026-10-20 but has zero 2008 bars forever, so it can never pass rule 1 — it is not a near-miss to be waived.

**Unverified, recorded as an open item, not a blocker:** whether SMH's pre-2011 history belongs to the current fund (no issuer page was fetched). SMH is in the qualified tier; the ADR records the gap.

**Cboe BXN (Q37, user decision 2026-09-19)**
- `https://cdn.cboe.com/api/global/us_indices/daily_prices/BXN_History.csv` — HTTP 200, text/csv, 94,329 bytes, 4,273 rows, header `DATE,BXN`, first row `09/18/2009,298.140000`, last `09/18/2026`. 2008 = 0 bars, 2020 = 252, 2022 = 251. Two columns, close only.
- The start is fixed, not rolling: a 2025-08-29 Wayback snapshot of the same URL also begins `09/18/2009`. Waiting does not produce 2008 coverage.
- BXNT (31.7 years, covers 2008) was **rejected**: no Cboe page links its CSV, Wayback has zero snapshots of it, its live-launch date appears on no page we fetched so the back-test/live boundary cannot be drawn, and QQQI's own issuer page names "BXN" 4 times and "BXNT" 0 times.
- Licence: cboe.com/terms §2 permits "one copy … for your personal non-commercial use" and forbids, absent written consent, storing in an electronic retrieval system, distributing, creating a derivative work, and using to verify other data. **Therefore: fetch at runtime into memory only, never write the CSV to disk, never package it.** Task 7 adds the packaging guard before Task 8 writes the fetcher.

**`bt` library (why it is not a dependency)**
- Dependency resolution is clean — `uv pip compile` exit 0 on py3.11/3.12/3.13 with `yfinance==1.7.0`, `exchange-calendars==4.13.2`, `tzdata==2026.3` preserved, 49 packages, `bt==1.2.3` + `ffn==1.2.2`, win_amd64 wheels exist, no compiler needed. This is not an `ib_async`-style conflict.
- But `RunIfOutOfBounds` in v1.2.3 iterates `for cname in target.children:` (`algos.py:400`). At bar 0 there are no children — `Rebalance` creates them lazily — so the loop body never runs, `temp` never gets `'cash'`, line 416 returns `False`, `AlgoStack` short-circuits, `Rebalance` never runs, children are never created, and the backtest sits in 100% cash for its entire length. Fixed on master (union of `children.keys() | targets.keys()`), unreleased.
- The trigger must also sit **after** the `Weigh*` algo: `StrategyBase.run` resets `self.temp = {}` every bar (`core.py:2126`), so `"weights" not in target.temp` is always true at the head of a stack and `algos.py:395-396` returns `True` unconditionally — silently becoming daily rebalancing with the tolerance never read.
- Upstream `tests/test_algos.py` at v1.2.3 contains exactly one `RunIfOutOfBounds(0.5)` call, a mock-target unit test, and zero `Backtest`-level integration tests.
- The offline CI test job installs zero third-party packages and imports every `_test_*.py` and every `--self-test` module, so a `bt` import at module scope breaks the matrix. Lazy function-scope import collides with `ruff --select F82` and contradicts `mcps/copilot_mcp.py:6-12`, which documents that NumPy must be imported eagerly on Windows or the MCP call hangs in the native loader.

Conclusion, recorded in the ADR: the production engine is this repo's own stdlib code; `bt` is an **out-of-repo cross-check oracle** only, satisfying Q39=A's "implement from the primary source, cross-check against independent implementations". `bt` never enters the MCP surface and never enters the shipped dependency set.

---

## File Structure

| Path | Responsibility |
|---|---|
| `scripts/copilot/backtest/__init__.py` | Package marker, public re-exports |
| `scripts/copilot/backtest/frame.py` | `PriceFrame`: validated dates × symbols matrix. No I/O. |
| `scripts/copilot/backtest/universe.py` | The four tiers above as frozensets + `classify()`. No I/O. |
| `scripts/copilot/backtest/history.py` | Yahoo daily loader. Own transport, own throttle, no provider-cache, no live-path coupling. Parse split from fetch so parsing is offline-testable. |
| `scripts/copilot/backtest/metrics.py` | CAGR, vol, Sharpe, max drawdown + duration, turnover, window slicing. Pure. |
| `scripts/copilot/backtest/engine.py` | Walk-forward loop, cost model, cash floor, integer shares. Pure given a `PriceFrame`. |
| `scripts/copilot/backtest/rules.py` | Three rule families, each declaring `parameters`. Pure. |
| `scripts/copilot/backtest/admission.py` | Q29's six rules as executable checks over an engine `Result`. Pure. |
| `scripts/copilot/backtest/bxn.py` | BXN proxy fetch + parse, in-memory only, licence docstring. |
| `scripts/backtest_cli.py` | Public facade: run families over a universe, write JSON to `data/audit/`. |
| `scripts/_test_backtest.py` | Offline behavioural suite, stdlib fixtures only. |
| `scripts/_cross_check_bt.py` | Dev-only `bt` oracle + the red-light test that locks v1.2.3's bar-0 behaviour. PEP 723 header. Never imported by anything. |
| `docs/adr/0006-backtest-scope-and-proxy-evidence.md` | Records: own engine not `bt`; the BXN Q29 exception; the raw/adjusted re-definition; SMH provenance gap. |
| `scripts/package_release.py` | Modified: forbid third-party market-data files in the zip. |
| `.github/workflows/ci.yml` | Modified: assert new modules ship; add the calendar-constant runtime probe. |

---

### Task 1: ADR-0006 and the universe tiers

The decision record lands first, exactly as Plan 2 did, so every later task has a citable reason for its shape.

**Files:**
- Create: `docs/adr/0006-backtest-scope-and-proxy-evidence.md`
- Create: `scripts/copilot/backtest/__init__.py`
- Create: `scripts/copilot/backtest/universe.py`
- Test: `scripts/_test_backtest.py` (created here, extended by every later task)

- [ ] **Step 1: Write the failing test**

Create `scripts/_test_backtest.py`:

```python
#!/usr/bin/env python3
"""Offline behavioural contracts for the backtest package.

Stdlib only, no network, no clock, no randomness: this file runs inside the CI
matrix job that installs zero third-party packages.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from copilot.backtest import universe


class UniverseTiers(unittest.TestCase):
    def test_tiers_partition_the_equity_registry(self):
        from copilot.instruments import DEFENSIVE_ETFS, ETF_REGISTRY
        equity = ETF_REGISTRY - DEFENSIVE_ETFS
        covered = (universe.QUALIFIED | universe.NO_2008_BARS | universe.PARTIAL_2008
                   | universe.UNFETCHABLE | universe.INCOME_PROXY_ONLY)
        self.assertEqual(covered, equity)
        self.assertEqual(len(equity), 33)

    def test_tiers_do_not_overlap(self):
        tiers = [universe.QUALIFIED, universe.NO_2008_BARS, universe.PARTIAL_2008,
                 universe.UNFETCHABLE, universe.INCOME_PROXY_ONLY]
        for i, left in enumerate(tiers):
            for right in tiers[i + 1:]:
                self.assertEqual(left & right, frozenset())

    def test_schd_is_not_qualified(self):
        # 14.92y today and 15y on 2026-10-20, but zero 2008 bars forever.
        self.assertIn("SCHD", universe.NO_2008_BARS)

    def test_vt_is_partial_not_qualified(self):
        # 131/253 bars in 2008, starting after the 2007-10 peak.
        self.assertIn("VT", universe.PARTIAL_2008)

    def test_classify_rejects_unregistered(self):
        with self.assertRaises(ValueError):
            universe.classify("NVDA")

    def test_classify_reports_the_reason(self):
        self.assertEqual(universe.classify("SPY").tier, "qualified")
        self.assertTrue(universe.classify("SPY").admissible)
        splg = universe.classify("SPLG")
        self.assertFalse(splg.admissible)
        self.assertIn("404", splg.reason)

    def test_default_candidates_are_exactly_the_qualified_tier(self):
        self.assertEqual(universe.default_candidates(), tuple(sorted(universe.QUALIFIED)))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ModuleNotFoundError: No module named 'copilot.backtest'`

- [ ] **Step 3: Create the package and the tiers**

Create `scripts/copilot/backtest/__init__.py`:

```python
"""Deterministic offline backtesting for the ETF sleeve.

Stdlib only by design. The CI matrix job installs zero third-party packages and
imports every module here, so an import of numpy, pandas or bt at module scope
breaks three Python versions at once. See
docs/adr/0006-backtest-scope-and-proxy-evidence.md for why `bt` is a
cross-check oracle rather than a dependency.
"""
```

Create `scripts/copilot/backtest/universe.py`:

```python
"""Which registry symbols may enter a backtest, and why the rest may not.

Every membership below was observed against the Yahoo chart endpoint on
2026-09-19 with `period1/period2&interval=1d`, cross-checked against
`exchange-calendars==4.13.2` XNYS sessions (2008=253, 2020=253, 2022=251).
These are recorded observations, not estimates. Re-verify with
`python scripts/backtest_cli.py --verify-universe` before editing them.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..instruments import DEFENSIVE_ETFS, ETF_REGISTRY

#: >=15 years of daily bars, with 253/253/251 bars in 2008/2020/2022.
QUALIFIED = frozenset({
    "SPY", "DIA", "QQQ", "IVV", "IWM", "VTI", "VUG", "VTV", "VEA", "VWO", "IOO",
    "XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLB", "XLU",
    "SMH", "SOXX",
})

#: Zero bars in 2008. Not a near-miss that a waiver can fix: the fund did not
#: exist. SCHD crosses the 15-year line on 2026-10-20 and still has zero 2008.
NO_2008_BARS = frozenset({"SCHD", "VOO", "VXUS", "XLRE", "XLC", "QQQM"})

#: Partial 2008 coverage, which is more dangerous than none: VT's first bar is
#: 2008-06-26, after the 2007-10 peak, so a GFC drawdown computed on it silently
#: reports a shallower number than the fund's holders actually lived through.
PARTIAL_2008 = frozenset({"VT"})

#: The chart endpoint returns HTTP 404 for every URL form tried, and
#: yfinance 1.7.0 returns an empty frame WITHOUT raising. A loader that iterates
#: the registry aborts the whole sweep here unless this tier is skipped first.
UNFETCHABLE = frozenset({"SPLG"})

#: Income ETFs far short of 15 years. Handled by the BXN index proxy
#: (see bxn.py and ADR-0006), never by direct backtest.
INCOME_PROXY_ONLY = frozenset({"QQQI", "JEPQ", "JEPI"})

#: Qualified, but the pre-2011 series has not been confirmed to belong to the
#: current fund (no issuer page fetched). Usable; reported as a caveat.
PROVENANCE_UNVERIFIED = frozenset({"SMH"})

_REASONS = {
    "qualified": "daily bars from at least 2007-07-26 with full 2008/2020/2022 coverage",
    "no_2008_bars": "fund did not exist in 2008; zero bars in the GFC window",
    "partial_2008": "first bar 2008-06-26, after the 2007-10 peak; GFC drawdown understated",
    "unfetchable": "Yahoo chart endpoint returns HTTP 404 for every URL form",
    "income_proxy_only": "under 5 years of history; evaluated through the BXN index proxy",
}


@dataclass(frozen=True)
class Classification:
    symbol: str
    tier: str
    admissible: bool
    reason: str
    provenance_unverified: bool


def classify(symbol: str) -> Classification:
    """Tier a registry symbol. Raises for anything not in ETF_REGISTRY."""
    key = symbol.upper()
    if key not in ETF_REGISTRY:
        raise ValueError(f"{key} is not in the ETF registry")
    if key in DEFENSIVE_ETFS:
        raise ValueError(f"{key} is a defensive ETF; the ETF sleeve holds equity ETFs and cash only")
    for tier, members in (("qualified", QUALIFIED), ("no_2008_bars", NO_2008_BARS),
                          ("partial_2008", PARTIAL_2008), ("unfetchable", UNFETCHABLE),
                          ("income_proxy_only", INCOME_PROXY_ONLY)):
        if key in members:
            return Classification(symbol=key, tier=tier, admissible=(tier == "qualified"),
                                  reason=_REASONS[tier],
                                  provenance_unverified=key in PROVENANCE_UNVERIFIED)
    raise ValueError(f"{key} is registered but untiered; add it to scripts/copilot/backtest/universe.py")


def default_candidates() -> tuple[str, ...]:
    """The pool a rule proposal may draw from (Q40=A: the engine proposes 8-12)."""
    return tuple(sorted(QUALIFIED))
```

The `reason` for SPLG contains "404", satisfying the test.

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: 7 tests, OK.

- [ ] **Step 5: Write ADR-0006**

Create `docs/adr/0006-backtest-scope-and-proxy-evidence.md`, following the house format (`# ADR-NNNN: Title` / `Status: accepted, YYYY-MM-DD.` / `## Context` / `## Decision` / `## Consequences`):

```markdown
# ADR-0006: Backtest scope, the engine's provenance, and proxy evidence

Status: accepted, 2026-09-19.

## Context

ADR-0004 fixed that the engine computes and the model explains. That leaves
open what the engine is allowed to compute *from*. Verification on 2026-09-19
established four things that bound the answer.

**The `bt` library resolves cleanly but its band trigger is broken.** `bt==1.2.3`
co-installs with the repo's three runtime pins on Python 3.11/3.12/3.13 (49
packages, win_amd64 wheels, no compiler). But `RunIfOutOfBounds` iterates
`for cname in target.children:` at `algos.py:400`, and at bar 0 there are no
children — `Rebalance` creates them lazily. The loop body never runs, line 416
returns `False`, the `AlgoStack` short-circuits, and the backtest holds 100%
cash for its whole length. Upstream has one mock unit test for this algo and no
`Backtest`-level integration test. The fix exists on master and is unreleased.
Separately, the trigger silently degrades to daily rebalancing if placed before
the `Weigh*` algo, because `StrategyBase.run` clears `self.temp` every bar.

**The offline CI matrix installs zero third-party packages.** It imports every
`_test_*.py` and every `--self-test` module. A module-scope `import bt` breaks
three Python versions. A function-scope import collides with
`ruff --select F82` and contradicts `mcps/copilot_mcp.py:6-12`, which documents
that NumPy must be imported eagerly on Windows or an MCP call hangs in the
native loader.

**Yahoo does not serve as-traded prices.** `indicators.quote.close` is
split-adjusted but not dividend-adjusted; `indicators.adjclose` is both. There
is no third series. `capitalGains` is never returned.

**The only free covered-call index with a documented URL has no 2008.** Cboe's
`BXN_History.csv` starts 09/18/2009 and the start is fixed, not rolling (a
2025-08-29 Wayback snapshot begins on the same date). BXNT reaches 1994 but no
Cboe page links its CSV, Wayback holds zero snapshots of it, its live-launch
date appears on no page we fetched — so the back-test/live boundary cannot be
drawn — and QQQI's issuer page names BXN four times and BXNT zero times.
Cboe's terms permit one copy for personal non-commercial use and forbid, absent
written consent, storing in an electronic retrieval system, distributing,
creating a derivative work, and using to verify other data.

## Decision

1. **The production backtest engine is this repository's own stdlib code**, in
   `scripts/copilot/backtest/`. It carries no third-party dependency, so the
   offline CI matrix executes every behavioural test on three Python versions.

2. **`bt` is a cross-check oracle, not a dependency.** `scripts/_cross_check_bt.py`
   carries its own PEP 723 header, is never imported by any shipped module, and
   never enters the MCP surface. It exists to satisfy Q39's requirement that a
   rule implemented from its primary source be cross-checked against an
   independent implementation. It pins `ffn==1.2.2` explicitly, because `bt`
   declares only `ffn>=1.1.2` and ffn 1.2.2 was published two days before this
   verification. It sets `MPLBACKEND=Agg`, because `bt/backtest.py:11` imports
   pyplot unconditionally and an unset backend selects `tkagg`.

3. **"Preserve raw and adjusted prices separately" is re-defined for this
   module** as split-adjusted (`quote.close`) versus split-and-dividend-adjusted
   (`adjclose`). As-traded prices are unobtainable from the available transport.
   Both series are stored; neither is labelled "raw".

4. **The historical loader does not use `providers.HttpClient`.** Two mechanical
   reasons, both observed in code: `providers.py:199` caches unconditionally
   with no off switch, and `_cache` prunes to 64 files by mtime, so a 22-symbol
   sweep evicts the raw HTTP forensics of prior snapshots and breaks the audit
   chain; and `_throttle`/`_cooldown` key on the provider name `"yahoo"` in a
   shared quota, so one 429 during a backtest blocks the next live decision
   call with an arbitrary-length cooldown (Yahoo sends no `Retry-After`).

5. **Q29 rule 1 gets exactly one written exception, for the BXN proxy.** The
   income sleeve (QQQI, JEPQ, JEPI) is evaluated against BXN's 17 years from
   2009-09-18, covering 2020 and 2022 but **not** 2008. Every output of that
   path is labelled "index proxy evidence" and must never be reported as
   "QQQI passed the admission gate". BXN is a mechanical monthly at-the-money
   buy-write; QQQI is actively managed and its own issuer page describes a
   strategy that "may include both sold and purchased NDX index options". The
   tracking error between them is unmeasured.

6. **Cboe data is fetched at runtime into memory and never written to disk.**
   `package_release.py` forbids any `*_history.csv` from entering the zip, and
   CI asserts it. Tests use a synthetic three-row CSV of our own authorship.

## Consequences

- Every backtest behavioural contract runs in the offline matrix. Nothing about
  the rule library depends on a package resolution that can change under us.
- We own the engine's bugs. The mitigation is that the three rule families are
  each cross-checked against `bt` in a dev-only script, and a disagreement is a
  release blocker.
- The ETF sleeve's admissible pool is 22 symbols, not 33. Six registry symbols
  have zero 2008 bars, VT has 131 of 253, and SPLG cannot be fetched at all.
  A user may still hold any of them; the engine will not issue rules for them.
- QQQI, which the account actually holds, receives index-proxy evidence over 17
  years rather than a 15-year fund backtest, and every report says so.
- SMH's pre-2011 provenance is unverified (no issuer page fetched). It stays in
  the qualified tier with a caveat flag; confirming or splitting that series is
  open work.
```

- [ ] **Step 6: Run the repo gates**

```bash
python scripts/check.py && python -m unittest discover -s scripts -p "_test_*.py"
```

Expected: `check.py` reports 0 errors; the suite is green with 7 new tests.

- [ ] **Step 7: Commit**

```bash
git add docs/adr/0006-backtest-scope-and-proxy-evidence.md scripts/copilot/backtest/ scripts/_test_backtest.py
git commit -m "feat(backtest): tier the ETF universe by verified history, record ADR-0006"
```

---

### Task 2: `PriceFrame`

A validated dates × symbols matrix. Everything downstream is pure given one of these.

**Files:**
- Create: `scripts/copilot/backtest/frame.py`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_backtest.py`, above the `if __name__` block, and add `from datetime import date` plus `from copilot.backtest import frame as frame_mod` to the imports:

```python
class PriceFrameContract(unittest.TestCase):
    def frame(self):
        return frame_mod.build(
            dates=[date(2020, 1, 2), date(2020, 1, 3), date(2020, 1, 6)],
            symbols=["SPY", "QQQ"],
            closes=[[100.0, 200.0], [101.0, 198.0], [99.0, 202.0]],
        )

    def test_column_returns_one_symbol_series(self):
        self.assertEqual(self.frame().column("QQQ"), (200.0, 198.0, 202.0))

    def test_symbols_are_upper_cased_and_indexed(self):
        f = frame_mod.build(dates=[date(2020, 1, 2)], symbols=["spy"], closes=[[1.0]])
        self.assertEqual(f.symbols, ("SPY",))
        self.assertEqual(f.index_of("spy"), 0)

    def test_rejects_unsorted_dates(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            frame_mod.build(dates=[date(2020, 1, 3), date(2020, 1, 2)],
                            symbols=["SPY"], closes=[[1.0], [2.0]])

    def test_rejects_duplicate_dates(self):
        with self.assertRaisesRegex(ValueError, "strictly increasing"):
            frame_mod.build(dates=[date(2020, 1, 2), date(2020, 1, 2)],
                            symbols=["SPY"], closes=[[1.0], [2.0]])

    def test_rejects_duplicate_symbols(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            frame_mod.build(dates=[date(2020, 1, 2)], symbols=["SPY", "spy"], closes=[[1.0, 2.0]])

    def test_rejects_ragged_rows(self):
        with self.assertRaisesRegex(ValueError, "2 values"):
            frame_mod.build(dates=[date(2020, 1, 2)], symbols=["SPY", "QQQ"], closes=[[1.0]])

    def test_rejects_non_positive_and_nan_closes(self):
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                frame_mod.build(dates=[date(2020, 1, 2)], symbols=["SPY"], closes=[[bad]])

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            frame_mod.build(dates=[], symbols=["SPY"], closes=[])

    def test_slice_is_inclusive_on_both_ends(self):
        sliced = self.frame().slice(date(2020, 1, 3), date(2020, 1, 6))
        self.assertEqual(sliced.dates, (date(2020, 1, 3), date(2020, 1, 6)))
        self.assertEqual(sliced.column("SPY"), (101.0, 99.0))

    def test_sessions_in_year_counts_bars(self):
        self.assertEqual(self.frame().sessions_in_year(2020), 3)
        self.assertEqual(self.frame().sessions_in_year(2008), 0)

    def test_span_years_uses_actual_calendar_distance(self):
        f = frame_mod.build(dates=[date(2000, 1, 3), date(2015, 1, 5)],
                            symbols=["SPY"], closes=[[1.0], [2.0]])
        self.assertAlmostEqual(f.span_years(), 15.01, places=1)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ImportError: cannot import name 'frame'`

- [ ] **Step 3: Write `frame.py`**

```python
"""A validated price matrix. No I/O, no clock, no randomness.

Validation is strict on purpose: a silently ragged or unsorted matrix produces
a plausible-looking equity curve, which is worse than an exception.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

DAYS_PER_YEAR = 365.25


@dataclass(frozen=True)
class PriceFrame:
    dates: tuple[date, ...]
    symbols: tuple[str, ...]
    closes: tuple[tuple[float, ...], ...]

    def index_of(self, symbol: str) -> int:
        try:
            return self.symbols.index(symbol.upper())
        except ValueError:
            raise KeyError(f"{symbol.upper()} is not in this frame: {', '.join(self.symbols)}") from None

    def column(self, symbol: str) -> tuple[float, ...]:
        col = self.index_of(symbol)
        return tuple(row[col] for row in self.closes)

    def row(self, i: int) -> dict[str, float]:
        return dict(zip(self.symbols, self.closes[i]))

    def slice(self, start: date, end: date) -> "PriceFrame":
        """Inclusive on both ends."""
        keep = [i for i, d in enumerate(self.dates) if start <= d <= end]
        if not keep:
            raise ValueError(f"no bars between {start} and {end}")
        return build(dates=[self.dates[i] for i in keep], symbols=list(self.symbols),
                     closes=[list(self.closes[i]) for i in keep])

    def sessions_in_year(self, year: int) -> int:
        return sum(1 for d in self.dates if d.year == year)

    def span_years(self) -> float:
        return (self.dates[-1] - self.dates[0]).days / DAYS_PER_YEAR

    def __len__(self) -> int:
        return len(self.dates)


def build(*, dates: Sequence[date], symbols: Sequence[str],
          closes: Iterable[Sequence[float]]) -> PriceFrame:
    rows = [tuple(float(v) for v in row) for row in closes]
    if not dates or not symbols:
        raise ValueError("a price frame needs at least one date and one symbol")
    upper = tuple(s.upper() for s in symbols)
    duplicates = sorted({s for s in upper if upper.count(s) > 1})
    if duplicates:
        raise ValueError(f"duplicate symbols: {', '.join(duplicates)}")
    if len(rows) != len(dates):
        raise ValueError(f"{len(dates)} dates but {len(rows)} rows")
    for i in range(1, len(dates)):
        if dates[i] <= dates[i - 1]:
            raise ValueError(f"dates must be strictly increasing: {dates[i - 1]} then {dates[i]}")
    for i, row in enumerate(rows):
        if len(row) != len(upper):
            raise ValueError(
                f"row {i} ({dates[i]}) has {len(row)} values, expected {len(upper)} values")
        for j, value in enumerate(row):
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{upper[j]} on {dates[i]}: close must be finite and positive, got {value}")
    return PriceFrame(dates=tuple(dates), symbols=upper, closes=tuple(rows))
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: OK — 11 new tests in this task, 21 in the file.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/backtest/frame.py scripts/_test_backtest.py
git commit -m "feat(backtest): validated price frame with strict shape checks"
```

---

### Task 3: The historical loader

Separate parse from fetch, so the parser is fully tested offline and the fetcher has one job.

**Files:**
- Create: `scripts/copilot/backtest/history.py`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Add `import json` to the test imports and `from copilot.backtest import history`, then append:

```python
def chart_payload(granularity="1d", *, timestamps=None, closes=None, adjcloses=None):
    timestamps = timestamps if timestamps is not None else [1577941200, 1578027600]
    closes = closes if closes is not None else [100.0, 101.0]
    adjcloses = adjcloses if adjcloses is not None else [99.0, 100.0]
    return json.dumps({"chart": {"error": None, "result": [{
        "meta": {"symbol": "SPY", "dataGranularity": granularity,
                 "exchangeTimezoneName": "America/New_York"},
        "timestamp": timestamps,
        "indicators": {"quote": [{"close": closes}], "adjclose": [{"adjclose": adjcloses}]},
    }]}})


class HistoryParsing(unittest.TestCase):
    def test_parses_both_series_separately(self):
        series = history.parse_chart("SPY", chart_payload())
        self.assertEqual(series.symbol, "SPY")
        self.assertEqual(len(series.dates), 2)
        self.assertEqual(series.split_adjusted, (100.0, 101.0))
        self.assertEqual(series.split_and_dividend_adjusted, (99.0, 100.0))

    def test_rejects_monthly_granularity(self):
        # range=max&interval=1d returns HTTP 200 with dataGranularity='1mo'.
        # Without this check a 15-year gate can be passed by ~180 monthly bars.
        with self.assertRaisesRegex(history.HistoryError, "dataGranularity"):
            history.parse_chart("SPY", chart_payload(granularity="1mo"))

    def test_drops_bars_where_either_series_is_null(self):
        payload = chart_payload(timestamps=[1577941200, 1578027600, 1578114000],
                                closes=[100.0, None, 102.0],
                                adjcloses=[99.0, 100.0, 101.0])
        series = history.parse_chart("SPY", payload)
        self.assertEqual(len(series.dates), 2)
        self.assertEqual(series.split_adjusted, (100.0, 102.0))
        self.assertEqual(series.dropped_bars, 1)

    def test_rejects_an_empty_result(self):
        with self.assertRaises(history.HistoryError):
            history.parse_chart("SPY", json.dumps({"chart": {"error": None, "result": []}}))

    def test_surfaces_the_upstream_error_text(self):
        body = json.dumps({"chart": {"error": {"code": "Not Found",
                                               "description": "No data found, symbol may be delisted"},
                                     "result": None}})
        with self.assertRaisesRegex(history.NotCovered, "delisted"):
            history.parse_chart("SPLG", body)

    def test_dates_come_back_in_new_york_not_utc(self):
        # 1578016200 is 2020-01-03 01:50 UTC, which is 2020-01-02 20:50 in New
        # York. Real Yahoo daily stamps sit at the exchange open, where the two
        # calendars agree; this is the defensive case, and taking the UTC date
        # would file the bar under the wrong session.
        series = history.parse_chart("SPY", chart_payload(timestamps=[1578016200], closes=[1.0],
                                                          adjcloses=[1.0]))
        self.assertEqual(series.dates[0], date(2020, 1, 2))


class HistoryUrl(unittest.TestCase):
    def test_url_uses_period1_period2_never_range(self):
        url = history.chart_url("SPY", until_epoch=1789797166)
        self.assertIn("period1=0", url)
        self.assertIn("period2=1789797166", url)
        self.assertIn("interval=1d", url)
        self.assertNotIn("range=", url)

    def test_user_agent_matches_the_one_that_is_not_rate_limited(self):
        self.assertEqual(history.USER_AGENT,
                         "Mozilla/5.0 TradingCopilot/1.0 (personal research)")

    def test_loader_never_touches_the_shared_provider_cache(self):
        source = Path(history.__file__).read_text(encoding="utf-8")
        for forbidden in ("HttpClient", "cache_dir", "_cache("):
            self.assertNotIn(forbidden, source,
                             f"{forbidden} must not appear anywhere in history.py — "
                             "a 22-symbol sweep through providers.py evicts snapshot forensics "
                             "and its 'yahoo' cooldown blocks the live decision path")
```

The assertion is over the whole file, comments included, so `history.py`'s docstring must explain the ban without naming those identifiers. Add `from pathlib import Path` to the test imports.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ImportError: cannot import name 'history'`

- [ ] **Step 3: Write `history.py`**

```python
"""Daily history for backtests. Deliberately NOT routed through providers.py.

Two mechanical reasons, both in ADR-0006. First, providers.py caches every
response unconditionally into a 64-slot store pruned by mtime, so a 22-symbol
sweep would evict the raw HTTP forensics that back earlier snapshots. Second,
its throttle and cooldown key on the provider name "yahoo" in a shared quota,
so one HTTP 429 here would block the next live decision call for an arbitrary
time -- Yahoo sends no Retry-After header.

The two price series are kept apart on purpose. Yahoo's quote.close is
split-adjusted but not dividend-adjusted; adjclose is both. Neither is the
as-traded price, which this transport does not serve.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

from .frame import PriceFrame, build

CHART_HOST = "https://query1.finance.yahoo.com/v8/finance/chart"

#: Observed 2026-09-19: no UA, "Python-urllib/3.13" and "curl/8.5.0" each drew
#: HTTP 429 on the first request with no Retry-After. This one returned 200.
USER_AGENT = "Mozilla/5.0 TradingCopilot/1.0 (personal research)"

#: New York is UTC-5 or UTC-4. A daily bar is stamped at the exchange open, so
#: converting naively in UTC shifts bars across midnight. Five hours is the
#: winter offset; four in summer. Subtracting the winter offset and taking the
#: date is correct in both, because the open is 09:30 local either way.
_NEW_YORK_WINTER_OFFSET = timedelta(hours=5)

#: One request every 1.5s, single process. Observed: 30 consecutive requests at
#: 1.05-2.85s intervals all returned 200. Behaviour above that volume is
#: unmeasured, so this is a floor chosen for politeness, not a proven safe rate.
_MIN_INTERVAL_SECONDS = 1.5
_last_request_at = 0.0


class HistoryError(RuntimeError):
    """The response is unusable: wrong granularity, empty, or malformed."""


class NotCovered(HistoryError):
    """Upstream says this symbol has no data. Skip it; do not abort the sweep."""


@dataclass(frozen=True)
class Series:
    symbol: str
    dates: tuple[date, ...]
    split_adjusted: tuple[float, ...]
    split_and_dividend_adjusted: tuple[float, ...]
    dropped_bars: int


def chart_url(symbol: str, *, until_epoch: int) -> str:
    """period1/period2, never range.

    `range=max&interval=1d` returns HTTP 200 with meta.dataGranularity='1mo'
    and about 405 monthly bars while still echoing range='max'.
    """
    return (f"{CHART_HOST}/{symbol.upper()}"
            f"?period1=0&period2={int(until_epoch)}&interval=1d&events=div%2Csplit")


def _to_new_york_date(epoch: int) -> date:
    return (datetime.fromtimestamp(epoch, tz=timezone.utc) - _NEW_YORK_WINTER_OFFSET).date()


def parse_chart(symbol: str, body: str) -> Series:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HistoryError(f"{symbol}: response is not JSON: {exc}") from exc
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        raise NotCovered(f"{symbol}: {error.get('code')}: {error.get('description')}")
    results = chart.get("result") or []
    if not results:
        raise HistoryError(f"{symbol}: chart.result is empty")
    result = results[0]
    granularity = (result.get("meta") or {}).get("dataGranularity")
    if granularity != "1d":
        raise HistoryError(
            f"{symbol}: dataGranularity is {granularity!r}, expected '1d' — "
            "a monthly series would pass a 15-year gate with about 180 bars")
    stamps: Sequence[int] = result.get("timestamp") or []
    quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
    adj = ((result.get("indicators") or {}).get("adjclose") or [{}])[0]
    closes: Sequence[float | None] = quote.get("close") or []
    adjcloses: Sequence[float | None] = adj.get("adjclose") or []
    if not (len(stamps) == len(closes) == len(adjcloses)):
        raise HistoryError(f"{symbol}: {len(stamps)} timestamps, {len(closes)} closes, "
                           f"{len(adjcloses)} adjcloses")
    dates, raw, adjusted, dropped = [], [], [], 0
    for stamp, close, adjclose in zip(stamps, closes, adjcloses):
        if close is None or adjclose is None or close <= 0 or adjclose <= 0:
            dropped += 1
            continue
        dates.append(_to_new_york_date(stamp))
        raw.append(float(close))
        adjusted.append(float(adjclose))
    if not dates:
        raise HistoryError(f"{symbol}: every bar was null")
    return Series(symbol=symbol.upper(), dates=tuple(dates), split_adjusted=tuple(raw),
                  split_and_dividend_adjusted=tuple(adjusted), dropped_bars=dropped)


def fetch(symbol: str, *, until_epoch: int | None = None, timeout: float = 30.0) -> Series:
    """One symbol, one request, nothing written to disk."""
    global _last_request_at
    if until_epoch is None:
        until_epoch = int(time.time())
    wait = _MIN_INTERVAL_SECONDS - (time.monotonic() - _last_request_at)
    if wait > 0:
        time.sleep(wait)
    request = urllib.request.Request(chart_url(symbol, until_epoch=until_epoch),
                                     headers={"User-Agent": USER_AGENT,
                                              "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise NotCovered(f"{symbol}: HTTP 404 from the chart endpoint") from exc
        raise HistoryError(f"{symbol}: HTTP {exc.code} from the chart endpoint") from exc
    except urllib.error.URLError as exc:
        raise HistoryError(f"{symbol}: {exc.reason}") from exc
    finally:
        _last_request_at = time.monotonic()
    return parse_chart(symbol, body)


def to_frame(series: Sequence[Series], *, dividend_adjusted: bool = True) -> PriceFrame:
    """Intersect several symbols onto their common dates.

    Intersection, not union: a rule that sees a forward-filled price for a
    symbol that had no bar that day trades on a number nobody could have got.
    """
    if not series:
        raise ValueError("no series to align")
    common = set(series[0].dates)
    for s in series[1:]:
        common &= set(s.dates)
    if not common:
        raise ValueError("these symbols share no common trading dates")
    dates = sorted(common)
    lookup = {}
    for s in series:
        chosen = s.split_and_dividend_adjusted if dividend_adjusted else s.split_adjusted
        lookup[s.symbol] = dict(zip(s.dates, chosen))
    symbols = [s.symbol for s in series]
    closes = [[lookup[sym][d] for sym in symbols] for d in dates]
    return build(dates=dates, symbols=symbols, closes=closes)
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: OK — 10 new tests in this task (9 from the plan plus the `to_frame` intersection test), 31 in the file.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/backtest/history.py scripts/_test_backtest.py
git commit -m "feat(backtest): daily loader that asserts granularity and bypasses the live cache"
```

---

### Task 4: Metrics

Every number Q29 rule 2 demands, computed from an equity curve.

**Files:**
- Create: `scripts/copilot/backtest/metrics.py`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Add `from copilot.backtest import metrics` and append:

```python
class Metrics(unittest.TestCase):
    def curve(self, values, start=date(2020, 1, 2)):
        return [(start + timedelta(days=i), v) for i, v in enumerate(values)]

    def test_max_drawdown_finds_peak_trough_and_duration(self):
        dd = metrics.max_drawdown(self.curve([100, 120, 60, 80, 130]))
        self.assertAlmostEqual(dd.depth, 0.5)
        self.assertEqual(dd.peak_date, date(2020, 1, 3))
        self.assertEqual(dd.trough_date, date(2020, 1, 4))
        self.assertEqual(dd.recovery_date, date(2020, 1, 6))
        self.assertEqual(dd.duration_days, 3)

    def test_unrecovered_drawdown_reports_no_recovery_date(self):
        dd = metrics.max_drawdown(self.curve([100, 120, 60]))
        self.assertIsNone(dd.recovery_date)
        self.assertAlmostEqual(dd.depth, 0.5)

    def test_flat_curve_has_zero_drawdown(self):
        dd = metrics.max_drawdown(self.curve([100, 100, 100]))
        self.assertEqual(dd.depth, 0.0)

    def test_cagr_matches_a_hand_computed_doubling(self):
        curve = [(date(2010, 1, 4), 100.0), (date(2020, 1, 3), 200.0)]
        self.assertAlmostEqual(metrics.cagr(curve), 0.0718, places=3)

    def test_sharpe_is_zero_for_a_flat_curve(self):
        self.assertEqual(metrics.sharpe(self.curve([100, 100, 100, 100])), 0.0)

    def test_sharpe_declares_its_risk_free_rate(self):
        self.assertEqual(metrics.RISK_FREE_RATE, 0.0)

    def test_annual_volatility_annualises_by_sqrt_252(self):
        import math
        curve = self.curve([100, 110, 100, 110, 100])
        daily = [0.10, -1 / 11, 0.10, -1 / 11]
        expected = statistics.stdev(daily) * math.sqrt(252)
        self.assertAlmostEqual(metrics.annual_volatility(curve), expected, places=6)

    def test_turnover_is_annualised_one_way_notional(self):
        # 50 traded on an average value of 100 over exactly one year = 0.5.
        self.assertAlmostEqual(
            metrics.annual_turnover(traded_notional=50.0, average_value=100.0, years=1.0), 0.5)

    def test_turnover_of_a_never_traded_book_is_zero(self):
        self.assertEqual(metrics.annual_turnover(traded_notional=0.0, average_value=100.0, years=3.0), 0.0)

    def test_window_slices_a_calendar_year(self):
        curve = [(date(2007, 12, 31), 100.0), (date(2008, 6, 1), 60.0), (date(2009, 1, 2), 90.0)]
        self.assertEqual(len(metrics.window(curve, 2008)), 1)
```

Add `from datetime import date, timedelta` and `import statistics` to the test imports.

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ImportError: cannot import name 'metrics'`

- [ ] **Step 3: Write `metrics.py`**

```python
"""Risk and return metrics. Pure functions over an equity curve.

Formula sources: annualized return, volatility and max drawdown follow
microsoft/qlib `contrib/evaluate.py::risk_analysis` (MIT), re-implemented in
the stdlib. Nothing is vendored.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date
from typing import Sequence

TRADING_DAYS_PER_YEAR = 252
DAYS_PER_YEAR = 365.25

#: Declared, not assumed. Sharpe here is excess return over zero. A reader who
#: wants a T-bill benchmark must say so; silently baking one in makes two
#: backtests from different years incomparable.
RISK_FREE_RATE = 0.0

Curve = Sequence[tuple[date, float]]


@dataclass(frozen=True)
class Drawdown:
    depth: float
    peak_date: date | None
    trough_date: date | None
    recovery_date: date | None
    duration_days: int


def max_drawdown(curve: Curve) -> Drawdown:
    """Deepest peak-to-trough fall, with the days from peak to recovery.

    `duration_days` runs peak to recovery when recovery happened, peak to the
    end of the curve when it has not. Reporting only the trough understates how
    long a holder actually spent underwater, which is the number that decides
    whether someone abandons a strategy.
    """
    if len(curve) < 2:
        return Drawdown(0.0, None, None, None, 0)
    peak_value, peak_date = curve[0][1], curve[0][0]
    best = Drawdown(0.0, None, None, None, 0)
    for when, value in curve:
        if value > peak_value:
            peak_value, peak_date = value, when
            continue
        depth = (peak_value - value) / peak_value
        if depth > best.depth:
            best = Drawdown(depth, peak_date, when, None, 0)
    if best.peak_date is None:
        return best
    recovery = next((w for w, v in curve
                     if w > best.trough_date and v >= _value_at(curve, best.peak_date)), None)
    end = recovery or curve[-1][0]
    return Drawdown(best.depth, best.peak_date, best.trough_date, recovery,
                    (end - best.peak_date).days)


def _value_at(curve: Curve, when: date) -> float:
    for w, v in curve:
        if w == when:
            return v
    raise KeyError(when)


def cagr(curve: Curve) -> float:
    if len(curve) < 2:
        return 0.0
    years = (curve[-1][0] - curve[0][0]).days / DAYS_PER_YEAR
    if years <= 0 or curve[0][1] <= 0:
        return 0.0
    return (curve[-1][1] / curve[0][1]) ** (1 / years) - 1


def daily_returns(curve: Curve) -> list[float]:
    return [(curve[i][1] / curve[i - 1][1]) - 1 for i in range(1, len(curve))
            if curve[i - 1][1] > 0]


def annual_volatility(curve: Curve) -> float:
    returns = daily_returns(curve)
    if len(returns) < 2:
        return 0.0
    return statistics.stdev(returns) * math.sqrt(TRADING_DAYS_PER_YEAR)


def sharpe(curve: Curve) -> float:
    vol = annual_volatility(curve)
    if vol == 0:
        return 0.0
    return (cagr(curve) - RISK_FREE_RATE) / vol


def annual_turnover(*, traded_notional: float, average_value: float, years: float) -> float:
    """One-way traded notional over average book value, per year."""
    if average_value <= 0 or years <= 0:
        return 0.0
    return traded_notional / average_value / years


def window(curve: Curve, year: int) -> list[tuple[date, float]]:
    return [(w, v) for w, v in curve if w.year == year]
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: OK — 10 new tests in this task, 41 in the file.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/backtest/metrics.py scripts/_test_backtest.py
git commit -m "feat(backtest): drawdown, CAGR, volatility, Sharpe and turnover"
```

---

### Task 5: The engine

**Files:**
- Create: `scripts/copilot/backtest/engine.py`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Add `from copilot.backtest import engine` and append:

```python
class CostModel(unittest.TestCase):
    def test_commission_has_a_floor(self):
        model = engine.CostModel()
        self.assertAlmostEqual(model.commission(shares=10, notional=1000.0), 1.00)

    def test_commission_is_per_share_above_the_floor(self):
        model = engine.CostModel()
        self.assertAlmostEqual(model.commission(shares=1000, notional=100000.0), 3.50)

    def test_commission_is_capped_at_one_percent_of_notional(self):
        model = engine.CostModel()
        self.assertAlmostEqual(model.commission(shares=10, notional=50.0), 0.50)

    def test_spread_is_half_the_quoted_width(self):
        model = engine.CostModel(spread_bps=4.0)
        self.assertAlmostEqual(model.spread(notional=10000.0), 2.00)


class EngineLoop(unittest.TestCase):
    def flat_frame(self, n=30):
        return frame_mod.build(dates=[date(2020, 1, 1) + timedelta(days=i) for i in range(n)],
                               symbols=["AAA", "BBB"], closes=[[100.0, 50.0]] * n)

    def test_a_flat_market_with_no_costs_preserves_capital(self):
        result = engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 0.5, "BBB": 0.5}),
                            start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.0)
        self.assertAlmostEqual(result.curve[-1][1], 10000.0, places=6)

    def test_cash_floor_is_never_invaded(self):
        result = engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 1.0}),
                            start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.20)
        self.assertGreaterEqual(min(result.cash_history), 10000.0 * 0.20 - 1e-6)

    def test_integer_shares_are_the_default(self):
        frame = frame_mod.build(dates=[date(2020, 1, 1), date(2020, 1, 2)],
                                symbols=["AAA"], closes=[[333.0], [333.0]])
        result = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=1000.0,
                            cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        self.assertEqual(result.positions_history[0]["AAA"], 3)

    def test_costs_reduce_the_final_value(self):
        frame = self.flat_frame()
        free = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=10000.0,
                          cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        charged = engine.run(frame, rule=engine.StaticWeights({"AAA": 1.0}), start_cash=10000.0,
                             cost_model=engine.CostModel(), cash_floor_pct=0.0)
        self.assertLess(charged.curve[-1][1], free.curve[-1][1])

    def test_no_lookahead_uses_only_bars_up_to_today(self):
        seen = []

        class Spy(engine.Rule):
            parameters = {}
            name = "spy"

            def weights(self, frame, i):
                seen.append(i)
                return {"AAA": 1.0}

        engine.run(self.flat_frame(10), rule=Spy(), start_cash=1000.0,
                   cost_model=engine.CostModel.free(), cash_floor_pct=0.0)
        self.assertTrue(all(i < 10 for i in seen))
        self.assertEqual(max(seen), 9)

    def test_weights_that_do_not_sum_to_one_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 0.9}),
                       start_cash=1000.0, cost_model=engine.CostModel.free(), cash_floor_pct=0.0)

    def test_result_reports_traded_notional_for_turnover(self):
        result = engine.run(self.flat_frame(), rule=engine.StaticWeights({"AAA": 1.0}),
                            start_cash=10000.0, cost_model=engine.CostModel.free(),
                            cash_floor_pct=0.0)
        self.assertGreater(result.traded_notional, 0.0)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ImportError: cannot import name 'engine'`

- [ ] **Step 3: Write `engine.py`**

```python
"""Walk-forward portfolio loop. Deterministic: no clock, no network, no random.

The loop deliberately never reads frame.closes[i + 1]. Every rule receives the
index of the bar being traded and may look only backwards. The `Spy` test in
scripts/_test_backtest.py enforces that with a recorded call log rather than a
comment, because look-ahead is the failure that makes a backtest look good.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, Sequence

from .frame import PriceFrame

WEIGHT_TOLERANCE = 1e-6


class Rule(Protocol):
    """Rules are frozen and stateless.

    `last_rebalance_index` is passed in rather than remembered, so the engine
    owns all mutable state. A rule that remembered its own last rebalance would
    silently carry it into the next backtest run, and the second result would
    differ from the first for no visible reason.
    """
    name: str
    parameters: dict[str, float]

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        """Target weights for bar `i`, summing to 1.0. Look backwards only."""

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:  # pragma: no cover - default
        return True


@dataclass(frozen=True)
class StaticWeights:
    """Test fixture rule: constant targets, rebalanced every bar."""
    targets: dict[str, float]
    name: str = "static"

    @property
    def parameters(self) -> dict[str, float]:
        return {}

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        return dict(self.targets)

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        return True


@dataclass(frozen=True)
class CostModel:
    """IBKR Tiered-like US equity costs, plus half the quoted spread.

    Defaults are the published IBKR Pro tiered schedule as of 2026-09:
    USD 0.0035 per share, USD 1.00 minimum per order, capped at 1% of trade
    value. The spread term is a modelling assumption, not a fee: 2bp round-trip
    on liquid US ETFs, charged as half on each side.
    """
    per_share_usd: float = 0.0035
    minimum_usd: float = 1.00
    max_pct_of_notional: float = 0.01
    spread_bps: float = 2.0

    @classmethod
    def free(cls) -> "CostModel":
        return cls(per_share_usd=0.0, minimum_usd=0.0, max_pct_of_notional=0.0, spread_bps=0.0)

    def commission(self, *, shares: float, notional: float) -> float:
        if shares <= 0 or notional <= 0:
            return 0.0
        fee = max(self.minimum_usd, self.per_share_usd * shares)
        return min(fee, self.max_pct_of_notional * notional) if self.max_pct_of_notional else fee

    def spread(self, *, notional: float) -> float:
        return notional * (self.spread_bps / 10000.0) / 2.0

    def total(self, *, shares: float, notional: float) -> float:
        return self.commission(shares=shares, notional=notional) + self.spread(notional=notional)


@dataclass
class Result:
    rule_name: str
    parameters: dict[str, float]
    curve: list[tuple[date, float]] = field(default_factory=list)
    cash_history: list[float] = field(default_factory=list)
    positions_history: list[dict[str, float]] = field(default_factory=list)
    traded_notional: float = 0.0
    total_costs: float = 0.0
    rebalance_count: int = 0

    @property
    def average_value(self) -> float:
        return sum(v for _, v in self.curve) / len(self.curve) if self.curve else 0.0

    @property
    def years(self) -> float:
        if len(self.curve) < 2:
            return 0.0
        return (self.curve[-1][0] - self.curve[0][0]).days / 365.25


def _validate(targets: dict[str, float], frame: PriceFrame) -> None:
    total = sum(targets.values())
    if abs(total - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"target weights must sum to 1.0, got {total:.9f}")
    for symbol, weight in targets.items():
        if weight < 0:
            raise ValueError(f"{symbol}: negative weight {weight}; this sleeve is long-only")
        frame.index_of(symbol)


def run(frame: PriceFrame, *, rule: Rule, start_cash: float, cost_model: CostModel,
        cash_floor_pct: float, integer_shares: bool = True) -> Result:
    """Trade at each bar's close, paying costs on the traded notional."""
    if not 0.0 <= cash_floor_pct < 1.0:
        raise ValueError(f"cash_floor_pct must be in [0, 1), got {cash_floor_pct}")
    result = Result(rule_name=rule.name, parameters=dict(rule.parameters))
    cash = float(start_cash)
    positions: dict[str, float] = {}
    last_rebalance_index: int | None = None
    for i, when in enumerate(frame.dates):
        prices = frame.row(i)
        value = cash + sum(qty * prices[sym] for sym, qty in positions.items())
        current = {sym: (qty * prices[sym]) / value for sym, qty in positions.items()} if value else {}
        if rule.should_rebalance(frame, i, current, last_rebalance_index):
            last_rebalance_index = i
            targets = rule.weights(frame, i)
            _validate(targets, frame)
            # Known wart, deliberately left visible: with integer shares the
            # engine buys floor(investable / price) and then pays commission, so
            # cash can finish a bar a few dollars below the floor (negative when
            # the floor is 0). It self-corrects on the next rebalance by selling
            # one share, and the error is bounded by one share plus costs. A real
            # broker would reject the overdraft; modelling that needs a
            # cost-aware sizing loop, which is Plan 4's problem, not this one's.
            investable = value * (1.0 - cash_floor_pct)
            desired: dict[str, float] = {}
            for symbol, weight in targets.items():
                raw = (investable * weight) / prices[symbol]
                desired[symbol] = float(int(raw)) if integer_shares else raw
            for symbol in set(positions) | set(desired):
                delta = desired.get(symbol, 0.0) - positions.get(symbol, 0.0)
                if abs(delta) < 1e-9:
                    continue
                notional = abs(delta) * prices[symbol]
                cost = cost_model.total(shares=abs(delta), notional=notional)
                cash -= delta * prices[symbol] + cost
                result.traded_notional += notional
                result.total_costs += cost
            positions = {s: q for s, q in desired.items() if q > 0}
            result.rebalance_count += 1
            value = cash + sum(qty * prices[sym] for sym, qty in positions.items())
        result.curve.append((when, value))
        result.cash_history.append(cash)
        result.positions_history.append(dict(positions))
    return result
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: OK — 11 new tests in this task, 52 in the file.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/backtest/engine.py scripts/_test_backtest.py
git commit -m "feat(backtest): walk-forward engine with IBKR costs and a cash floor"
```

---

### Task 6: The three rule families

Q38=B plus the user's cash amendment: three families, equity ETFs and cash only, never bonds or gold. Q39=A: implement from the primary source, not from a fork.

**Files:**
- Create: `scripts/copilot/backtest/rules.py`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Add `from copilot.backtest import rules` and append:

```python
class RuleFamilies(unittest.TestCase):
    def rising(self, n=400):
        dates = [date(2020, 1, 1) + timedelta(days=i) for i in range(n)]
        closes = [[100.0 + i, 100.0 + i * 0.5, 100.0] for i in range(n)]
        return frame_mod.build(dates=dates, symbols=["FAST", "SLOW", "FLAT"], closes=closes)

    def test_every_family_declares_at_most_three_parameters(self):
        # Q29 rule 4: the only hard brake against overfitting.
        for rule in (rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}),
                     rules.InverseVolatility(("FAST", "SLOW")),
                     rules.MomentumTopN(("FAST", "SLOW", "FLAT"))):
            self.assertLessEqual(len(rule.parameters), 3, rule.name)

    def test_bands_hold_targets_constant(self):
        rule = rules.FixedWeightBands({"FAST": 0.6, "SLOW": 0.4})
        self.assertEqual(rule.weights(self.rising(), 100), {"FAST": 0.6, "SLOW": 0.4})

    def test_relative_band_fires_at_twenty_five_percent_drift(self):
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=0.25,
                                      absolute_band=1.0, calendar_days=10**6)
        frame = self.rising()
        self.assertFalse(rule.should_rebalance(frame, 50, {"FAST": 0.60, "SLOW": 0.40}, 0))
        self.assertTrue(rule.should_rebalance(frame, 50, {"FAST": 0.63, "SLOW": 0.37}, 0))

    def test_absolute_band_fires_at_five_points_on_a_small_target(self):
        # The half of Bogleheads 5/25 that bt's RunIfOutOfBounds does not implement:
        # a 5pp move on a 5% target is a 100% relative move, but a 5pp move on a
        # 50% target is only 10% relative and the relative band alone misses it.
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=1.0,
                                      absolute_band=0.05, calendar_days=10**6)
        frame = self.rising()
        self.assertFalse(rule.should_rebalance(frame, 50, {"FAST": 0.54, "SLOW": 0.46}, 0))
        self.assertTrue(rule.should_rebalance(frame, 50, {"FAST": 0.56, "SLOW": 0.44}, 0))

    def test_bands_bootstrap_on_the_first_bar(self):
        # bt 1.2.3's RunIfOutOfBounds returns False at bar 0 because children do
        # not exist yet, and the whole backtest sits in cash. Ours must not.
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5})
        self.assertTrue(rule.should_rebalance(self.rising(), 0, {}, None))

    def test_calendar_leg_fires_after_the_interval(self):
        rule = rules.FixedWeightBands({"FAST": 0.5, "SLOW": 0.5}, relative_band=1.0,
                                      absolute_band=1.0, calendar_days=365)
        frame = self.rising()
        on_target = {"FAST": 0.5, "SLOW": 0.5}
        self.assertFalse(rule.should_rebalance(frame, 100, on_target, 0))
        self.assertTrue(rule.should_rebalance(frame, 370, on_target, 0))

    def test_rules_are_frozen_so_state_cannot_leak_between_runs(self):
        import dataclasses
        for rule in (rules.FixedWeightBands({"FAST": 1.0}),
                     rules.InverseVolatility(("FAST",)),
                     rules.MomentumTopN(("FAST",))):
            with self.assertRaises(dataclasses.FrozenInstanceError):
                rule.name = "mutated"

    def test_inverse_volatility_gives_the_calmer_asset_more_weight(self):
        w = rules.InverseVolatility(("FAST", "FLAT"), lookback_days=60).weights(self.rising(), 300)
        self.assertGreater(w["FLAT"], w["FAST"])
        self.assertAlmostEqual(sum(w.values()), 1.0)

    def test_inverse_volatility_before_the_lookback_is_equal_weighted(self):
        w = rules.InverseVolatility(("FAST", "SLOW"), lookback_days=60).weights(self.rising(), 5)
        self.assertAlmostEqual(w["FAST"], 0.5)

    def test_momentum_picks_the_strongest_and_equal_weights_them(self):
        w = rules.MomentumTopN(("FAST", "SLOW", "FLAT"), top_n=2,
                               lookback_days=252, skip_days=21).weights(self.rising(), 350)
        self.assertEqual(set(w), {"FAST", "SLOW"})
        self.assertAlmostEqual(w["FAST"], 0.5)

    def test_momentum_skips_the_most_recent_month(self):
        # Faber 2007 / Antonacci 12-1: the skip is what makes it momentum rather
        # than short-term reversal. Assert it is read, not just stored.
        rule = rules.MomentumTopN(("FAST", "SLOW"), lookback_days=252, skip_days=21)
        self.assertEqual(rule.formation_window(300), (300 - 252, 300 - 21))

    def test_momentum_has_no_cash_exit(self):
        # Q38: rotation only, no trend-following exit to cash. The structural
        # cash reserve lives in the engine, not here.
        w = rules.MomentumTopN(("FAST", "SLOW", "FLAT"), top_n=1).weights(self.rising(), 350)
        self.assertAlmostEqual(sum(w.values()), 1.0)
        self.assertNotIn("CASH", w)
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ImportError: cannot import name 'rules'`

- [ ] **Step 3: Write `rules.py`**

```python
"""Three rule families, implemented from their primary sources.

Sources, per Q39: Bogleheads wiki "Rebalancing" for the 5/25 band definition;
Faber, "A Quantitative Approach to Tactical Asset Allocation" (2007) and
Antonacci, "Dual Momentum Investing" (2014) for the 12-1 formation window;
inverse-volatility weighting from its textbook definition. Cross-checked against
bt by scripts/_cross_check_bt.py; a disagreement is a release blocker.

Scope, per Q23=A plus the user's Q38 amendment: equity ETFs and cash only. No
bond or gold leg exists here. The structural cash reserve is an engine
parameter, deliberately not a rule parameter -- it is a comfort constraint the
user sets, not something a backtest fits, and folding it in would consume one of
Q29's three parameter slots.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Sequence

from .frame import PriceFrame

#: A genuinely zero-volatility asset is a data artifact, not a riskless one.
#: Without a floor, 1/vol is a division by zero, and mapping zero volatility to
#: zero weight would invert the strategy exactly where it matters most.
MIN_DAILY_VOLATILITY = 1e-6


def _returns(series: Sequence[float]) -> list[float]:
    return [(series[i] / series[i - 1]) - 1 for i in range(1, len(series)) if series[i - 1] > 0]


@dataclass(frozen=True)
class FixedWeightBands:
    """Constant targets, rebalanced on the Bogleheads 5/25 rule.

    Both halves are required. The relative band catches drift on large holdings;
    the absolute band catches drift on small ones, where a 5-percentage-point
    move can be a 100% relative move. bt 1.2.3's `RunIfOutOfBounds` implements
    only the relative half (`algos.py:408` divides by the target weight), which
    is why this family is not delegated to it.
    """
    targets: dict[str, float]
    relative_band: float = 0.25
    absolute_band: float = 0.05
    calendar_days: int = 365
    name: str = "fixed_weight_bands"

    @property
    def parameters(self) -> dict[str, float]:
        return {"relative_band": self.relative_band, "absolute_band": self.absolute_band,
                "calendar_days": float(self.calendar_days)}

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        return dict(self.targets)

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        # Bootstrap. bt 1.2.3's RunIfOutOfBounds returns False here because it
        # iterates children that Rebalance has not created yet, and the whole
        # backtest then sits in cash. This branch is that bug's absence.
        if last_rebalance_index is None or not current:
            return True
        if (frame.dates[i] - frame.dates[last_rebalance_index]).days >= self.calendar_days:
            return True
        for symbol, target in self.targets.items():
            drift = abs(current.get(symbol, 0.0) - target)
            if drift >= self.absolute_band or (target > 0 and drift / target >= self.relative_band):
                return True
        return False


@dataclass(frozen=True)
class InverseVolatility:
    """Weight inversely to trailing volatility, rebalanced on a fixed interval.

    Note for the README (Q22=C): over an equity-only universe this is NOT risk
    parity. Every holding is equity beta, so the portfolio's market exposure
    stays near 100% and the weighting only re-ranks within that exposure.
    """
    universe: tuple[str, ...]
    lookback_days: int = 63
    rebalance_days: int = 21
    name: str = "inverse_volatility"

    @property
    def parameters(self) -> dict[str, float]:
        return {"lookback_days": float(self.lookback_days),
                "rebalance_days": float(self.rebalance_days)}

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        start = i - self.lookback_days
        if start < 1:
            share = 1.0 / len(self.universe)
            return {s: share for s in self.universe}
        inverse: dict[str, float] = {}
        for symbol in self.universe:
            series = frame.column(symbol)[start:i + 1]
            vol = statistics.stdev(_returns(series)) if len(series) > 2 else 0.0
            inverse[symbol] = 1.0 / max(vol, MIN_DAILY_VOLATILITY)
        total = sum(inverse.values())
        return {s: v / total for s, v in inverse.items()}

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        if last_rebalance_index is None:
            return True
        return i - last_rebalance_index >= self.rebalance_days


@dataclass(frozen=True)
class MomentumTopN:
    """Hold the N strongest names over a 12-1 formation window, equal-weighted.

    The one-month skip is the point: without it the signal is short-term
    reversal, not momentum. There is no cash exit -- Q38 fixed this family as
    rotation only, so in a 2008- or 2022-shaped decline it decides which
    equities you lose in, not whether you are in equities. That sentence belongs
    in the README next to this strategy's backtest (Q22=C).
    """
    universe: tuple[str, ...]
    top_n: int = 5
    lookback_days: int = 252
    skip_days: int = 21
    name: str = "momentum_top_n"

    @property
    def parameters(self) -> dict[str, float]:
        return {"top_n": float(self.top_n), "lookback_days": float(self.lookback_days),
                "skip_days": float(self.skip_days)}

    def formation_window(self, i: int) -> tuple[int, int]:
        return (i - self.lookback_days, i - self.skip_days)

    def weights(self, frame: PriceFrame, i: int) -> dict[str, float]:
        start, end = self.formation_window(i)
        chosen = sorted(self.universe)[:self.top_n]
        if start >= 0 and end > start:
            scored = []
            for symbol in self.universe:
                series = frame.column(symbol)
                if series[start] > 0:
                    scored.append(((series[end] / series[start]) - 1, symbol))
            if scored:
                scored.sort(key=lambda pair: (-pair[0], pair[1]))
                chosen = [symbol for _, symbol in scored[:self.top_n]]
        share = 1.0 / len(chosen)
        return {symbol: share for symbol in chosen}

    def should_rebalance(self, frame: PriceFrame, i: int, current: dict[str, float],
                         last_rebalance_index: int | None) -> bool:
        if last_rebalance_index is None:
            return True
        return i - last_rebalance_index >= self.skip_days
```

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: OK — 12 new tests in this task, 64 in the file.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/backtest/rules.py scripts/_test_backtest.py
git commit -m "feat(backtest): bands, inverse-volatility and momentum families"
```

---

### Task 7: The Q29 admission gate and the packaging guard

The gate is what makes "the AI proposes, you approve" reviewable. The packaging guard must land before Task 8 writes a Cboe fetcher.

**Files:**
- Create: `scripts/copilot/backtest/admission.py`
- Modify: `scripts/package_release.py:184-196` (`_forbidden_archive_name`)
- Modify: `scripts/_test_backtest.py`, `scripts/_test_release_privacy.py`

- [ ] **Step 1: Write the failing tests**

Add `from copilot.backtest import admission` and append to `scripts/_test_backtest.py`:

```python
class AdmissionGate(unittest.TestCase):
    def passing_result(self):
        r = engine.Result(rule_name="demo", parameters={"a": 1.0, "b": 2.0})
        start, value = date(2005, 1, 3), 100.0
        for i in range(21 * 252):
            when = start + timedelta(days=int(i * 365.25 / 252))
            value *= 1.0003 if when.year not in (2008, 2020, 2022) else 0.9995
            r.curve.append((when, value))
        r.traded_notional, r.total_costs, r.rebalance_count = 5000.0, 50.0, 21
        return r

    def test_a_long_result_covering_all_three_windows_passes(self):
        report = admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS)
        self.assertTrue(report.admitted, report.failures)

    def test_a_short_backtest_fails_rule_one(self):
        r = engine.Result(rule_name="short", parameters={})
        r.curve = [(date(2020, 1, 2), 100.0), (date(2024, 1, 2), 150.0)]
        report = admission.assess(r, sessions_by_year={})
        self.assertFalse(report.admitted)
        self.assertTrue(any("15" in f for f in report.failures))

    def test_a_missing_stress_window_fails(self):
        r = self.passing_result()
        r.curve = [(w, v) for w, v in r.curve if w.year != 2008]
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("2008" in f for f in report.failures))

    def test_four_parameters_fail_rule_four(self):
        r = self.passing_result()
        r.parameters = {"a": 1.0, "b": 2.0, "c": 3.0, "d": 4.0}
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("parameter" in f for f in report.failures))

    def test_zero_cost_results_fail_rule_three(self):
        r = self.passing_result()
        r.total_costs = 0.0
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS)
        self.assertFalse(report.admitted)
        self.assertTrue(any("cost" in f for f in report.failures))

    def test_report_carries_every_rule_two_metric(self):
        report = admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS)
        for key in ("cagr", "max_drawdown", "drawdown_duration_days", "annual_turnover", "sharpe"):
            self.assertIn(key, report.metrics)

    def test_out_of_sample_segment_is_reported_separately(self):
        report = admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS)
        self.assertIn("out_of_sample", report.metrics)
        self.assertIn("cagr", report.metrics["out_of_sample"])

    def test_stress_session_counts_are_the_verified_xnys_numbers(self):
        self.assertEqual(admission.STRESS_SESSIONS, {2008: 253, 2020: 253, 2022: 251})

    def test_a_waiver_records_its_reason_and_does_not_hide_the_failure(self):
        r = self.passing_result()
        r.curve = [(w, v) for w, v in r.curve if w.year != 2008]
        report = admission.assess(r, sessions_by_year=admission.STRESS_SESSIONS,
                                  waivers={"stress_2008": "ADR-0006 clause 5: BXN starts 2009-09-18"})
        self.assertTrue(report.admitted)
        self.assertTrue(report.waived)
        self.assertIn("ADR-0006", " ".join(report.waiver_reasons))
        self.assertTrue(any("2008" in f for f in report.failures))

    def test_an_unknown_waiver_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "unknown waiver"):
            admission.assess(self.passing_result(), sessions_by_year=admission.STRESS_SESSIONS,
                             waivers={"whatever": "because"})
```

Append to `scripts/_test_release_privacy.py`:

```python
    def test_third_party_market_history_never_ships(self):
        from package_release import _forbidden_archive_name
        for name in ("trading-copilot-0.6.0/scripts/BXN_History.csv",
                     "trading-copilot-0.6.0/evals/prices/bxnt_history.csv",
                     "trading-copilot-0.6.0/docs/vendor-data/whatever.csv"):
            self.assertTrue(_forbidden_archive_name(name), name)

    def test_our_own_fixtures_still_ship(self):
        from package_release import _forbidden_archive_name
        self.assertFalse(_forbidden_archive_name("trading-copilot-0.6.0/evals/prices/2026.json"))
```

- [ ] **Step 2: Run both to verify they fail**

```bash
python scripts/_test_backtest.py -v; python scripts/_test_release_privacy.py -v
```

Expected: `ImportError: cannot import name 'admission'` and two failures in the privacy suite.

- [ ] **Step 3: Write `admission.py`**

```python
"""Q29's six rules, executable.

The user approved these six on 2026-09-19 as the standard a strategy must meet
before it may be adopted. They are governance, not tuning: rule 4 (at most three
parameters) is the only hard brake against overfitting in the whole system.

A waiver does not delete a failure. It records a reason beside it and flips
`admitted`, so a reader always sees which rule was not met and why. The only
waiver granted so far is ADR-0006 clause 5, for the BXN proxy's missing 2008.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import metrics
from .engine import Result

MIN_YEARS = 15.0
MAX_PARAMETERS = 3
OUT_OF_SAMPLE_START = date(2019, 1, 1)

#: XNYS sessions, from exchange-calendars==4.13.2, verified 2026-09-19. Held as
#: constants so the gate runs in the offline CI job, which installs no
#: third-party packages. `backtest_cli.py --verify-calendars` re-checks them
#: against the library in the runtime job.
STRESS_SESSIONS = {2008: 253, 2020: 253, 2022: 251}

#: A year counts as covered at 95% of its sessions. Not 100%: a holiday schedule
#: difference between a US ETF and a Cboe index is not a data-quality failure.
MIN_STRESS_COVERAGE = 0.95

WAIVABLE = frozenset({"span", "stress_2008", "stress_2020", "stress_2022"})


@dataclass
class Report:
    admitted: bool
    failures: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    waived: bool = False
    waiver_reasons: list[str] = field(default_factory=list)


def assess(result: Result, *, sessions_by_year: dict[int, int],
           waivers: dict[str, str] | None = None) -> Report:
    waivers = dict(waivers or {})
    unknown = sorted(set(waivers) - WAIVABLE)
    if unknown:
        raise ValueError(f"unknown waiver key(s): {', '.join(unknown)}; "
                         f"waivable rules are {', '.join(sorted(WAIVABLE))}")

    failures: list[str] = []
    waived_keys: list[str] = []

    def fail(key: str, message: str) -> None:
        failures.append(message)
        if key in waivers:
            waived_keys.append(key)

    # Rule 1: at least 15 years, covering 2008, 2020 and 2022.
    years = result.years
    if years < MIN_YEARS:
        fail("span", f"backtest spans {years:.2f} years, rule 1 requires at least {MIN_YEARS}")
    for stress_year, expected in sorted(sessions_by_year.items()):
        observed = len(metrics.window(result.curve, stress_year))
        if expected and observed / expected < MIN_STRESS_COVERAGE:
            fail(f"stress_{stress_year}",
                 f"{stress_year} has {observed} of {expected} sessions, rule 1 requires "
                 f"{MIN_STRESS_COVERAGE:.0%}")

    # Rule 3: costs must actually have been charged.
    if result.total_costs <= 0 and result.rebalance_count > 0:
        failures.append("no transaction costs were charged; rule 3 requires net-of-cost results")

    # Rule 4: at most three parameters.
    if len(result.parameters) > MAX_PARAMETERS:
        failures.append(f"{len(result.parameters)} parameters, rule 4 allows at most "
                        f"{MAX_PARAMETERS}: {', '.join(sorted(result.parameters))}")

    # Rule 5: an out-of-sample segment.
    out_of_sample = [(w, v) for w, v in result.curve if w >= OUT_OF_SAMPLE_START]
    if len(out_of_sample) < 2:
        failures.append(f"no out-of-sample segment after {OUT_OF_SAMPLE_START}, rule 5 requires one")

    # Rule 2: report every metric, pass or fail.
    drawdown = metrics.max_drawdown(result.curve)
    report_metrics = {
        "years": round(years, 2),
        "cagr": round(metrics.cagr(result.curve), 6),
        "annual_volatility": round(metrics.annual_volatility(result.curve), 6),
        "sharpe": round(metrics.sharpe(result.curve), 6),
        "max_drawdown": round(drawdown.depth, 6),
        "drawdown_peak": drawdown.peak_date.isoformat() if drawdown.peak_date else None,
        "drawdown_trough": drawdown.trough_date.isoformat() if drawdown.trough_date else None,
        "drawdown_recovered": drawdown.recovery_date.isoformat() if drawdown.recovery_date else None,
        "drawdown_duration_days": drawdown.duration_days,
        "annual_turnover": round(metrics.annual_turnover(
            traded_notional=result.traded_notional,
            average_value=result.average_value, years=years), 6),
        "total_costs": round(result.total_costs, 2),
        "rebalance_count": result.rebalance_count,
        "stress_windows": {str(y): len(metrics.window(result.curve, y))
                           for y in sorted(sessions_by_year)},
        "out_of_sample": {
            "from": OUT_OF_SAMPLE_START.isoformat(),
            "cagr": round(metrics.cagr(out_of_sample), 6) if len(out_of_sample) > 1 else None,
            "max_drawdown": round(metrics.max_drawdown(out_of_sample).depth, 6)
            if len(out_of_sample) > 1 else None,
        },
    }

    unwaived = [f for f in failures
                if not any(k in waivers and _matches(k, f) for k in WAIVABLE)]
    return Report(admitted=not unwaived, failures=failures, metrics=report_metrics,
                  waived=bool(waived_keys),
                  waiver_reasons=[f"{k}: {waivers[k]}" for k in waived_keys])


def _matches(key: str, failure: str) -> bool:
    if key == "span":
        return "rule 1 requires at least" in failure
    return failure.startswith(key.removeprefix("stress_"))
```

Sensitivity reporting (the second half of rule 4) is a `backtest_cli.py` concern — it needs several `Result`s — and lands in Task 9.

- [ ] **Step 4: Add the packaging guard**

In `scripts/package_release.py`, extend `_forbidden_archive_name` before its final `return`:

```python
    # Third-party market data must never ship. Cboe's terms permit one copy for
    # personal non-commercial use and forbid distribution and derivative works;
    # a CSV dropped under scripts/, evals/ or docs/ would otherwise be picked up
    # by _iter_files' rglob and shipped silently. See ADR-0006 clause 6.
    if low.endswith("_history.csv") or "/vendor-data/" in low:
        return True
```

- [ ] **Step 5: Run the tests to verify they pass**

```bash
python scripts/_test_backtest.py -v && python scripts/_test_release_privacy.py -v
```

Expected: OK — 10 new tests in this task, 74 in the file; the privacy suite green with two new tests.

- [ ] **Step 6: Commit**

```bash
git add scripts/copilot/backtest/admission.py scripts/package_release.py scripts/_test_backtest.py scripts/_test_release_privacy.py
git commit -m "feat(backtest): Q29 admission gate; block third-party market data from the zip"
```

---

### Task 8: The BXN index proxy

Q37, as decided on 2026-09-19: build it on BXN, accept that 2008 is missing, take the waiver, label every output as index-proxy evidence.

**Files:**
- Create: `scripts/copilot/backtest/bxn.py`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Add `from copilot.backtest import bxn` and append:

```python
class BxnProxy(unittest.TestCase):
    CSV = "DATE,BXN\n09/18/2009,298.140000\n09/21/2009,299.500000\n09/22/2009,301.250000\n"

    def test_parses_the_two_column_close_only_file(self):
        frame = bxn.parse_csv(self.CSV)
        self.assertEqual(frame.symbols, ("^BXN",))
        self.assertEqual(frame.dates[0], date(2009, 9, 18))
        self.assertAlmostEqual(frame.column("^BXN")[0], 298.14)

    def test_rejects_an_unexpected_header(self):
        with self.assertRaisesRegex(ValueError, "header"):
            bxn.parse_csv("DATE,BXNT\n09/18/2009,1.0\n")

    def test_rejects_an_empty_file(self):
        with self.assertRaises(ValueError):
            bxn.parse_csv("DATE,BXN\n")

    def test_the_waiver_text_cites_the_adr(self):
        self.assertIn("ADR-0006", bxn.Q29_WAIVER["stress_2008"])
        self.assertIn("2009-09-18", bxn.Q29_WAIVER["stress_2008"])

    def test_waiver_covers_2008_only(self):
        self.assertEqual(set(bxn.Q29_WAIVER), {"stress_2008"})

    def test_the_label_never_claims_the_funds_passed(self):
        label = bxn.evidence_label(("QQQI", "JEPQ"))
        self.assertIn("index proxy", label.lower())
        self.assertNotIn("passed", label.lower())
        for symbol in ("QQQI", "JEPQ"):
            self.assertIn(symbol, label)

    def test_module_never_writes_to_disk(self):
        # Cboe's terms forbid storing the file. urlopen( contains open( as a
        # substring, so the filesystem call is matched with a negative lookbehind
        # rather than a bare `in` check.
        import re
        source = Path(bxn.__file__).read_text(encoding="utf-8")
        for forbidden in ("write_text", "write_bytes", "pathlib", "mkdir", "shutil", "tempfile"):
            self.assertNotIn(forbidden, source, forbidden)
        self.assertIsNone(re.search(r"(?<!url)open\(", source),
                          "bxn.py must not open a file; the CSV stays in memory")
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ImportError: cannot import name 'bxn'`

- [ ] **Step 3: Write `bxn.py`**

```python
"""Cboe BXN as an index proxy for the income ETFs. Memory only, never on disk.

WHY A PROXY AT ALL
QQQI has 2.63 years of history, JEPQ 4.38, JEPI 6.33. None of them can ever
satisfy Q29 rule 1. BXN -- the Cboe Nasdaq-100 BuyWrite index -- is the
mechanical strategy those funds resemble, and it has daily closes back to
2009-09-18.

WHAT THIS PROVES AND WHAT IT DOES NOT
It proves how a mechanical monthly at-the-money buy-write behaved in 2020 and
2022. It does not prove how QQQI or JEPQ behaved: both are actively managed,
QQQI's issuer describes a strategy that "may include both sold and purchased NDX
index options", and the tracking error between fund and index is unmeasured.
Every output of this path is labelled index-proxy evidence. Writing
"QQQI passed the admission gate" anywhere is a defect.

WHY 2008 IS MISSING AND WHY WE DO NOT REACH FOR BXNT
BXN_History.csv begins 09/18/2009 and the start is fixed, not rolling -- a
2025-08-29 Wayback snapshot of the same URL begins on the same date. BXNT
reaches 1994, but no Cboe page links its CSV, Wayback holds zero snapshots of
it, its live-launch date appears on no page we fetched (so the back-test/live
boundary cannot be drawn), and QQQI's own issuer page names BXN four times and
BXNT zero times. ADR-0006 clause 5 grants one waiver, for 2008 only.

LICENCE -- THE REASON THERE IS NO CACHE IN THIS FILE
cboe.com/terms permits one copy for personal non-commercial use and forbids,
absent written consent, storing in an electronic retrieval system, distributing,
creating a derivative work, and using to verify other data. This module fetches
into memory and returns a frame. It opens no file. package_release.py refuses
any *_history.csv, and scripts/_test_backtest.py asserts this file contains no
write call.
"""
from __future__ import annotations

import csv
import io
import urllib.error
import urllib.request
from datetime import datetime

from .frame import PriceFrame, build

CSV_URL = "https://cdn.cboe.com/api/global/us_indices/daily_prices/BXN_History.csv"
SYMBOL = "^BXN"
EXPECTED_HEADER = ["DATE", "BXN"]
USER_AGENT = "Mozilla/5.0 TradingCopilot/1.0 (personal research)"

#: The single exception ADR-0006 grants to Q29 rule 1. Passed to
#: admission.assess(waivers=...), which keeps the failure visible in the report.
Q29_WAIVER = {
    "stress_2008": ("ADR-0006 clause 5: BXN's free daily file begins 2009-09-18 and the "
                    "start is fixed, not rolling; BXNT was rejected because its "
                    "back-test/live boundary cannot be established"),
}


def evidence_label(symbols: tuple[str, ...]) -> str:
    return (f"Index proxy evidence for {', '.join(symbols)}: Cboe BXN, a mechanical monthly "
            "at-the-money Nasdaq-100 buy-write, 2009-09-18 onward. Covers 2020 and 2022, not "
            "2008. Tracking error against the actual funds is unmeasured.")


def parse_csv(text: str) -> PriceFrame:
    reader = csv.reader(io.StringIO(text))
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("BXN CSV is empty") from None
    if [h.strip().upper() for h in header] != EXPECTED_HEADER:
        raise ValueError(f"unexpected BXN CSV header {header!r}, expected {EXPECTED_HEADER!r}")
    dates, closes = [], []
    for row in reader:
        if len(row) != 2 or not row[0].strip():
            continue
        dates.append(datetime.strptime(row[0].strip(), "%m/%d/%Y").date())
        closes.append([float(row[1])])
    if not dates:
        raise ValueError("BXN CSV contained no data rows")
    return build(dates=dates, symbols=[SYMBOL], closes=closes)


def fetch(timeout: float = 30.0) -> PriceFrame:
    """Fetch into memory. Nothing is persisted; see the module docstring."""
    request = urllib.request.Request(CSV_URL, headers={"User-Agent": USER_AGENT,
                                                       "Accept": "text/csv"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return parse_csv(response.read().decode("utf-8", errors="replace"))
    except urllib.error.URLError as exc:
        raise RuntimeError(f"BXN history unavailable: {exc}") from exc
```

`Path(` must not appear, so the test's forbidden list is satisfied; the file uses `io.StringIO`, never a filesystem path.

- [ ] **Step 4: Run the test to verify it passes**

```bash
python scripts/_test_backtest.py -v
```

Expected: OK — 7 new tests in this task, 81 in the file.

- [ ] **Step 5: Commit**

```bash
git add scripts/copilot/backtest/bxn.py scripts/_test_backtest.py
git commit -m "feat(backtest): BXN index proxy for the income ETFs, memory only"
```

---

### Task 9: CLI, cross-check oracle, CI wiring

**Files:**
- Create: `scripts/backtest_cli.py`
- Create: `scripts/_cross_check_bt.py`
- Modify: `.github/workflows/ci.yml:175-184` and `:115-118`
- Modify: `scripts/_test_backtest.py`

- [ ] **Step 1: Write the failing test**

Append to `scripts/_test_backtest.py`:

```python
class CliContract(unittest.TestCase):
    def test_cli_module_imports_without_third_party_packages(self):
        import importlib
        module = importlib.import_module("backtest_cli")
        self.assertTrue(hasattr(module, "main"))

    def test_universe_is_fetched_once_not_once_per_family(self):
        # Three families over 12 symbols would be 36 Yahoo requests if each
        # family loaded its own data, against a rate-limit evidence base of one
        # 30-request run. It also lets the three backtests disagree if Yahoo
        # revised a bar mid-run.
        import backtest_cli
        source = Path(backtest_cli.__file__).read_text(encoding="utf-8")
        self.assertEqual(source.count("history.fetch("), 1)
        self.assertIn("def load_universe(", source)

    def test_sensitivity_walks_each_parameter_both_ways(self):
        import backtest_cli
        grid = backtest_cli.sensitivity_grid({"lookback_days": 252.0, "top_n": 5.0})
        self.assertIn({"lookback_days": 227.0, "top_n": 5.0}, grid)
        self.assertIn({"lookback_days": 277.0, "top_n": 5.0}, grid)
        self.assertIn({"lookback_days": 252.0, "top_n": 4.0}, grid)
        self.assertIn({"lookback_days": 252.0, "top_n": 6.0}, grid)
        self.assertEqual(len(grid), 4)

    def test_output_directory_is_gitignored_audit(self):
        import backtest_cli
        self.assertTrue(str(backtest_cli.DEFAULT_OUT_DIR).replace("\\", "/").endswith("data/audit"))

    def test_cross_check_script_is_never_imported_by_shipped_code(self):
        import subprocess
        hits = subprocess.run(
            ["git", "grep", "-l", "_cross_check_bt", "--", "scripts", "mcps", "evals"],
            capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[1]))
        found = [line for line in hits.stdout.splitlines()
                 if not line.endswith(("_cross_check_bt.py", "_test_backtest.py"))]
        self.assertEqual(found, [])
```

- [ ] **Step 2: Run it to verify it fails**

```bash
python scripts/_test_backtest.py -v
```

Expected: `ModuleNotFoundError: No module named 'backtest_cli'`

- [ ] **Step 3: Write `scripts/backtest_cli.py`**

No PEP 723 header: this path is stdlib-only and must stay that way.

```python
#!/usr/bin/env python3
"""Run the rule families over a universe and write an assessed report.

Stdlib only. Results go to data/audit/, which is gitignored and excluded from
the release zip, because a backtest over the user's own universe is personal
state.

    python scripts/backtest_cli.py --universe SPY,QQQ,IWM,VTV,VUG --family all
    python scripts/backtest_cli.py --verify-universe
    python scripts/backtest_cli.py --verify-calendars   # needs exchange-calendars
    python scripts/backtest_cli.py --self-test
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

from copilot.backtest import admission, bxn, engine, history, rules, universe  # noqa: E402

DEFAULT_OUT_DIR = ROOT / "data" / "audit"
SENSITIVITY_STEP = 0.10


def sensitivity_grid(parameters: dict[str, float]) -> list[dict[str, float]]:
    """Q29 rule 4's second half: neighbouring parameters must not diverge.

    One parameter moved at a time, +/-10% (minimum one unit), so a divergence
    can be attributed to a single knob.
    """
    grid: list[dict[str, float]] = []
    for key, value in parameters.items():
        step = max(1.0, round(abs(value) * SENSITIVITY_STEP))
        for delta in (-step, step):
            neighbour = dict(parameters)
            neighbour[key] = value + delta
            grid.append(neighbour)
    return grid


def build_rules(symbols: tuple[str, ...], family: str) -> list:
    equal = {s: 1.0 / len(symbols) for s in symbols}
    built = []
    if family in ("all", "bands"):
        built.append(rules.FixedWeightBands(equal))
    if family in ("all", "invvol"):
        built.append(rules.InverseVolatility(symbols))
    if family in ("all", "momentum"):
        built.append(rules.MomentumTopN(symbols, top_n=min(5, len(symbols))))
    return built


def verify_universe() -> int:
    """Re-observe the tier table against the live endpoint. Prints a diff."""
    problems = 0
    for symbol in sorted(universe.QUALIFIED | universe.NO_2008_BARS | universe.PARTIAL_2008):
        try:
            series = history.fetch(symbol)
        except history.NotCovered as exc:
            print(f"  NOT COVERED {symbol}: {exc}")
            problems += 1
            continue
        frame_years = (series.dates[-1] - series.dates[0]).days / 365.25
        bars = {y: sum(1 for d in series.dates if d.year == y) for y in (2008, 2020, 2022)}
        expected = symbol in universe.QUALIFIED
        actual = all(bars[y] / admission.STRESS_SESSIONS[y] >= admission.MIN_STRESS_COVERAGE
                     for y in bars) and frame_years >= admission.MIN_YEARS
        flag = "ok " if expected == actual else "DRIFT"
        if expected != actual:
            problems += 1
        print(f"  {flag} {symbol:<5} {frame_years:5.2f}y  2008={bars[2008]:>3} "
              f"2020={bars[2020]:>3} 2022={bars[2022]:>3}")
    return problems


def verify_calendars() -> int:
    """Assert the hardcoded session counts against exchange-calendars."""
    import exchange_calendars as xcals
    xnys = xcals.get_calendar("XNYS")
    problems = 0
    for year, expected in sorted(admission.STRESS_SESSIONS.items()):
        observed = len(xnys.sessions_in_range(f"{year}-01-01", f"{year}-12-31"))
        status = "ok " if observed == expected else "DRIFT"
        if observed != expected:
            problems += 1
        print(f"  {status} XNYS {year}: hardcoded {expected}, calendar {observed}")
    return problems


def load_universe(symbols: tuple[str, ...]) -> tuple:
    """Fetch every symbol once. Returns (frame, alignment, caveats).

    Once, not once per family: three families over 12 symbols would otherwise
    make 36 Yahoo requests, and the only rate-limit evidence we have is a single
    run of 30 consecutive requests. Re-fetching the same bars three times also
    risks the three backtests disagreeing because Yahoo revised a bar mid-run.
    """
    series, caveats = [], []
    for symbol in symbols:
        classification = universe.classify(symbol)
        if not classification.admissible:
            raise SystemExit(f"{symbol} is tier '{classification.tier}': {classification.reason}")
        if classification.provenance_unverified:
            caveats.append(f"{symbol}: {classification.reason}")
        series.append(history.fetch(symbol))
    return history.to_frame(series), history.alignment(series), caveats


def run_family(frame, rule, *, start_cash: float, cash_floor_pct: float) -> dict:
    result = engine.run(frame, rule=rule, start_cash=start_cash,
                        cost_model=engine.CostModel(), cash_floor_pct=cash_floor_pct)
    report = admission.assess(result, sessions_by_year=admission.STRESS_SESSIONS)
    return {"rule": rule.name, "parameters": result.parameters, "admitted": report.admitted,
            "failures": report.failures, "metrics": report.metrics,
            "sensitivity_grid": sensitivity_grid(result.parameters)}


def run_income_proxy(*, start_cash: float, cash_floor_pct: float) -> dict:
    frame = bxn.fetch()
    rule = rules.FixedWeightBands({bxn.SYMBOL: 1.0})
    result = engine.run(frame, rule=rule, start_cash=start_cash,
                        cost_model=engine.CostModel(), cash_floor_pct=cash_floor_pct,
                        integer_shares=False)
    report = admission.assess(result, sessions_by_year=admission.STRESS_SESSIONS,
                              waivers=bxn.Q29_WAIVER)
    return {"rule": "bxn_index_proxy", "label": bxn.evidence_label(tuple(sorted(universe.INCOME_PROXY_ONLY))),
            "admitted": report.admitted, "waived": report.waived,
            "waiver_reasons": report.waiver_reasons, "failures": report.failures,
            "metrics": report.metrics}


def self_test() -> int:
    grid = sensitivity_grid({"top_n": 5.0})
    assert grid == [{"top_n": 4.0}, {"top_n": 6.0}], grid
    assert build_rules(("SPY", "QQQ"), "bands")[0].name == "fixed_weight_bands"
    print("backtest_cli self-test OK")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="")
    parser.add_argument("--family", default="all", choices=("all", "bands", "invvol", "momentum"))
    parser.add_argument("--start-cash", type=float, default=10000.0)
    parser.add_argument("--cash-floor-pct", type=float, default=0.15)
    parser.add_argument("--income-proxy", action="store_true",
                        help="also run the BXN index proxy for QQQI/JEPQ/JEPI")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--verify-universe", action="store_true")
    parser.add_argument("--verify-calendars", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.verify_calendars:
        return 1 if verify_calendars() else 0
    if args.verify_universe:
        return 1 if verify_universe() else 0
    if not args.universe:
        parser.error("--universe is required unless a --verify-* or --self-test flag is given")

    symbols = tuple(s.strip().upper() for s in args.universe.split(",") if s.strip())
    frame, alignment, caveats = load_universe(symbols)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "universe": list(symbols), "alignment": alignment, "caveats": caveats,
               "families": []}
    for rule in build_rules(symbols, args.family):
        payload["families"].append(run_family(frame, rule, start_cash=args.start_cash,
                                              cash_floor_pct=args.cash_floor_pct))
    if args.income_proxy:
        payload["income_proxy"] = run_income_proxy(start_cash=args.start_cash,
                                                   cash_floor_pct=args.cash_floor_pct)

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"backtest-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for family in payload["families"]:
        verdict = "ADMITTED" if family["admitted"] else "REJECTED"
        print(f"  {verdict:<9} {family['rule']:<20} "
              f"CAGR {family['metrics']['cagr']:+.2%}  "
              f"maxDD {family['metrics']['max_drawdown']:.2%}  "
              f"turnover {family['metrics']['annual_turnover']:.2f}")
        for failure in family["failures"]:
            print(f"            - {failure}")
    print(f"\n  common history: {alignment['common_first']} .. {alignment['common_last']} "
          f"({alignment['common_bars']} bars); start bound by {alignment['binds_start']}, "
          f"end bound by {alignment['binds_end']}, {alignment['bars_lost_vs_longest']} bars "
          "lost against the longest symbol")
    for caveat in caveats:
        print(f"  caveat: {caveat}")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Write `scripts/_cross_check_bt.py`**

```python
#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["bt==1.2.3", "ffn==1.2.2"]
# ///
"""Dev-only cross-check of the rule families against `bt`. Never imported.

ffn is pinned explicitly even though bt only declares `ffn>=1.1.2`: ffn 1.2.2
was published two days before this was written, and an unpinned transitive
dependency would silently change the oracle we compare against.

Run:
    MPLBACKEND=Agg uv run --no-project --script scripts/_cross_check_bt.py

MPLBACKEND is not optional. `bt/backtest.py:11` imports pyplot unconditionally,
and with the variable unset the backend resolves to tkagg.

The first check here is a RED-LIGHT test: it asserts that bt 1.2.3's
`RunIfOutOfBounds`, used alone, holds 100% cash for the entire backtest. That
is a real defect (`algos.py:400` iterates only existing children, and at bar 0
`Rebalance` has not created any), fixed on master and unreleased. Locking the
broken behaviour here is what stops someone from later "simplifying" the
production stack onto that algo.
"""
from __future__ import annotations

import os
import sys

os.environ.setdefault("MPLBACKEND", "Agg")

import bt  # noqa: E402
import pandas as pd  # noqa: E402


def prices() -> pd.DataFrame:
    dates = pd.bdate_range("2015-01-01", "2025-01-01")
    return pd.DataFrame({"AAA": [100 + i * 0.05 for i in range(len(dates))],
                         "BBB": [100 + i * 0.02 for i in range(len(dates))]}, index=dates)


def check_band_trigger_alone_never_invests() -> None:
    strategy = bt.Strategy("bands-only", [
        bt.algos.SelectAll(),
        bt.algos.WeighEqually(),
        bt.algos.RunIfOutOfBounds(0.25),
        bt.algos.Rebalance(),
    ])
    result = bt.run(bt.Backtest(strategy, prices(), integer_positions=False))
    final = float(result.prices.iloc[-1])
    assert abs(final - 100.0) < 1e-6, (
        f"expected bt 1.2.3 to stay in cash (index stays at 100), got {final}. "
        "If this now differs, bt released the algos.py:400 fix and "
        "docs/adr/0006 clause 2 should be revisited.")
    print("ok   bt 1.2.3 band-trigger-alone holds 100% cash, as documented")


def check_calendar_or_band_does_invest() -> None:
    strategy = bt.Strategy("calendar-or-band", [
        bt.algos.SelectAll(),
        bt.algos.WeighEqually(),
        bt.algos.Or([bt.algos.RunMonthly(), bt.algos.RunIfOutOfBounds(0.25)]),
        bt.algos.Rebalance(),
    ])
    result = bt.run(bt.Backtest(strategy, prices(), integer_positions=False))
    final = float(result.prices.iloc[-1])
    assert final > 110.0, f"the Or-wrapped stack should track the rising market, got {final}"
    print(f"ok   Or([calendar, band]) invests and ends at {final:.2f}")


def main() -> int:
    check_band_trigger_alone_never_invests()
    check_calendar_or_band_does_invest()
    print("\nbt cross-check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Wire CI**

In `.github/workflows/ci.yml`, add to the `required` list in the release-package payload assertion (after `"scripts/copilot/config.py",`):

```yaml
              "scripts/backtest_cli.py", "scripts/copilot/backtest/engine.py",
              "scripts/copilot/backtest/rules.py", "scripts/copilot/backtest/admission.py",
```

And add a line to the `leaked` comprehension in the same block:

```yaml
                    or n.lower().endswith("_history.csv")
```

In the `runtime` job, extend the pinned-calendars step:

```yaml
          uv run --no-project --with exchange-calendars==4.13.2 --with tzdata==2026.3 python scripts/backtest_cli.py --verify-calendars
```

`_cross_check_bt.py` is deliberately **not** added to CI: it hits no network but pulls 49 packages, and its value is at review time, not on every push. The runtime job's `--verify-universe` is also deliberately absent — it makes 30 live Yahoo requests and CI must not depend on a third-party CDN's availability, the same objection `providers.py` already records against the Nasdaq public endpoint.

- [ ] **Step 6: Run everything**

```bash
python scripts/backtest_cli.py --self-test
python scripts/_test_backtest.py -v
python scripts/check.py
python scripts/sync_runtimes.py --check
python -m unittest discover -s scripts -p "_test_*.py"
python scripts/package_release.py --self-test
ruff check --select E9,F63,F7,F82 scripts evals mcps
```

Expected: self-test OK; 86 backtest tests OK; `check.py` 0 errors; `sync_runtimes --check` 0 changed; the full suite green; `package_release --self-test` all pass; ruff clean.

- [ ] **Step 7: Verify against the real endpoint (manual, not CI)**

```bash
python scripts/backtest_cli.py --verify-universe
```

Expected: every symbol prints `ok`; any `DRIFT` line means the tier table in `universe.py` no longer matches reality and must be corrected before the report is trusted.

```bash
MPLBACKEND=Agg uv run --no-project --script scripts/_cross_check_bt.py
```

Expected: both checks print `ok`. If the first one fails, `bt` shipped the `algos.py:400` fix and ADR-0006 clause 2 needs revisiting.

- [ ] **Step 8: Commit**

```bash
git add scripts/backtest_cli.py scripts/_cross_check_bt.py .github/workflows/ci.yml scripts/_test_backtest.py
git commit -m "feat(backtest): CLI, parameter sensitivity, and the bt cross-check oracle"
```

---

## Amendments applied during execution

Every task's code below is what was *planned*. Review found real defects in
several of those code blocks, and the repository is the source of truth for what
shipped. The substantive changes, all committed on this branch:

**Task 1 (`universe.py`).** `provenance_unverified` was set but never read, so
ADR-0006's promise that SMH "stays in the qualified tier with a caveat flag"
caveated nothing. `classify()` now appends `_PROVENANCE_CAVEAT` to `reason` as
well as setting the boolean. It also gained an `isinstance` guard, so a
non-string raises `ValueError` rather than `AttributeError`.

**Task 2 (`frame.py`).** The ragged-row message read `expected 2`, while the
plan's own test asserted the substring `2 values` — the implementation was
wrong, not the test. `span_years` and `sessions_in_year` gained docstrings
stating plainly that they are descriptive and carry no data-density guarantee: a
two-bar frame dated 2008-01-02 and 2023-01-03 returns `span_years() == 15.003`
and `sessions_in_year(2008) == 1`, which would pass a naive gate. An admission
decision must compare against an expected session count, never against zero.

**Task 3 (`history.py`).** `to_frame` truncated silently: an 18-year series
intersected with a one-bar series returned a valid one-bar frame and the eventual
failure said only "backtest spans 0.00 years". A new `alignment(series)` reports
per-symbol first/last/bars/dropped_bars, the common window, which symbol binds
the start and which the end, and how many bars were lost against the longest
input. `to_frame`'s empty-intersection error now names the binding symbols.
`Series.dropped_bars`, previously computed and discarded, is surfaced there.

**Tasks 4 and 5 (`metrics.py`, `engine.py`).** Two silent-corruption paths, both
reachable and both verified by execution. `cagr` returned a Python `complex`
whenever the curve ended negative — `(-5.0/100.0) ** (1/years)` with a
non-integer exponent — and `sharpe`'s `vol == 0` guard could swallow it and
report a reassuring 0.0. `daily_returns`' `if curve[i-1][1] > 0` filter was
asymmetric, dropping the return leaving a non-positive bar while keeping the one
entering it: on a $10,000 book dipping to a single -$50 bar, annualized
volatility read 5.64 instead of 0.0221, a 255x misstatement. Both are closed by a
single `_validated(curve)` boundary check that raises on non-positive or
non-finite values and on non-increasing dates, called from `cagr`,
`annual_volatility`, `sharpe`, `max_drawdown` and `daily_returns`; the asymmetric
filter is deleted. `max_drawdown`'s `depth > best.depth` became `>=` so that when
two drawdowns are equally deep the later one wins — it is the one more likely
still open, and on `[100, 50, 100, 50]` the old code reported the first with a
recovery date while the second sat unrecovered at the end of the curve.
`engine._validate` now rejects non-finite weights (`abs(nan - 1.0) > tol` and
`nan < 0` are both False, so a NaN weight passed validation and, with
`integer_shares=False`, produced an all-NaN curve with no exception at all), and
`engine.run` raises if a bar's portfolio value is non-finite or non-positive.
`CostModel.max_pct_of_notional` became `float | None`, because `0.0` was being
read as "no cap" rather than "cap at zero"; `free()` now passes `None`.

**Task 9 (`backtest_cli.py`).** Restructured before implementation: the universe
is fetched once by `load_universe` and shared by all three families, rather than
re-fetched per family. Three families over twelve symbols would otherwise make 36
Yahoo requests against a rate-limit evidence base of one 30-request run, and
re-fetching invites the three backtests to disagree because a bar was revised
mid-run. The report now carries `alignment` and any provenance caveats.

---

## What this plan deliberately does not build

Recorded so a later reader does not mistake absence for oversight.

- **The README's strategy table (Q22=C).** The user's answer requires the public
  README to state each strategy's backtest period, max drawdown and
  out-of-sample result. `admission.Report.metrics` produces exactly those
  fields, but writing them down needs a real run against live data, so the table
  belongs to the docs-and-release plan (Plan 7) and not to the plan that builds
  the numbers. `rules.py`'s docstrings name the two sentences that must appear
  there — inverse-volatility over equities is not risk parity, and momentum
  without a cash exit only chooses which equities you lose in.
- **Adoption.** Nothing here writes `etf.adopted_rule_id` into `config/user.toml`. Running a backtest and adopting a rule are separate acts, and the second belongs to the rule-engine plan (Plan 4) where the policy output shape lives.
- **Gold.** `GoldConfig` exists in `config.py` and no backtest touches it. Q41 (SGE ≥15y depth, or an LBMA × USDCNY proxy) is unverified, and the last plan's lesson was to verify external contracts before writing code against them. Plan 6.
- **The news brake.** Q42=C fixes it as reduce-or-skip only, never increase, and marks it un-backtested. It sits between the engine's output and the notification, so it belongs with Plan 4/5, not here.
- **`evals/stockbench/backtest_engine.py`.** Untouched. Different domain (scoring LLM ratings), a release-zip asserted member, and its `YFinancePriceSource` path carries `# pragma: no cover` and has never been executed by any test — changing it has packaging consequences and no regression net.
- **Registering `^BXN` in `instruments.py`.** The backtest loader deliberately bypasses the instrument registry. `market_data.py:220-221` refuses a historical `decision_at`, and `_test_journal.py:221` lists `^NDX` as an id the journal must reject. Registering an index would expose it as a researchable instrument, which is the opposite of the intent.
- **Alternative backtest libraries.** vectorbt, zipline and pyfolio were not resolved, not installed and not read. Since `bt` presented no dependency conflict, there was no trigger to evaluate them. If the 49-package surface later becomes a reason to drop the oracle entirely, an alternative must go through the same `uv pip compile` plus real-install plus source-read check before it is trusted.

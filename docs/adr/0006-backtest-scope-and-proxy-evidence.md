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
`ruff --select F82` and contradicts `mcps/copilot_mcp.py:9-13`, which documents
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
- A lookback family consumes its warm-up from the start of the frame
  (`engine.run` skips the first `rule.warmup_bars` bars outright, producing no
  curve point for them), so evaluating a 252-bar-lookback strategy over a
  window that must cover 2008 requires bars from 2007-01 or earlier. Of the 22
  qualified symbols, VEA (first bar 2007-07-26) is the only one that cannot
  supply them: a momentum universe that includes VEA fails Q29 rule 1 for a
  reason that has nothing to do with the strategy, and the rejection is
  otherwise unexplained. `universe.FIRST_BAR` and `warmup_headroom_bars`
  record the per-symbol headroom so this is attributable rather than
  discovered only after an admission rejection; `admission.assess` also names
  the curve's actual start date in every stress-year failure message.

#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = ["bt==1.2.3", "ffn==1.2.2"]
# ///
"""Dev-only cross-check of this repo's engine against `bt`. Never imported.

WHY THIS EXISTS
Q39 fixed that the rule families are implemented from their primary sources
(Bogleheads 5/25, Faber 2007, Antonacci 2014) and cross-checked against an
independent implementation, rather than forked from one. This script is that
cross-check. It also locks two `bt` defects that ADR-0006 clause 2 depends on,
so nobody later "simplifies" the production stack onto them.

`ffn` is pinned explicitly even though `bt` declares only `ffn>=1.1.2`: ffn
1.2.2 was published two days before this was first verified, and an unpinned
transitive dependency would silently change the oracle we compare against.

`MPLBACKEND=Agg` is not optional. `bt/backtest.py:11` imports pyplot
unconditionally, and with the variable unset the backend resolves to `tkagg`.

This file deliberately contains no `--self-test` flag. CI's self-test sweep
greps for that string and runs every module that has it in a job that installs
zero third-party packages, where `import bt` would fail.

Run:
    MPLBACKEND=Agg uv run --no-project --script scripts/_cross_check_bt.py

OBSERVED 2026-09-19 (not inferred -- this script was run, and these are its
numbers): bt 1.2.3 on pandas 3.0.6, CPython 3.13 win_amd64, 45 packages
resolved, prebuilt `core.cp313-win_amd64.pyd`, no compiler invoked.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("MPLBACKEND", "Agg")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bt  # noqa: E402
import pandas as pd  # noqa: E402

from copilot.backtest import engine  # noqa: E402
from copilot.backtest import frame as frame_mod  # noqa: E402

#: Observed max relative deviation over 2610 bars was 8.37e-15 -- about 38 ULPs
#: of float64, pure accumulation round-off. 1e-12 leaves ~120x headroom against
#: a pandas or platform float change while staying far tighter than any real
#: behavioural divergence, which would show up at 1e-6 or larger.
AGREEMENT_TOLERANCE = 1e-12


def prices() -> pd.DataFrame:
    """A strictly rising two-asset fixture with no drawdown anywhere.

    Monotonicity is load-bearing for the red-light check below: on a series that
    never declines, any real investment followed by any exit leaves the NAV
    permanently above its 100.0 starting index, so "ends at exactly 100.0" can
    only mean the strategy never traded at all.
    """
    dates = pd.bdate_range("2015-01-01", "2025-01-01")
    return pd.DataFrame({"AAA": [100 + i * 0.05 for i in range(len(dates))],
                         "BBB": [100 + i * 0.02 for i in range(len(dates))]}, index=dates)


def _nav(algos: list, df: pd.DataFrame, name: str) -> pd.Series:
    """Run a bt strategy and return its NAV curve as a Series.

    `Result.prices` is a DataFrame with one column per backtest, not a Series;
    `.iloc[-1]` on it yields a row, and `float()` of that row raises TypeError.
    """
    result = bt.run(bt.Backtest(bt.Strategy(name, algos), df, integer_positions=False))
    return result.prices.iloc[:, 0]


def check_band_trigger_alone_never_invests(df: pd.DataFrame) -> None:
    """RED LIGHT: lock bt 1.2.3's bar-0 defect so nobody adopts that algo.

    `algos.py:400` iterates `target.children`, which `Rebalance` has not created
    yet at bar 0, so the loop body never runs, `temp` never gets 'cash', line 416
    returns False, the AlgoStack short-circuits, and the backtest holds 100% cash
    for its entire length. Fixed on master, unreleased.

    Asserting `nunique() == 1` rather than only the final value: it proves the
    curve never moved at any point, not merely that it came back to where it
    started.
    """
    nav = _nav([
        bt.algos.SelectAll(),
        bt.algos.WeighEqually(),
        bt.algos.RunIfOutOfBounds(0.25),
        bt.algos.Rebalance(),
    ], df, "bands-only")
    distinct = int(nav.nunique())
    assert distinct == 1 and abs(float(nav.iloc[-1]) - 100.0) < 1e-9, (
        f"expected bt 1.2.3 to hold 100% cash for the whole run (one distinct NAV "
        f"value of 100.0); got {distinct} distinct values ending at {float(nav.iloc[-1])}. "
        "If this now differs, bt released the algos.py:400 fix and "
        "docs/adr/0006-backtest-scope-and-proxy-evidence.md clause 2 should be revisited.")
    print(f"ok   bands-only holds 100% cash end to end ({distinct} distinct NAV value)")


def check_calendar_or_band_does_invest(df: pd.DataFrame) -> float:
    """The documented workaround: Or([calendar, band]) bootstraps and invests."""
    nav = _nav([
        bt.algos.SelectAll(),
        bt.algos.WeighEqually(),
        bt.algos.Or([bt.algos.RunMonthly(), bt.algos.RunIfOutOfBounds(0.25)]),
        bt.algos.Rebalance(),
    ], df, "calendar-or-band")
    final = float(nav.iloc[-1])
    assert final > 110.0, f"the Or-wrapped stack should track the rising market, got {final}"
    print(f"ok   Or([calendar, band]) invests and ends at {final:.4f}")
    return final


def check_trigger_before_weigh_degrades_to_daily(df: pd.DataFrame, or_wrapped_final: float) -> None:
    """Lock the second gotcha: a trigger placed before Weigh* stops being a band.

    `StrategyBase.run` clears `self.temp` every bar (core.py:2126), so
    `"weights" not in target.temp` is always true at the head of a stack and
    algos.py:395-396 returns True unconditionally. The tolerance is never read
    and the stack silently becomes daily rebalancing.

    Asserted by equality with an explicitly daily stack, and by inequality with
    the correctly-ordered monthly stack -- together those pin the behaviour from
    both sides, which neither check does alone.
    """
    before = float(_nav([
        bt.algos.SelectAll(),
        bt.algos.RunIfOutOfBounds(0.25),
        bt.algos.WeighEqually(),
        bt.algos.Rebalance(),
    ], df, "band-before-weigh").iloc[-1])
    daily = float(_nav([
        bt.algos.RunDaily(),
        bt.algos.SelectAll(),
        bt.algos.WeighEqually(),
        bt.algos.Rebalance(),
    ], df, "daily-equal").iloc[-1])
    assert before == daily, (
        f"a band trigger placed before Weigh* should degrade to daily rebalancing; "
        f"got {before} against a daily stack's {daily}")
    assert before != or_wrapped_final, (
        "band-before-weigh and the correctly-ordered Or stack produced the same "
        "NAV, so this fixture no longer distinguishes the two orderings")
    print(f"ok   band-before-weigh == daily rebalancing ({before:.10f}), "
          f"and differs from the Or stack ({or_wrapped_final:.10f})")


def check_our_engine_matches_bt(df: pd.DataFrame) -> None:
    """THE cross-check Q39 asks for: our engine against an independent one.

    Common ground is daily-rebalanced equal weight with fractional shares and
    zero costs -- the portfolio mechanics both implement identically. The band
    LOGIC is deliberately not compared: bt's RunIfOutOfBounds implements only the
    relative 25% half of Bogleheads 5/25 (algos.py:408 divides by the target
    weight), so agreement there would mean ours was wrong.

    bt prepends one bar dated one business day before the data starts, so its
    curve is one longer than ours; dropping that row aligns the indices exactly.
    """
    bt_nav = _nav([
        bt.algos.RunDaily(),
        bt.algos.SelectAll(),
        bt.algos.WeighEqually(),
        bt.algos.Rebalance(),
    ], df, "daily-equal")
    aligned = bt_nav.iloc[1:]
    our_dates = [d.date() for d in df.index]
    assert len(aligned) == len(our_dates), (
        f"expected bt to prepend exactly one bar; got {len(bt_nav)} against {len(our_dates)}")
    assert all(a.date() == b for a, b in zip(aligned.index, our_dates)), (
        "bt's dates do not line up with the fixture after dropping its prepended bar")

    frame = frame_mod.build(dates=our_dates, symbols=list(df.columns),
                            closes=[list(row) for row in df.values])
    result = engine.run(frame,
                        rule=engine.StaticWeights({"AAA": 0.5, "BBB": 0.5}),
                        start_cash=100.0,
                        cost_model=engine.CostModel.free(),
                        cash_floor_pct=0.0,
                        integer_shares=False)
    ours = [value for _, value in result.curve]
    assert len(ours) == len(aligned), f"{len(ours)} our bars against {len(aligned)} bt bars"

    deviations = [abs(o - float(b)) / float(b) for o, b in zip(ours, aligned)]
    worst = max(range(len(deviations)), key=deviations.__getitem__)
    assert deviations[worst] < AGREEMENT_TOLERANCE, (
        f"our engine and bt diverge by {deviations[worst]:.3e} at bar {worst} "
        f"({our_dates[worst]}): ours={ours[worst]!r}, bt={float(aligned.iloc[worst])!r}. "
        f"Tolerance is {AGREEMENT_TOLERANCE:.0e}. A divergence this large is a "
        "behavioural difference, not float round-off -- investigate before shipping.")
    print(f"ok   our engine matches bt over {len(ours)} bars "
          f"(max relative deviation {deviations[worst]:.3e} at bar {worst}, "
          f"tolerance {AGREEMENT_TOLERANCE:.0e})")
    print(f"     final: ours={ours[-1]!r}  bt={float(aligned.iloc[-1])!r}")


def main() -> int:
    df = prices()
    print(f"fixture: {len(df)} business days, "
          f"AAA {df['AAA'].iloc[0]} -> {df['AAA'].iloc[-1]}, "
          f"BBB {df['BBB'].iloc[0]} -> {df['BBB'].iloc[-1]}")
    print(f"environment: bt {getattr(bt, '__version__', '?')}, pandas {pd.__version__}, "
          f"{Path(bt.core.__file__).name}\n")
    check_band_trigger_alone_never_invests(df)
    or_wrapped_final = check_calendar_or_band_does_invest(df)
    check_trigger_before_weigh_degrades_to_daily(df, or_wrapped_final)
    check_our_engine_matches_bt(df)
    print("\nbt cross-check complete")
    return 0


if __name__ == "__main__":
    sys.exit(main())

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

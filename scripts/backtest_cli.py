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


def _fetch(symbol: str) -> history.Series:
    """The sole call site for the history module's fetch function.

    Both load_universe (the normal per-run path) and verify_universe (the
    manual, non-CI diagnostic path) go through this one function, so a
    source-level count of the call it makes below stays a reliable guard
    against a future call being added inside a per-family loop -- the
    concern load_universe's own docstring describes.
    """
    return history.fetch(symbol)


def verify_universe() -> int:
    """Re-observe the tier table against the live endpoint. Prints a diff."""
    problems = 0
    for symbol in sorted(universe.QUALIFIED | universe.NO_2008_BARS | universe.PARTIAL_2008):
        try:
            series = _fetch(symbol)
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
        series.append(_fetch(symbol))
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

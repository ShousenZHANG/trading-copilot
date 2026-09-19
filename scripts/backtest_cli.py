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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

from copilot.backtest import admission, bxn, engine, history, metrics, rules, universe  # noqa: E402
from copilot.backtest import frame as frame_mod  # noqa: E402

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


def verify_universe(symbols: Iterable[str] | None = None) -> int:
    """Re-observe the tier table against the live endpoint. Prints a diff.

    Defaults to universe.default_candidates() (the qualified tier, sorted)
    rather than a separately hand-picked set, so this and the CLI's normal
    --universe flag share one notion of "the symbols worth checking" instead
    of two independently maintained lists. Pass --universe alongside
    --verify-universe to check a different set instead -- a near-miss tier
    such as universe.NO_2008_BARS, or a couple of symbols for a quick check.

    Builds a real PriceFrame per symbol and reads span_years()/
    sessions_in_year() from it rather than hand-rolling the identical
    (last - first).days / 365.25 arithmetic on the raw Series: those methods
    are otherwise implemented, documented and unit-tested with no production
    caller.
    """
    problems = 0
    checked = sorted(symbols) if symbols is not None else list(universe.default_candidates())
    for symbol in checked:
        unfetchable = symbol in universe.UNFETCHABLE
        try:
            series = _fetch(symbol)
        except history.NotCovered as exc:
            # A symbol the tier table already records as unfetchable coming back
            # unfetchable is the table being RIGHT, not a problem to report. Only
            # an unexpected disappearance is drift.
            flag = "ok " if unfetchable else "DRIFT"
            if not unfetchable:
                problems += 1
            print(f"  {flag} {symbol:<5} not covered: {exc}")
            continue
        if unfetchable:
            # The other direction: a symbol recorded as unfetchable that now
            # fetches. The table is stale and the tier should be re-derived.
            problems += 1
            print(f"  DRIFT {symbol:<5} is tiered unfetchable but returned "
                  f"{len(series.dates)} bars")
            continue
        symbol_frame = frame_mod.build(dates=list(series.dates), symbols=[symbol],
                                       closes=[[c] for c in series.split_and_dividend_adjusted])
        frame_years = symbol_frame.span_years()
        bars = {y: symbol_frame.sessions_in_year(y) for y in (2008, 2020, 2022)}
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

    A fetch failure partway through a sweep must not surface as a raw
    traceback naming internal module paths, and it must not leave the run
    silently writing nothing: it is turned into a one-line SystemExit naming
    the symbol and the upstream reason, the same style as the inadmissible-
    symbol exit just below.
    """
    series, caveats = [], []
    for symbol in symbols:
        classification = universe.classify(symbol)
        if not classification.admissible:
            message = f"{symbol} is tier '{classification.tier}': {classification.reason}"
            if classification.tier == "income_proxy_only":
                message += "; pass --income-proxy to evaluate it through the BXN index proxy instead"
            raise SystemExit(message)
        if classification.provenance_unverified:
            caveats.append(f"{symbol}: {classification.reason}")
        try:
            series.append(_fetch(symbol))
        except history.NotCovered as exc:
            raise SystemExit(f"{symbol} is not covered by the upstream endpoint: {exc}") from exc
        except history.HistoryError as exc:
            raise SystemExit(f"{symbol} could not be fetched: {exc}") from exc
    return history.to_frame(series), history.alignment(series), caveats


#: sensitivity_grid's own docstring is explicit that it implements only half
#: of Q29 rule 4's divergence check -- "report a grid" -- and nothing runs a
#: backtest on the neighbours it produces. This note is the other half of
#: that sentence, copied into every report so the same limit travels with the
#: numbers: no automatic pass/fail threshold is applied, because none has
#: been established. Q29 rule 4 says "report parameter sensitivity"; inventing
#: a cutoff would be making up a governance rule the user never approved.
SENSITIVITY_NOTE = ("Reported for the user's judgement. No automatic pass/fail threshold is "
                    "applied to these deltas, because none has been established.")


def _evaluate_neighbour(frame, base_rule, neighbour_params: dict[str, float], *,
                        start_cash: float, cash_floor_pct: float) -> dict:
    """One sensitivity-grid neighbour: its own rule, its own backtest, its own outcome.

    A neighbour rejected by with_parameters' __post_init__ guards (or one
    whose backtest cannot run at all) is recorded as an error, not raised --
    a threshold-free divergence report must show that a neighbour is invalid
    at least as clearly as it shows one that is merely different, and
    crashing the whole family's report over one bad neighbour would show
    neither.
    """
    try:
        neighbour_rule = base_rule.with_parameters(neighbour_params)
        result = engine.run(frame, rule=neighbour_rule, start_cash=start_cash,
                            cost_model=engine.CostModel(), cash_floor_pct=cash_floor_pct)
        return {"parameters": neighbour_params, "cagr": round(metrics.cagr(result.curve), 6),
                "max_drawdown": round(metrics.max_drawdown(result.curve).depth, 6)}
    except ValueError as exc:
        return {"parameters": neighbour_params, "error": str(exc)}


def run_family(frame, rule, *, start_cash: float, cash_floor_pct: float) -> dict:
    result = engine.run(frame, rule=rule, start_cash=start_cash,
                        cost_model=engine.CostModel(), cash_floor_pct=cash_floor_pct)
    report = admission.assess(result, sessions_by_year=admission.STRESS_SESSIONS)
    base_cagr = metrics.cagr(result.curve)
    neighbours = [_evaluate_neighbour(frame, rule, neighbour, start_cash=start_cash,
                                      cash_floor_pct=cash_floor_pct)
                 for neighbour in sensitivity_grid(result.parameters)]
    deltas = [abs(n["cagr"] - base_cagr) for n in neighbours if "cagr" in n]
    return {"rule": rule.name, "parameters": result.parameters, "caveats": list(rule.caveats),
            "admitted": report.admitted, "failures": report.failures, "metrics": report.metrics,
            "sensitivity": {"neighbours": neighbours,
                           "max_abs_cagr_delta": round(max(deltas), 6) if deltas else None,
                           "note": SENSITIVITY_NOTE}}


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


def _synthetic_series(symbol: str, *, start: date = date(2000, 1, 3),
                      end: date = date(2023, 1, 1)) -> history.Series:
    """A deterministic, multi-year daily series. No network, no clock, no random.

    Used only by self_test(), to stand in for _fetch so main() can be run for
    real -- argument parsing, load_universe, every rule family, JSON
    serialization -- without a live Yahoo request.
    """
    dates, d = [], start
    while d < end:
        if d.weekday() < 5:
            dates.append(d)
        d += timedelta(days=1)
    closes = tuple(100.0 + i * 0.01 for i in range(len(dates)))
    return history.Series(symbol=symbol, dates=tuple(dates), split_adjusted=closes,
                          split_and_dividend_adjusted=closes, dropped_bars=0)


def self_test() -> int:
    grid = sensitivity_grid({"top_n": 5.0})
    assert grid == [{"top_n": 4.0}, {"top_n": 6.0}], grid
    assert build_rules(("SPY", "QQQ"), "bands")[0].name == "fixed_weight_bands"

    # Genuine end-to-end run, not just the two pure-function checks above: a
    # mutation that replaced main()'s body (after argument parsing) with
    # `raise RuntimeError(...)` left both asserts above passing and this
    # function printing "OK" -- self_test() never actually called main(),
    # load_universe, run_family, argument parsing or JSON serialization.
    # _fetch is substituted with a synthetic, deterministic Series for two
    # real (qualified) symbols so this stays offline and repeatable; "fake"
    # is the data, not the symbol names, which still have to pass
    # universe.classify().
    import tempfile
    from unittest import mock
    with tempfile.TemporaryDirectory() as tmp:
        with mock.patch.object(sys.modules[__name__], "_fetch", side_effect=_synthetic_series):
            code = main(["--universe", "SPY,QQQ", "--family", "bands", "--out-dir", tmp])
        assert code == 0, f"main() returned {code}, expected 0"
        reports = list(Path(tmp).glob("backtest-*.json"))
        assert len(reports) == 1, f"expected exactly one report file, found {reports}"
        payload = json.loads(reports[0].read_text(encoding="utf-8"))
        for key in ("generated_at", "universe", "alignment", "caveats", "families"):
            assert key in payload, f"report is missing top-level key {key!r}: {sorted(payload)}"
        assert payload["families"], "report has no families"
        assert payload["families"][0]["rule"] == "fixed_weight_bands"

    print("backtest_cli self-test OK")
    return 0


def _parse_universe(raw: str, *, parser: argparse.ArgumentParser, allow_empty: bool = False) -> tuple[str, ...]:
    """Split, upper-case, and validate a comma-separated --universe value.

    Rejects duplicates and (unless allow_empty) an empty result via
    parser.error, before any symbol is fetched. Left unvalidated:
    "SPY,SPY,QQQ" fetched SPY twice -- already violating the fetch-once
    property -- before frame.build's own "duplicate symbols: SPY" surfaced as
    an uncaught traceback, and "--universe ' , , '" passed the non-empty-
    string guard on the raw flag, yielded an empty symbol tuple, and crashed
    with an uncaught "no series to align" three calls later. allow_empty is
    for --verify-universe, where an empty --universe means "no override, use
    the default" rather than an error.
    """
    symbols = tuple(s.strip().upper() for s in raw.split(",") if s.strip())
    if not symbols:
        if allow_empty:
            return symbols
        parser.error("--universe must name at least one symbol")
    duplicates = sorted({s for s in symbols if symbols.count(s) > 1})
    if duplicates:
        parser.error(f"--universe lists duplicate symbol(s): {', '.join(duplicates)}")
    return symbols


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
        override = _parse_universe(args.universe, parser=parser, allow_empty=True)
        return 1 if verify_universe(override or None) else 0
    if not args.universe:
        parser.error("--universe is required unless a --verify-* or --self-test flag is given")

    symbols = _parse_universe(args.universe, parser=parser)
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
        for caveat in family["caveats"]:
            print(f"            caveat: {caveat}")
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

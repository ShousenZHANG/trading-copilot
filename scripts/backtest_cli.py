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
from copilot.backtest import goldhistory, goldrules  # noqa: E402

DEFAULT_OUT_DIR = ROOT / "data" / "audit"
SENSITIVITY_STEP = 0.10

GOLD_FAMILY = "scheduled_accumulation"

#: SGE bid/ask on Au99.99 is a few tenths of a percent; 20 bps is a
#: conservative round-trip stand-in. No per-share fee exists for a metal bought
#: by weight, so per_share_usd and minimum_usd are zero. Same figures
#: _test_ruleset.py adopts a gold rule with, so the CLI and the tests measure
#: the same strategy.
GOLD_COST_MODEL = engine.CostModel(per_share_usd=0.0, minimum_usd=0.0,
                                   max_pct_of_notional=0.01, spread_bps=20.0)

#: The two failures ADR-0008 clause 2 grants a written waiver for. They are
#: passed only on --adopt: a plain report shows them UNWAIVED, because the point
#: of the report is to let the reader see what the waiver is covering before
#: anyone signs it.
GOLD_WAIVERS = {"span": "ADR-0008 clause 2: SGE Au99.99 history begins 2016-12-19",
                "stress_2008": "ADR-0008 clause 2: no Chinese gold series covers 2008"}

#: No sensitivity grid is reported for gold, and this says so where the empty
#: list is read. Two independent reasons: ScheduledAccumulation has no
#: `with_parameters`, which _evaluate_neighbour needs; and even if it did, the
#: grid would be meaningless -- engine.run rebalances a one-instrument universe
#: to weight 1.0, so interval_days 21 and 252 end 3 CNY apart in 360,000
#: (measured 2026-09-20). A grid of indistinguishable neighbours reads as
#: "robust across parameters" when the truth is "the engine cannot tell them
#: apart at all".
GOLD_SENSITIVITY_NOTE = ("No sensitivity grid: this engine cannot exercise a contribution "
                         "schedule, so neighbouring intervals are indistinguishable from each "
                         "other and from buy-and-hold. " + goldrules.DISCLOSURE)


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
    # XSHG is the Shanghai Stock Exchange calendar. SGE is not XSHG -- the gold
    # exchange runs its own schedule including a night session -- so this is a
    # PROXY check, not an identity. It exists to catch an order-of-magnitude
    # error in SGE_STRESS_SESSIONS, not to prove the counts exact. A difference
    # of a few sessions is expected and is reported, not failed.
    xshg = xcals.get_calendar("XSHG")
    for year, expected in sorted(admission.SGE_STRESS_SESSIONS.items()):
        if expected == 0:
            print(f"  -- SGE {year}: no vendored history, waived by ADR-0008 clause 2")
            continue
        observed = len(xshg.sessions_in_range(f"{year}-01-01", f"{year}-12-31"))
        drift = abs(observed - expected)
        status = "ok" if drift <= 5 else "DRIFT"
        print(f"  {status} SGE {year}: vendored {expected}, XSHG proxy {observed} (diff {drift})")
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


def gold_alignment() -> tuple[dict, list[str]]:
    """The gold sleeve's stand-in for load_universe's alignment block.

    One symbol means nothing can bind the common window or be lost against a
    longer series, so those fields are the degenerate answers rather than
    omitted -- a reader comparing two reports should not have to work out
    whether a missing key means zero or means unmeasured.
    """
    meta = goldhistory.provenance()
    alignment = {"common_first": meta["first_session"], "common_last": meta["last_session"],
                 "common_bars": meta["row_count"],
                 "binds_start": goldhistory.SYMBOL, "binds_end": goldhistory.SYMBOL,
                 "bars_lost_vs_longest": 0}
    caveats = [f"one vendored source: {meta['upstream']} ({meta['source_url']}), refreshed "
               f"{meta['refreshed_at']}; akshare and SGEProvider read the same upstream, so "
               "their agreement is not corroboration",
               goldrules.DISCLOSURE]
    return alignment, caveats


def run_gold(*, start_cash: float, cash_floor_pct: float, waivers=None) -> dict:
    """Backtest the gold family over the vendored SGE series.

    Separate from run_family for three reasons, each of which would be a silent
    wrong answer if the ETF path were reused: the stress table must be SGE's,
    not XNYS's (admission.stress_sessions_for); shares must be fractional,
    because gold is bought by weight; and the sensitivity grid cannot run -- see
    GOLD_SENSITIVITY_NOTE.
    """
    frame = goldhistory.load()
    rule = goldrules.FAMILIES[GOLD_FAMILY]()
    result = engine.run(frame, rule=rule, start_cash=start_cash,
                        cost_model=GOLD_COST_MODEL, cash_floor_pct=cash_floor_pct,
                        integer_shares=False)
    report = admission.assess(result, sessions_by_year=admission.stress_sessions_for("gold"),
                              waivers=dict(waivers or {}))
    return {"rule": rule.name, "parameters": result.parameters,
            "caveats": [goldrules.DISCLOSURE],
            "admitted": report.admitted, "waived": report.waived,
            "waiver_reasons": list(report.waiver_reasons),
            "failures": report.failures, "metrics": report.metrics,
            "sensitivity": {"neighbours": [], "max_abs_cagr_delta": None,
                            "note": GOLD_SENSITIVITY_NOTE}}


def adopt_gold_rule(*, start_cash: float, cash_floor_pct: float, db_path=None) -> dict:
    """Record the gold rule as adopted, carrying ADR-0008's written waivers.

    Here rather than in copilot_cli.py for the reason adopt_rule states: only
    the process that just ran engine.run holds a Result that still satisfies
    ruleset.build_adoption's binding.
    """
    from copilot import service

    frame = goldhistory.load()
    rule = goldrules.FAMILIES[GOLD_FAMILY]()
    result = engine.run(frame, rule=rule, start_cash=start_cash,
                        cost_model=GOLD_COST_MODEL, cash_floor_pct=cash_floor_pct,
                        integer_shares=False)
    cost_model = {"per_share_usd": GOLD_COST_MODEL.per_share_usd,
                  "minimum_usd": GOLD_COST_MODEL.minimum_usd,
                  "max_pct_of_notional": GOLD_COST_MODEL.max_pct_of_notional,
                  "spread_bps": GOLD_COST_MODEL.spread_bps}
    adoption_inputs = dict(
        sleeve="gold", family=rule.name, parameters=dict(result.parameters),
        universe=(goldhistory.SYMBOL,), targets=None, result=result,
        cost_model=cost_model, cash_floor_pct=cash_floor_pct, integer_shares=False,
        waivers=GOLD_WAIVERS)
    receipt = service.adopt(adoption_inputs, db_path=db_path)
    receipt["next_step"] = (f'set gold.adopted_rule_id = "{receipt["adoption"]["rule_id"]}" in '
                            "config/user.toml to make this rule live")
    return receipt


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

    # --adopt: a real end-to-end adoption, over four symbols so the equal-weight
    # bands rule (25% each) clears the ETF sleeve's single-name limit at the
    # default 15% cash floor (worst case 0.85 * 0.25 = 21.25%). Same synthetic,
    # offline, deterministic series as the run above; only the flag differs.
    import io
    import re
    from contextlib import redirect_stdout
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "adopt-selftest.sqlite")
        buffer = io.StringIO()
        with mock.patch.object(sys.modules[__name__], "_fetch", side_effect=_synthetic_series):
            with redirect_stdout(buffer):
                code = main(["--universe", "SPY,QQQ,DIA,IVV", "--family", "bands",
                            "--adopt", "--db", db])
        assert code == 0, f"main() --adopt returned {code}, expected 0"
        receipt = json.loads(buffer.getvalue())
        assert re.match(r"^rule-[0-9a-f]{16}$", receipt.get("rule_id", "")), receipt
        assert "config/user.toml" in receipt["next_step"], receipt
        assert receipt["rule_id"] in receipt["next_step"], receipt
        from copilot import journal
        stored = journal.load_adoption(receipt["rule_id"], db_path=db)
        assert stored["sleeve"] == "etf", stored

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


def _target_weights(family: str, symbols: tuple[str, ...]) -> dict[str, float] | None:
    """The equal-weight targets build_rules would give this family, or None.

    Mirrors build_rules' own `equal = {s: 1.0 / len(symbols) for s in symbols}`
    so --adopt records exactly the targets the backtest actually ran with.
    fixed_weight_bands is the only family with fixed targets; the other two
    compute their weights at evaluation time and store no targets at all.
    """
    if family != "bands":
        return None
    return {s: 1.0 / len(symbols) for s in symbols}


def adopt_rule(symbols: tuple[str, ...], family: str, *, start_cash: float,
              cash_floor_pct: float, db_path=None) -> dict:
    """Run one family for real and hand the in-memory Result to service.adopt.

    This is the whole reason --adopt lives here rather than in copilot_cli.py:
    ruleset.build_adoption binds result.rule_name/parameters/universe to the
    family/parameters/universe being adopted, and that binding cannot survive a
    JSON round trip -- a hand-written payload describing a backtest is exactly
    the bypass Task 4 closes. Only the process that just ran engine.run() holds
    a Result that still carries that guarantee.
    """
    from copilot import service

    frame, alignment, caveats = load_universe(symbols)
    rules_built = build_rules(symbols, family)
    if len(rules_built) != 1:
        raise SystemExit(f"--adopt requires exactly one rule family, not {len(rules_built)} "
                         f"(got --family {family!r}); pass bands, invvol or momentum")
    rule = rules_built[0]
    result = engine.run(frame, rule=rule, start_cash=start_cash,
                        cost_model=engine.CostModel(), cash_floor_pct=cash_floor_pct)
    cost_model = {"per_share_usd": engine.CostModel().per_share_usd,
                  "minimum_usd": engine.CostModel().minimum_usd,
                  "max_pct_of_notional": engine.CostModel().max_pct_of_notional,
                  "spread_bps": engine.CostModel().spread_bps}
    adoption_inputs = dict(
        sleeve="etf", family=rule.name, parameters=dict(result.parameters),
        universe=tuple(symbols), targets=_target_weights(family, symbols), result=result,
        cost_model=cost_model, cash_floor_pct=cash_floor_pct, integer_shares=True)
    return service.adopt(adoption_inputs, db_path=db_path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", default="")
    parser.add_argument("--sleeve", choices=("etf", "gold"), default="etf",
                        help="which sleeve to backtest. 'gold' fixes the universe to GOLD.CNY, "
                             "reads the vendored SGE Au99.99 series, sizes in fractional grams "
                             "and judges stress-year coverage on the SGE calendar.")
    parser.add_argument("--family", default="all",
                        choices=("all", "bands", "invvol", "momentum", GOLD_FAMILY))
    parser.add_argument("--start-cash", type=float, default=10000.0)
    parser.add_argument("--cash-floor-pct", type=float, default=0.15)
    parser.add_argument("--income-proxy", action="store_true",
                        help="also run the BXN index proxy for QQQI/JEPQ/JEPI")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    parser.add_argument("--verify-universe", action="store_true")
    parser.add_argument("--verify-calendars", action="store_true")
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--adopt", action="store_true",
                        help="record this backtest as an adopted rule via service.adopt(), and "
                             "print the receipt (rule_id and next_step). Requires a single "
                             "--family (not 'all'): an adoption names exactly one rule. This is "
                             "the only supported way to adopt a rule -- copilot_cli.py's own "
                             "`adopt` subcommand is read-only (it prints back a stored adoption) "
                             "because only this in-process Result satisfies the binding "
                             "ruleset.build_adoption enforces; a JSON payload could describe a "
                             "backtest that never ran.")
    parser.add_argument("--db", help="isolated SQLite path for --adopt; default data/state/copilot.sqlite")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.verify_calendars:
        return 1 if verify_calendars() else 0
    if args.verify_universe:
        override = _parse_universe(args.universe, parser=parser, allow_empty=True)
        return 1 if verify_universe(override or None) else 0

    if args.sleeve == "gold":
        # Every refusal here is a combination that would otherwise produce a
        # quietly wrong report rather than an error: an ETF family over a
        # single metal, a caller-supplied basket for a sleeve whose universe is
        # fixed by ruleset.build_adoption, or the BXN income proxy (an equity
        # diagnostic) stapled onto a gold run.
        if args.family not in ("all", GOLD_FAMILY):
            parser.error(f"--sleeve gold runs only --family {GOLD_FAMILY} (or 'all'); "
                         f"{args.family!r} divides a book across N names and gold is one "
                         "instrument")
        if args.universe:
            parser.error("--sleeve gold has a fixed universe (GOLD.CNY); drop --universe")
        if args.income_proxy:
            parser.error("--income-proxy is an ETF-sleeve diagnostic and has no gold meaning")
        if args.adopt:
            try:
                receipt = adopt_gold_rule(start_cash=args.start_cash,
                                          cash_floor_pct=args.cash_floor_pct, db_path=args.db)
            except ValueError as exc:
                raise SystemExit(f"cannot adopt this rule: {exc}") from exc
            print(json.dumps(receipt, indent=2, ensure_ascii=False))
            return 0
        alignment, caveats = gold_alignment()
        payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
                   "universe": [goldhistory.SYMBOL], "alignment": alignment,
                   "caveats": caveats,
                   "families": [run_gold(start_cash=args.start_cash,
                                         cash_floor_pct=args.cash_floor_pct)]}
        return _write_report(payload, alignment, caveats, out_dir=Path(args.out_dir))

    if args.family == GOLD_FAMILY:
        parser.error(f"--family {GOLD_FAMILY} belongs to the gold sleeve; pass --sleeve gold")
    if not args.universe:
        parser.error("--universe is required unless a --verify-* or --self-test flag is given")

    symbols = _parse_universe(args.universe, parser=parser)

    if args.adopt:
        if args.family == "all":
            parser.error("--adopt requires a single --family (not 'all'); an adoption names "
                         "exactly one rule")
        try:
            receipt = adopt_rule(symbols, args.family, start_cash=args.start_cash,
                                cash_floor_pct=args.cash_floor_pct, db_path=args.db)
        except ValueError as exc:
            raise SystemExit(f"cannot adopt this rule: {exc}") from exc
        print(json.dumps(receipt, indent=2, ensure_ascii=False))
        return 0

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
    return _write_report(payload, alignment, caveats, out_dir=Path(args.out_dir))


def _write_report(payload: dict, alignment: dict, caveats: list, *, out_dir: Path) -> int:
    """Write the JSON report and print the human summary. Shared by both sleeves.

    Extracted so the gold path prints the same verdict line, the same failure
    list and the same caveats as the ETF path. A second copy of this would be a
    second place for a failure to go unprinted.
    """
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

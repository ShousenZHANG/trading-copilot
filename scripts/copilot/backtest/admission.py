"""Q29's six rules, executable.

The user approved these six on 2026-09-19 as the standard a strategy must meet
before it may be adopted. They are governance, not tuning: rule 4 (at most three
parameters) is the only hard brake against overfitting in the whole system.

A waiver does not delete a failure. It records a reason beside it and flips
`admitted`, so a reader always sees which rule was not met and why. The only
waiver granted so far is ADR-0006 clause 5, for the BXN proxy's missing 2008.

Waivers are keyed to the rule that failed, never matched against failure text.
Every failure is tagged with its rule key at the point it is raised, and a
waiver can only cancel the failure(s) carrying that exact key. Rules 3
("costs"), 4 ("parameters") and 5 ("out_of_sample") are not members of
`WAIVABLE` at all, so they are structurally impossible to waive -- not merely
unwaived by convention. (An earlier version matched a waiver against failure
text with a string-prefix heuristic; a rule-4 message that happened to start
with the digits of a waived stress year was silently swallowed by it. See the
AdmissionGate tests for the adversarial case that caught it.)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from . import metrics
from .engine import Result

MIN_YEARS = 15.0
MAX_PARAMETERS = 3

#: Post-2019 predates the publication of every rule family this repo ships --
#: Faber 2007, Antonacci 2014, and the Bogleheads 5/25 definition -- so it is
#: honest out-of-sample evidence *for those published rules*. It is NOT
#: out-of-sample relative to whoever tunes a rule's parameters today: fitting
#: parameters against 2019-2026 data and then "validating" on the same window
#: would clear this check with zero real protection, and nothing in this
#: module can detect that. The guarantee is about publication date, not about
#: who is doing the fitting -- say so rather than letting the constant imply
#: a stronger guarantee than it carries.
OUT_OF_SAMPLE_START = date(2019, 1, 1)

#: FixedWeightBands' calendar leg rebalances at most once per 365-day cycle
#: (rules.py, `calendar_days=365` default). A hold-out shorter than two years
#: cannot contain even two full rebalance cycles of the simplest rule family,
#: so it can show only a single snapshot, not the strategy actually behaving
#: out of sample.
MIN_OUT_OF_SAMPLE_YEARS = 2.0

#: XNYS sessions, from exchange-calendars==4.13.2, verified 2026-09-19. Held as
#: constants so the gate runs in the offline CI job, which installs no
#: third-party packages. `backtest_cli.py --verify-calendars` re-checks them
#: against the library in the runtime job.
STRESS_SESSIONS = {2008: 253, 2020: 253, 2022: 251}

#: A year counts as covered at 99% of its sessions. At 253 expected sessions
#: that allows at most 2 missing, not the 12 a 95% threshold would allow --
#: enough to drop the entire week of the Lehman collapse (2008-09-15..09-19)
#: plus seven more days and still report 2008 as covered. Not 100%: a holiday
#: schedule difference between a US ETF and a Cboe index is not a
#: data-quality failure.
MIN_STRESS_COVERAGE = 0.99

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
    blank = sorted(key for key, reason in waivers.items() if not reason or not reason.strip())
    if blank:
        raise ValueError(f"waiver(s) {', '.join(blank)} have no stated reason; "
                         "a waiver without a reason defeats the mechanism")

    # (rule_key, message) pairs. A waiver cancels a failure only by an exact
    # key match -- never by matching message text. Rules 3, 4 and 5 use keys
    # that are not members of WAIVABLE, so no waivers dict can ever cancel
    # them, by construction.
    raw: list[tuple[str, str]] = []

    def fail(key: str, message: str) -> None:
        raw.append((key, message))

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
        # A ratio can stay above the coverage floor while an entire month is
        # silently absent (a provider outage, a bad join). This answers "did
        # the strategy actually see September 2008" directly, which a ratio
        # never can.
        months_seen = {w.month for w, _ in result.curve if w.year == stress_year}
        missing_months = [m for m in range(1, 13) if m not in months_seen]
        if missing_months:
            fail(f"stress_{stress_year}",
                 f"{stress_year} has no bars in month(s) {', '.join(str(m) for m in missing_months)}; "
                 "rule 1 requires the strategy to have actually seen every month of a stress year")

    # Rule 3: costs must actually have been charged. Not in WAIVABLE.
    if result.total_costs <= 0 and result.rebalance_count > 0:
        fail("costs", "no transaction costs were charged; rule 3 requires net-of-cost results")

    # Rule 4: at most three parameters. Not in WAIVABLE -- the only hard brake
    # against overfitting in the system must never be waivable.
    if len(result.parameters) > MAX_PARAMETERS:
        fail("parameters", f"{len(result.parameters)} parameters, rule 4 allows at most "
                           f"{MAX_PARAMETERS}: {', '.join(sorted(result.parameters))}")

    # Rule 5: an out-of-sample segment, long enough to show more than a single
    # snapshot. Not in WAIVABLE.
    out_of_sample = [(w, v) for w, v in result.curve if w >= OUT_OF_SAMPLE_START]
    oos_years = ((out_of_sample[-1][0] - out_of_sample[0][0]).days / 365.25
                if len(out_of_sample) >= 2 else 0.0)
    if len(out_of_sample) < 2:
        fail("out_of_sample", f"no out-of-sample segment after {OUT_OF_SAMPLE_START}, rule 5 requires one")
    elif oos_years < MIN_OUT_OF_SAMPLE_YEARS:
        fail("out_of_sample",
             f"out-of-sample segment is {oos_years:.2f} years, rule 5 requires at least "
             f"{MIN_OUT_OF_SAMPLE_YEARS}")

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
            "years": round(oos_years, 2) if len(out_of_sample) > 1 else None,
            "cagr": round(metrics.cagr(out_of_sample), 6) if len(out_of_sample) > 1 else None,
            "max_drawdown": round(metrics.max_drawdown(out_of_sample).depth, 6)
            if len(out_of_sample) > 1 else None,
        },
    }

    # dict.fromkeys dedupes while keeping first-seen order: a single stress
    # year can fail both the coverage check and the missing-month check above,
    # and a waiver that covers it should be reported once, not twice.
    waived_keys = list(dict.fromkeys(key for key, _ in raw if key in waivers))
    unwaived = [message for key, message in raw if key not in waivers]
    failures = [message for _, message in raw]
    return Report(admitted=not unwaived, failures=failures, metrics=report_metrics,
                  waived=bool(waived_keys),
                  waiver_reasons=[f"{k}: {waivers[k]}" for k in waived_keys])

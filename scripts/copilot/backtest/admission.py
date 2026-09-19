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

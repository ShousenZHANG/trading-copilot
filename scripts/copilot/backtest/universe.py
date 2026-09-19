"""Which registry symbols may enter a backtest, and why the rest may not.

Every membership below was observed against the Yahoo chart endpoint on
2026-09-19 with `period1/period2&interval=1d`, cross-checked against
`exchange-calendars==4.13.2` XNYS sessions (2008=253, 2020=253, 2022=251).
These are recorded observations, not estimates. Re-verify with
`python scripts/backtest_cli.py --verify-universe` before editing them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Iterable

from ..instruments import DEFENSIVE_ETFS, ETF_REGISTRY
from .frame import DAYS_PER_YEAR
from .metrics import TRADING_DAYS_PER_YEAR

#: >=15 years of daily bars, with 253/253/251 bars in 2008/2020/2022.
QUALIFIED = frozenset({
    "SPY", "DIA", "QQQ", "IVV", "IWM", "VTI", "VUG", "VTV", "VEA", "VWO", "IOO",
    "XLK", "XLV", "XLF", "XLE", "XLY", "XLP", "XLI", "XLB", "XLU",
    "SMH", "SOXX",
})

#: Verified first daily bar for every QUALIFIED symbol (source: "Verified
#: facts this plan is built on",
#: docs/superpowers/plans/2026-09-19-03-backtest-and-rule-library.md). Exists
#: so a lookback family's warm-up cost can be checked against a symbol's real
#: history instead of discovered only after an admission rejection with no
#: attributable cause -- see warmup_headroom_bars below.
FIRST_BAR: dict[str, date] = {
    "SPY": date(1993, 1, 29),
    "DIA": date(1998, 1, 20),
    "XLK": date(1998, 12, 22),
    "XLV": date(1998, 12, 22),
    "XLF": date(1998, 12, 22),
    "XLE": date(1998, 12, 22),
    "XLY": date(1998, 12, 22),
    "XLP": date(1998, 12, 22),
    "XLI": date(1998, 12, 22),
    "XLB": date(1998, 12, 22),
    "XLU": date(1998, 12, 22),
    "QQQ": date(1999, 3, 10),
    "IVV": date(2000, 5, 19),
    "IWM": date(2000, 5, 26),
    "SMH": date(2000, 6, 5),
    "IOO": date(2000, 12, 8),
    "VTI": date(2001, 6, 15),
    "SOXX": date(2001, 7, 13),
    "VUG": date(2004, 1, 30),
    "VTV": date(2004, 1, 30),
    "VWO": date(2005, 3, 10),
    "VEA": date(2007, 7, 26),
}

#: Rule 1 requires the curve to cover 2008. A lookback family consumes its
#: warm-up from the start of the frame (engine.run skips i < warmup_bars
#: outright), so this is the cutoff a symbol's pre-history is measured
#: against by default.
DEFAULT_WARMUP_CUTOFF = date(2008, 1, 1)

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

#: Appended to `reason` for any symbol in PROVENANCE_UNVERIFIED, so a caller
#: that only surfaces `reason` still sees the caveat. bxn.py and the CLI cite
#: this same text rather than restating it.
_PROVENANCE_CAVEAT = "; pre-2011 series not confirmed to belong to the current fund"

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
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-empty string")
    key = symbol.upper()
    if key not in ETF_REGISTRY:
        raise ValueError(f"{key} is not in the ETF registry")
    if key in DEFENSIVE_ETFS:
        raise ValueError(f"{key} is a defensive ETF; the ETF sleeve holds equity ETFs and cash only")
    for tier, members in (("qualified", QUALIFIED), ("no_2008_bars", NO_2008_BARS),
                          ("partial_2008", PARTIAL_2008), ("unfetchable", UNFETCHABLE),
                          ("income_proxy_only", INCOME_PROXY_ONLY)):
        if key in members:
            unverified = key in PROVENANCE_UNVERIFIED
            reason = _REASONS[tier] + (_PROVENANCE_CAVEAT if unverified else "")
            return Classification(symbol=key, tier=tier, admissible=(tier == "qualified"),
                                  reason=reason, provenance_unverified=unverified)
    raise ValueError(f"{key} is registered but untiered; add it to scripts/copilot/backtest/universe.py")


def default_candidates() -> tuple[str, ...]:
    """The pool a rule proposal may draw from (Q40=A: the engine proposes 8-12)."""
    return tuple(sorted(QUALIFIED))


def warmup_headroom_bars(symbols: Iterable[str], before: date = DEFAULT_WARMUP_CUTOFF) -> dict[str, int]:
    """Approximate trading bars each symbol has between its first bar and `before`.

    An ESTIMATE for a warning, never a gate: 252 bars/year is the standard
    rule of thumb, not a scheduled session count, and this does not touch
    exchange-calendars or any admission threshold. A lookback family consumes
    its warm-up from the start of the frame (engine.run skips the first
    `rule.warmup_bars` bars outright), so a symbol whose headroom before
    `before` is shorter than a rule's warmup_bars cannot supply that rule with
    real data over the period `before` is meant to represent -- and rejecting
    that rule's admission for such a universe is correct, not a bug, but the
    cause is invisible unless something reports the headroom. See ADR-0006's
    Consequences section.

    Raises for a symbol with no recorded FIRST_BAR entry rather than guessing;
    every QUALIFIED symbol has one (see
    test_first_bar_covers_every_qualified_symbol_exactly).
    """
    headroom: dict[str, int] = {}
    for symbol in symbols:
        key = symbol.upper()
        first = FIRST_BAR.get(key)
        if first is None:
            raise ValueError(f"{key} has no recorded first-bar date in FIRST_BAR")
        bars = round(max(0, (before - first).days) / DAYS_PER_YEAR * TRADING_DAYS_PER_YEAR)
        headroom[key] = bars
    return headroom

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
        """Count of bars dated in `year`. Descriptive only.

        This is a raw count, not a density guarantee: it says nothing about
        which days are missing or why. An admission decision must compare
        this against an expected session count for the year (e.g. an
        exchange calendar's ~252), never against zero or against this method
        alone — a frame can hold a single bar for a year and still return 1.
        """
        return sum(1 for d in self.dates if d.year == year)

    def span_years(self) -> float:
        """Calendar distance from the first bar to the last. Descriptive only.

        This is endpoint arithmetic, not a data-density guarantee: a 2-bar
        frame with dates 2008-01-02 and 2023-01-03 returns 15.003 here even
        though everything between those two bars is missing. Do not use this
        as an admission gate by itself — combine it with `sessions_in_year`
        against an expected session count.
        """
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

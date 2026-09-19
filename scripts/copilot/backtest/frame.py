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

    def __post_init__(self) -> None:
        """Validate on construction, not only through build().

        A bare frozen dataclass with no validation here meant every check
        build() used to perform was skippable by constructing PriceFrame
        directly -- and this repository is going open source, where a
        stranger has no reason to know build() is the only safe entry point.
        The module docstring's "worse than an exception" applies equally to
        a frame nobody validated. build() remains the normalizing entry
        point (upper-cased symbols, float-coerced and tuple-ized inputs);
        this only validates, so it runs unconditionally and cannot be
        bypassed.
        """
        if not self.dates or not self.symbols:
            raise ValueError("a price frame needs at least one date and one symbol")
        duplicates = sorted({s for s in self.symbols if self.symbols.count(s) > 1})
        if duplicates:
            raise ValueError(f"duplicate symbols: {', '.join(duplicates)}")
        if len(self.closes) != len(self.dates):
            raise ValueError(f"{len(self.dates)} dates but {len(self.closes)} rows")
        for i in range(1, len(self.dates)):
            if self.dates[i] <= self.dates[i - 1]:
                raise ValueError(
                    f"dates must be strictly increasing: {self.dates[i - 1]} then {self.dates[i]}")
        for i, row in enumerate(self.closes):
            if len(row) != len(self.symbols):
                raise ValueError(
                    f"row {i} ({self.dates[i]}) has {len(row)} values, expected {len(self.symbols)} values")
            for j, value in enumerate(row):
                if not math.isfinite(value) or value <= 0:
                    raise ValueError(
                        f"{self.symbols[j]} on {self.dates[i]}: close must be finite and positive, got {value}")

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
    """Normalize inputs and construct. All validation lives in `__post_init__`.

    This function's job is purely the normalization a caller should not have
    to do by hand -- upper-casing symbols, coercing every close to `float`,
    tuple-izing dates/symbols/closes. It cannot skip validation on a bad
    input: `PriceFrame.__post_init__` runs unconditionally as soon as this
    constructs the instance below.
    """
    rows = tuple(tuple(float(v) for v in row) for row in closes)
    upper = tuple(s.upper() for s in symbols)
    return PriceFrame(dates=tuple(dates), symbols=upper, closes=rows)

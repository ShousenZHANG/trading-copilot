"""Read the vendored SGE Au99.99 daily series. Stdlib only, no network.

The file this reads is a CACHE of one upstream (sge.com.cn), refreshed by
scripts/refresh_gold_history.py. It is not a second source and agreement
between it and SGEProvider is not corroboration -- see that script's docstring
and CLAUDE.md's "same upstream through two wrappers is one source".

Every refusal below exists because a silently-wrong price series is this
module's worst failure mode: the backtest would run, the admission gate would
pass, a rule would be adopted, and nothing downstream could tell. Sorting a
descending file or dropping a conflicting duplicate would each produce exactly
that, so both raise instead.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Sequence

from .frame import PriceFrame, build

SYMBOL = "GOLD.CNY"
DATASET = Path(__file__).resolve().parent.parent.parent.parent / "reference" / "sge-au9999-daily.csv"

#: Au99.99 traded roughly 230-1000 CNY/gram over the vendored window. These are
#: unit-slip guards, not market forecasts: a per-ounce or per-kilo series would
#: be off by 31x or 1000x and land far outside them.
MIN_PLAUSIBLE_CNY_PER_GRAM = 100.0
MAX_PLAUSIBLE_CNY_PER_GRAM = 5000.0


def _lines(path: Path | None) -> list[str]:
    target = Path(path) if path is not None else DATASET
    if not target.exists():
        raise FileNotFoundError(
            f"{target} is missing; regenerate it with "
            "`uv run --no-project --quiet --script scripts/refresh_gold_history.py`")
    return target.read_text(encoding="utf-8").splitlines()


def provenance(path: Path | None = None) -> dict:
    """The header block plus the span it describes."""
    meta: dict = {}
    for line in _lines(path):
        if not line.startswith("#"):
            break
        key, _, value = line[1:].partition(":")
        meta[key.strip()] = value.strip()
    for field in ("source_url", "upstream", "refreshed_at"):
        if not meta.get(field):
            raise ValueError(f"dataset provenance is incomplete: {field} is missing. "
                             "A price series with no stated origin cannot back an adoption.")
    parsed = rows(path)
    meta.update(first_session=parsed[0]["session"], last_session=parsed[-1]["session"],
                row_count=len(parsed))
    return meta


def rows(path: Path | None = None) -> list[dict]:
    """Ascending, deduplicated `{"session", "close"}` records."""
    parsed: list[dict] = []
    seen: dict[str, float] = {}
    for number, line in enumerate(_lines(path), start=1):
        if not line.strip() or line.startswith("#") or line.startswith("session,"):
            continue
        try:
            session, raw = line.split(",")
            close = float(raw)
        except ValueError as exc:
            raise ValueError(f"line {number}: expected `session,close`, got {line!r}") from exc
        date.fromisoformat(session)        # ValueError names the bad session itself
        if not MIN_PLAUSIBLE_CNY_PER_GRAM < close < MAX_PLAUSIBLE_CNY_PER_GRAM:
            if close <= 0:
                raise ValueError(f"{session}: close must be positive, got {close}")
            raise ValueError(f"{session}: close {close} is outside the plausible "
                             f"CNY-per-gram band; check the unit of the source series")
        if session in seen:
            if seen[session] != close:
                raise ValueError(f"{session}: conflicting closes {seen[session]} and {close}; "
                                 "the dataset is corrupt, not merely duplicated")
            continue
        if parsed and session <= parsed[-1]["session"]:
            raise ValueError(f"{session}: sessions must be ascending (previous was "
                             f"{parsed[-1]['session']}); refusing to sort, because a file "
                             "written out of order means the writer is broken")
        seen[session] = close
        parsed.append({"session": session, "close": close})
    if not parsed:
        raise ValueError("dataset contains no price rows")
    return parsed


def load(path: Path | None = None) -> PriceFrame:
    """Single-symbol frame for GOLD.CNY."""
    parsed = rows(path)
    return build(dates=[date.fromisoformat(r["session"]) for r in parsed],
                 symbols=[SYMBOL],
                 closes=[[r["close"]] for r in parsed])


def verify_tail(vendored: Sequence[dict], live: Sequence[dict], *, sessions: int = 20) -> dict:
    """Compare the newest `sessions` vendored rows against a live fetch.

    A session present in `vendored` but absent from `live` is reported with
    `live: None` rather than skipped: "the exchange no longer publishes this
    session" and "the tails agree" must not look the same to a caller.
    """
    live_by_session = {str(bar["session"]): float(bar["close"]) for bar in live}
    tail = list(vendored)[-max(1, int(sessions)):]
    mismatches = []
    for row in tail:
        session = str(row["session"])
        ours = float(row["close"])
        theirs = live_by_session.get(session)
        if theirs is None or abs(theirs - ours) > 1e-9:
            mismatches.append({"session": session, "vendored": ours, "live": theirs})
    return {"compared": len(tail), "mismatches": mismatches}

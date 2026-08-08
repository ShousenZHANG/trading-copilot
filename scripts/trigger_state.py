#!/usr/bin/env python3
"""Cross-run trigger dedup state for the deterministic portfolio check.

WHY THIS EXISTS
---------------
``scripts/portfolio_check.py`` used to be stateless: every invocation
re-evaluated the pre-committed trigger lines from scratch. Running it twice in
one day — the normal habit — re-fired the *same* breach and re-dispatched paid
Opus agents to re-answer a question that was already answered. That is real
money burned on a duplicate answer.

This module persists "which semantic trigger fired, and when", so a breach that
is still true but already acted on gets *reported* and *suppressed* instead of
re-dispatched.

Pattern source: TNT-Likely/PanWatch (MIT) — cooldown windows, ``repeat_mode=once``,
state-change (edge) triggering, and fail-open notification marking.

DESIGN RULES
------------
- ``filter_new_triggers`` is PURE: ``now`` is an injected parameter, never read
  from the clock inside the function. Only the CLI layer touches
  ``datetime.now``. Same inputs -> same output, so it is unit-testable.
- Fail-open: a missing/corrupt state file, an unreadable record, or an
  unparseable timestamp degrades to "never fired" (dispatch) rather than
  swallowing a real trigger. Missing a genuine breach costs more than one
  duplicate agent run.
- A suppressed trigger does NOT extend its own cooldown. Only a *dispatched*
  trigger updates ``last_fired``, so a persistently-true breach re-fires exactly
  once per TTL window instead of never.
- State is keyed by the trigger's SEMANTIC KIND, not by the formatted message:
  prices are embedded in the message and change every run, so hashing the whole
  string would defeat dedup entirely.

CLI
---
    python scripts/trigger_state.py --self-test
    python scripts/trigger_state.py --list
    python scripts/trigger_state.py --reset
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from runtime import force_utf8_stdio  # noqa: E402

force_utf8_stdio()

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "portfolio_state.json"
STATE_VERSION = 1
DEFAULT_TTL_HOURS = 24.0


@dataclass(frozen=True)
class TriggerRecord:
    """One semantic trigger's fire history. Timestamps are ISO-8601 UTC."""

    trigger_id: str
    first_seen: str
    last_fired: str
    fire_count: int


@dataclass(frozen=True)
class TriggerDecision:
    """Per-message verdict produced by :func:`filter_new_triggers`."""

    message: str
    trigger_id: str
    suppressed: bool
    hours_since_last_fire: float | None
    fire_count: int


# Semantic kinds. Each rule is (trigger_id, required lowercase substrings).
# Order matters: the NDQ 50d rule is checked before the NDQ stop rule because
# portfolio_check evaluates them in that same precedence.
_KIND_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("ndq_below_50d", ("ndq", "50d")),
    ("ndq_below_stop", ("ndq", "stop")),
    ("ioo_below_50d", ("ioo", "50d")),
    ("vix_systemic", ("vix",)),
    ("nvda_look_through", ("nvda", "look-through")),
)

_WORD_RE = re.compile(r"[a-z0-9]+")


def _slug(message: str, max_words: int = 4) -> str:
    """Fallback id for unknown kinds: first ``max_words`` non-numeric words.

    Tokens containing digits are dropped because trigger messages embed live
    prices and thresholds; keeping them would produce a new id every run.
    """
    words = [
        word
        for word in _WORD_RE.findall(message.lower())
        if not any(char.isdigit() for char in word)
    ]
    return "_".join(words[:max_words]) if words else "unknown_trigger"


def stable_trigger_id(message: str) -> str:
    """Map a formatted trigger message to a stable id for its semantic kind."""
    text = message.lower()
    for trigger_id, tokens in _KIND_RULES:
        if all(token in text for token in tokens):
            return trigger_id
    return _slug(message)


def _as_utc(moment: datetime) -> datetime:
    """Normalize to aware UTC; naive input is assumed to already be UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    return _as_utc(moment).replace(microsecond=0).isoformat()


def _parse_iso(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError):
        return None
    return _as_utc(parsed)


def _hours_since(record: TriggerRecord, now: datetime) -> float | None:
    """Hours since the last fire, or None when unusable (-> fail open).

    A negative age means the stored stamp is in the future (clock skew or a
    hand-edited file); treat it as unusable so a real breach still dispatches.
    """
    stamp = _parse_iso(record.last_fired)
    if stamp is None:
        return None
    hours = (_as_utc(now) - stamp).total_seconds() / 3600.0
    return hours if hours >= 0.0 else None


def filter_new_triggers(
    fired: Sequence[str],
    state: Mapping[str, TriggerRecord],
    now: datetime,
    ttl_hours: float = DEFAULT_TTL_HOURS,
) -> tuple[list[TriggerDecision], list[TriggerDecision]]:
    """Split fired trigger messages into (to_dispatch, suppressed).

    A trigger whose semantic kind already fired within ``ttl_hours`` is
    suppressed. ``now`` is injected, never read from the clock here.
    """
    to_dispatch: list[TriggerDecision] = []
    suppressed: list[TriggerDecision] = []
    seen_this_run: set[str] = set()
    for message in fired:
        trigger_id = stable_trigger_id(message)
        record = state.get(trigger_id)
        age = _hours_since(record, now) if record is not None else None
        count = record.fire_count if record is not None else 0
        if trigger_id in seen_this_run:
            suppressed.append(TriggerDecision(message, trigger_id, True, 0.0, count))
        elif record is not None and age is not None and age < ttl_hours:
            suppressed.append(TriggerDecision(message, trigger_id, True, age, count))
        else:
            seen_this_run.add(trigger_id)
            to_dispatch.append(TriggerDecision(message, trigger_id, False, age, count))
    return to_dispatch, suppressed


def record_fires(
    state: Mapping[str, TriggerRecord],
    dispatched: Sequence[TriggerDecision],
    now: datetime,
) -> dict[str, TriggerRecord]:
    """Return a NEW state mapping with dispatched triggers marked as fired."""
    stamp = _iso(now)
    updated = dict(state)
    for decision in dispatched:
        previous = updated.get(decision.trigger_id)
        if previous is None:
            updated[decision.trigger_id] = TriggerRecord(
                decision.trigger_id, stamp, stamp, 1
            )
        else:
            updated[decision.trigger_id] = replace(
                previous, last_fired=stamp, fire_count=previous.fire_count + 1
            )
    return updated


def _record_from_dict(key: str, value: object) -> TriggerRecord | None:
    if not isinstance(value, dict):
        return None
    first_seen = value.get("first_seen")
    last_fired = value.get("last_fired")
    if not isinstance(first_seen, str) or not isinstance(last_fired, str):
        return None
    try:
        fire_count = int(value.get("fire_count", 1))
    except (TypeError, ValueError):
        fire_count = 1
    trigger_id = value.get("trigger_id")
    if not isinstance(trigger_id, str) or not trigger_id:
        trigger_id = key
    return TriggerRecord(trigger_id, first_seen, last_fired, max(fire_count, 1))


def load_state(path: Path = STATE_PATH) -> dict[str, TriggerRecord]:
    """Load persisted state; any problem degrades to empty state (fail open)."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(
            f"warning: unreadable trigger state at {path} ({exc}); starting fresh",
            file=sys.stderr,
        )
        return {}
    records = raw.get("records") if isinstance(raw, dict) else None
    if not isinstance(records, dict):
        print(
            f"warning: unexpected trigger state schema at {path}; starting fresh",
            file=sys.stderr,
        )
        return {}
    loaded: dict[str, TriggerRecord] = {}
    for key, value in records.items():
        record = _record_from_dict(str(key), value)
        if record is not None:
            loaded[record.trigger_id] = record
    return loaded


def save_state(state: Mapping[str, TriggerRecord], path: Path = STATE_PATH) -> bool:
    """Atomically persist state. Returns False (with a warning) on I/O failure."""
    payload = {
        "version": STATE_VERSION,
        "records": {key: asdict(state[key]) for key in sorted(state)},
    }
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        print(f"warning: could not write trigger state to {path} ({exc})", file=sys.stderr)
        return False
    return True


def clear_state(path: Path = STATE_PATH) -> None:
    """Delete the state file so every trigger re-dispatches on the next run."""
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        print(f"warning: could not clear trigger state at {path} ({exc})", file=sys.stderr)


# --------------------------------------------------------------------------
# Built-in unit tests (deterministic). Run: python scripts/trigger_state.py --self-test
# --------------------------------------------------------------------------
def _dispatch_ids(
    fired: Sequence[str],
    state: Mapping[str, TriggerRecord],
    now: datetime,
    ttl: float = DEFAULT_TTL_HOURS,
) -> list[str]:
    return [d.trigger_id for d in filter_new_triggers(fired, state, now, ttl)[0]]


def _self_test() -> int:  # noqa: C901 - flat list of assertions, no nesting
    import tempfile

    t0 = datetime(2026, 1, 5, 9, 30, tzinfo=timezone.utc)
    ndq50_a = "NDQ below 50d SMA 56.61 — mid-term support lost"
    ndq50_b = "NDQ below 50d SMA 55.02 — mid-term support lost"  # same kind, new price
    ioo50 = "IOO below 50d SMA 186.00 — support lost"
    vix = "VIX 27.4 > 25 — systemic risk regime"
    nvda = "NVDA look-through 6.1% > 5% gate (standing breach)"

    empty: dict[str, TriggerRecord] = {}
    first, _ = filter_new_triggers([ndq50_a], empty, t0)
    after_first = record_fires(empty, first, t0)
    later = t0.replace(hour=13, minute=0)  # +3.5h
    tomorrow = datetime(2026, 1, 6, 11, 0, tzinfo=timezone.utc)  # +25.5h
    both = record_fires(empty, filter_new_triggers([ndq50_a, ioo50], empty, t0)[0], t0)
    skewed = {"vix_systemic": TriggerRecord("vix_systemic", "not-a-date", "not-a-date", 1)}

    cases: list[tuple[str, bool]] = [
        ("id map: ndq 50d", stable_trigger_id(ndq50_a) == "ndq_below_50d"),
        ("id map: ndq stop", stable_trigger_id("NDQ below stop 60.00 — trend damage")
            == "ndq_below_stop"),
        ("id map: ioo 50d", stable_trigger_id(ioo50) == "ioo_below_50d"),
        ("id map: vix", stable_trigger_id(vix) == "vix_systemic"),
        ("id map: nvda look-through", stable_trigger_id(nvda) == "nvda_look_through"),
        ("id map: unknown -> slug",
            stable_trigger_id("GOLD spot 4210 below floor") == "gold_spot_below_floor"),
        ("id map: slug ignores embedded numbers",
            stable_trigger_id("GOLD spot 4210 below floor")
            == stable_trigger_id("GOLD spot 3990 below floor")),
        ("first fire dispatches", _dispatch_ids([ndq50_a], empty, t0) == ["ndq_below_50d"]),
        ("immediate re-run suppressed", _dispatch_ids([ndq50_a], after_first, t0) == []),
        ("re-run inside TTL suppressed", _dispatch_ids([ndq50_a], after_first, later) == []),
        ("re-run after TTL dispatches", _dispatch_ids([ndq50_a], after_first, tomorrow)
            == ["ndq_below_50d"]),
        ("same kind at a DIFFERENT price still dedupes",
            _dispatch_ids([ndq50_b], after_first, later) == []),
        ("distinct triggers independent",
            _dispatch_ids([ndq50_a, ioo50], after_first, later) == ["ioo_below_50d"]),
        ("duplicate kind within one run collapses",
            _dispatch_ids([ndq50_a, ndq50_b], empty, t0) == ["ndq_below_50d"]),
        ("suppression does not extend the window",
            _dispatch_ids([ndq50_a], after_first, later) == []
            and _dispatch_ids([ndq50_a], after_first, tomorrow) == ["ndq_below_50d"]),
        ("ttl_hours=0 disables dedup",
            _dispatch_ids([ndq50_a], after_first, t0, 0.0) == ["ndq_below_50d"]),
        ("fire_count increments",
            record_fires(after_first, filter_new_triggers([ndq50_a], after_first, tomorrow)[0],
                         tomorrow)["ndq_below_50d"].fire_count == 2),
        ("record_fires does not mutate input", after_first["ndq_below_50d"].fire_count == 1),
        ("first_seen preserved across re-fires",
            record_fires(after_first, filter_new_triggers([ndq50_a], after_first, tomorrow)[0],
                         tomorrow)["ndq_below_50d"].first_seen
            == after_first["ndq_below_50d"].first_seen),
        ("two kinds recorded independently", sorted(both) == ["ioo_below_50d", "ndq_below_50d"]),
        ("unparseable timestamp fails open",
            _dispatch_ids([vix], skewed, t0) == ["vix_systemic"]),
        ("future timestamp fails open",
            _dispatch_ids([ndq50_a], record_fires(empty, first, tomorrow), t0)
            == ["ndq_below_50d"]),
        ("suppressed decision carries age and count",
            round(filter_new_triggers([ndq50_a], after_first, later)[1][0]
                  .hours_since_last_fire or 0.0, 2) == 3.5),
    ]

    with tempfile.TemporaryDirectory() as tmpdir:
        missing = Path(tmpdir) / "nope" / "portfolio_state.json"
        corrupt = Path(tmpdir) / "corrupt.json"
        corrupt.write_text("{not json at all", encoding="utf-8")
        wrong = Path(tmpdir) / "wrong.json"
        wrong.write_text('{"version": 1, "records": []}', encoding="utf-8")
        roundtrip = Path(tmpdir) / "sub" / "state.json"
        save_state(after_first, roundtrip)
        cases += [
            ("missing state file -> empty", load_state(missing) == {}),
            ("corrupt state file recovers", load_state(corrupt) == {}),
            ("bad schema recovers", load_state(wrong) == {}),
            ("save/load round-trip", load_state(roundtrip) == dict(after_first)),
            ("save creates parent dirs", roundtrip.exists()),
            ("reload still suppresses", _dispatch_ids([ndq50_b], load_state(roundtrip), later) == []),
        ]
        clear_state(roundtrip)
        cases.append(("clear_state removes file", not roundtrip.exists()))
        cases.append(("clear_state is idempotent", (clear_state(roundtrip), True)[1]))

    passed = 0
    for label, ok in cases:
        passed += ok
        print(f"  {'ok ' if ok else 'XX '} {label}")
    print(f"\n{passed}/{len(cases)} trigger_state unit tests passed.")
    return 0 if passed == len(cases) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="Trigger dedup state for portfolio_check")
    ap.add_argument("--state-file", default=str(STATE_PATH), help="state JSON path")
    ap.add_argument("--reset", action="store_true", help="delete the state file")
    ap.add_argument("--self-test", action="store_true", help="run built-in unit tests")
    args = ap.parse_args()

    if args.self_test:
        return _self_test()

    path = Path(args.state_file)
    if args.reset:
        clear_state(path)
        print(f"cleared trigger state: {path}")
        return 0

    state = load_state(path)
    if not state:
        print(f"no trigger state recorded at {path}")
        return 0
    print(f"trigger state ({path}):")
    for key in sorted(state):
        record = state[key]
        print(
            f"  {record.trigger_id:20} last_fired {record.last_fired}  "
            f"first_seen {record.first_seen}  fires {record.fire_count}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())

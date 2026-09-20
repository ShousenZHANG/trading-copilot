"""The bounded news brake. One-way by construction: it can only ever reduce.

WHY IT IS SHAPED THIS WAY
Q35 put macro surprise into the rules and news into a bounded adjustment.
ADR-0005 clause 1 then removed the macro leg, because Finnhub's economic
calendar is premium and returns HTTP 403 -- as does its news-sentiment endpoint,
which is not recorded anywhere else and was found by probing. What survives is
news alone, and news cannot be backtested here: the company-news archive is one
rolling year, and a request beyond it returns HTTP 200 with an empty array, so a
replay harness would report success while testing nothing. Coverage also spans
two orders of magnitude across the universe -- 240 items in seven days for SPY
against 2 for VWO -- so no count threshold could be calibrated.

The Q42 answer fixed the consequence: something with no evidence behind it gets
a veto, never an accelerator. `apply` cannot return more than it was given, and
a test asserts that across the whole level set.

The model selects the level and states a reason; the engine bounds it. That is
ADR-0004 clause 3: the model may apply the brake, and nothing else.

ZERO IS A RESULT, NOT AN ERROR
`skip` returns 0, and `reduce_50` returns 0 for a single share. Callers turn that
into a hold with no quantity. The first draft wrote the post-brake quantity into
a proposal while gating on the pre-brake one, so `skip` hit policy's
positive-number check and raised out of an MCP tool with no try/except.

BRAKE EVIDENCE IS NOT PROPOSAL EVIDENCE
News records carry `critical_evidence_eligible: False` (research_data.py:97,351).
Citing one in a proposal's `evidence_ids` makes policy emit
"evidence {eid} cannot support a critical recommendation claim" and the whole
decision becomes data_insufficient. Brake evidence travels in its own field and
is never merged. `evaluate_rule` verifies each id against the snapshot; this
module only enforces shape.
"""
from __future__ import annotations

from typing import Mapping, Sequence

#: Exactly the three the user approved. Widening this set is a design change,
#: not a refactor: every extra level is another unbacktested degree of freedom.
LEVELS = ("none", "reduce_50", "skip")

DISCLOSURE = "新闻刹车未回测：它只能减少或跳过，永远不能加仓"


def _quantity(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"quantity must be an int, got {type(value).__name__}")
    if value < 0:
        raise ValueError(f"quantity must not be negative, got {value}")
    return value


def apply(*, level: str, quantity: int) -> int:
    """Post-brake quantity. Never greater than `quantity`; 0 is a valid result."""
    if level not in LEVELS:
        raise ValueError(f"brake level must be one of {LEVELS}, got {level!r}")
    count = _quantity(quantity)
    if level == "skip":
        return 0
    if level == "reduce_50":
        return count // 2      # floor: halving must never round up
    return count


def record(*, level: str, reason: str, evidence_ids: Sequence[str]) -> dict:
    """Build the auditable brake record carried on every decision."""
    if level not in LEVELS:
        raise ValueError(f"brake level must be one of {LEVELS}, got {level!r}")
    if isinstance(evidence_ids, str) or (evidence_ids is not None
                                         and not isinstance(evidence_ids, (list, tuple))):
        raise ValueError("evidence_ids must be a list of evidence ids, not a bare string")
    ids = [str(e) for e in (evidence_ids or [])]
    text = str(reason or "").strip()
    if level != "none":
        if not text:
            raise ValueError("a brake that changes the order requires a stated reason")
        if not ids:
            raise ValueError("a brake that changes the order requires at least one "
                             "evidence_ids entry naming what it read")
    return {"level": level, "reason": text, "evidence_ids": ids,
            "backtested": False, "disclosure": DISCLOSURE}


def applied(*, record: Mapping[str, object], quantity: int) -> dict:
    """Apply a brake record to a quantity, reporting both sides."""
    level = str(record.get("level", "none"))
    post = apply(level=level, quantity=quantity)
    return {"level": level, "reason": record.get("reason", ""),
            "evidence_ids": list(record.get("evidence_ids") or []),
            "backtested": False, "disclosure": DISCLOSURE,
            "pre_brake_quantity": _quantity(quantity), "post_brake_quantity": post}

"""A merchant gold ask the USER reports, turned into snapshot evidence.

WHY THIS EXISTS
policy.py's gold branch requires a proposal's `retail_quote` to equal a
`retail_quote` field on a snapshot evidence record, and it makes that the ONLY
way a gold proposal can become executable (GOLD.CNY is `tradable: false`).
Nothing in this repository has ever written that field, so the gate was
unreachable by construction: every gold answer was research-only, for a reason
no reader could act on.

WHY THE USER REPORTS IT
There is no public source for a Chinese retail gold ask. Probing akshare on
2026-09-20 found the exchange's own intraday ticks, the exchange's AM/PM
fixings, and World Gold Council tonnage -- three exchange-side series and no
merchant. A bank's 积存金 price lives behind that bank's authenticated app.
So the number comes from the person holding the phone.

WHAT THIS VALIDATES, AND WHAT IT DOES NOT
It validates SHAPE and FRESHNESS: a named merchant, a named product, a price in
CNY per fine gram inside a plausible band, and a timezone-aware observation the
gate will re-check against its own 24-hour window. It does NOT verify the price.
The record says `verification: self_reported` and carries no field a reader
could mistake for a confirmation. See ADR-0008 clause 3.

The gate's purpose is to stop an SGE BENCHMARK being spent as a PURCHASE PRICE.
A reported merchant ask, unverified, is the right kind of number; the benchmark
is the wrong kind at any level of verification.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime

SYMBOL = "GOLD.CNY"

#: The SGE benchmark sat near 947 CNY/gram on 2026-09-18 and retail asks carry a
#: premium over it. This band is wide enough for a decade of drift in either
#: direction and narrow enough to catch the one error that actually happens: a
#: per-OUNCE figure (about 31x larger) typed into a per-gram field, which would
#: size the contribution 31x too small.
MIN_PLAUSIBLE_ASK = 100.0
MAX_PLAUSIBLE_ASK = 5000.0


def _text(name: str, value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string naming who quoted the price, "
                         f"got {value!r}")
    return value.strip()


def _observed(value) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("observed_at must be an ISO-8601 timestamp string")
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError as exc:
        raise ValueError(f"observed_at is not an ISO-8601 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ValueError("observed_at must carry a timezone offset; a naive timestamp "
                         "cannot be compared against the snapshot clock")
    return parsed.isoformat()


def build(*, merchant: str, product: str, ask_per_fine_gram: float, observed_at: str,
          purity: str = "0.9999", note: str = "") -> dict:
    """Validate a reported merchant quote. Shape and freshness only."""
    price = float(ask_per_fine_gram)
    if not price > 0:
        raise ValueError(f"ask_per_fine_gram must be positive, got {ask_per_fine_gram!r}")
    if not MIN_PLAUSIBLE_ASK < price < MAX_PLAUSIBLE_ASK:
        raise ValueError(f"ask_per_fine_gram {price} is outside the plausible band for a "
                         f"price per fine gram in CNY; a per-ounce figure belongs in a "
                         f"different field")
    quote = {"merchant": _text("merchant", merchant),
             "product": _text("product", product),
             "currency": "CNY", "unit": "gram",
             "ask_per_fine_gram": price,
             "purity": str(purity),
             "observed_at": _observed(observed_at),
             "verification": "self_reported"}
    if note:
        quote["note"] = str(note)
    return quote


def _reseal(snapshot: dict) -> dict:
    # Delegated rather than reimplemented. The content address is one canonical
    # form, and policy.py calls market_data.verify_snapshot on every assessment;
    # a second copy of the JSON separators, sort order or prefix here would keep
    # agreeing until the day snapshot_digest changed, and then every snapshot
    # this module wrote would fail integrity verification for no visible reason.
    # Imported lazily for the reason policy.py states at its own call site:
    # market_data pulls in the network adapters, and a CLI client that only
    # records a quote should not load them.
    from .market_data import snapshot_digest

    snapshot["snapshot_id"] = snapshot_digest(snapshot)
    return snapshot


def attach(snapshot: dict, quote: dict, *, now: datetime) -> dict:
    """Return a NEW snapshot carrying the quote as its own evidence record.

    The input is deep-copied and resealed. A snapshot is content-addressed and
    immutable in the journal; adding evidence in place would leave the stored
    id describing different bytes.
    """
    if SYMBOL not in snapshot.get("instruments", {}):
        raise ValueError(f"snapshot {snapshot.get('snapshot_id')} carries no {SYMBOL} "
                         "instrument, so a gold quote has nothing to attach to")
    updated = copy.deepcopy(snapshot)
    payload = copy.deepcopy(quote)
    evidence_id = "retail_" + hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False,
                   separators=(",", ":"), allow_nan=False).encode()).hexdigest()[:16]
    if any(record.get("evidence_id") == evidence_id
           for record in snapshot.get("evidence", []) if isinstance(record, dict)):
        # Attaching the same quote to a snapshot that already carries it would
        # put two records under one evidence_id, and the next assess_proposal
        # would raise "duplicate evidence_id" out of a tool call -- far from the
        # mistake, and unreadable. The id is a hash of the quote alone, so an
        # identical re-report is detectable here and nowhere later.
        raise ValueError(f"snapshot {snapshot.get('snapshot_id')} already carries this exact "
                         f"quote as {evidence_id}; re-report it against the original snapshot, "
                         "or change what was observed")
    # The id of the record goes INSIDE the payload, because that is how the gate
    # finds the record: policy's gold branch does
    # `evidence.get(proposal["retail_quote"]["evidence_id"])` and then demands
    # `rec["retail_quote"] == quote`. A payload without this field resolves to
    # no record at all, so retail_quote reads "unknown" however valid the quote
    # is -- which is the unreachable state this whole module exists to end. The
    # id is derived from the quote WITHOUT it, so it stays deterministic.
    payload["evidence_id"] = evidence_id
    record = {
        "evidence_id": evidence_id,
        "provider": "user_reported",
        "upstream": payload["merchant"],
        "source_url": None,
        "instrument_id": SYMBOL,
        "asset_class": "physical_gold",
        "currency": "CNY",
        "unit": "gram",
        "price_kind": "merchant_retail_ask",
        "observed_at": payload["observed_at"],
        "retrieved_at": now.isoformat(),
        "status": "ok",
        # News-style flag: a self-reported number must never be citable as the
        # critical evidence behind a recommendation's factual claims. It is a
        # price to transact at, not a fact about the market.
        "critical_evidence_eligible": False,
        "retail_quote": payload,
    }
    updated["evidence"] = list(updated.get("evidence", [])) + [record]
    instrument = updated["instruments"][SYMBOL]
    # The id is deliberately NOT appended to instrument["evidence_ids"]. That
    # list is the instrument's MARKET evidence -- policy derives market_ids from
    # it and then validates every id in that set on EVERY proposal, cited or
    # not, demanding a source_url, a matching session and a matching price_kind.
    # A merchant ask has no URL, is not a session close, and is not the
    # instrument's price_kind, so appending it made attaching a quote turn a
    # previously valid gold research answer into "evidence ... is absent or
    # unverified" -- capturing a price would have broken the answers that need
    # no price. Consumers find this record by scanning evidence for
    # `retail_quote` with `instrument_id == "GOLD.CNY"`, which is how it is
    # already written.
    instrument["issues"] = [i for i in instrument.get("issues", [])
                            if i != "retail_product_sell_buyback_quotes_required_for_purchase_price"]
    return _reseal(updated)

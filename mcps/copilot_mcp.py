# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp[cli]>=1.2.0,<2", "yfinance==1.7.0", "exchange-calendars==4.13.2", "tzdata==2026.3"]
# ///
"""Conversation tools backed by the shared, local evidence and operation journal."""
from pathlib import Path
import sys

# Load native NumPy/pandas dependencies before AnyIO starts the stdio threads.
# On Windows, first importing NumPy inside a running MCP call can stall in its
# native module loader; a transport-only handshake did not reveal that failure.
import exchange_calendars  # noqa: F401
import yfinance  # noqa: F401

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from copilot import service  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("trading-copilot")


@mcp.tool()
def collect_market_snapshot(instrument_ids: list[str], horizon: str = "daily") -> dict:
    """Fetch and persist current free data. GOLD.CNY means Chinese investment bullion.

    Read status/quality_status: a successful tool call can return blocked data.
    Only reference returned evidence IDs. No personal transaction is created.
    """
    return service.snapshot_view(service.collect(instrument_ids, horizon))


@mcp.tool()
def get_evidence_snapshot(snapshot_id: str, full: bool = False) -> dict:
    """Read a previously collected snapshot, preserving its original expiry and timestamps."""
    return service.snapshot_view(service.snapshot(snapshot_id), full=full)


@mcp.tool()
def get_investment_context(instrument_ids: list[str] | None = None, sleeve: str = "etf") -> dict:
    """Read operations/decisions and coverage for one sleeve: etf (USD) or gold (CNY)."""
    return service.context(instrument_ids, sleeve=sleeve)


@mcp.tool()
def assess_investment_proposal(snapshot_id: str, proposal: dict) -> dict:
    """Check a structured proposal against stored evidence and current holdings; persist decision.

    Required proposal keys: instrument_id, action (buy/hold/reduce/sell/avoid),
    mode (tactical/accumulation), horizon (daily/swing/long_term), reasons,
    conditions, evidence_ids. Return the assessed message, not the proposed action.
    Missing or expired evidence pauses advice. This never executes a trade.
    """
    return service.review(snapshot_id, proposal)


@mcp.tool()
def record_investment_operation(operation: dict, idempotency_key: str) -> dict:
    """Record an explicit user-reported execution, intent, completion, correction or reversal.

    Preserve the user's original statement. execution_status=executed only for an
    actual completed operation; missing details stay pending. Use a stable key
    for retries. Never turn a recommendation into a filled trade. Return receipt
    only after commit. Corrections reference operation_id and expected_version.
    """
    return service.record(operation, idempotency_key)


@mcp.tool()
def get_data_capabilities() -> dict:
    """Check credential presence without revealing values. Does not prove live entitlement."""
    return service.capabilities()


@mcp.tool()
def declare_holdings_coverage(base_currency: str = "USD", sleeve: str = "etf") -> dict:
    """Record that the user has confirmed their recorded holdings are complete.

    Call this only when the user has explicitly said so. It is their assertion,
    not an inference from what happens to be recorded. Any later trade
    invalidates it and it must be made again.

    A declaration covers ONE sleeve in ONE currency and never crosses: "etf" is
    the USD book, "gold" the CNY book. Declaring USD coverage does not let a
    gold order be sized, and declaring gold coverage says nothing about the ETFs.
    """
    return service.declare_coverage(sleeve=sleeve, base_currency=base_currency)


@mcp.tool()
def record_retail_gold_quote(snapshot_id: str, merchant: str, product: str,
                             ask_per_fine_gram: float, observed_at: str,
                             account_id: str | None = None) -> dict:
    """Attach a merchant gold ask YOU observed to a stored snapshot.

    The Shanghai Gold Exchange benchmark is not a price anyone can buy at, so a
    gold order cannot be sized without this. The system validates the shape and
    the freshness of what you report; it does not and cannot verify the price.
    `observed_at` must be ISO-8601 with a timezone offset and within 24 hours.
    Supply `account_id` only when the user explicitly identifies that account,
    using the same local id as its recorded fills. Without it, the daily quota
    conservatively includes all accounts; merchant text does not identify one.
    """
    return service.capture_retail_quote(
        snapshot_id=snapshot_id, merchant=merchant, product=product,
        ask_per_fine_gram=ask_per_fine_gram, observed_at=observed_at,
        account_id=account_id)


@mcp.tool()
def evaluate_adopted_rule(snapshot_id: str, sleeve: str = "etf", brake_level: str = "none",
                          brake_reason: str = "",
                          brake_evidence_ids: list[str] | None = None) -> dict:
    """Run the adopted rule against a stored snapshot. The engine computes every number.

    You cannot supply a quantity, price or rule id. The only thing you choose is
    the brake: "none", "reduce_50" or "skip", which may reduce or cancel a
    purchase and can never enlarge one or change a sale. A non-"none" level needs
    a stated reason and at least one news evidence id from the snapshot, and is
    reported as not backtested. Report the engine's orders, never your own
    figures.

    `sleeve` picks which adopted rule runs: "etf" (USD, whole shares) or "gold"
    (CNY, grams of Au99.99). A gold evaluation additionally needs a CNY coverage
    declaration and a merchant quote recorded by record_retail_gold_quote;
    without either it returns a research view with no gram figure.
    """
    return service.evaluate(snapshot_id=snapshot_id, sleeve=sleeve,
                            brake={"level": brake_level, "reason": brake_reason,
                                   "evidence_ids": brake_evidence_ids or []})


if __name__ == "__main__":
    mcp.run(transport="stdio")

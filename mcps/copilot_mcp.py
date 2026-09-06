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
def get_investment_context(instrument_ids: list[str] | None = None) -> dict:
    """Read actual operations and prior decisions. Unknown portfolio completeness stays unknown."""
    return service.context(instrument_ids)


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


if __name__ == "__main__":
    mcp.run(transport="stdio")

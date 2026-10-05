# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp[cli]==1.30.0", "yfinance==1.7.0", "exchange-calendars==4.13.2", "tzdata==2026.3"]
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
def collect_market_snapshot(instrument_ids: list[str], horizon: str = "daily",
                            allow_us_stocks: bool = False) -> dict:
    """Fetch and persist current free data. GOLD.CNY means Chinese investment bullion.

    Read status/quality_status: a successful tool call can return blocked data.
    Only reference returned evidence IDs. No personal transaction is created.
    """
    return service.snapshot_view(service.collect(instrument_ids, horizon, allow_us_stocks=allow_us_stocks))


@mcp.tool()
def get_evidence_snapshot(snapshot_id: str, full: bool = False) -> dict:
    """Read a previously collected snapshot, preserving its original expiry and timestamps."""
    return service.snapshot_view(service.snapshot(snapshot_id), full=full)


@mcp.tool()
def get_investment_context(instrument_ids: list[str] | None = None, sleeve: str = "etf") -> dict:
    """Read a minimal sanitized holdings/coverage summary; full raw statements stay local."""
    return service.advisor_context(instrument_ids, sleeve=sleeve)


@mcp.tool()
def get_operation_context(operation_id: str) -> dict:
    """Read the bounded structured fields needed to correct one user's local operation."""
    return service.operation_context(operation_id)


@mcp.tool()
def analyze_market_signals(snapshot_id: str, instrument_ids: list[str] | None = None,
                            horizon: str = "swing") -> dict:
    """Compute reproducible research signals from a saved snapshot. Never outputs orders."""
    return service.analyze_signals(snapshot_id, instrument_ids, horizon)


@mcp.tool()
def scan_investment_opportunities(instrument_ids: list[str] | None = None,
                                  horizon: str = "swing") -> dict:
    """Research the configured candidate pool plus a benchmark; retain missing/rejected candidates."""
    return service.research_scan(instrument_ids, horizon)


@mcp.tool()
def collect_broker_snapshot(instrument_ids: list[str] | None = None) -> dict:
    """Read IBKR account/positions/cash/orders/fills/actual quotes. No order API exists.

    This is optional and disabled until configured locally. Inspect complete,
    coverage and issues; requested real-time data never proves received entitlement.
    """
    return service.broker_snapshot(instrument_ids)


@mcp.tool()
def prepare_manual_trade_plan(research_snapshot_id: str, execution_snapshot_id: str,
                               mode: str = "long_term") -> dict:
    """Calculate a funded review card from saved research and fresh broker facts.

    Prices, whole-share quantities and fees come from the adopted local rule
    and confirmed risk configuration. No adopted template or missing prerequisites
    returns blocked/research_only. A review card never submits an order.
    """
    return service.prepare_trade_plan(research_snapshot_id, execution_snapshot_id, mode)


@mcp.tool()
def get_manual_trade_plan(plan_id: str) -> dict:
    """Read a saved card without reviving expired or invalidated order figures."""
    return service.get_trade_plan(plan_id)


@mcp.tool()
def confirm_manual_plan_review(plan_id: str, statement: str, expected_version: int,
                                outcome: str = "approve") -> dict:
    """Record the user's explicit Review of this card/version, never infer approval.

    Supply the actual user statement and matching outcome approve/reject/cancel.
    A refusal never becomes approve; do not rewrite the user's words to gain approval.
    This is a local receipt; the user still trades manually after fresh preflight.
    """
    return service.confirm_plan_review(plan_id, statement, expected_version, outcome)


@mcp.tool()
def revalidate_manual_trade_plan(plan_id: str, expected_version: int) -> dict:
    """Refresh IBKR immediately before manual action and invalidate changed orders/cash/quotes."""
    return service.revalidate_trade_plan(plan_id, expected_version)


@mcp.tool()
def confirm_account_cash_flow(previous_snapshot_id: str, execution_snapshot_id: str,
                               net_external_flow_usd: str, statement: str) -> dict:
    """Record explicitly confirmed net deposits/withdrawals for this observation interval.

    Zero also needs explicit user confirmation. Never infer cash flow from NAV
    change; this affects return/drawdown, not holdings or available broker cash.
    """
    return service.confirm_broker_cash_flow(previous_snapshot_id, execution_snapshot_id,
                                          net_external_flow_usd, statement)


@mcp.tool()
def confirm_holding_modes(execution_snapshot_id: str, allocations: dict, statement: str,
                          expected_account_version: str, expected_version: int | None = None,
                            entry_sessions: dict | None = None, entry_plan_ids: dict | None = None) -> dict:
    """Record explicit user assignment of actual held shares to long_term/swing.

    allocations is symbol -> {long_term: decimal-string shares, swing: shares}.
    Total assignment cannot exceed broker holdings. Optional entry_sessions
    supplies user-confirmed actual entry dates for time exits; entry_plan_ids
    links actual swing lots to a frozen entry card's protective reference.
    This link does not assert that Review caused a fill. Fill/ownership
    changes need renewed confirmation; ticker matching never guesses mode/date.
    """
    return service.confirm_mode_allocations(execution_snapshot_id, allocations, statement,
                                            expected_account_version, expected_version,
                                            entry_sessions=entry_sessions, entry_plan_ids=entry_plan_ids)


@mcp.tool()
def confirm_strategy_execution_state(execution_snapshot_id: str, rule_id: str, mode: str,
                                     statement: str, expected_account_version: str,
                                     last_execution_session: str | None = None,
                                     no_prior_executions: bool = False,
                                     expected_version: int | None = None) -> dict:
    """Record actual user-confirmed strategy cadence, not a model inference.

    The user confirms either first activation with no prior executions or the
    last actual completed execution session. This is bound to current account
    observations and the configured adopted rule. Review is not a fill.
    """
    return service.confirm_strategy_execution_state(execution_snapshot_id, rule_id, mode,
        statement, expected_account_version, last_execution_session, no_prior_executions, expected_version)


@mcp.tool()
def assess_investment_proposal(snapshot_id: str, proposal: dict) -> dict:
    """Check a structured proposal against stored evidence and current holdings; persist decision.

    Required proposal keys: instrument_id, action (buy/hold/reduce/sell/avoid),
    mode (tactical/accumulation), horizon (daily/swing/long_term), reasons,
    conditions, evidence_ids. Return the assessed message, not the proposed action.
    Missing or expired evidence pauses advice. This never executes a trade.
    """
    return service.public_response(service.review(snapshot_id, proposal))


@mcp.tool()
def record_investment_operation(operation: dict, idempotency_key: str) -> dict:
    """Record an explicit user-reported execution, intent, completion, correction or reversal.

    Preserve the user's original statement. execution_status=executed only for an
    actual completed operation; missing details stay pending. Use a stable key
    for retries. Never turn a recommendation into a filled trade. Return receipt
    only after commit. Corrections reference operation_id and expected_version.
    """
    return service.public_response(service.record(operation, idempotency_key))


@mcp.tool()
def get_data_capabilities() -> dict:
    """Check credential presence without revealing values. Does not prove live entitlement."""
    return service.capabilities()


@mcp.tool()
def get_execution_readiness(execution_snapshot_id: str | None = None,
                            research_snapshot_id: str | None = None,
                            route: str = 'strategy', instrument_ids: list[str] | None = None) -> dict:
    """Read all prerequisites at once; never authorize orders.

    Act on named checks: intent awaits human confirmation, drawdown is computed
    by the plan compiler, income evidence is advisory. Overall needs_evidence
    does not require historical strategy adoption on the user_directed route.
    """
    return service.execution_readiness(execution_snapshot_id=execution_snapshot_id,
        research_snapshot_id=research_snapshot_id, route=route, instrument_ids=instrument_ids)


@mcp.tool()
def collect_income_evidence(instrument_ids: list[str]) -> dict:
    """Store official issuer/exchange QQQI/JEPQ distribution events; incomplete coverage stays visible."""
    return service.collect_distributions(instrument_ids)


@mcp.tool()
def analyze_income_evidence(snapshot_id: str, parameters: dict | None = None) -> dict:
    """Analyze stored distributions, raw NAV total return and explicit withholding/FX scenarios.

    parameters optionally includes prices, nav_history, withholding_scenarios,
    fx_scenarios. Unknown tax and future payouts are never guessed.
    """
    return service.income_report(snapshot_id, parameters)


@mcp.tool()
def compare_income_portfolios(snapshot_id: str, parameters: dict) -> dict:
    """Compare bounded integer holdings under explicit income objectives. Research only, no orders.

    parameters requires prices, budget_usd, cost_model (per_share_usd, minimum_usd,
    other_cost_bps), constraints (cash_floor_pct, weight_bounds by symbol min/max).
    Optional objective, withholding_scenarios, fx_scenarios, nav_history, existing_holdings.
    Never treats highest historic distributions as the highest future total return.
    """
    return service.income_report(snapshot_id, parameters, compare=True)


@mcp.tool()
def record_distribution_receipt(receipt: dict, idempotency_key: str) -> dict:
    """Record actual user-reported credited cash, not announcements. Requires original human statement.

    receipt: statement/source_message_id/opening_balance_id/instrument_id/payment_date/
    currency/net_amount; gross_amount and withholding_amount remain null if unknown.
    Corrections/reversals require event_type, operation_id and expected_version.
    Does not change broker cash, holdings or external deposit/withdrawal declarations.
    """
    return service.record_distribution_receipt(receipt,idempotency_key)


@mcp.tool()
def get_distribution_receipts() -> dict:
    """Read sanitized committed distribution receipt history and versions."""
    return service.distribution_receipts()


@mcp.tool()
def confirm_manual_allocation_intent(execution_snapshot_id: str, research_snapshot_id: str,
                                     intent: dict, statement: str, expected_account_version: str) -> dict:
    """Record the human's actual one-off ETF targets/budget, sales and limits. No invented confirmation.

    intent: kind buy_budget_usd or target_shares, targets symbol->decimal string,
    allowed_sells list, risk_limits containing cash_floor_pct, max_single_name_pct,
    max_trade_notional_pct and long_term_nav_cap_pct. This does not adopt a strategy.
    """
    return service.confirm_manual_intent(execution_snapshot_id, research_snapshot_id,
        intent, statement, expected_account_version)


@mcp.tool()
def prepare_directed_trade_plan(intent_id: str) -> dict:
    """Compute a one-off manual ETF card from a saved explicit intent and fresh stored snapshots.

    No alpha validation claim. Follow with actual human Review and fresh preflight.
    """
    return service.prepare_directed_plan(intent_id)


@mcp.tool()
def confirm_opening_holdings(execution_snapshot_id: str, statement: str, source_message_id: str,
                             idempotency_key: str, expected_account_version: str,
                             expected_portfolio_version: str, event_type: str = 'record',
                             operation_id: str | None = None, expected_version: int | None = None) -> dict:
    """Record/correct/reverse explicitly confirmed broker opening holdings; never invent historical fills or tax basis."""
    return service.confirm_opening_balance(execution_snapshot_id, statement, source_message_id,
        idempotency_key, expected_account_version, expected_portfolio_version,
        event_type, operation_id, expected_version)


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
    return service.public_response(service.capture_retail_quote(
        snapshot_id=snapshot_id, merchant=merchant, product=product,
        ask_per_fine_gram=ask_per_fine_gram, observed_at=observed_at,
        account_id=account_id))


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
    return service.public_response(service.evaluate(snapshot_id=snapshot_id, sleeve=sleeve,
                            brake={"level": brake_level, "reason": brake_reason,
                                   "evidence_ids": brake_evidence_ids or []}))


if __name__ == "__main__":
    mcp.run(transport="stdio")

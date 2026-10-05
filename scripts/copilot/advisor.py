"""Allow-listed conversational projections; the full local journal remains private."""
from __future__ import annotations

import copy
from decimal import Decimal, InvalidOperation


PRIVATE_FIELDS = frozenset({
    "account_id", "account_number", "acctNumber", "acct_number", "account_key",
    "holding_key", "external_trade_id", "external_payment_id", "source_message_id", "statement",
    "original_statement", "operation_ids", "execution_id", "order_key", "perm_id",
    "local_path", "path", "email_to", "confirmation", "data_attestation", "history_attestation",
})


def public_view(value):
    """Strip local identifiers from tool results without changing persisted inputs."""
    if isinstance(value, dict):
        claim_path = (isinstance(value.get("evidence_id"), str)
                      and isinstance(value.get("path"), str) and value["path"].startswith("/"))
        return {key: public_view(item) for key, item in value.items()
                if key not in PRIVATE_FIELDS or (key == "path" and claim_path)}
    if isinstance(value, (list, tuple)):
        return [public_view(item) for item in value]
    return copy.deepcopy(value)


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _decimal(value):
    return format(value, "f") if value is not None else None


def _holdings(rows):
    groups = {}
    accounts = set()
    for row in rows:
        accounts.add(row.get("account_id"))
        # Gold item counts with different weights/purities cannot be added as
        # if they were one fungible retail product. Currency is never omitted.
        identity = {key: row.get(key) for key in ("instrument_id", "currency", "unit")}
        if row.get("instrument_id") == "GOLD.CNY":
            identity.update({key: row.get(key) for key in ("purity", "weight_grams")})
        key = tuple(identity.items())
        aggregate = groups.setdefault(key, {
            **identity, "quantity": Decimal(0), "gross_cost_basis": Decimal(0),
            "cost_basis_including_fees": Decimal(0), "pure_gold_grams": Decimal(0),
            "opening_balance_required": False, "fees_unknown": False,
        })
        for field in ("quantity", "gross_cost_basis", "cost_basis_including_fees"):
            parsed = _number(row.get(field))
            aggregate[field] = (aggregate[field] + parsed
                                if aggregate[field] is not None and parsed is not None else None)
        if row.get("instrument_id") == "GOLD.CNY":
            parsed = _number(row.get("pure_gold_grams"))
            aggregate["pure_gold_grams"] = (aggregate["pure_gold_grams"] + parsed
                                           if aggregate["pure_gold_grams"] is not None and parsed is not None else None)
        aggregate["opening_balance_required"] |= row.get("opening_balance_required") is True
        aggregate["fees_unknown"] |= row.get("fees_unknown") is True
    result = []
    for aggregate in groups.values():
        for field in ("quantity", "gross_cost_basis", "cost_basis_including_fees", "pure_gold_grams"):
            aggregate[field] = _decimal(aggregate[field])
        if aggregate["instrument_id"] != "GOLD.CNY":
            aggregate.pop("pure_gold_grams")
        aggregate["scope"] = "combined_research_summary; not account-specific buying power"
        result.append(aggregate)
    return result, len(accounts) > 1


def operation_summary(state):
    """Enough to identify/correct a record without returning the user's raw text."""
    result = {key: copy.deepcopy(state[key]) for key in
              ("operation_id", "version", "status", "missing_fields", "duplicate_candidates") if key in state}
    operation = state.get("operation", {})
    if operation.get('record_kind')=='opening_balance':
        result['operation']=public_view({key:copy.deepcopy(operation[key]) for key in
            ('record_kind','execution_snapshot_id','snapshot_id','account_version','observed_as_of','positions','opening_balance_id') if key in operation})
        return result
    result["operation"] = {key: copy.deepcopy(operation[key]) for key in
                           ("instrument_id", "side", "quantity", "unit", "price", "price_basis",
                            "currency", "occurred_at", "fees", "execution_status", "purity", "weight_grams")
                           if key in operation}
    return result


def context_summary(context):
    """Default model context. Monetary totals stay grouped by currency and unit."""
    allowed = ("portfolio_version", "portfolio_complete", "completeness", "base_currency",
               "fx_status", "portfolio_value", "as_of", "context_source", "sleeve",
               "sleeve_portfolio_version", "last_completed_execution_at", "recommendations_truncated")
    result = {key: copy.deepcopy(context[key]) for key in allowed if key in context}
    result["holdings"], multiple = _holdings(context.get("holdings", []))
    result["sleeve_holdings"], _ = _holdings(context.get("sleeve_holdings", []))
    result["holdings_by_currency"] = {
        currency: [item for item in result["holdings"] if item["currency"] == currency]
        for currency in sorted({item["currency"] for item in result["holdings"] if item["currency"]})}
    result["multiple_accounts"] = multiple
    result["pending_operations"] = [operation_summary(state) for state in context.get("pending_operations", [])]
    result["intents"] = [operation_summary(state) for state in context.get("intents", [])]
    result["operations_available_locally"] = bool(context.get("operations"))
    result["recommendations"] = [
        {key: copy.deepcopy(item[key]) for key in
         ("decision_id", "instrument_id", "action", "created_at", "valid_until", "rule_id",
          "view", "requires_reevaluation", "execution_scope", "invalidation_reasons") if key in item}
        for item in context.get("recommendations", [])]
    result["view"] = "sanitized_advisor_context"
    return result


def execution_summary(snapshot):
    """Account facts needed for analysis, without raw orders/fills or identifiers."""
    result = {key: public_view(snapshot[key]) for key in
              ("schema_version", "snapshot_id", "account_version", "as_of", "valid_until", "status", "complete",
               "issues", "coverage", "account", "cash", "positions", "quotes", "fx_rates") if key in snapshot}
    result["open_orders"] = [{key: copy.deepcopy(row[key]) for key in
                              ("instrument_id", "side", "remaining_quantity", "limit_price", "currency", "status")
                              if key in row} for row in snapshot.get("orders", [])]
    result["fill_count"] = len(snapshot.get("fills", []))
    result["view"] = "sanitized_execution_summary; original snapshot remains local"
    return result

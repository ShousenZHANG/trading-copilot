"""Comparable sleeve valuations; planned contributions are never performance.

Changing holdings or declared cash starts a new observation segment. Without
dated account cash flows there is no honest way to splice those segments into
one return curve. A populated book's first observation is therefore a baseline,
not a measured zero drawdown. Old untyped recommendation values are research
history, not comparable valuations in a journal-backed context.
"""
from __future__ import annotations

from decimal import Decimal

from .riskinputs import portfolio_drawdown


def observe(*, context: dict, sleeve: str, total_value: float, cash_value: float | None,
            invested: bool, snapshot_id: str, mark_basis: str) -> tuple[dict, dict]:
    currency = "CNY" if sleeve == "gold" else "USD"
    version = context.get("sleeve_portfolio_version", context.get("portfolio_version"))
    current = {"schema_version": 1, "sleeve": sleeve, "currency": currency,
               "holdings_version": version, "total_value": str(Decimal(str(total_value))),
               "cash_value": None if cash_value is None else str(Decimal(str(cash_value))),
               "mark_basis": mark_basis}
    strict = context.get("context_source") == "journal"
    binding = ("sleeve", "currency", "holdings_version", "cash_value", "mark_basis")
    for summary in context.get("valuation_history") or []:
        if not isinstance(summary, dict) or any(summary.get(key) != current[key] for key in binding):
            continue
        try:
            peak = Decimal(str(summary["peak_value"]))
            deepest = Decimal(str(summary["max_drawdown"]))
            count = summary["sample_count"]
        except (KeyError, ValueError, ArithmeticError):
            continue
        if (not peak.is_finite() or peak <= 0 or not deepest.is_finite() or not 0 <= deepest <= 1
                or type(count) is not int or count < 1):
            continue
        same_snapshot = summary.get("latest_snapshot_id") == snapshot_id
        if not invested:
            return current, {"drawdown": 0.0, "drawdown_sample_count": count + int(not same_snapshot),
                             "drawdown_history_status": "empty_book"}
        if count == 1 and same_snapshot:
            return current, {"drawdown_sample_count": 1, "drawdown_history_status": "baseline_required"}
        drawdown = max(deepest, (peak - Decimal(str(total_value))) / peak, Decimal(0))
        return current, {"drawdown": float(drawdown), "drawdown_sample_count": count + int(not same_snapshot),
                         "drawdown_history_status": "full_current_holdings_and_cash_segment"}
    rows = list(reversed(context.get("recommendations") or []))
    if rows and all(row.get("created_at") for row in rows):
        rows.sort(key=lambda row: str(row["created_at"]))
    values, seen = [], set()
    for row in rows:
        stamp = row.get("snapshot_id")
        if stamp == snapshot_id or (stamp is not None and stamp in seen):
            continue
        prior = row.get("portfolio_valuation")
        if isinstance(prior, dict):
            if any(prior.get(key) != current[key] for key in binding):
                continue
            raw = prior.get("total_value")
        elif not strict and row.get("sleeve", sleeve) == sleeve and row.get("currency", currency) == currency:
            raw = row.get("portfolio_total_value")
        else:
            continue
        try:
            value = Decimal(str(raw))
        except (ValueError, ArithmeticError):
            continue
        if not value.is_finite() or value <= 0:
            continue
        values.append(float(value))
        if stamp is not None:
            seen.add(stamp)
    if not invested and (strict or not values):
        return current, {"drawdown": 0.0, "drawdown_sample_count": len(values) + 1,
                         "drawdown_history_status": "empty_book"}
    if strict and context.get("recommendations_truncated"):
        return current, {"drawdown_sample_count": len(values) + 1,
                         "drawdown_history_status": "history_truncated"}
    if strict and not values:
        return current, {"drawdown_sample_count": 1,
                         "drawdown_history_status": "baseline_required"}
    values.append(float(total_value))
    if not any(value > 0 for value in values):
        return current, {"drawdown_sample_count": 0,
                         "drawdown_history_status": "valuation_unavailable"}
    drawdown, count = portfolio_drawdown(values)
    return current, {"drawdown": drawdown, "drawdown_sample_count": count,
                     "drawdown_history_status": "current_holdings_and_cash_segment"}

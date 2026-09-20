"""Whole-share order deltas that fit the budget after costs.

TWO THINGS THE FIRST DRAFT GOT WRONG, BOTH FIXED HERE

First, an order is a DELTA. The earlier version computed "how many shares to end
up holding" from cash alone while the direction came from comparing weights, so
`QQQ reduce 17` meant "hold 17 at the end" and read as "sell 17". Every order
below carries `target_shares`, `delta_shares` and an explicit `side`, and
`delta_shares` is what a broker would be told.

Second, there is ONE denominator: holdings market value plus investable cash.
Comparing a target weight measured against cash to a current weight measured
against holdings is meaningless once any position exists.

The cash floor is withheld from that same total, because `engine.run` reserves a
fraction of `cash + positions` (engine.py:167). Reserving a fraction of cash
instead would make a rule reserve a different amount live than it did in the
backtest it was admitted under.

PRECONDITION: `affordable_shares` assumes `price >= 1.0`. Its descent subtracts
the observed overshoot in whole shares, which is exact at ordinary prices; for
sub-dollar prices it can stop one or two shares short of maximal. No registry
symbol trades under a dollar, and a test pins maximality over the prices that
do occur.
"""
from __future__ import annotations

import math
from typing import Mapping

WEIGHT_TOLERANCE = 1e-6


def affordable_shares(*, budget: float, price: float, cost_model) -> int:
    """Largest whole share count whose notional plus costs fits `budget`."""
    if not math.isfinite(price) or price <= 0:
        raise ValueError(f"price must be a finite positive number, got {price!r}")
    if not math.isfinite(budget) or budget <= 0:
        return 0
    shares = int(budget // price)
    while shares > 0:
        notional = shares * price
        total = notional + cost_model.total(shares=shares, notional=notional)
        if total <= budget:
            return shares
        shares -= max(1, int((total - budget) // price))
    return 0


def plan_orders(*, weights: Mapping[str, float], prices: Mapping[str, float],
                held_shares: Mapping[str, float], investable_cash: float,
                cash_floor_pct: float, cost_model) -> dict:
    """Turn target weights plus what is held into whole-share deltas.

    Sells are computed first and are never limited by cash: a sell raises cash.
    Buys then draw from the remaining budget in a deterministic (sorted) order,
    so the total spend cannot exceed what is available even when rounding on one
    symbol frees change a later one could use.

    A symbol is `unfunded` whenever it carries a positive target weight but
    ends the plan holding nothing -- either because the shared cash budget ran
    out partway through the buy pass, or because its own allocated slice never
    reached one whole share at its price in the first place (e.g. a $500 slice
    of a $100,000 stock). Both are "this weight bought nothing", and both must
    surface the same way rather than the second one reading as an intentional
    zero target: a zero-weight symbol being sold down to zero is not unfunded,
    it is the plan working as intended.
    """
    if not 0.0 <= cash_floor_pct < 1.0:
        raise ValueError(f"cash_floor_pct must be in [0, 1), got {cash_floor_pct}")
    if not weights:
        raise ValueError("weights must be a nonempty mapping")
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError(f"target weights must sum to 1.0, got {total_weight:.9f}")
    for symbol, weight in weights.items():
        if not math.isfinite(weight) or weight < 0:
            raise ValueError(f"{symbol}: weight must be finite and non-negative, got {weight!r}")

    held = {str(s).upper(): float(q) for s, q in (held_shares or {}).items() if q}
    symbols = sorted(set(weights) | set(held))
    holdings_value = sum(held[s] * prices[s] for s in held)
    total_value = holdings_value + float(investable_cash)
    investable_value = total_value * (1.0 - cash_floor_pct)

    targets: dict[str, int] = {}
    for symbol in symbols:
        price = prices[symbol]      # KeyError is correct: a missing price is a defect
        if price <= 0:
            raise ValueError(f"{symbol}: price must be positive, got {price!r}")
        targets[symbol] = int((investable_value * weights.get(symbol, 0.0)) // price)

    orders, unfunded = [], []
    cash = float(investable_cash)
    # Sells first: they fund the buys.
    for symbol in symbols:
        delta = targets[symbol] - int(held.get(symbol, 0))
        if delta >= 0:
            continue
        notional = -delta * prices[symbol]
        cost = cost_model.total(shares=-delta, notional=notional)
        cash += notional - cost
    for symbol in symbols:
        price = prices[symbol]
        current = int(held.get(symbol, 0))
        delta = targets[symbol] - current
        cost = 0.0
        if delta > 0:
            affordable = affordable_shares(budget=cash, price=price, cost_model=cost_model)
            if affordable < delta:
                delta = affordable
            if delta > 0:
                notional = delta * price
                cost = cost_model.total(shares=delta, notional=notional)
                cash -= notional + cost
        elif delta < 0:
            notional = -delta * price
            cost = cost_model.total(shares=-delta, notional=notional)
        # A weight that never reached one whole share at this price bought
        # nothing just as surely as a weight the shared cash ran out on --
        # both leave this symbol holding zero despite a positive target
        # weight, and both must be reported the same way. A zero weight
        # ending at zero is a deliberate exit, not an unfunded buy.
        if weights.get(symbol, 0.0) > 0.0 and current + delta == 0:
            unfunded.append(symbol)
        side = "hold" if delta == 0 else "buy" if delta > 0 else "sell"
        orders.append({
            "instrument_id": symbol,
            "target_weight": float(weights.get(symbol, 0.0)),
            "held_shares": current,
            "target_shares": current + delta,
            "delta_shares": delta,
            "side": side,
            "limit_price": float(price),
            "notional": round(abs(delta) * price, 2),
            "estimated_cost": round(cost, 2),
        })
    return {
        "orders": orders, "unfunded": unfunded,
        "total_value": round(total_value, 2),
        "holdings_value": round(holdings_value, 2),
        "investable_cash": round(float(investable_cash), 2),
        "investable_value": round(investable_value, 2),
        "cash_remaining": round(cash, 2),
    }

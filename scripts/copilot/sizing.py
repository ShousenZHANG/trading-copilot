"""Funded order deltas, reserving cash and costs in one shared calculation.

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

The live ETF default requires whole-share existing holdings. Fractional ETF
holdings need a separately supported execution path; they are never truncated
into whole shares. Fractional sizing here is used by the gold backtest.

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


def _funded_targets(*, weights, prices, held, cash, total_value, cash_floor_pct,
                    cost_model, integer_shares):
    """Reserve cash first, then fit the entire rebalance including sell fees.

    Shrinking the investable book scales the intended allocation together;
    sorted evaluation keeps the result independent of hash iteration order.
    A bounded search handles both whole shares and fractional gold quantities.
    """
    symbols = sorted(set(weights) | set(held))
    reserve = total_value * cash_floor_pct
    investable = total_value - reserve

    def candidate(amount):
        targets, remaining = {}, cash
        for symbol in symbols:
            raw = amount * weights.get(symbol, 0.0) / prices[symbol]
            targets[symbol] = int(raw) if integer_shares else raw
            delta = targets[symbol] - held.get(symbol, 0.0)
            if abs(delta) < 1e-9:
                continue
            notional = abs(delta) * prices[symbol]
            remaining -= delta * prices[symbol] + cost_model.total(shares=abs(delta), notional=notional)
        return targets, remaining

    targets, remaining = candidate(investable)
    if remaining >= reserve:
        return targets, remaining
    best, best_cash = candidate(0.0)
    if best_cash < reserve:
        raise ValueError("rebalance costs cannot preserve the portfolio value and cash reserve")
    lower, upper = 0.0, investable
    for _ in range(64):
        middle = (lower + upper) / 2.0
        proposed, proposed_cash = candidate(middle)
        if proposed_cash >= reserve:
            lower, best, best_cash = middle, proposed, proposed_cash
        else:
            upper = middle
    return best, best_cash


def plan_orders(*, weights: Mapping[str, float], prices: Mapping[str, float],
                held_shares: Mapping[str, float], investable_cash: float,
                cash_floor_pct: float, cost_model, integer_shares: bool = True) -> dict:
    """Turn target weights plus what is held into whole-share deltas.

    The whole rebalance is costed before exposing orders, including sell fees.
    The cash reserve cannot fund commissions. Fractional quantities are used
    only by the backtest gold path; live ETF callers keep whole-share defaults.

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
    if any(not math.isfinite(q) or q < 0 or (integer_shares and not q.is_integer()) for q in held.values()):
        raise ValueError("held shares must be finite, nonnegative and match the share basis")
    symbols = sorted(set(weights) | set(held))
    holdings_value = sum(held[s] * prices[s] for s in held)
    total_value = holdings_value + float(investable_cash)
    investable_value = total_value * (1.0 - cash_floor_pct)

    for symbol in symbols:
        price = prices[symbol]      # KeyError is correct: a missing price is a defect
        if not math.isfinite(price) or price <= 0:
            raise ValueError(f"{symbol}: price must be positive, got {price!r}")
    if not math.isfinite(total_value) or total_value <= 0 or not math.isfinite(investable_cash) or investable_cash < 0:
        raise ValueError("portfolio value must be finite and positive with nonnegative cash")
    targets, cash = _funded_targets(weights=weights, prices=prices, held=held,
                                   cash=float(investable_cash), total_value=total_value,
                                   cash_floor_pct=cash_floor_pct, cost_model=cost_model,
                                   integer_shares=integer_shares)

    orders, unfunded = [], []
    for symbol in symbols:
        price = prices[symbol]
        current = int(held.get(symbol, 0)) if integer_shares else held.get(symbol, 0.0)
        delta = targets[symbol] - current
        cost = 0.0
        if abs(delta) >= 1e-9:
            notional = abs(delta) * price
            cost = cost_model.total(shares=abs(delta), notional=notional)
        else:
            delta = 0 if integer_shares else 0.0
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

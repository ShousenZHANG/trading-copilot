"""Turn a CNY contribution into grams of Au99.99, honouring every [gold] limit.

WHY THIS IS NOT sizing.py
sizing.plan_orders requires target weights summing to 1.0 across a multi-symbol
universe, computes WHOLE shares, and works in the portfolio's single currency.
Gold is bought by a CNY amount in a fixed increment (Bank of China's 积存金:
1200 CNY minimum, 200 CNY steps), yields FRACTIONAL grams, and is capped by
orders per day. Three of those four contradict sizing.py outright.

EVERY ADJUSTMENT IS DOWNWARD
A contribution is floored to the increment, trimmed to the remaining budget,
and truncated when converted to grams. Nothing here ever rounds up. Rounding a
5,100 CNY contribution to 5,200 would spend money the user did not offer;
rounding 5.05263 grams to 5.0527 would claim more metal than the money buys.

A REFUSAL IS NOT AN ERROR
Below the minimum, at the daily cap, or out of budget are all ordinary states
with an amount of zero and a stated reason. They are returned, not raised. Only
an input that cannot describe a real order -- a non-positive ask, a negative
holding, an increment of zero -- raises.

ALL REASONS, NOT THE FIRST
A refusal carries every constraint it violated. Reporting only the first sends
the user to fix one thing, retry, and hit the next.
"""
from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

SYMBOL = "GOLD.CNY"

#: Bank 积存金 statements quote holdings to 0.0001 g. Matching that keeps the
#: journal reconcilable against the statement the user can actually see.
GRAM_PLACES = Decimal("0.0001")
CNY_PLACES = Decimal("1")


def _positive(name: str, value) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError(f"{name} must be a finite positive number, got {value!r}")
    return number


def _non_negative(name: str, value) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number < 0:
        raise ValueError(f"{name} must be a finite non-negative number, got {value!r}")
    return number


def plan_contribution(*, ask_per_fine_gram: float, investable_total_cny: float,
                      held_grams: float, min_order_cny: int, order_increment_cny: int,
                      orders_today: int, max_orders_per_day: int,
                      contribution_cny: float) -> dict:
    """One gold contribution, or a stated refusal."""
    ask = _positive("ask_per_fine_gram", ask_per_fine_gram)
    held = _non_negative("held_grams", held_grams)
    budget = _non_negative("investable_total_cny", investable_total_cny)
    wanted = _non_negative("contribution_cny", contribution_cny)
    increment = _positive("order_increment_cny", order_increment_cny)
    minimum = _non_negative("min_order_cny", min_order_cny)
    if minimum % increment != 0:
        raise ValueError(f"min_order_cny {min_order_cny} must be a multiple of "
                         f"order_increment_cny {order_increment_cny}; otherwise no floored "
                         "contribution can ever reach the minimum")
    if type(orders_today) is not int or orders_today < 0:
        raise ValueError(f"orders_today must be a nonnegative integer, got {orders_today!r}")
    if type(max_orders_per_day) is not int or max_orders_per_day < 1:
        raise ValueError(f"max_orders_per_day must be at least 1, got {max_orders_per_day!r}")

    refusals: list[str] = []
    reasons: list[str] = []

    # held_value is NOT quantized. Flooring it understates what is already in
    # metal, which raises `remaining` by up to a yuan -- and that fraction is
    # enough to clear a whole 200-CNY step, so the contribution came out one
    # increment LARGER than exact arithmetic allows and the total crept past
    # the budget. Measured: 42.1053 g at 950 is 40,000.035, floored to 40,000,
    # leaving "10,000" of a 50,000 budget instead of 9,999.965 -- a 10,000 CNY
    # order against a true headroom of 9,800, and 50,000.035 committed in all.
    # Trivial in money, but it made this module's own "nothing here ever rounds
    # up" false, and a nearly-true invariant is the kind that bites later.
    held_value = held * ask
    remaining = budget - held_value
    if remaining < 0:
        remaining = Decimal(0)

    amount = wanted
    if amount > remaining:
        amount = remaining
        reasons.append(f"trimmed to the remaining budget "
                       f"{remaining.quantize(CNY_PLACES, rounding=ROUND_DOWN)} CNY "
                       f"(investable_total_cny {budget} less "
                       f"{held_value.quantize(CNY_PLACES, rounding=ROUND_DOWN)} already in metal)")

    floored = (amount // increment) * increment
    # No `not reasons` guard. A budget trim followed by an increment floor is
    # TWO reductions, and reporting only the first left the second silent: a
    # 5,160 headroom became a 5,000 order with nothing saying where the 160
    # went. `amount` here is the post-trim figure, so this compares the floor
    # against what actually reached it rather than against the original ask.
    if floored != amount and floored > 0:
        reasons.append(f"floored to a multiple of order_increment_cny {increment}")
    amount = floored

    if orders_today >= max_orders_per_day:
        refusals.append(f"max_orders_per_day {max_orders_per_day} already reached "
                        f"({orders_today} recorded today)")
    if amount < minimum:
        if remaining < minimum:
            refusals.append(f"investable_total_cny leaves {remaining} CNY, below "
                            f"min_order_cny {minimum}")
        else:
            refusals.append(f"{amount} CNY is below min_order_cny {minimum}")

    if refusals:
        return {"instrument_id": SYMBOL, "action": "hold", "amount_cny": "0",
                "grams": "0.0000", "ask_per_fine_gram": str(ask),
                "held_grams": str(held.quantize(GRAM_PLACES, rounding=ROUND_DOWN)),
                "target_grams": str(held.quantize(GRAM_PLACES, rounding=ROUND_DOWN)),
                "reasons": reasons, "refusals": refusals}

    grams = (amount / ask).quantize(GRAM_PLACES, rounding=ROUND_DOWN)
    reasons.append(f"{amount} CNY at {ask} CNY per fine gram")
    return {"instrument_id": SYMBOL, "action": "buy",
            "amount_cny": str(amount.quantize(CNY_PLACES, rounding=ROUND_DOWN)),
            "grams": str(grams), "ask_per_fine_gram": str(ask),
            "held_grams": str(held.quantize(GRAM_PLACES, rounding=ROUND_DOWN)),
            "target_grams": str((held + grams).quantize(GRAM_PLACES, rounding=ROUND_DOWN)),
            "reasons": reasons, "refusals": []}

"""CNY gold contributions: money in, grams out, every [gold] constraint honoured."""
from __future__ import annotations

import unittest
from decimal import Decimal

from copilot import goldsizing

BASE = dict(ask_per_fine_gram=950.0, investable_total_cny=50000.0, held_grams=0.0,
            min_order_cny=1200, order_increment_cny=200, orders_today=0,
            max_orders_per_day=10, contribution_cny=5000.0)


def plan(**overrides):
    return goldsizing.plan_contribution(**{**BASE, **overrides})


class MoneyIsDecimalNeverFloat(unittest.TestCase):
    """CLAUDE.md: monetary journal values use decimal strings."""

    def test_the_amount_is_a_string(self):
        self.assertIsInstance(plan()["amount_cny"], str)

    def test_the_grams_are_a_string(self):
        self.assertIsInstance(plan()["grams"], str)

    def test_the_amount_parses_as_an_exact_decimal(self):
        self.assertEqual(Decimal(plan(contribution_cny=5000.0)["amount_cny"]), Decimal("5000"))

    def test_grams_are_amount_over_ask_to_four_places(self):
        # 4800 / 950 = 5.052631... -> 5.0526 grams. Truncated, never rounded up:
        # rounding up would claim more metal than the money buys.
        result = plan(contribution_cny=4800.0)
        self.assertEqual(result["grams"], "5.0526")


class TheIncrementAndMinimumAreEnforced(unittest.TestCase):
    def test_a_contribution_is_floored_to_the_increment(self):
        # 5150 -> 5000 at a 200 step. Floored, not rounded: 5100 must not
        # become 5200 and spend money the user did not offer.
        self.assertEqual(plan(contribution_cny=5150.0)["amount_cny"], "5000")

    def test_an_exact_multiple_is_unchanged(self):
        self.assertEqual(plan(contribution_cny=5000.0)["amount_cny"], "5000")

    def test_below_the_minimum_refuses_rather_than_rounding_up(self):
        result = plan(contribution_cny=800.0)
        self.assertEqual(result["action"], "hold")
        self.assertIn("min_order_cny", " ".join(result["refusals"]))
        self.assertEqual(result["amount_cny"], "0")

    def test_a_contribution_that_floors_below_the_minimum_refuses(self):
        # 1150 floors to 1000, which is under the 1200 minimum.
        result = plan(contribution_cny=1150.0)
        self.assertEqual(result["action"], "hold")
        self.assertIn("min_order_cny", " ".join(result["refusals"]))

    def test_exactly_the_minimum_is_accepted(self):
        self.assertEqual(plan(contribution_cny=1200.0)["action"], "buy")


class TheDailyOrderCapIsEnforced(unittest.TestCase):
    def test_under_the_cap_proceeds(self):
        self.assertEqual(plan(orders_today=9, max_orders_per_day=10)["action"], "buy")

    def test_at_the_cap_refuses(self):
        result = plan(orders_today=10, max_orders_per_day=10)
        self.assertEqual(result["action"], "hold")
        self.assertIn("max_orders_per_day", " ".join(result["refusals"]))

    def test_over_the_cap_refuses(self):
        self.assertEqual(plan(orders_today=11, max_orders_per_day=10)["action"], "hold")


class TheInvestableTotalIsACeiling(unittest.TestCase):
    def test_a_contribution_beyond_the_remaining_budget_is_trimmed(self):
        # 49,000 CNY already in metal at 950 -> 51.5789 g held. Budget 50,000.
        # Remaining is 1,000, which floors to 1,000 and is under the 1,200
        # minimum, so this refuses rather than overspending.
        result = plan(held_grams=51.5789, contribution_cny=5000.0)
        self.assertEqual(result["action"], "hold")
        self.assertIn("investable_total_cny", " ".join(result["refusals"]))

    def test_a_partial_budget_trims_to_what_remains(self):
        # 40,000 held at 950 -> 42.1053 g. Remaining 10,000 - trim 12,000 to 10,000.
        result = plan(held_grams=42.1053, contribution_cny=12000.0)
        self.assertEqual(result["action"], "buy")
        self.assertEqual(result["amount_cny"], "10000")

    def test_a_zero_budget_refuses_and_says_so(self):
        result = plan(investable_total_cny=0.0)
        self.assertEqual(result["action"], "hold")
        self.assertIn("investable_total_cny", " ".join(result["refusals"]))


class BadInputsRaiseRatherThanProducingAnOrder(unittest.TestCase):
    def test_a_non_positive_ask_raises(self):
        with self.assertRaisesRegex(ValueError, "ask_per_fine_gram"):
            plan(ask_per_fine_gram=0.0)

    def test_a_negative_holding_raises(self):
        with self.assertRaisesRegex(ValueError, "held_grams"):
            plan(held_grams=-1.0)

    def test_an_increment_of_zero_raises(self):
        with self.assertRaisesRegex(ValueError, "order_increment_cny"):
            plan(order_increment_cny=0)

    def test_a_minimum_that_is_not_a_multiple_of_the_increment_raises(self):
        # 1250 at a 200 step can never be produced by flooring, so every
        # contribution would refuse for a reason the user cannot act on.
        with self.assertRaisesRegex(ValueError, "multiple"):
            plan(min_order_cny=1250, order_increment_cny=200)


class TheResultExplainsItself(unittest.TestCase):
    def test_a_buy_states_the_target(self):
        # 4750 is NOT a multiple of the 200 CNY step, so it floors to 4600
        # first: 4600 / 950 = 4.842105... -> 4.8421 g, target 10 + 4.8421.
        # The target therefore states what the money actually buys, not what
        # the user asked to spend -- a target computed from the pre-floor ask
        # would over-report the holding by 0.1579 g on this one order and
        # drift further apart on every later one.
        result = plan(held_grams=10.0, contribution_cny=4750.0)
        self.assertEqual(result["held_grams"], "10.0000")
        self.assertEqual(result["amount_cny"], "4600")
        self.assertEqual(result["target_grams"], "14.8421")

    def test_a_refusal_carries_every_reason_not_just_the_first(self):
        result = plan(contribution_cny=800.0, orders_today=10, max_orders_per_day=10)
        joined = " ".join(result["refusals"])
        self.assertIn("min_order_cny", joined)
        self.assertIn("max_orders_per_day", joined)


if __name__ == "__main__":
    unittest.main()

"""The gold accumulation family. Single asset, so the decision is WHEN, not HOW MUCH of each."""
from __future__ import annotations

import unittest
from datetime import date, timedelta

from copilot.backtest.frame import build
from copilot.backtest.goldrules import FAMILIES, ScheduledAccumulation


def frame(bars=300, start_price=400.0, step=0.5, start=date(2024, 1, 1)):
    dates, closes, day, i = [], [], start, 0
    while len(dates) < bars:
        if day.weekday() < 5:
            dates.append(day)
            closes.append([start_price + i * step])
            i += 1
        day += timedelta(days=1)
    return build(dates=dates, symbols=["GOLD.CNY"], closes=closes)


def falling_frame(bars=300, start_price=900.0, step=-1.0):
    return frame(bars=bars, start_price=start_price, step=step)


class TheRuleIsFrozenAndSelfDescribing(unittest.TestCase):
    def test_it_cannot_be_mutated_between_runs(self):
        # The ETF families were made frozen after state leaked between backtest
        # runs through a rule that cached its own last rebalance index.
        rule = ScheduledAccumulation()
        with self.assertRaises(Exception):
            rule.interval_days = 5

    def test_it_reports_at_most_three_parameters(self):
        # admission.MAX_PARAMETERS is 3 and is structurally unwaivable.
        self.assertLessEqual(len(ScheduledAccumulation().parameters), 3)

    def test_the_registry_names_it(self):
        self.assertIs(FAMILIES["scheduled_accumulation"], ScheduledAccumulation)


class TheRuleAlwaysHoldsGoldAndOnlyGold(unittest.TestCase):
    def test_weights_are_a_full_allocation_to_the_one_symbol(self):
        self.assertEqual(ScheduledAccumulation().weights(frame(), 250), {"GOLD.CNY": 1.0})

    def test_weights_sum_to_one_for_sizing(self):
        # sizing and goldsizing both reject a weight set that does not sum to 1.
        self.assertAlmostEqual(sum(ScheduledAccumulation().weights(frame(), 250).values()), 1.0)


class TheScheduleFires(unittest.TestCase):
    def test_the_first_call_always_accumulates(self):
        self.assertTrue(ScheduledAccumulation().should_rebalance(frame(), 250, {}, None))

    def test_it_waits_the_full_interval(self):
        rule = ScheduledAccumulation(interval_days=21)
        f = frame()
        self.assertFalse(rule.should_rebalance(f, 220, {"GOLD.CNY": 1.0}, 200))
        self.assertTrue(rule.should_rebalance(f, 221, {"GOLD.CNY": 1.0}, 200))

    def test_the_interval_counts_bars_not_calendar_days(self):
        # Bars, because the vendored series is trading-day spaced. Calendar
        # days would make a Spring Festival week shorten the interval.
        rule = ScheduledAccumulation(interval_days=21)
        f = frame()
        self.assertEqual((f.dates[221] - f.dates[200]).days, 29,
                         "21 bars spans more than 21 calendar days")
        self.assertTrue(rule.should_rebalance(f, 221, {"GOLD.CNY": 1.0}, 200))


class TheTrendFilterIsOptionalAndOneWay(unittest.TestCase):
    """pause_below_trend can only ever SKIP a contribution, never add one."""

    def test_it_is_off_by_default(self):
        self.assertFalse(ScheduledAccumulation().pause_below_trend)

    def test_off_means_a_falling_market_still_accumulates(self):
        rule = ScheduledAccumulation(interval_days=21, pause_below_trend=False)
        self.assertTrue(rule.should_rebalance(falling_frame(), 221, {"GOLD.CNY": 1.0}, 200))

    def test_on_means_a_price_below_trend_pauses(self):
        rule = ScheduledAccumulation(interval_days=21, trend_days=200, pause_below_trend=True)
        self.assertFalse(rule.should_rebalance(falling_frame(), 221, {"GOLD.CNY": 1.0}, 200))

    def test_on_still_accumulates_above_trend(self):
        rule = ScheduledAccumulation(interval_days=21, trend_days=200, pause_below_trend=True)
        self.assertTrue(rule.should_rebalance(frame(), 221, {"GOLD.CNY": 1.0}, 200))

    def test_the_filter_never_fires_off_schedule(self):
        # Being above trend is not a reason to buy early. Only the schedule
        # opens the door; the filter can only close it.
        rule = ScheduledAccumulation(interval_days=21, pause_below_trend=True)
        self.assertFalse(rule.should_rebalance(frame(), 210, {"GOLD.CNY": 1.0}, 200))


class WarmupProtectsThePartialWindow(unittest.TestCase):
    def test_warmup_covers_the_trend_window_when_the_filter_is_on(self):
        self.assertGreaterEqual(ScheduledAccumulation(trend_days=200, pause_below_trend=True).warmup_bars, 200)

    def test_warmup_is_zero_when_no_trend_is_read(self):
        # A pure schedule reads no history, so making it wait 200 bars would
        # throw away the first year of every backtest for nothing.
        self.assertEqual(ScheduledAccumulation(pause_below_trend=False).warmup_bars, 0)


class BadParametersAreRefusedAtConstruction(unittest.TestCase):
    def test_a_non_positive_interval_is_refused(self):
        with self.assertRaisesRegex(ValueError, "interval_days"):
            ScheduledAccumulation(interval_days=0)

    def test_a_trend_window_shorter_than_two_bars_is_refused(self):
        with self.assertRaisesRegex(ValueError, "trend_days"):
            ScheduledAccumulation(trend_days=1, pause_below_trend=True)


if __name__ == "__main__":
    unittest.main()

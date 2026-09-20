#!/usr/bin/env python3
"""Offline contracts for rule identity, sizing, the brake and risk inputs.

Stdlib only, no network, no clock: this runs in the CI matrix job that installs
zero third-party packages.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from copilot import brake
from copilot import riskinputs
from copilot import ruleset
from copilot import sizing
from copilot.backtest import engine as bt_engine
from copilot.backtest import frame as frame_mod
from copilot.backtest import rules as rule_families


def admitted_result(family="momentum_top_n", parameters=None, days=5200):
    """A real engine.Result from a real backtest, not a hand-written dict.

    ruleset must derive the admission verdict itself rather than trusting a
    caller's claim, so the tests have to hand it something a backtest actually
    produced.
    """
    parameters = parameters or {"top_n": 2.0, "lookback_days": 252.0, "skip_days": 21.0}
    symbols = ["IWM", "QQQ", "SPY"]
    start = date(2005, 1, 3)
    dates, closes, day = [], [], start
    i = 0
    while len(dates) < days:
        if day.weekday() < 5:
            dates.append(day)
            closes.append([100.0 + i * 0.01, 100.0 + i * 0.02, 100.0 + i * 0.015])
            i += 1
        day += timedelta(days=1)
    frame = frame_mod.build(dates=dates, symbols=symbols, closes=closes)
    rule = rule_families.MomentumTopN(
        tuple(symbols), top_n=int(parameters["top_n"]),
        lookback_days=int(parameters["lookback_days"]),
        skip_days=int(parameters["skip_days"]))
    return frame, rule, bt_engine.run(
        frame, rule=rule, start_cash=100000.0,
        cost_model=bt_engine.CostModel(), cash_floor_pct=0.15)


class RuleIdentity(unittest.TestCase):
    def material(self, **overrides):
        base = dict(
            family="momentum_top_n",
            parameters={"top_n": 2.0, "lookback_days": 252.0, "skip_days": 21.0},
            universe=("IWM", "QQQ", "SPY"),
            targets=None,
            cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                        "max_pct_of_notional": 0.01, "spread_bps": 2.0},
            cash_floor_pct=0.15,
            integer_shares=True,
            admission={"admitted": True, "waived": False},
        )
        base.update(overrides)
        return base

    def test_rule_id_is_stable_and_well_formed(self):
        self.assertEqual(ruleset.rule_id(**self.material()),
                         ruleset.rule_id(**self.material()))
        self.assertRegex(ruleset.rule_id(**self.material()), r"^rule-[0-9a-f]{16}$")

    def test_universe_order_does_not_change_the_id(self):
        self.assertEqual(ruleset.rule_id(**self.material(universe=("IWM", "QQQ", "SPY"))),
                         ruleset.rule_id(**self.material(universe=("SPY", "IWM", "QQQ"))))

    def test_every_identity_input_changes_the_id(self):
        # The first draft tested three of nine inputs. Two rules differing only
        # in cash_floor_pct would have collided: the second either fails to
        # store (JournalConflict) or runs live reserving 15% while its admitted
        # metrics were measured reserving 50%.
        base = ruleset.rule_id(**self.material())
        variants = {
            "family": {"family": "inverse_volatility"},
            "parameters": {"parameters": {"top_n": 3.0, "lookback_days": 252.0, "skip_days": 21.0}},
            "universe": {"universe": ("IWM", "QQQ")},
            "targets": {"targets": {"QQQ": 1.0}},
            "cost_model": {"cost_model": {"per_share_usd": 0.005, "minimum_usd": 1.0,
                                          "max_pct_of_notional": 0.01, "spread_bps": 2.0}},
            "cash_floor_pct": {"cash_floor_pct": 0.50},
            "integer_shares": {"integer_shares": False},
            "admitted": {"admission": {"admitted": False, "waived": False}},
            "waived": {"admission": {"admitted": True, "waived": True}},
        }
        for name, override in variants.items():
            self.assertNotEqual(base, ruleset.rule_id(**self.material(**override)),
                                f"{name} does not participate in identity")

    def test_an_uncapped_cost_model_differs_from_a_zero_cap(self):
        # engine.CostModel records this as a bug fixed once already: 0.0 used to
        # mean "no cap" and None now does, so the two must not hash alike.
        uncapped = self.material()
        uncapped["cost_model"] = {**uncapped["cost_model"], "max_pct_of_notional": None}
        zero = self.material()
        zero["cost_model"] = {**zero["cost_model"], "max_pct_of_notional": 0.0}
        self.assertNotEqual(ruleset.rule_id(**uncapped), ruleset.rule_id(**zero))

    def test_an_incomplete_cost_model_is_refused(self):
        # CostModel(**{}) silently constructs the default IBKR schedule, so an
        # empty mapping would mean the admitted metrics no longer describe the
        # rule that runs.
        with self.assertRaisesRegex(ValueError, "cost_model"):
            ruleset.rule_id(**self.material(cost_model={}))
        with self.assertRaisesRegex(ValueError, "cost_model"):
            ruleset.rule_id(**self.material(cost_model={"per_share_usd": 0.0035}))

    def test_a_rule_id_survives_the_prose_gate_whole(self):
        import re
        from copilot.policy import _IDENTITY_OR_DATE
        text = f"adopted rule {ruleset.rule_id(**self.material())} produced this order"
        stripped = re.sub(r"\s+", " ", _IDENTITY_OR_DATE.sub(" ", text)).strip()
        self.assertEqual(stripped, "adopted rule produced this order")


class AdoptionRecord(unittest.TestCase):
    def build(self, **overrides):
        frame, rule, result = admitted_result()
        kwargs = dict(sleeve="etf", family=rule.name, parameters=dict(rule.parameters),
                      universe=tuple(frame.symbols), targets=None, result=result,
                      cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                                  "max_pct_of_notional": 0.01, "spread_bps": 2.0},
                      cash_floor_pct=0.15, integer_shares=True)
        kwargs.update(overrides)
        return ruleset.build_adoption(**kwargs)

    def test_the_admission_verdict_is_derived_not_supplied(self):
        # build_adoption calls backtest.admission.assess itself. A caller cannot
        # hand it {"admitted": True} for a strategy that never ran.
        record = self.build()
        self.assertIn("admission", record)
        self.assertIn("metrics", record["admission"])
        self.assertIn("cagr", record["admission"]["metrics"])
        self.assertIsInstance(record["admission"]["admitted"], bool)

    def test_signature_has_no_admission_parameter(self):
        import inspect
        self.assertNotIn("admission", inspect.signature(ruleset.build_adoption).parameters)

    def test_a_rejected_backtest_cannot_be_adopted(self):
        # A short curve fails rule 1, so the gate refuses the adoption.
        _, rule, short = admitted_result(days=400)
        with self.assertRaisesRegex(ValueError, "admitted"):
            self.build(result=short)

    def test_the_record_carries_the_admitted_metrics(self):
        # ADR-0004's last consequence promises every order carries the backtest
        # statistics it was adopted under, so they must be stored here.
        record = self.build()
        for key in ("years", "cagr", "max_drawdown", "annual_turnover", "sharpe"):
            self.assertIn(key, record["admission"]["metrics"], key)

    def test_universe_is_stored_sorted(self):
        self.assertEqual(self.build()["universe"], ["IWM", "QQQ", "SPY"])

    def test_only_the_etf_sleeve_exists(self):
        # ADR-0005 clause 2 deferred the gold sleeve; a gold adoption would skip
        # every universe check and could then be pointed at by etf.adopted_rule_id.
        self.assertEqual(ruleset.SLEEVES, ("etf",))
        with self.assertRaisesRegex(ValueError, "sleeve"):
            self.build(sleeve="gold")

    def test_a_non_admissible_symbol_is_refused(self):
        # SCHD is registered but has zero 2008 bars, so it never passed Q29.
        with self.assertRaises(ValueError):
            self.build(universe=("QQQ", "SCHD"))

    def test_an_unregistered_symbol_is_refused(self):
        with self.assertRaises(ValueError):
            self.build(universe=("QQQ", "NVDA"))

    def test_an_unknown_parameter_key_is_refused(self):
        # parameters={"topn": 3.0} would hash into the identity while
        # _rule_weights reads "top_n" and falls back to its default, so the
        # recorded identity would not describe the rule that runs.
        with self.assertRaisesRegex(ValueError, "topn"):
            self.build(parameters={"topn": 3.0, "lookback_days": 252.0, "skip_days": 21.0})

    def test_a_missing_parameter_key_is_refused(self):
        with self.assertRaisesRegex(ValueError, "skip_days"):
            self.build(parameters={"top_n": 2.0, "lookback_days": 252.0})

    def test_fixed_weight_targets_must_cover_the_universe(self):
        with self.assertRaisesRegex(ValueError, "targets"):
            self.build(family="fixed_weight_bands",
                       parameters={"relative_band": 0.25, "absolute_band": 0.05,
                                   "calendar_days": 365.0},
                       targets={"QQQ": 1.0})

    def test_fixed_weight_targets_must_sum_to_one(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            self.build(family="fixed_weight_bands",
                       parameters={"relative_band": 0.25, "absolute_band": 0.05,
                                   "calendar_days": 365.0},
                       targets={"IWM": 0.3, "QQQ": 0.3, "SPY": 0.3})


class ResultMustDescribeTheAdoption(unittest.TestCase):
    """Defect 1 (CRITICAL, post-845b810 review): build_adoption must refuse a
    Result that does not describe the family/parameters/universe being
    adopted. Without this, two working bypasses reach the same outcome as
    the literal `{"admitted": True}` the module already blocks: (a) a
    hand-built Result with fabricated fields, and (b) a genuine admitted
    Result claimed for a different configuration than the one that actually
    produced it -- its metrics then describe a strategy that never ran with
    those parameters/universe.
    """
    def build(self, **overrides):
        frame, rule, result = admitted_result()
        kwargs = dict(sleeve="etf", family=rule.name, parameters=dict(rule.parameters),
                      universe=tuple(frame.symbols), targets=None, result=result,
                      cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                                  "max_pct_of_notional": 0.01, "spread_bps": 2.0},
                      cash_floor_pct=0.15, integer_shares=True)
        kwargs.update(overrides)
        return ruleset.build_adoption(**kwargs)

    def test_the_genuinely_matched_case_still_succeeds(self):
        record = self.build()
        self.assertTrue(record["admission"]["admitted"])

    def test_a_fabricated_result_with_a_mismatched_family_is_refused(self):
        # Bypass (a): a hand-built Result whose fields do not correspond to
        # any real backtest. Reusing a genuine, admitted curve/costs here
        # proves the refusal comes from the name mismatch, not from the
        # admission gate rejecting the curve.
        _, _, real = admitted_result()
        fake = bt_engine.Result(rule_name="not_even_a_real_family_name",
                                parameters=dict(real.parameters), curve=list(real.curve),
                                universe=real.universe, traded_notional=real.traded_notional,
                                total_costs=real.total_costs, rebalance_count=real.rebalance_count)
        with self.assertRaisesRegex(ValueError, "rule_name"):
            self.build(result=fake)

    def test_a_result_admitted_under_different_parameters_is_refused(self):
        # Bypass (b): a real, admitted top_n=2 Result must not be recordable
        # under a top_n=5 identity -- the attached admission.metrics.cagr
        # would then describe a strategy that never ran.
        with self.assertRaisesRegex(ValueError, "parameters"):
            self.build(parameters={"top_n": 5.0, "lookback_days": 50.0, "skip_days": 5.0})

    def test_a_result_admitted_over_a_different_universe_is_refused(self):
        with self.assertRaisesRegex(ValueError, "universe"):
            self.build(universe=("QQQ",))


class CostAwareSizing(unittest.TestCase):
    def model(self):
        return bt_engine.CostModel()

    def test_zero_cost_sizing_is_the_naive_floor(self):
        self.assertEqual(sizing.affordable_shares(
            budget=1000.0, price=333.0, cost_model=bt_engine.CostModel.free()), 3)

    def test_costs_reduce_the_count_when_the_naive_floor_does_not_fit(self):
        # Verify this arithmetic against the real CostModel defaults before
        # trusting the expected value; if 3 shares at 333.0 plus commission and
        # half-spread does NOT exceed 1000.0, change the fixture to a case that
        # does and say so.
        self.assertEqual(sizing.affordable_shares(
            budget=1000.0, price=333.0, cost_model=self.model()), 2)

    def test_the_result_always_fits_and_is_maximal(self):
        model = self.model()
        for budget in (100.0, 1000.0, 9999.99, 20000.0, 123456.78):
            for price in (1.0, 37.5, 333.0, 612.34):
                shares = sizing.affordable_shares(budget=budget, price=price, cost_model=model)
                if shares:
                    notional = shares * price
                    self.assertLessEqual(
                        notional + model.total(shares=shares, notional=notional),
                        budget + 1e-9, f"{budget}/{price}/{shares} did not fit")
                more = (shares + 1) * price
                self.assertGreater(more + model.total(shares=shares + 1, notional=more),
                                   budget, f"{budget}/{price}/{shares} was not maximal")

    def test_a_non_positive_price_raises_and_a_non_positive_budget_is_zero(self):
        with self.assertRaises(ValueError):
            sizing.affordable_shares(budget=100.0, price=0.0, cost_model=self.model())
        self.assertEqual(sizing.affordable_shares(budget=0.0, price=10.0, cost_model=self.model()), 0)
        self.assertEqual(sizing.affordable_shares(budget=-5.0, price=10.0, cost_model=self.model()), 0)


class OrderPlanning(unittest.TestCase):
    def plan(self, **overrides):
        kwargs = dict(weights={"AAA": 0.5, "BBB": 0.5},
                      prices={"AAA": 100.0, "BBB": 50.0},
                      held_shares={},
                      investable_cash=10000.0,
                      cash_floor_pct=0.0,
                      cost_model=bt_engine.CostModel.free())
        kwargs.update(overrides)
        return sizing.plan_orders(**kwargs)

    def test_an_empty_book_produces_pure_buys(self):
        plan = self.plan()
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["AAA"]["side"], "buy")
        self.assertEqual(by_symbol["AAA"]["delta_shares"], 50)
        self.assertEqual(by_symbol["AAA"]["target_shares"], 50)
        self.assertEqual(by_symbol["BBB"]["delta_shares"], 100)

    def test_an_overweight_holding_produces_a_sell_of_the_difference(self):
        # The failure the first draft would have shipped: 600 held against a
        # target of 17 rendered as "reduce 17 shares", which reads as sell 17.
        plan = self.plan(held_shares={"AAA": 200}, prices={"AAA": 100.0, "BBB": 50.0},
                         investable_cash=0.0)
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["AAA"]["side"], "sell")
        self.assertEqual(by_symbol["AAA"]["target_shares"], 100)
        self.assertEqual(by_symbol["AAA"]["delta_shares"], -100)

    def test_the_denominator_is_holdings_plus_cash(self):
        # 200 AAA at 100.0 is 20000 of holdings; 20000 of cash makes 40000. A
        # 50% target is 20000, which is the 200 shares already held, so the
        # delta is zero and the side is hold.
        plan = self.plan(held_shares={"AAA": 200}, investable_cash=20000.0,
                         weights={"AAA": 0.5, "BBB": 0.5})
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["AAA"]["delta_shares"], 0)
        self.assertEqual(by_symbol["AAA"]["side"], "hold")
        self.assertAlmostEqual(plan["total_value"], 40000.0)

    def test_a_drift_inside_the_tolerance_is_a_hold_with_no_delta(self):
        plan = self.plan(held_shares={"AAA": 50, "BBB": 100}, investable_cash=0.0)
        for order in plan["orders"]:
            self.assertEqual(order["delta_shares"], 0)
            self.assertEqual(order["side"], "hold")

    def test_the_cash_floor_is_withheld_from_the_total_not_from_cash(self):
        # engine.run reserves a fraction of cash + positions, so live sizing
        # must use the same base or the same admitted rule reserves a different
        # amount in production than it did in the backtest.
        plan = self.plan(held_shares={"AAA": 100}, investable_cash=10000.0,
                         cash_floor_pct=0.20, weights={"AAA": 1.0},
                         prices={"AAA": 100.0})
        self.assertAlmostEqual(plan["total_value"], 20000.0)
        self.assertAlmostEqual(plan["investable_value"], 16000.0)
        self.assertEqual(plan["orders"][0]["target_shares"], 160)
        self.assertEqual(plan["orders"][0]["delta_shares"], 60)

    def test_a_buy_that_cash_cannot_fund_is_reported_not_silently_shrunk(self):
        plan = self.plan(weights={"AAA": 0.5, "BBB": 0.5},
                         prices={"AAA": 100.0, "BBB": 100000.0},
                         investable_cash=1000.0)
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["BBB"]["delta_shares"], 0)
        self.assertIn("BBB", plan["unfunded"])

    def test_sells_are_not_limited_by_cash(self):
        # Selling raises cash; a sell must never be trimmed by the cash budget.
        plan = self.plan(held_shares={"AAA": 500}, investable_cash=0.0,
                         weights={"AAA": 1.0}, prices={"AAA": 100.0},
                         cash_floor_pct=0.50)
        self.assertEqual(plan["orders"][0]["side"], "sell")
        self.assertEqual(plan["orders"][0]["target_shares"], 250)
        self.assertEqual(plan["orders"][0]["delta_shares"], -250)

    def test_weights_that_do_not_sum_to_one_are_refused(self):
        with self.assertRaisesRegex(ValueError, "sum"):
            self.plan(weights={"AAA": 0.9})

    def test_a_missing_price_is_a_keyerror(self):
        with self.assertRaises(KeyError):
            self.plan(prices={"AAA": 100.0})

    def test_a_held_symbol_outside_the_target_set_is_sold_to_zero(self):
        # A momentum rotation drops names. Leaving them held would silently
        # diverge from the weights the backtest measured.
        plan = self.plan(weights={"AAA": 1.0}, prices={"AAA": 100.0, "OLD": 20.0},
                         held_shares={"OLD": 300}, investable_cash=0.0)
        by_symbol = {o["instrument_id"]: o for o in plan["orders"]}
        self.assertEqual(by_symbol["OLD"]["target_shares"], 0)
        self.assertEqual(by_symbol["OLD"]["delta_shares"], -300)
        self.assertEqual(by_symbol["OLD"]["side"], "sell")


class NewsBrake(unittest.TestCase):
    def test_levels_are_exactly_the_three_the_user_approved(self):
        self.assertEqual(brake.LEVELS, ("none", "reduce_50", "skip"))

    def test_apply_is_one_way_over_the_whole_level_set(self):
        for level in brake.LEVELS:
            for quantity in range(0, 101):
                self.assertLessEqual(brake.apply(level=level, quantity=quantity), quantity)

    def test_reduce_50_floors_and_zero_is_a_valid_result(self):
        # Returning 0 is the contract, not an error: 1 share halved is 0 shares,
        # and the caller turns that into a hold rather than an order.
        self.assertEqual(brake.apply(level="reduce_50", quantity=17), 8)
        self.assertEqual(brake.apply(level="reduce_50", quantity=3), 1)
        self.assertEqual(brake.apply(level="reduce_50", quantity=1), 0)
        self.assertEqual(brake.apply(level="skip", quantity=17), 0)
        self.assertEqual(brake.apply(level="none", quantity=17), 17)

    def test_rounding_is_down_not_nearest(self):
        # round(q/2) would keep 2 of 3 -- 67% -- which is not a 50% reduction.
        self.assertEqual(brake.apply(level="reduce_50", quantity=3), 1)
        self.assertEqual(brake.apply(level="reduce_50", quantity=5), 2)
        self.assertEqual(brake.apply(level="reduce_50", quantity=7), 3)

    def test_a_negative_or_non_integer_quantity_raises(self):
        for bad in (-7, -1, 1.5, True, "3", None):
            with self.assertRaises((ValueError, TypeError)):
                brake.apply(level="none", quantity=bad)

    def test_an_unknown_level_raises(self):
        for bad in ("increase", "reduce_25", "double", "", None):
            with self.assertRaisesRegex(ValueError, "brake level"):
                brake.apply(level=bad, quantity=10)

    def test_a_non_none_level_requires_a_reason(self):
        for blank in ("", "   ", None):
            with self.assertRaisesRegex(ValueError, "reason"):
                brake.record(level="skip", reason=blank, evidence_ids=["ev_1"])

    def test_none_needs_no_reason_and_carries_no_evidence_requirement(self):
        record = brake.record(level="none", reason="", evidence_ids=[])
        self.assertEqual(record["level"], "none")
        self.assertEqual(record["evidence_ids"], [])

    def test_a_bare_string_of_evidence_ids_is_refused(self):
        # [str(e) for e in "ev_news_1"] would store nine single characters, and
        # this is the only evidence field in the system with no validation
        # behind it -- its input comes from headlines the model read.
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            brake.record(level="skip", reason="halt", evidence_ids="ev_news_1")

    def test_a_non_none_level_requires_at_least_one_evidence_id(self):
        with self.assertRaisesRegex(ValueError, "evidence_ids"):
            brake.record(level="reduce_50", reason="halt", evidence_ids=[])

    def test_every_record_is_marked_unbacktested(self):
        # ADR-0005 clause 1 requires this wherever the brake appears. Finnhub's
        # company-news archive is one rolling year and returns HTTP 200 with an
        # empty array beyond it, so a replay harness would look green while
        # testing nothing.
        for level in brake.LEVELS:
            record = brake.record(level=level,
                                  reason="" if level == "none" else "issuer halt",
                                  evidence_ids=[] if level == "none" else ["ev_1"])
            self.assertFalse(record["backtested"])
            self.assertIn("未回测", record["disclosure"])

    def test_applied_reports_both_quantities(self):
        applied = brake.applied(
            record=brake.record(level="reduce_50", reason="halt", evidence_ids=["ev_1"]),
            quantity=17)
        self.assertEqual(applied["pre_brake_quantity"], 17)
        self.assertEqual(applied["post_brake_quantity"], 8)
        self.assertFalse(applied["backtested"])


class RiskInputs(unittest.TestCase):
    def test_post_trade_weight_uses_the_post_trade_share_count(self):
        # The gate's fingerprint binds these numbers to THIS trade, so they must
        # describe the book after it, not before.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=100, price=100.0, total_value=40000.0,
            sector_values={"diversified": 10000.0}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[40000.0])
        self.assertAlmostEqual(inputs["post_trade_weight"], 0.25)

    def test_sector_weight_counts_the_whole_sector_after_the_trade(self):
        inputs = riskinputs.compute(
            symbol="XLK", target_shares=50, price=200.0, total_value=100000.0,
            sector_values={"technology": 5000.0}, sector_of="technology",
            average_dollar_volume=1e9, value_history=[100000.0])
        # 50 * 200 = 10000 for this holding, plus 5000 already in the sector
        # from other holdings, over 100000.
        self.assertAlmostEqual(inputs["post_trade_sector_weight"], 0.15)

    def test_liquidity_is_order_notional_over_average_dollar_volume(self):
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=10, price=500.0, total_value=100000.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1_000_000.0, value_history=[100000.0],
            delta_shares=10)
        self.assertAlmostEqual(inputs["position_adv_fraction"], 0.005)

    def test_liquidity_measures_the_traded_amount_not_the_held_amount(self):
        # A hold trades nothing, so it consumes no liquidity.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1000, price=500.0, total_value=1e9,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1_000_000.0, value_history=[1e9], delta_shares=0)
        self.assertEqual(inputs["position_adv_fraction"], 0.0)

    def test_a_single_observation_has_zero_drawdown(self):
        # Not a cheat: with one value the drawdown from peak genuinely is zero,
        # and it becomes meaningful as history accumulates. The sample count is
        # reported so a short history is visible rather than implied.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=100.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[100.0])
        self.assertEqual(inputs["drawdown"], 0.0)
        self.assertEqual(inputs["drawdown_sample_count"], 1)

    def test_drawdown_is_measured_from_the_peak(self):
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=80.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[100.0, 120.0, 90.0, 80.0])
        self.assertAlmostEqual(inputs["drawdown"], (120.0 - 80.0) / 120.0)
        self.assertEqual(inputs["drawdown_sample_count"], 4)

    def test_no_correlation_key_is_produced(self):
        # policy marks correlation not_applicable for the ETF sleeve, so
        # supplying a value here would be a number nobody reads pretending to
        # be a measurement.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=100.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=1e9, value_history=[100.0])
        self.assertNotIn("max_correlation", inputs)

    def test_every_produced_value_is_in_the_unit_interval(self):
        # policy treats a value outside [0, 1] as "unknown", not "fail", so an
        # out-of-range number would silently block execution instead of failing
        # a limit.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1000, price=500.0, total_value=100000.0,
            sector_values={"diversified": 400000.0}, sector_of="diversified",
            average_dollar_volume=1000.0, value_history=[100000.0], delta_shares=1000)
        for key in ("post_trade_weight", "post_trade_sector_weight",
                    "position_adv_fraction", "drawdown"):
            self.assertGreaterEqual(inputs[key], 0.0, key)
            self.assertLessEqual(inputs[key], 1.0, key)

    def test_a_zero_total_value_raises_rather_than_dividing(self):
        with self.assertRaisesRegex(ValueError, "total_value"):
            riskinputs.compute(
                symbol="QQQ", target_shares=1, price=1.0, total_value=0.0,
                sector_values={}, sector_of="diversified",
                average_dollar_volume=1e9, value_history=[100.0])

    def test_a_missing_volume_omits_the_liquidity_key(self):
        # Absent, not zero: zero would read as "no liquidity risk measured as
        # pass", and policy turns an absent key into "unknown", which blocks.
        inputs = riskinputs.compute(
            symbol="QQQ", target_shares=1, price=1.0, total_value=100.0,
            sector_values={}, sector_of="diversified",
            average_dollar_volume=None, value_history=[100.0])
        self.assertNotIn("position_adv_fraction", inputs)


class AverageDollarVolume(unittest.TestCase):
    def test_it_averages_close_times_volume(self):
        bars = [{"session": "2026-09-01", "close": 100.0, "volume": 1000},
                {"session": "2026-09-02", "close": 110.0, "volume": 2000}]
        self.assertAlmostEqual(riskinputs.average_dollar_volume(bars, sessions=2),
                               (100.0 * 1000 + 110.0 * 2000) / 2)

    def test_it_uses_only_the_most_recent_sessions(self):
        bars = [{"session": f"2026-08-{d:02d}", "close": 1.0, "volume": 1} for d in range(1, 26)]
        bars += [{"session": "2026-09-01", "close": 100.0, "volume": 100}]
        self.assertAlmostEqual(riskinputs.average_dollar_volume(bars, sessions=1), 10000.0)

    def test_bars_without_volume_yield_none(self):
        # The Nasdaq equity record has no volume at all, so this must not be
        # mistaken for zero volume.
        bars = [{"session": "2026-09-01", "close": 100.0}]
        self.assertIsNone(riskinputs.average_dollar_volume(bars, sessions=1))

    def test_an_empty_series_yields_none(self):
        self.assertIsNone(riskinputs.average_dollar_volume([], sessions=20))


if __name__ == "__main__":
    unittest.main()

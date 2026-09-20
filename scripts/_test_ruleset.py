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

from copilot import ruleset
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


if __name__ == "__main__":
    unittest.main()

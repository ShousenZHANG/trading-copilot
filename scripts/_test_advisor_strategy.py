"""Independent advisor templates, frozen historical evidence and explicit adoption."""
from __future__ import annotations

import sys
import unittest
import copy
import math
from unittest.mock import patch
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _test_market_data import FixtureProvider, NOW, snapshot
from copilot.advisor_strategy import (TemplateSpec, evaluate_candidate, validate_history,
    build_adoption, verify_adoption, compile_signal, spec_hash, history_hash,
    runtime_fingerprint, cost_hash, _replay, DEFAULT_COST_MODEL)

HISTORY_CONFIRMATION = "我已审核历史数据来源、交易日历、分红拆股、时点标的与冻结样本外区间"
ADOPTION_CONFIRMATION = "我明确采用此顾问策略"


class HistoryCalendarFixture:
    """Injected artificial schedule only; never a production US calendar."""
    def is_session(self, instrument, session):
        return date.fromisoformat(session).weekday() < 5

    def close(self, instrument, session):
        return datetime.combine(date.fromisoformat(session), time(20), timezone.utc)

    def local_midnight(self, instrument, session):
        # Explicit artificial winter-day boundary; never a timezone fallback.
        return datetime.combine(date.fromisoformat(session), time(5), timezone.utc)


def dividend_event(ex_session="2020-01-07", payment_session="2020-01-10", amount=1.):
    return {"event_id": "dividend-fixture-1", "ex_session": ex_session,
            "payment_session": payment_session, "known_at": "2020-01-06T15:00:00Z",
            "cash_available_at": payment_session + "T15:00:00Z", "amount_per_pre_ex_share": amount,
            "currency": "USD", "unit": "USD_per_pre_ex_raw_share", "distribution_type": "ordinary_cash",
            "source_evidence_id": "fixture"}


def replay_fixture(spec, bars, events):
    closes = {bar["session"]: HistoryCalendarFixture().close({}, bar["session"])
              for bar in bars[spec.universe[0]]}
    return _replay(spec, bars, DEFAULT_COST_MODEL, dividend_events=events, session_closes=closes)


def validate_fixture(spec, bundle, **kwargs):
    return validate_history(spec, bundle, calendar=HistoryCalendarFixture(), **kwargs)


def history_fixture(*, data_role="fixture"):
    """Artificial weekday data; not a verified market dataset or strategy evidence."""
    bars, counts, cursor = [], {}, date(2006, 1, 2)
    while cursor <= date(2023, 12, 29):
        if cursor.weekday() < 5:
            price = 100 + len(bars) * .03 + 3 * math.sin(len(bars) / 27)
            bars.append({"session": cursor.isoformat(), "open": price, "high": price + 1,
                         "low": price - 1, "close": price, "adjusted_close": price,
                         "split_ratio": 1.0, "dividend_cash_per_share": 0.0,
                         "available_at": cursor.isoformat() + "T21:00:00Z"})
            counts[cursor.year] = counts.get(cursor.year, 0) + 1
        cursor += timedelta(days=1)
    bundle = {"schema_version": 1, "bars": {"QQQ": bars}, "dividend_events": {"QQQ": []}, "provenance": {
        "point_in_time_verified": True, "calendar_verified": True,
        "corporate_actions_complete": True, "survivorship_verified": True,
        "universe_method": "point_in_time_membership", "universe_evidence": "fixture-only",
        "dividend_event_basis": "pre_ex_entitlement_with_explicit_cash_availability", "dividend_events_complete": True,
        "execution_price_basis": "raw_unadjusted_usd",
        "indicator_price_basis": "total_return_adjusted",
        "source_evidence": [{"id": "fixture", "url": "https://example.com/fixture",
                             "sha256": "a" * 64}], "data_role": data_role}}
    return bundle, counts


def frozen_fixture(spec, bundle):
    return {"kind": "current_frozen_replay", "spec_hash": spec_hash(spec),
            "code_hash": runtime_fingerprint(), "data_hash": history_hash(bundle),
            "cost_hash": cost_hash(),
            "locked_at": "2026-09-06T10:00:00Z", "training_end": "2018-12-31",
            "oos_start": "2019-01-01", "oos_end": "2023-12-29",
            "holdout_locked": True, "holdout_tuned": False}


def adopted_fixture(family="long_term_trend", symbol="QQQ"):
    """TEST ONLY market-schema simulation with explicit artificial-data review.

    Never a real imported market dataset, personal adoption or performance claim.
    Production fixture-role evidence is separately rejected by build_adoption.
    """
    spec = TemplateSpec(family, (symbol,))
    bundle, counts = history_fixture(data_role="market")
    bundle["bars"] = {symbol: bundle["bars"]["QQQ"]}
    bundle["dividend_events"] = {symbol: bundle["dividend_events"]["QQQ"]}
    validation = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle),
                                  expected_sessions=counts, data_attestation=HISTORY_CONFIRMATION, now=NOW)
    return build_adoption(spec, validation, ADOPTION_CONFIRMATION)


class TemplateContracts(unittest.TestCase):
    def test_stale_unknown_candidate_does_not_invent_exit_or_orders(self):
        current = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP")])
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        result = evaluate_candidate(spec, current, now=NOW + timedelta(days=1))
        item = result["symbols"]["QQQ"]
        self.assertEqual(item["direction"], "unknown")
        self.assertFalse(item["technical_exit"])
        self.assertIsNone(item["reference_trigger_raw"])
        self.assertNotIn("quantity", item)
    def test_templates_are_deterministic_candidates_not_automatic_adoptions(self):
        stored = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP")])
        spec = TemplateSpec("swing_breakout", ("QQQ",))
        candidate = evaluate_candidate(spec, stored, now=NOW)
        item = candidate["symbols"]["QQQ"]
        self.assertEqual(candidate["kind"], "advisor_strategy_candidate")
        self.assertEqual(candidate["execution_scope"], "research_only")
        self.assertEqual(item["direction"], "enter")
        self.assertEqual(item["reference_trigger_raw"], 127.8)
        self.assertAlmostEqual(item["protective_reference_raw"], 123.9)
        self.assertEqual(item["max_holding_sessions"], 20)
        self.assertNotIn("quantity", item)
        for family, parameters in (("swing_breakout", {"extra": 1}),
                                   ("long_term_trend", {"trend_sessions": True})):
            with self.subTest(family=family), self.assertRaises(ValueError):
                TemplateSpec(family, ("QQQ",), parameters)
        with self.assertRaises(ValueError):
            TemplateSpec("long_term_trend", ("^NDX",))


class HistoricalContracts(unittest.TestCase):
    def test_explicit_public_dividend_events_validate_and_missing_facts_fail_closed(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        bundle["dividend_events"]["QQQ"] = [dividend_event()]
        result = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertTrue(result["admitted"], result["issues"])
        audit = result["replay"]["dividend_entitlement_events"][0]
        self.assertGreater(audit["eligible_pre_ex_raw_shares"], 0)
        self.assertEqual(audit["entitlement_usd"], audit["cash_credited_usd"])
        self.assertEqual(audit["credited_session"], "2020-01-10")
        for field, value in (("known_at", "2020-01-07T05:00:00Z"), ("cash_available_at", "2030-01-10T15:00:00Z"),
                             ("currency", "CNY"), ("unit", "USD_per_payment_day_share"),
                             ("distribution_type", "stock_distribution"), ("payment_session", "2020-01-06"),
                             ("source_evidence_id", "unbound"), ("amount_per_pre_ex_share", 1000)):
            with self.subTest(field=field):
                changed = copy.deepcopy(bundle)
                changed["dividend_events"]["QQQ"][0][field] = value
                failed = validate_fixture(spec, changed, freeze_manifest=frozen_fixture(spec, changed), expected_sessions=counts, now=NOW)
                self.assertFalse(failed["admitted"], field)
        for mutate in (lambda b: b["dividend_events"]["QQQ"][0].pop("known_at"),
                       lambda b: b["dividend_events"]["QQQ"].append(copy.deepcopy(b["dividend_events"]["QQQ"][0])),
                       lambda b: b.pop("dividend_events"),
                       lambda b: b["provenance"].update(dividend_events_complete=False),
                       lambda b: b["bars"]["QQQ"][500].update(dividend_cash_per_share=1)):
            changed = copy.deepcopy(bundle)
            mutate(changed)
            failed = validate_fixture(spec, changed, freeze_manifest=frozen_fixture(spec, changed), expected_sessions=counts, now=NOW)
            self.assertFalse(failed["admitted"])

    def test_missing_timezone_database_cannot_turn_utc_midnight_into_exchange_midnight(self):
        class NoTimezoneFixture:
            is_session = HistoryCalendarFixture.is_session
            close = HistoryCalendarFixture.close
        from zoneinfo import ZoneInfoNotFoundError
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        bundle["dividend_events"]["QQQ"] = [dividend_event()]
        with patch("copilot.advisor_strategy.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing tzdb")):
            result = validate_history(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts,
                                      calendar=NoTimezoneFixture(), now=NOW)
        self.assertFalse(result["admitted"])
        self.assertIn("dividend_calendar_or_timezone_unavailable:QQQ", result["issues"])
    def test_ex_date_entitlement_survives_sell_and_excludes_later_buyer(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",), {"trend_sessions": 2, "rebalance_sessions": 2})
        days = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09", "2020-01-10")
        for closes, entitled in (([99, 100, 101, 100, 99, 99, 99], True), ([100, 99, 98, 99, 100, 101, 102], False)):
            with self.subTest(entitled=entitled):
                bars = [{"session": day, "open": value, "high": value + 1, "low": value - 1,
                         "close": value, "adjusted_close": value, "split_ratio": 1, "dividend_cash_per_share": 0}
                        for day, value in zip(days, closes)]
                result, _ = replay_fixture(spec, {"QQQ": bars}, {"QQQ": [dividend_event()]})
                buy = next(trade for trade in result["trades"] if trade["side"] == "buy")
                amount = buy["quantity"] if entitled else 0
                self.assertEqual(result["cash_distributions_received"], amount)
                self.assertEqual(result["dividend_entitlements_usd"], amount)
                self.assertEqual(result["terminal_dividend_receivables_usd"], 0)
                if entitled:
                    self.assertTrue(any(trade["side"] == "sell" and trade["execution_session"] < days[-1] for trade in result["trades"]))

    def test_tail_and_after_close_payments_preserve_receivable_without_funding(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",), {"trend_sessions": 2, "rebalance_sessions": 252})
        days = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08")
        bars = [{"session": day, "open": 100 + i, "high": 101 + i, "low": 99 + i,
                 "close": 100 + i, "adjusted_close": 100 + i, "split_ratio": 1, "dividend_cash_per_share": 0}
                for i, day in enumerate(days)]
        event = dividend_event(payment_session="2020-01-08")
        event["cash_available_at"] = "2020-01-08T21:00:00Z"
        result, _ = replay_fixture(spec, {"QQQ": bars}, {"QQQ": [event]})
        bought = next(trade["quantity"] for trade in result["trades"] if trade["side"] == "buy")
        self.assertEqual(result["cash_distributions_received"], 0)
        self.assertEqual(result["terminal_dividend_receivables_usd"], bought)
        self.assertEqual(result["terminal_settled_cash"], result["terminal_cash"])
        extended = bars + [{"session": "2020-01-09", "open": 105, "high": 106, "low": 104,
                            "close": 105, "adjusted_close": 105, "split_ratio": 1, "dividend_cash_per_share": 0}]
        later, _ = replay_fixture(spec, {"QQQ": extended}, {"QQQ": [event]})
        self.assertEqual(later["cash_distributions_received"], bought)
        self.assertEqual(later["terminal_dividend_receivables_usd"], 0)
        self.assertEqual(later["dividend_entitlement_events"][0]["credited_session"], "2020-01-09")
        event["payment_session"] = "2020-01-09"
        event["cash_available_at"] = "2020-01-09T15:00:00Z"
        tail, _ = replay_fixture(spec, {"QQQ": bars}, {"QQQ": [event]})
        self.assertEqual(tail["terminal_dividend_receivables_usd"], bought)
        self.assertEqual(tail["cash_distributions_received"], 0)

    def test_same_ex_and_payment_day_locks_old_shares_once(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",), {"trend_sessions": 2, "rebalance_sessions": 2})
        days = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07")
        bars = [{"session": day, "open": value, "high": value + 1, "low": value - 1,
                 "close": value, "adjusted_close": value, "split_ratio": 1, "dividend_cash_per_share": 0}
                for day, value in zip(days, [99, 100, 101, 100])]
        result, curve = replay_fixture(spec, {"QQQ": bars}, {"QQQ": [dividend_event(payment_session=days[-1])]})
        quantity = next(trade["quantity"] for trade in result["trades"] if trade["side"] == "buy")
        self.assertEqual(result["cash_distributions_received"], quantity)
        self.assertEqual(result["dividend_entitlements_usd"], quantity)
        self.assertEqual(result["terminal_dividend_receivables_usd"], 0)
        self.assertEqual(result["dividend_entitlement_events"][0]["credited_session"], days[-1])
        self.assertAlmostEqual(curve[-1][1], curve[-2][1], msg="raw ex-price loss is offset once, not twice, by entitlement")
    def test_ten_percent_highwater_brake_blocks_reentry_but_not_sell_or_reset(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",), {"trend_sessions": 2, "rebalance_sessions": 2})
        start, days = date(2020, 1, 2), []
        while len(days) < 12:
            if start.weekday() < 5:
                days.append(start.isoformat())
            start += timedelta(days=1)
        closes = [99, 100, 101, 10, 9, 8, 9, 10, 11, 12, 13, 14]
        bars = [{"session": day, "open": value, "high": value + 1, "low": value - 1,
                 "close": value, "adjusted_close": value, "split_ratio": 1, "dividend_cash_per_share": 0}
                for day, value in zip(days, closes)]
        result, _ = _replay(spec, {"QQQ": bars}, DEFAULT_COST_MODEL)
        sells = [trade for trade in result["trades"] if trade["side"] == "sell"]
        self.assertTrue(sells, "brake must allow actual risk-reducing exits")
        self.assertEqual(len([trade for trade in result["trades"] if trade["side"] == "buy"]), 1,
                         "recovering prices cannot reset the unrecovered account highwater")
        self.assertGreater(result["drawdown_brake_count"], 0)
        self.assertEqual(result["highwater_nav_usd"], 100_000)
        self.assertEqual(result["terminal_shares"]["QQQ"], 0)
    def test_persisted_identity_rejects_code_drift_and_confirmation_has_no_rule_id_effect(self):
        adopted = adopted_fixture()
        spec = adopted["spec"]
        another = build_adoption(spec, adopted["validation"], "I explicitly adopt this advisor strategy.")
        self.assertEqual(adopted["rule_id"], another["rule_id"])
        with patch("copilot.advisor_strategy.runtime_fingerprint", return_value="0" * 64):
            self.assertFalse(verify_adoption(adopted))
        for statement in ("我不采用此顾问策略", "I will adopt this advisor strategy", '"I explicitly adopt this advisor strategy"', "test I explicitly adopt this advisor strategy"):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                build_adoption(spec, adopted["validation"], statement)

    def test_missing_real_calendar_fails_even_with_all_source_flags(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        from copilot.data_calendar import CalendarUnavailable
        with patch("copilot.advisor_strategy.MarketCalendar.is_session", side_effect=CalendarUnavailable("unavailable")):
            result = validate_history(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertFalse(result["admitted"])
        self.assertIn("calendar_unavailable:QQQ", result["issues"])
    def test_cash_dividends_and_splits_use_raw_held_shares_once(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",), {"trend_sessions": 2, "rebalance_sessions": 252})
        raw = [99, 100, 101, 50.5, 50.5, 50.5]
        days = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09")
        bars = [{"session": session, "open": value, "high": value + .5, "low": value - .5,
                 "close": value, "adjusted_close": [49.5, 50, 50.5, 50.5, 50.5, 50.5][i],
                 "split_ratio": 2 if i == 3 else 1, "dividend_cash_per_share": 0}
                for i, (session, value) in enumerate(zip(days, raw))]
        report, curve = replay_fixture(spec, {"QQQ": bars}, {"QQQ": [dividend_event(payment_session=days[4])]})
        buy = next(trade for trade in report["trades"] if trade["side"] == "buy")
        self.assertEqual(report["terminal_shares"]["QQQ"], buy["quantity"] * 2)
        self.assertEqual(report["cash_distributions_received"], buy["quantity"])
        self.assertAlmostEqual(curve[3][1] - curve[2][1], buy["quantity"])
        self.assertAlmostEqual(curve[4][1], curve[3][1], msg="payment converts receivable to cash, not another NAV gain")

    def test_joint_post_cost_caps_and_future_perturbation_are_order_independent(self):
        universe = ("QQQ", "SPY", "IWM", "VUG")
        spec = TemplateSpec("long_term_trend", universe, {"trend_sessions": 2, "rebalance_sessions": 2})
        days = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09", "2020-01-10")
        bars = {symbol: [{"session": day, "open": value, "high": value + .1, "low": value - .1,
                         "close": value, "adjusted_close": value, "split_ratio": 1, "dividend_cash_per_share": 0}
                        for day, value in zip(days, [1, 1.01, 1.02, 1.03, 1.04, 1.05, 1.06])] for symbol in universe}
        first, curve = _replay(spec, bars, DEFAULT_COST_MODEL)
        reverse, _ = _replay(TemplateSpec(spec.family, tuple(reversed(universe)), spec.parameters), bars, DEFAULT_COST_MODEL)
        key = lambda report: sorted((trade["symbol"], trade["execution_session"], trade["quantity"], trade["side"]) for trade in report["trades"])
        self.assertEqual(key(first), key(reverse))
        initial = [trade for trade in first["trades"] if trade["execution_session"] == days[2]]
        exposure = sum(trade["quantity"] * trade["price"] for trade in initial)
        self.assertLessEqual(exposure, curve[2][1] * .9)
        changed = copy.deepcopy(bars)
        for symbol in universe:
            changed[symbol][-1].update(open=100, high=101, low=99, close=100, adjusted_close=100)
        perturbed, _ = _replay(spec, changed, DEFAULT_COST_MODEL)
        before = lambda report: [trade for trade in report["trades"] if trade["execution_session"] < days[-1]]
        self.assertEqual(before(first), before(perturbed))

    def test_risk_reducing_sell_is_not_blocked_by_buy_fee_limit(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",), {"trend_sessions": 2, "rebalance_sessions": 2})
        days = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09")
        prices = [99, 100, 101, .0001, .0001, .0001]
        bars = [{"session": day, "open": value, "high": value + .1, "low": value / 2,
                 "close": value, "adjusted_close": value, "split_ratio": 1, "dividend_cash_per_share": 0}
                for day, value in zip(days, prices)]
        result, _ = _replay(spec, {"QQQ": bars}, DEFAULT_COST_MODEL)
        sells = [trade for trade in result["trades"] if trade["side"] == "sell"]
        self.assertTrue(sells)
        self.assertGreater(sells[0]["fee"], sells[0]["quantity"] * sells[0]["price"] * .01)
        self.assertEqual(result["terminal_shares"]["QQQ"], 0)
        self.assertGreaterEqual(result["terminal_settled_cash"], 0)

    def test_cost_freeze_payment_basis_and_prospective_chronology_fail_closed(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        freeze = frozen_fixture(spec, bundle)
        freeze["cost_hash"] = "0" * 64
        result = validate_fixture(spec, bundle, freeze_manifest=freeze, expected_sessions=counts, now=NOW)
        self.assertIn("freeze_cost_mismatch", result["issues"])
        bundle["provenance"]["dividend_event_basis"] = "ex_date"
        result = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertIn("dividend_entitlement_event_basis_unverified", result["issues"])
        bundle["provenance"]["dividend_event_basis"] = "pre_ex_entitlement_with_explicit_cash_availability"
        bundle["provenance"]["execution_price_basis"] = "split_adjusted"
        result = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertIn("execution_raw_price_basis_unverified", result["issues"])
        bundle["provenance"]["execution_price_basis"] = "raw_unadjusted_usd"
        freeze = frozen_fixture(spec, bundle)
        freeze["kind"] = "prospective"
        result = validate_fixture(spec, bundle, freeze_manifest=freeze, expected_sessions=counts, now=NOW)
        self.assertIn("prospective_freeze_must_precede_oos", result["issues"])
    def test_rotation_cannot_finance_new_buys_with_same_card_sales(self):
        spec = TemplateSpec("long_term_trend", ("QQQ", "SPY", "IWM", "VUG"),
                            {"trend_sessions": 2, "rebalance_sessions": 2})
        dates = ("2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07", "2020-01-08", "2020-01-09", "2020-01-10")
        prices = {symbol: [99, 100, 101, 600, 590, 590, 590] for symbol in spec.universe[:3]}
        prices["VUG"] = [100, 99, 98, 98, 110, 111, 112]
        bars = {symbol: [{"session": session, "open": close, "high": close + 1, "low": close - 1,
                         "close": close, "adjusted_close": close, "split_ratio": 1., "dividend_cash_per_share": 0.}
                        for session, close in zip(dates, closes)] for symbol, closes in prices.items()}
        replay, _ = _replay(spec, bars, DEFAULT_COST_MODEL)
        self.assertTrue(any(trade["side"] == "sell" and trade["execution_session"] == dates[5] for trade in replay["trades"]))
        self.assertFalse(any(trade["side"] == "buy" and trade["execution_session"] == dates[5] for trade in replay["trades"]))
        self.assertGreater(replay["terminal_unsettled_receivables"], 0)

    def test_execution_does_not_buy_after_next_close_falls_below_frozen_trigger(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        bar = bundle["bars"]["QQQ"][200]
        bar.update(open=1., high=2., low=.5, close=1., adjusted_close=1.)
        result = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertTrue(result["admitted"], result["issues"])
        self.assertFalse(any(trade["side"] == "buy" and trade["execution_session"] == bar["session"] for trade in result["replay"]["trades"]))

    def test_independent_user_review_required_even_when_bundle_claims_market(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture(data_role="market")
        bundle["provenance"]["data_attestation"] = "self-asserted JSON cannot stand in for CLI review"
        validation = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertTrue(validation["admitted"])
        self.assertFalse(validation["market_validated"])
        self.assertFalse(validation["source_authenticated"])
        self.assertFalse(validation["adoption_eligible"])
        with self.assertRaises(ValueError):
            build_adoption(spec, validation, "Explicit strategy confirmation alone")
        for family in ("long_term_trend", "swing_breakout"):
            with self.subTest(family=family):
                adopted = adopted_fixture(family)
                self.assertTrue(verify_adoption(adopted))
                current = snapshot([FixtureProvider(), FixtureProvider("alpaca", "Alpaca SIP")])
                signal = compile_signal(adopted, current, "long_term" if family == "long_term_trend" else "swing", now=NOW)
                self.assertEqual(signal["symbols"]["QQQ"]["direction"], "enter")
                self.assertEqual(signal["execution_scope"], "validated_template")
                self.assertFalse(adopted["validation"]["market_validated"])
                changed = copy.deepcopy(adopted)
                changed["spec"]["parameters"]["extra"] = 1
                self.assertFalse(verify_adoption(changed))
                with self.assertRaises(ValueError):
                    compile_signal(adopted, current, "wrong", now=NOW)
    def test_validator_computes_net_replay_and_keeps_fixture_non_adoptable(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        result = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle),
                                  expected_sessions=counts, now=NOW)
        self.assertTrue(result["admitted"], result["issues"])
        self.assertFalse(result["market_validated"])
        self.assertGreater(result["replay"]["fees_paid"], 0)
        self.assertGreater(result["replay"]["trade_count"], 0)
        first = result["replay"]["trades"][0]
        self.assertLess(first["signal_session"], first["execution_session"])
        self.assertEqual(result["execution_assumptions"]["execution_timing"], "next_session_close")
        self.assertIn("same_session_close", result["sensitivity"])
        self.assertIn("double_cost", result["sensitivity"])
        self.assertIn("parameters", result["sensitivity"])
        self.assertGreater(len(result["sensitivity"]["parameters"]["variants"]), 0)
        self.assertEqual(result["sensitivity"]["parameters"]["selection"], "none")
        self.assertIn("drawdown_duration_days", result["replay"])
        self.assertIn("annual_turnover", result["replay"])
        self.assertIn("drawdown_peak", result["replay"])
        self.assertIn("buy_and_hold_basket", result["benchmarks"])
        self.assertEqual(result["benchmarks"]["cash"]["net_return"], 0)
        self.assertEqual(result["initial_nav_usd"], 100_000)
        self.assertIn("personal_account_capital_and_integer_share_minimum_fee_nonlinearity", result["excluded_account_overlays"])
        self.assertEqual(result["freeze"]["kind"], "current_frozen_replay")
        with self.assertRaisesRegex(ValueError, "fixture"):
            build_adoption(spec, result, "I explicitly adopt this template")

    def test_history_and_freeze_unknowns_fail_closed(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        for field in ("point_in_time_verified", "calendar_verified", "corporate_actions_complete", "survivorship_verified"):
            changed = copy.deepcopy(bundle)
            changed["provenance"][field] = False
            result = validate_fixture(spec, changed, freeze_manifest=frozen_fixture(spec, changed), expected_sessions=counts, now=NOW)
            self.assertFalse(result["admitted"])
            self.assertIn(field, " ".join(result["issues"]))
        freeze = frozen_fixture(spec, bundle)
        freeze["code_hash"] = "0" * 64
        result = validate_fixture(spec, bundle, freeze_manifest=freeze, expected_sessions=counts, now=NOW)
        self.assertFalse(result["admitted"])
        self.assertIn("freeze_code_mismatch", result["issues"])
        future = copy.deepcopy(bundle)
        future["bars"]["QQQ"][500]["available_at"] = "2030-01-01T00:00:00Z"
        result = validate_fixture(spec, future, freeze_manifest=frozen_fixture(spec, future), expected_sessions=counts, now=NOW)
        self.assertFalse(result["admitted"])
        self.assertIn("bar_availability_after_session:QQQ", result["issues"])

    def test_real_close_time_and_independent_affirmative_review_are_required(self):
        spec = TemplateSpec("long_term_trend", ("QQQ",))
        bundle, counts = history_fixture()
        bundle["bars"]["QQQ"][-1]["available_at"] = bundle["bars"]["QQQ"][-1]["session"] + "T14:00:00Z"
        result = validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, now=NOW)
        self.assertFalse(result["admitted"])
        self.assertIn("bar_available_before_session_close:QQQ", result["issues"])
        for statement in ("我未审核历史数据", "I will review the historical data", '"I have reviewed"', "TEST ONLY I reviewed", "yes"):
            with self.subTest(statement=statement), self.assertRaises(ValueError):
                validate_fixture(spec, bundle, freeze_manifest=frozen_fixture(spec, bundle), expected_sessions=counts, data_attestation=statement, now=NOW)


if __name__ == "__main__":
    unittest.main()

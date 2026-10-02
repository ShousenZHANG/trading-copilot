"""Execution regressions through the shared facade and temporary SQLite state."""
from __future__ import annotations

import copy
import sqlite3
import unittest
from datetime import timedelta
from decimal import Decimal

from copilot import journal, service
import _test_copilot_service as facade_fixtures
from _test_policy import NOW, seal


class ExecutionFlow(unittest.TestCase):
    def environment(self, gold=False):
        fixture = (facade_fixtures.TheGoldSleeveIsReachableFromTheSharedFacade()
                   if gold else facade_fixtures.EngineFacade())
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        return fixture

    def record(self, fixture, *, key, symbol, quantity, unit, occurred_at, **details):
        operation = {
            "statement": f"I already bought {quantity} {unit} of {symbol}.",
            "source_message_id": key, "external_trade_id": key,
            "execution_status": "executed", "instrument_id": symbol,
            "side": "buy", "quantity": quantity, "unit": unit,
            "price": "100" if symbol != "GOLD.CNY" else "962.5",
            "currency": "USD" if symbol != "GOLD.CNY" else "CNY",
            "occurred_at": occurred_at, "fees": "0", "account_id": "fixture-account",
            **details,
        }
        receipt = service.record(operation, key, db_path=fixture.db, now=NOW)
        self.assertEqual(receipt["status"], "executed")
        return receipt

    def fresh_snapshot(self, fixture, snapshot_id, now):
        snapshot = copy.deepcopy(service.snapshot(snapshot_id, db_path=fixture.db))
        snapshot["created_at"] = now.isoformat()
        snapshot["decision_at"] = now.isoformat()
        for evidence in snapshot["evidence"]:
            evidence["retrieved_at"] = now.isoformat()
        seal(snapshot)
        journal.save_snapshot(snapshot, db_path=fixture.db)
        return snapshot["snapshot_id"]

    def test_real_decimal_holding_is_valued_and_reduced_instead_of_bought(self):
        fixture = self.environment()
        self.record(fixture, key="etf-held", symbol="QQQ", quantity="400", unit="share",
                    occurred_at="2025-01-02")
        service.declare_coverage(db_path=fixture.db)
        config = fixture.config(pointer=fixture.rule_id)
        first = service.evaluate(snapshot_id=fixture.snapshot_id, config_path=config,
                                 db_path=fixture.db, now=NOW)
        self.assertEqual(first["cash_plan"]["holdings_value"], 45980.0)
        next_time = NOW + timedelta(minutes=1)
        snapshot_id = self.fresh_snapshot(fixture, fixture.snapshot_id, next_time)
        second = service.evaluate(snapshot_id=snapshot_id, config_path=config,
                                  db_path=fixture.db, now=next_time)
        qqq = next(order for order in second["orders"] if order["instrument_id"] == "QQQ")
        self.assertEqual(qqq["held_shares"], 400)
        self.assertEqual(qqq["action"], "reduce")
        self.assertGreater(qqq["quantity"], 0)

    def test_second_row_storage_failure_rolls_back_the_whole_evaluation(self):
        fixture = self.environment()
        with sqlite3.connect(fixture.db) as connection:
            connection.execute("""CREATE TRIGGER fixture_reject_spy BEFORE INSERT ON recommendations
                WHEN json_extract(NEW.payload, '$.instrument_id') = 'SPY'
                BEGIN SELECT RAISE(ABORT, 'fixture second-row failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, "second-row failure"):
            service.evaluate(snapshot_id=fixture.snapshot_id,
                             config_path=fixture.config(pointer=fixture.rule_id),
                             db_path=fixture.db, now=NOW)
        self.assertEqual(service.context(db_path=fixture.db)["recommendations"], [])

    def test_changing_the_adopted_cash_reserve_requires_a_new_backtest(self):
        fixture = self.environment()
        config = fixture.config(pointer=fixture.rule_id)
        config.write_text(config.read_text(encoding="utf-8").replace(
            "min_cash_reserve_pct = 0.6", "min_cash_reserve_pct = 0.7"), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "cash reserve"):
            service.evaluate(snapshot_id=fixture.snapshot_id, config_path=config,
                             db_path=fixture.db, now=NOW)
        self.assertEqual(service.context(db_path=fixture.db)["recommendations"], [])

    def test_changing_the_configured_universe_does_not_run_the_old_rule(self):
        fixture = self.environment()
        config = fixture.config(pointer=fixture.rule_id)
        config.write_text(config.read_text(encoding="utf-8").replace(
            "[etf]\n", '[etf]\nuniverse = ["IWM", "VTI"]\n'), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "universe"):
            service.evaluate(snapshot_id=fixture.snapshot_id, config_path=config,
                             db_path=fixture.db, now=NOW)
        self.assertEqual(service.context(db_path=fixture.db)["recommendations"], [])

    def test_legacy_adoptions_remain_readable_but_cannot_run_the_new_engine(self):
        fixture = self.environment()
        legacy = copy.deepcopy(journal.load_adoption(fixture.rule_id, db_path=fixture.db))
        legacy.update(schema_version=1, rule_id="rule-1111222233334444")
        journal.record_adoption(legacy, db_path=fixture.db)
        with self.assertRaisesRegex(ValueError, "backtest"):
            service.evaluate(snapshot_id=fixture.snapshot_id,
                             config_path=fixture.config(pointer=legacy["rule_id"]),
                             db_path=fixture.db, now=NOW)
        self.assertEqual(journal.load_adoption(legacy["rule_id"], db_path=fixture.db)["schema_version"], 1)
        self.assertEqual(service.context(db_path=fixture.db)["recommendations"], [])

    def test_a_gold_fill_does_not_open_a_second_contribution_in_the_same_session(self):
        fixture = self.environment(gold=True)
        fixture.declare()
        snapshot_id = fixture.quoted_snapshot()
        first = fixture.evaluate(snapshot_id)
        self.assertEqual(first["orders"][0]["action"], "buy")
        latest = fixture.snapshot["instruments"]["GOLD.CNY"]["latest_session"]
        self.record(fixture, key="gold-filled", symbol="GOLD.CNY",
                    quantity=first["orders"][0]["grams"], unit="gram",
                    occurred_at=latest + "T07:30:00+00:00", merchant="中国银行", purity="0.9999")
        fixture.declare()
        second = service.evaluate(snapshot_id=snapshot_id, sleeve="gold",
                                  config_path=fixture.config(), db_path=fixture.db,
                                  now=NOW + timedelta(minutes=1))
        self.assertEqual(second["orders"][0]["action"], "hold")
        self.assertFalse(second["rebalance_due"])

    def test_gold_daily_count_uses_completed_fills_and_the_injected_clock(self):
        fixture = self.environment(gold=True)
        for index in range(10):
            self.record(fixture, key=f"gold-today-{index}", symbol="GOLD.CNY",
                        quantity="1", unit="gram",
                        occurred_at=f"2026-09-06T00:{index:02d}:00+00:00",
                        merchant="中国银行", purity="0.9999")
        fixture.declare()
        current = service.context(db_path=fixture.db, sleeve="gold", now=NOW)
        self.assertEqual(current["gold_orders_today"], 10)
        result = fixture.evaluate(fixture.quoted_snapshot())
        self.assertNotEqual(result["orders"][0]["action"], "buy")

    def test_coin_units_are_converted_to_fine_grams_before_budgeting(self):
        fixture = self.environment(gold=True)
        self.record(fixture, key="gold-coins", symbol="GOLD.CNY", quantity="2", unit="item",
                    occurred_at="2025-01-02", merchant="中国银行", purity="0.9999",
                    weight_grams="31.1034768", price="60000", price_basis="total")
        fixture.declare()
        result = fixture.evaluate(fixture.quoted_snapshot())
        order = result["orders"][0]
        self.assertEqual(Decimal(order["held_grams"]), Decimal("62.2007"))
        self.assertNotEqual(order["action"], "buy")
        self.assertTrue(any("investable_total_cny" in reason for reason in order["refusals"]))

    def test_usd_evaluations_cannot_change_a_gold_drawdown(self):
        fixture = self.environment(gold=True)
        fixture.declare(sleeve="etf", currency="USD")
        version = service.context(db_path=fixture.db)["portfolio_version"]
        journal.record_recommendation({"decision_id": "fixture-usd-history",
            "snapshot_id": fixture.snapshot["snapshot_id"], "portfolio_version": version,
            "instrument_id": "QQQ", "action": "hold", "sleeve": "etf",
            "portfolio_total_value": 20000.0, "currency": "USD"}, db_path=fixture.db)
        fixture.declare()
        result = fixture.evaluate(fixture.quoted_snapshot())
        self.assertEqual(result["orders"][0]["risk_checks"]["drawdown"]["value"], 0.0)
        self.assertEqual(result["orders"][0]["action"], "buy")
        current = service.context(db_path=fixture.db, sleeve="gold")
        self.assertTrue(all(row["instrument_id"] == "GOLD.CNY" for row in current["recommendations"]))


if __name__ == "__main__":
    unittest.main()

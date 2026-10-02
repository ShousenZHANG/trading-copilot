"""Execution-timing experiments use synthetic bars and isolated reports only."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

from copilot.backtest import engine, frame as frame_mod, rules


def prices(rows):
    return frame_mod.build(
        dates=[date(2024, 1, 2) + timedelta(days=i) for i in range(len(rows))],
        symbols=["AAA", "BBB"][:len(rows[0])], closes=rows)


class NextSessionExecution(unittest.TestCase):
    def test_next_close_does_not_capture_the_gap_before_its_first_fill(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close
        frame = prices([[100], [200]])
        result = run_next_session_close(
            frame, rule=engine.StaticWeights({"AAA": 1}), start_cash=1000,
            cost_model=engine.CostModel.free(), cash_floor_pct=0)
        self.assertNotIsInstance(result, engine.Result)
        self.assertEqual(result.execution_scope, "research_only")
        self.assertEqual(result.curve, [(frame.dates[0], 1000), (frame.dates[1], 1000)])
        self.assertEqual(result.positions_history, [{}, {"AAA": 5}])
        self.assertEqual(result.rebalance_count, 1)
        self.assertEqual(result.unexecuted_tail_signal["signal_session"], frame.dates[-1].isoformat())

    def test_rules_cannot_access_future_rows_dates_or_columns(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close
        seen = []
        test = self

        class Spy(engine.StaticWeights):
            def weights(self, frame, i):
                test.assertEqual(len(frame), i + 1)
                test.assertEqual(len(frame.dates), i + 1)
                test.assertEqual(len(frame.closes), i + 1)
                test.assertEqual(len(frame.column("AAA")), i + 1)
                with test.assertRaises(IndexError):
                    frame.row(i + 1)
                seen.append(i)
                return super().weights(frame, i)

        run_next_session_close(prices([[100], [150], [80]]),
                               rule=Spy({"AAA": 1}), start_cash=1000,
                               cost_model=engine.CostModel.free(), cash_floor_pct=0)
        self.assertEqual(seen, [0, 1, 2])

    def test_target_selection_is_frozen_before_the_next_close_changes_the_winner(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close

        class Winner(engine.StaticWeights):
            def weights(self, frame, i):
                symbol = max(frame.row(i), key=frame.row(i).get)
                return {symbol: 1}

        targets = []
        for future_row in ([50, 300], [300, 50]):
            result = run_next_session_close(
                prices([[110, 100], future_row]), rule=Winner({}), start_cash=1000,
                cost_model=engine.CostModel.free(), cash_floor_pct=0)
            targets.append(result.execution_history[0]["targets"])
            self.assertEqual(result.execution_history[0]["signal_session"], "2024-01-02")
            self.assertEqual(result.execution_history[0]["execution_session"], "2024-01-03")
            self.assertIn("AAA", result.positions_history[-1])
            self.assertNotIn("BBB", result.positions_history[-1])
        self.assertEqual(targets, [{"AAA": 1}, {"AAA": 1}])

    def test_next_published_row_can_be_several_calendar_days_later(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close
        frame = frame_mod.build(dates=[date(2024, 1, 5), date(2024, 1, 8)],
                                symbols=["AAA"], closes=[[100], [120]])
        result = run_next_session_close(frame, rule=engine.StaticWeights({"AAA": 1}),
                                       start_cash=1000, cost_model=engine.CostModel.free(),
                                       cash_floor_pct=0)
        self.assertEqual(result.execution_history[0]["execution_session"], "2024-01-08")

    def test_one_final_signal_has_no_execution_or_cost(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close
        result = run_next_session_close(prices([[100]]), rule=engine.StaticWeights({"AAA": 1}),
                                       start_cash=1000, cost_model=engine.CostModel(),
                                       cash_floor_pct=0)
        self.assertEqual(result.curve[0][1], 1000)
        self.assertEqual(result.execution_history, [])
        self.assertEqual(result.total_costs, 0)
        self.assertEqual(result.rebalance_count, 0)
        self.assertIsNotNone(result.unexecuted_tail_signal)

    def test_unfunded_execution_attempt_does_not_advance_cadence(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close
        result = run_next_session_close(
            prices([[p] for p in (150, 151, 150, 150, 80)]),
            rule=rules.InverseVolatility(("AAA",), lookback_days=2, rebalance_days=21),
            start_cash=100, cost_model=engine.CostModel.free(), cash_floor_pct=0)
        self.assertEqual(result.positions_history, [{}, {}, {"AAA": 1}])
        self.assertEqual([row["executed"] for row in result.execution_history], [False, True])
        self.assertEqual(result.rebalance_attempt_count, 2)
        self.assertEqual(result.rebalance_count, 1)
        self.assertIsNone(result.unexecuted_tail_signal)

    def test_gap_fills_keep_costs_and_reserve_funded_on_both_sides(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close

        class Rotate(engine.StaticWeights):
            def weights(self, frame, i):
                return {"AAA" if i % 2 == 0 else "BBB": 1}

        for integer in (True, False):
            with self.subTest(integer=integer):
                result = run_next_session_close(
                    prices([[100, 100], [250, 30], [20, 280], [300, 20]]),
                    rule=Rotate({}), start_cash=1000, cost_model=engine.CostModel(),
                    cash_floor_pct=0.2, integer_shares=integer)
                self.assertTrue(all(row["cash_after"] >= row["cash_reserve"] - 1e-8
                                    for row in result.execution_history))
                self.assertTrue(all(value >= 0 for value in result.cash_history))

    def test_delaying_execution_is_not_a_performance_lower_bound(self):
        from copilot.backtest.execution_sensitivity import compare_execution_timing
        report = compare_execution_timing(
            prices([[100], [50], [75]]), rule=engine.StaticWeights({"AAA": 1}),
            start_cash=1000, cost_model=engine.CostModel.free(), cash_floor_pct=0)
        self.assertEqual(report["same_bar_close"]["final_nav"], 750)
        self.assertEqual(report["next_session_close"]["final_nav"], 1500)
        self.assertFalse(report["adoption_eligible"])
        self.assertNotIn("admitted", report)

    def test_paired_metrics_include_initial_costs_for_both_modes(self):
        from copilot.backtest.execution_sensitivity import compare_execution_timing
        report = compare_execution_timing(
            prices([[100], [100]]), rule=engine.StaticWeights({"AAA": 1}),
            start_cash=1000, cost_model=engine.CostModel(), cash_floor_pct=0)
        self.assertLess(report["same_bar_close"]["net_return"], 0)
        self.assertEqual(report["same_bar_close"]["net_return"],
                         report["next_session_close"]["net_return"])
        self.assertEqual(report["metric_basis"], "same_initial_cash_including_entry_costs")

    def test_research_timing_result_cannot_enter_the_admission_gate(self):
        from copilot.backtest import admission
        from copilot.backtest.execution_sensitivity import run_next_session_close
        result = run_next_session_close(prices([[100], [100]]),
                                       rule=engine.StaticWeights({"AAA": 1}),
                                       start_cash=1000, cost_model=engine.CostModel.free(),
                                       cash_floor_pct=0)
        with self.assertRaisesRegex(ValueError, "engine.Result"):
            admission.assess(result, sessions_by_year=admission.STRESS_SESSIONS)

    def test_negative_warmup_cannot_read_a_future_bar_by_negative_index(self):
        from copilot.backtest.execution_sensitivity import run_next_session_close
        with self.assertRaisesRegex(ValueError, "warmup_bars"):
            run_next_session_close(prices([[100], [200]]),
                                   rule=engine.StaticWeights({"AAA": 1}, warmup_bars=-1),
                                   start_cash=1000, cost_model=engine.CostModel.free(),
                                   cash_floor_pct=0)


class ExecutionSensitivityCli(unittest.TestCase):
    def test_opt_in_report_keeps_the_same_bar_verdict_and_adds_research_only_comparison(self):
        import backtest_cli
        frame = frame_mod.build(dates=[date(2024, 1, 2) + timedelta(days=i) for i in range(4)],
                                symbols=["SPY", "QQQ"],
                                closes=[[100, 50], [110, 48], [120, 49], [130, 51]])
        alignment = {"common_first": "2024-01-02", "common_last": "2024-01-05",
                     "common_bars": 4, "binds_start": "SPY", "binds_end": "QQQ",
                     "bars_lost_vs_longest": 0}
        with tempfile.TemporaryDirectory() as folder:
            output = io.StringIO()
            with patch.object(backtest_cli, "load_universe", return_value=(frame, alignment, [])), \
                    contextlib.redirect_stdout(output):
                code = backtest_cli.main([
                    "--universe", "SPY,QQQ", "--family", "bands", "--out-dir", folder,
                    "--execution-sensitivity", "next-session-close"])
            self.assertEqual(code, 0)
            report = json.loads(next(Path(folder).glob("backtest-*.json")).read_text(encoding="utf-8"))
        family = report["families"][0]
        self.assertEqual(family["execution_assumptions"]["execution_price"], "same_bar_close")
        self.assertIn("admitted", family)
        comparison = family["execution_sensitivity"]
        self.assertEqual(comparison["execution_scope"], "research_only")
        self.assertFalse(comparison["adoption_eligible"])
        self.assertNotIn("admitted", comparison)
        self.assertIn("next-session close", output.getvalue())

    def test_execution_sensitivity_and_adoption_are_rejected_before_loading_or_writing(self):
        import backtest_cli
        with tempfile.TemporaryDirectory() as folder:
            error = io.StringIO()
            with patch.object(backtest_cli, "load_universe") as load, \
                    patch.object(backtest_cli, "adopt_rule") as adopt, \
                    contextlib.redirect_stderr(error), self.assertRaises(SystemExit):
                backtest_cli.main(["--universe", "SPY,QQQ", "--family", "bands", "--adopt",
                                   "--db", str(Path(folder) / "never-created.sqlite"),
                                   "--execution-sensitivity", "next-session-close"])
            load.assert_not_called()
            adopt.assert_not_called()
            self.assertEqual(list(Path(folder).iterdir()), [])
        self.assertIn("cannot be combined with --adopt", error.getvalue())

    def test_gold_and_income_proxy_cannot_be_presented_as_etf_execution_sensitivity(self):
        import backtest_cli
        for extra in (["--sleeve", "gold"], ["--universe", "SPY", "--income-proxy"]):
            with self.subTest(extra=extra), \
                    patch.object(backtest_cli, "load_universe") as load, \
                    patch.object(backtest_cli, "run_gold") as gold, \
                    patch.object(backtest_cli, "run_income_proxy") as proxy, \
                    contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                backtest_cli.main([*extra, "--execution-sensitivity", "next-session-close"])
            load.assert_not_called()
            gold.assert_not_called()
            proxy.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)

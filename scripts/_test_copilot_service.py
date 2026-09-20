"""Shared facade tests: isolated databases, no live network or personal operations."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copilot import journal, service
from _test_policy import NOW, bands_adoption, engine_fixture, fixture, proposal, seal
from _test_ruleset import admitted_result

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "copilot.sqlite"

    def test_collect_persists_exact_snapshot(self):
        value = fixture()
        with patch("copilot.market_data.collect_snapshot", return_value=value), patch.object(service, "load_credentials"):
            collected = service.collect(["QQQ"], db_path=self.db, research=False)
        self.assertEqual(service.snapshot(collected["snapshot_id"], db_path=self.db), value)
        reviewed = service.review(value["snapshot_id"], proposal(), db_path=self.db, now=NOW)
        self.assertEqual(reviewed["decision"]["execution_scope"], "research_only")
        self.assertNotIn("quantity", reviewed["decision"])
        self.assertEqual(service.context(db_path=self.db)["holdings"], [])

    def test_unapproved_action_never_leaks_into_rendered_action(self):
        value = fixture()
        value["instruments"]["QQQ"]["quality_status"] = "unknown"
        seal(value)
        journal.save_snapshot(value, db_path=self.db)
        reviewed = service.review(value["snapshot_id"], proposal(), db_path=self.db, now=NOW)
        self.assertEqual(reviewed["decision"]["action"], "data_insufficient")
        self.assertIn("暂停具体建议", reviewed["message"])
        self.assertNotIn("可考虑买入", reviewed["message"])

    def test_unknown_snapshot_and_forged_ids_rejected(self):
        with self.assertRaises(KeyError):
            service.review("invented", proposal(), db_path=self.db)
        value = fixture()
        journal.save_snapshot(value, db_path=self.db)
        p = proposal()
        p["evidence_ids"] = ["invented"]
        self.assertEqual(service.review(value["snapshot_id"], p, db_path=self.db, now=NOW)["decision"]["action"], "data_insufficient")

    def test_summary_preserves_storage_and_identity(self):
        value = fixture()
        value["evidence"][0]["bars"] = [{"close": i} for i in range(260)]
        original = copy.deepcopy(value)
        view = service.snapshot_view(value)
        self.assertEqual(value, original)
        self.assertEqual(view["snapshot_id"], value["snapshot_id"])
        self.assertEqual(view["evidence"][0]["bar_count"], 260)
        self.assertEqual(len(view["evidence"][0]["recent_bars"]), 5)
        self.assertEqual(service.snapshot_view(value, full=True), original)

    def test_cli_restart_and_pending_receipt(self):
        args = [sys.executable, str(service.ROOT / "scripts/copilot_cli.py"), "--db", str(self.db)]
        op = {"statement": "I already bought QQQ, quantity unknown.",
              "execution_status": "executed", "instrument_id": "QQQ", "side": "buy", "source_message_id": "fixture-pending"}
        saved = subprocess.run(args + ["record", "--idempotency-key", "fixture-only"], input=json.dumps(op),
                               text=True, capture_output=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(saved.stdout)["status"], "pending")
        read = subprocess.run(args + ["context"], text=True, capture_output=True, encoding="utf-8", check=True)
        self.assertEqual(json.loads(read.stdout)["holdings"], [])
        self.assertEqual(len(json.loads(read.stdout)["pending_operations"]), 1)

    def test_concurrent_holdings_change_prevents_recommendation_commit(self):
        value = fixture()
        journal.save_snapshot(value, db_path=self.db)
        original = journal.record_recommendation
        def changed(decision, **kwargs):
            journal.record_operation({"statement": "I already bought QQQ.", "instrument_id": "QQQ",
                "execution_status": "executed", "quantity": "1", "price": "100", "currency": "USD",
                "unit": "share", "side": "buy", "occurred_at": "2026-09-04", "source_message_id": "fixture-racer"}, "racer", db_path=self.db)
            return original(decision, **kwargs)
        with patch("copilot.journal.record_recommendation", side_effect=changed):
            with self.assertRaises(journal.JournalConflict):
                service.review(value["snapshot_id"], proposal(), db_path=self.db, now=NOW)
        self.assertEqual(service.context(db_path=self.db)["recommendations"], [])

    def test_gold_and_index_views_keep_instrument_scope(self):
        for symbol, marker in [("^NDX", "指数研究观点"), ("GOLD.CNY", "黄金基准研究")]:
            value = fixture(symbol)
            journal.save_snapshot(value, db_path=self.db)
            reviewed = service.review(value["snapshot_id"], proposal(symbol), db_path=self.db, now=NOW)
            self.assertIn(marker, reviewed["message"])

    def test_gold_missing_research_not_hidden_by_other_warnings(self):
        value = fixture("GOLD.CNY")
        value["research_issues"] = [{"provider": "fred", "status": "not_configured"}]
        seal(value)
        journal.save_snapshot(value, db_path=self.db)
        reviewed = service.review(value["snapshot_id"], proposal("GOLD.CNY"), db_path=self.db, now=NOW)
        self.assertIn("宏观依据不可用", reviewed["message"])

class EngineFacade(unittest.TestCase):
    """service.declare_coverage/adopt/evaluate over a real journal database.

    The snapshot and adoption reuse _test_policy.engine_fixture/bands_adoption
    (QQQ+SPY, 50/50 fixed-weight bands) rather than inventing a third fixture
    shape -- this is exactly the pair Task 9's EngineProducedOrders already
    proved evaluate_rule can turn into actionable orders.
    """

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "copilot.sqlite"
        self.now = NOW
        self.snapshot = engine_fixture()
        self.news_evidence_id = "news_1"
        self.snapshot["evidence"].append({
            "evidence_id": self.news_evidence_id, "provider": "finnhub", "status": "ok",
            "source_url": "https://example.test/news",
            "critical_evidence_eligible": False,
            "observed_at": "2025-01-02T20:00:00+00:00",
            "retrieved_at": "2026-09-06T00:59:00+00:00",
            "data": {"headline": "issuer halt"}})
        seal(self.snapshot)
        self.snapshot_id = self.snapshot["snapshot_id"]
        journal.save_snapshot(self.snapshot, db_path=self.db)
        version = service.context(db_path=self.db)["portfolio_version"]
        journal.record_coverage_declaration(sleeve="etf", base_currency="USD",
                                            portfolio_version=version, db_path=self.db)
        self.store_adoption()
        self.db_without_coverage = Path(self.temp.name) / "no-coverage.sqlite"
        journal.save_snapshot(self.snapshot, db_path=self.db_without_coverage)
        journal.record_adoption(bands_adoption(rule_id=self.rule_id),
                                db_path=self.db_without_coverage)

    def store_adoption(self, *, sleeve="etf", db_path=None):
        """Write an adoption at a rule_id derived from sleeve, and remember it.

        record_adoption's primary key is rule_id, so an "etf" and a "gold"
        adoption need distinct ids to coexist in one database without
        conflicting as "the same id, different payload". self.rule_id is
        updated as a side effect so a caller can immediately do
        `self.config(pointer=self.rule_id)` against whichever one it just
        stored -- this is what lets test_evaluate_refuses_a_non_etf_adoption
        point the config at the gold adoption it just wrote.
        """
        rule_id = "rule-" + ("0" * 16 if sleeve == "etf" else "1" * 16)
        record = bands_adoption(sleeve=sleeve, rule_id=rule_id)
        journal.record_adoption(record, db_path=db_path or self.db)
        self.rule_id = rule_id
        return record

    def config(self, *, pointer):
        text = (
            "schema_version = 1\n\n"
            "[etf]\n"
            f'adopted_rule_id = "{pointer}"\n'
            "investable_cash_usd = 20000.0\n"
            "min_cash_reserve_pct = 0.6\n"
            "max_drawdown_pct = 0.2\n\n"
            "[gold]\n"
            "investable_total_cny = 0\n"
            "min_order_cny = 1200\n"
            "order_increment_cny = 200\n"
            "max_orders_per_day = 10\n\n"
            "[notify]\n"
            'email_to = ""\n'
            'timezone = "UTC"\n'
            'scan_time_local = "07:00"\n'
            'language = "zh"\n')
        path = Path(self.temp.name) / f"config-{abs(hash(pointer))}.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def adoption_inputs(self):
        """A real, admitted engine.Result -- never a JSON payload (see service.adopt)."""
        frame, rule, result = admitted_result()
        return dict(sleeve="etf", family=rule.name, parameters=dict(rule.parameters),
                   universe=tuple(frame.symbols), targets=None, result=result,
                   cost_model={"per_share_usd": 0.0035, "minimum_usd": 1.0,
                               "max_pct_of_notional": 0.01, "spread_bps": 2.0},
                   cash_floor_pct=0.15, integer_shares=True)

    def evaluate_with_a_failing_row(self):
        """Race a concurrent trade into the write loop; return the would-be evaluation_id.

        record_recommendation commits one row per call, with no primitive that
        spans several rows in one transaction. The lever here makes the very
        FIRST write see a stale portfolio_version (a trade recorded between
        service.evaluate's own context() read and that first write), so the
        loop raises before attempting any row -- "nothing persisted" holds
        because nothing was ever written, not because of any new atomicity
        this change adds.

        That is a real, reportable limit, not a hidden gap: if the SAME race
        instead landed on the second or later order, the earlier row(s)
        would already be committed by the time the failure surfaced, because
        each record_recommendation call opens and commits its own
        transaction independently. A genuinely atomic multi-row write would
        need a new primitive (one transaction spanning the whole basket, or
        a batch-insert function) that this task does not add. This helper
        demonstrates the achievable half of the guarantee -- fail before the
        first write -- not a general fix for a failure on row N>1.
        """
        from copilot.config import load_config
        from copilot.policy import evaluate_rule
        config_path = self.config(pointer=self.rule_id)
        settings = load_config(config_path)
        adoption_record = journal.load_adoption(self.rule_id, db_path=self.db)
        stored_snapshot = service.snapshot(self.snapshot_id, db_path=self.db)
        current = service.context(db_path=self.db)
        expected = evaluate_rule(adoption=adoption_record, snapshot=stored_snapshot,
                                 context=current,
                                 investable_cash=settings.etf.investable_cash_usd, now=self.now)
        original = journal.record_recommendation

        def racer(decision, **kwargs):
            journal.record_operation(
                {"statement": "I already bought QQQ.", "instrument_id": "QQQ",
                 "execution_status": "executed", "quantity": "1", "price": "100",
                 "currency": "USD", "unit": "share", "side": "buy",
                 "occurred_at": "2025-01-06", "source_message_id": "facade-racer"},
                "facade-racer", db_path=self.db)
            return original(decision, **kwargs)

        with patch("copilot.journal.record_recommendation", side_effect=racer):
            with self.assertRaises(journal.JournalConflict):
                service.evaluate(snapshot_id=self.snapshot_id, config_path=config_path,
                                 db_path=self.db, now=self.now)
        return expected["evaluation_id"]

    def test_evaluate_refuses_when_nothing_is_adopted(self):
        with self.assertRaisesRegex(ValueError, "adopted_rule_id"):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer=""), db_path=self.db)

    def test_evaluate_refuses_a_pointer_that_does_not_resolve(self):
        with self.assertRaises(KeyError):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer="rule-ffffffffffffffff"),
                             db_path=self.db)

    def test_evaluate_refuses_a_non_etf_adoption(self):
        # etf.adopted_rule_id is the only pointer that exists, so a gold
        # adoption could otherwise be pointed at and executed.
        self.store_adoption(sleeve="gold")
        with self.assertRaisesRegex(ValueError, "sleeve"):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer=self.rule_id), db_path=self.db)

    def test_every_order_is_persisted_under_one_evaluation_id(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db, now=self.now)
        stored = service.context(db_path=self.db)["recommendations"]
        matching = [r for r in stored if r.get("evaluation_id") == result["evaluation_id"]]
        self.assertEqual(len(matching), len(result["orders"]))
        for order in result["orders"]:
            self.assertEqual(order["evaluation_id"], result["evaluation_id"])

    def test_nothing_is_persisted_when_one_order_cannot_be_stored(self):
        # record_recommendation opens its own transaction per row, so a mid-loop
        # failure would leave half a basket in a table with immutability
        # triggers, permanently, with nothing marking the rest as missing.
        result = self.evaluate_with_a_failing_row()
        stored = service.context(db_path=self.db)["recommendations"]
        self.assertEqual([r for r in stored if r.get("evaluation_id") == result], [])

    def test_the_rendered_message_marks_an_applied_brake_unbacktested(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db, now=self.now,
                                  brake={"level": "reduce_50", "reason": "halt",
                                         "evidence_ids": [self.news_evidence_id]})
        self.assertIn("未回测", result["message"])

    def test_no_brake_adds_no_disclosure(self):
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db, now=self.now)
        self.assertNotIn("未回测", result["message"])

    def test_a_research_only_result_says_why_rather_than_printing_an_empty_list(self):
        # The first draft printed the heading "按已采纳规则计算的委托：" and then
        # skipped every line, leaving a promise with nothing under it.
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer=self.rule_id),
                                  db_path=self.db_without_coverage, now=self.now)
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertNotIn("按已采纳规则计算的委托", result["message"])
        self.assertIn("覆盖", result["message"])

    def test_brake_evidence_must_exist_in_the_snapshot(self):
        # The only evidence field with no validation behind it, and its input is
        # a headline the model read.
        with self.assertRaisesRegex(ValueError, "brake"):
            service.evaluate(snapshot_id=self.snapshot_id,
                             config_path=self.config(pointer=self.rule_id),
                             db_path=self.db, now=self.now,
                             brake={"level": "skip", "reason": "halt",
                                    "evidence_ids": ["ev_does_not_exist"]})

    def test_declare_coverage_reports_what_invalidates_it(self):
        receipt = service.declare_coverage(db_path=self.db)
        self.assertIn("失效", receipt["message"])
        self.assertTrue(receipt["history"][0]["current"])

    def test_adopt_records_and_points_at_the_next_step(self):
        receipt = service.adopt(self.adoption_inputs(), db_path=self.db)
        self.assertRegex(receipt["rule_id"], r"^rule-[0-9a-f]{16}$")
        self.assertIn("config/user.toml", receipt["next_step"])
        self.assertIn(receipt["rule_id"], receipt["next_step"])

    def test_an_absent_universe_symbol_still_gets_a_real_portfolio_version(self):
        # _absent_decision's portfolio_version comes from context(), and
        # service.evaluate always builds that context via service.context(),
        # which never returns an empty portfolio_version (even an empty book
        # hashes to a real digest) -- so the absent row is never rejected by
        # record_recommendation's non-empty-string check. This universe
        # deliberately names IWM, which is not in the snapshot, to prove the
        # absent-symbol row round-trips through persistence like any other.
        record = bands_adoption(rule_id="rule-2222222222222222",
                                universe=["QQQ", "SPY", "IWM"],
                                targets={"QQQ": 1 / 3, "SPY": 1 / 3, "IWM": 1 / 3})
        journal.record_adoption(record, db_path=self.db)
        result = service.evaluate(snapshot_id=self.snapshot_id,
                                  config_path=self.config(pointer="rule-2222222222222222"),
                                  db_path=self.db, now=self.now)
        self.assertEqual(result["execution_scope"], "research_only")
        self.assertIn("IWM", result["blocked_symbols"])
        absent = next(o for o in result["orders"] if o["instrument_id"] == "IWM")
        self.assertTrue(absent["portfolio_version"])
        stored = service.context(db_path=self.db)["recommendations"]
        matching = [r for r in stored if r.get("evaluation_id") == result["evaluation_id"]]
        self.assertEqual(len(matching), len(result["orders"]))


def rendered(**result):
    """render_evaluation over a minimal result. `stored` is unread by it."""
    base = {"execution_scope": "actionable", "rebalance_due": True, "coverage_known": True,
            "blocked_symbols": [], "orders": [], "brake": {"level": "none"}}
    base.update(result)
    return service.render_evaluation(base, {})


def order(symbol, **fields):
    base = {"instrument_id": symbol, "action": "buy", "execution_scope": "actionable",
            "limit_price": 100.0, "brake": {"level": "none"}}
    base.update(fields)
    return base


def braked(symbol, pre, post, level="skip"):
    item = order(symbol, brake={"level": level, "pre_brake_quantity": pre,
                                "post_brake_quantity": post, "applied_to_side": "buy",
                                "changed": pre != post,
                                "disclosure": "新闻刹车未回测：它只能减少或跳过，永远不能加仓"})
    if post:
        item["quantity"] = post
    else:
        item["action"] = "hold"
        item["execution_scope"] = "research_only"
    return item


class TheRenderedMessageAccountsForEveryOrder(unittest.TestCase):
    """What the user reads must not be missing an order the engine produced.

    render_evaluation skipped any order without a `quantity`, which is exactly
    what a brake that zeroes a buy produces: the model states a news reason, the
    engine drops the size to nothing, and the line disappeared. The user saw a
    shorter basket with no indication that a name had been suppressed, and if
    the brake zeroed everything the message read "风险检查未全部通过" -- naming
    the wrong cause entirely, since every risk check had passed.
    """

    def test_a_normal_basket_lists_each_priced_order(self):
        message = rendered(orders=[order("SPY", quantity=8), order("QQQ", quantity=4)])
        self.assertIn("SPY 买入 8 股，限价 100.0", message)
        self.assertIn("QQQ 买入 4 股，限价 100.0", message)

    def test_a_brake_zeroed_buy_is_still_reported(self):
        message = rendered(orders=[order("SPY", quantity=8), braked("QQQ", 5, 0)],
                           brake={"level": "skip", "disclosure": "刹车说明"})
        self.assertIn("QQQ", message)
        self.assertIn("5", message, "the suppressed size must be visible")

    def test_a_halved_buy_reports_the_size_that_survived(self):
        message = rendered(orders=[braked("SPY", 9, 4, level="reduce_50")],
                           brake={"level": "reduce_50", "disclosure": "刹车说明"})
        self.assertIn("SPY 买入 4 股", message)

    def test_a_basket_the_brake_emptied_does_not_blame_the_risk_checks(self):
        message = rendered(execution_scope="research_only",
                           orders=[braked("SPY", 8, 0), braked("QQQ", 4, 0)],
                           brake={"level": "skip", "disclosure": "刹车说明"})
        self.assertNotIn("风险检查", message)
        self.assertIn("SPY", message)
        self.assertIn("QQQ", message)

    def test_a_real_risk_failure_names_the_symbol_that_failed(self):
        message = rendered(execution_scope="research_only",
                           orders=[order("SPY", quantity=8),
                                   order("QQQ", action="hold", execution_scope="research_only")])
        self.assertIn("QQQ", message)
        self.assertNotIn("SPY 买入", message)

    def test_a_symbol_the_rule_did_not_select_is_not_called_a_risk_failure(self):
        # Post-fix, an unselected-and-unheld symbol carries no_action_required
        # and the basket stays actionable, so it must not be listed as a cause.
        message = rendered(orders=[order("SPY", quantity=8),
                                   order("VTV", action="hold", execution_scope="research_only",
                                         no_action_required=True)])
        self.assertIn("SPY 买入 8 股", message)
        self.assertNotIn("VTV", message)

    def test_an_idle_symbol_is_not_named_alongside_a_real_risk_failure(self):
        # The research-only branch is where the cause is written, so the
        # no_action_required filter has to hold there too: an idle symbol
        # listed as a cause sends the user looking for a limit that never
        # failed. QQQ is the genuine failure here; VTV simply was not selected.
        message = rendered(execution_scope="research_only",
                           orders=[order("QQQ", action="hold", execution_scope="research_only"),
                                   order("VTV", action="hold", execution_scope="research_only",
                                         no_action_required=True)])
        self.assertIn("风险检查未通过：QQQ", message)
        self.assertNotIn("VTV", message)

    def test_no_heading_is_printed_with_nothing_under_it(self):
        # A `skip` brake leaves every order without a quantity while the basket
        # itself is still actionable, so the heading "按已采纳规则计算的委托："
        # was printed over an empty list.
        message = rendered(orders=[braked("SPY", 8, 0), braked("QQQ", 4, 0)],
                           brake={"level": "skip", "disclosure": "刹车说明"})
        self.assertNotIn("按已采纳规则计算的委托", message)
        self.assertIn("SPY", message)

    def test_a_limit_price_is_printed_as_a_number_a_broker_accepts(self):
        # 153.92000000000002 is float error from the close, not a price; the
        # user copies this line into an order ticket.
        message = rendered(orders=[order("VTI", quantity=64, limit_price=153.92000000000002)])
        self.assertIn("限价 153.92", message)
        self.assertNotIn("153.92000000000002", message)

    def test_the_existing_causes_still_win_over_the_new_ones(self):
        self.assertIn("数据未通过校验：VWO", rendered(
            execution_scope="research_only", blocked_symbols=["VWO"]))
        self.assertIn("持仓覆盖未声明", rendered(
            execution_scope="research_only", coverage_known=False))
        self.assertIn("规则未触发再平衡", rendered(
            execution_scope="research_only", rebalance_due=False))


if __name__ == "__main__":
    unittest.main()

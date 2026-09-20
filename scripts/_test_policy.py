"""Offline behavioral policy gates; fixtures are synthetic and never persisted."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from datetime import datetime, timezone

from copilot.instruments import get_instrument
from copilot.policy import assess_proposal, proposal_fingerprint

NOW = datetime(2026, 9, 6, 2, 0, tzinfo=timezone.utc)


def seal(snapshot):
    value = {k: v for k, v in snapshot.items() if k != "snapshot_id"}
    snapshot["snapshot_id"] = "snap_" + hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return snapshot


def fixture(symbol="QQQ"):
    return seal({"schema_version": 1, "created_at": "2026-09-06T01:00:00+00:00",
        "decision_at": "2026-09-06T01:00:00+00:00", "valid_until": "2026-09-06T12:00:00+00:00",
        "status": "ready", "instruments": {symbol: {**get_instrument(symbol),
        "quality_status": "pass", "latest_session": "2026-09-04", "expected_session": "2026-09-04",
        "price": 100.0, "indicators": {"sma200": 95.0, "sample_count": 260}, "evidence_ids": ["market"], "issues": [], "sources": ["synthetic"]}},
        "evidence": [{"evidence_id": "market", "provider": "synthetic", "source_url": "https://example.test/prices",
        "observed_at": "2026-09-04T20:00:00+00:00", "retrieved_at": "2026-09-06T00:59:00+00:00", "status": "ok"}], "issues": []})


def proposal(symbol="QQQ", action="buy"):
    return {"instrument_id": symbol, "action": action, "mode": "accumulation", "horizon": "long_term",
        "reasons": ["synthetic fixture thesis"], "conditions": ["recheck new disclosures"], "evidence_ids": ["market"]}


def complete_context(snapshot, p=None, **risk_inputs):
    inputs = {"post_trade_weight": .02, "post_trade_sector_weight": .2,
              "max_correlation": .5, "position_adv_fraction": .001, "drawdown": .01}
    inputs.update(risk_inputs)
    return {"portfolio_version": "v1", "portfolio_complete": True, "base_currency": "USD",
        "snapshot_id": snapshot["snapshot_id"], "risk_proposal_fingerprint": proposal_fingerprint(p or proposal()),
        "verified_risk_inputs": inputs}


class PolicyTests(unittest.TestCase):
    def test_registered_etf_requires_verified_market_evidence(self):
        # Two independent gates. Verification state and freshness have always
        # been the policy's own; the identity fields are bound here as well as
        # at collection time (market_data._validate_source), so a snapshot that
        # was not produced by the collector cannot cite evidence describing a
        # different instrument. Provider/upstream are deliberately not checked:
        # price sources are added over time and must not need a policy edit.
        for provider, upstream in (("yahoo", "Yahoo Finance"), ("nasdaq", "Nasdaq US market data")):
            snapshot = fixture("JEPI")
            snapshot["instruments"]["JEPI"].update(identity_status="provider_confirmed")
            snapshot["evidence"][0].update(provider=provider, upstream=upstream, instrument_id="JEPI",
                asset_class="etf", currency="USD", unit="share", price_kind="regular_session_close")
            seal(snapshot)
            self.assertEqual(assess_proposal(proposal("JEPI"), snapshot, now=NOW)["action"], "buy")
            for change in ({"status": "unknown"}, {"source_url": ""},
                           {"critical_evidence_eligible": False},
                           {"retrieved_at": "2026-09-06T03:00:00+00:00"},
                           {"observed_at": "2026-09-03T20:00:00+00:00"},
                           {"latest_session": "2026-09-03"},
                           {"instrument_id": "QQQ"}, {"asset_class": "stock"},
                           {"currency": "CNY"}, {"unit": "gram"},
                           {"price_kind": "indicative"}):
                bad = copy.deepcopy(snapshot)
                bad["evidence"][0].update(change)
                seal(bad)
                self.assertEqual(assess_proposal(proposal("JEPI"), bad, now=NOW)["action"], "data_insufficient", change)

    def test_registered_etf_cannot_be_reclassified_as_stock(self):
        snapshot = fixture()
        snapshot["instruments"]["QQQ"].update(asset_class="stock", identity_status="provider_confirmed")
        seal(snapshot)
        self.assertEqual(assess_proposal(proposal(), snapshot, now=NOW)["action"], "data_insufficient")

    def test_optional_research_is_not_a_required_daily_bar(self):
        snapshot = fixture()
        snapshot["evidence"].extend([
            {"evidence_id": "sec", "provider": "sec", "source_url": "https://www.sec.gov/Archives/example", "status": "ok", "critical_evidence_eligible": True,
             "observed_at": "2026-08-01T12:00:00+00:00", "retrieved_at": "2026-09-06T00:59:00+00:00", "data": {"revenue": 1000}},
            {"evidence_id": "fred", "provider": "fred", "source_url": "https://fred.stlouisfed.org/series/CPIAUCSL", "status": "unknown", "critical_evidence_eligible": False,
             "observed_at": None, "retrieved_at": "2026-09-06T00:59:00+00:00", "data": {"value": 300}},
        ])
        snapshot["instruments"]["QQQ"]["evidence_ids"] += ["sec", "fred"]
        snapshot["instruments"]["QQQ"]["research"] = {"filings": {"evidence_ids": ["sec"]}, "CPIAUCSL": {"evidence_ids": ["fred"]}}
        seal(snapshot)
        p = proposal()
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "buy")
        p["evidence_ids"].append("sec")
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "buy")
        p["evidence_ids"].append("fred")
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "data_insufficient")

    def test_unverified_critical_evidence_even_with_ok_status(self):
        snapshot = fixture()
        snapshot["evidence"][0]["critical_evidence_eligible"] = False
        seal(snapshot)
        self.assertEqual(assess_proposal(proposal(), snapshot, now=NOW)["action"], "data_insufficient")

    def test_numeric_claims_match_exact_stored_fields(self):
        snapshot = fixture()
        p = proposal()
        p["reasons"] = ["最新完整收盘价为100，SMA200为95"]
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "data_insufficient")
        p["claims"] = [
            {"evidence_id": "market", "path": "/instruments/QQQ/price", "value": 100.0},
            {"evidence_id": "market", "path": "/instruments/QQQ/indicators/sma200", "value": 95.0},
        ]
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "buy")
        p["claims"][0]["value"] = 101.0
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "data_insufficient")

    def test_numeric_labels_dates_and_rounding(self):
        snapshot = fixture()
        snapshot["instruments"]["QQQ"]["price"] = 100.1234
        seal(snapshot)
        p = proposal()
        p["reasons"] = ["截至2026-09-04，RSI14与SMA200仍需结合解读，收盘价100.12"]
        p["claims"] = [{"evidence_id": "market", "path": "/instruments/QQQ/price", "value": 100.1234}]
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "buy")
        p["conditions"] = ["跌至80时复核"]
        d = assess_proposal(p, snapshot, now=NOW)
        self.assertEqual(d["action"], "data_insufficient")
        self.assertEqual(d["conditions"], [])

    def test_claim_paths_cannot_borrow_other_instruments(self):
        p = proposal()
        p["claims"] = [{"evidence_id": "market", "path": "/instruments/AAPL/price", "value": 100}]
        self.assertEqual(assess_proposal(p, fixture(), now=NOW)["action"], "data_insufficient")

    def test_numeric_claim_cannot_change_units_or_scale(self):
        p = proposal()
        p["claims"] = [{"evidence_id": "market", "path": "/instruments/QQQ/price", "value": 100.0}]
        for line in ("价格100亿美元", "涨幅100%", "预计上涨100倍", "价格100 billion", "价格100e9", "上涨.5%"):
            p["reasons"] = [line]
            self.assertEqual(assess_proposal(p, fixture(), now=NOW)["action"], "data_insufficient", line)

    def test_computed_return_percentage_has_explicit_known_basis(self):
        snapshot = fixture()
        snapshot["instruments"]["QQQ"]["indicators"]["return_20_sessions"] = .0234
        seal(snapshot)
        p = proposal()
        p["reasons"] = ["区间收益为2.34%"]
        p["claims"] = [{"evidence_id": "market", "path": "/instruments/QQQ/indicators/return_20_sessions", "value": .0234}]
        self.assertEqual(assess_proposal(p, snapshot, now=NOW)["action"], "buy")
    def test_weekend_last_complete_session_valid(self):
        # quantity/target_weight are engine outputs, not a model claim; this
        # test is about session validity and about them being withheld while
        # not executable, so it exercises the engine path.
        p = proposal()
        p.update(quantity=10, target_weight=.02)
        d = assess_proposal(p, fixture(), now=NOW, source="engine")
        self.assertEqual(d["action"], "buy")
        self.assertEqual(d["execution_scope"], "research_only")
        self.assertNotIn("quantity", d)
        self.assertNotIn("target_weight", d)
        self.assertEqual(d["risk_checks"]["single_name"]["status"], "unknown")

    def test_freshness_and_quality_fail_closed_no_automatic_sell(self):
        for change in ({"quality_status": "unknown"}, {"quality_status": "fail"}, {"latest_session": "2025-01-01"}, {"price": None}, {"currency": "CNY"}):
            snapshot = fixture()
            snapshot["instruments"]["QQQ"].update(change)
            seal(snapshot)
            for action in ("buy", "sell", "hold"):
                self.assertEqual(assess_proposal(proposal(action=action), snapshot, now=NOW)["action"], "data_insufficient")

    def test_expired_and_tampered_snapshots(self):
        snapshot = fixture()
        snapshot["valid_until"] = "2026-09-06T01:00:00+00:00"
        seal(snapshot)
        self.assertEqual(assess_proposal(proposal(), snapshot, now=NOW)["action"], "data_insufficient")
        snapshot = fixture()
        snapshot["instruments"]["QQQ"]["price"] = 123.0
        self.assertIn("integrity", " ".join(assess_proposal(proposal(), snapshot, now=NOW)["reasons"]))

    def test_unknown_or_future_evidence(self):
        p = proposal()
        p["evidence_ids"] = ["invented"]
        self.assertEqual(assess_proposal(p, fixture(), now=NOW)["action"], "data_insufficient")
        snapshot = fixture()
        snapshot["evidence"][0]["observed_at"] = "2026-09-07T20:00:00+00:00"
        seal(snapshot)
        self.assertEqual(assess_proposal(proposal(), snapshot, now=NOW)["action"], "data_insufficient")

    def test_proposal_pass_flags_have_no_authority(self):
        # The claim under test is that self-declared risk_checks/portfolio_complete
        # on the proposal carry no authority; quantity is engine-shaped, so use
        # the engine path to keep the quantity-is-withheld assertion meaningful.
        p = proposal()
        p.update(risk_checks={"all": "pass"}, portfolio_complete=True, quantity=10)
        d = assess_proposal(p, fixture(), now=NOW, source="engine")
        self.assertEqual(d["risk_checks"]["single_name"]["status"], "unknown")
        self.assertNotIn("quantity", d)

    def test_measured_risk_fail_downgrades_purchase(self):
        # QQQ is an ETF: the single-name limit is 25%, so this must sit above
        # that to still exercise the downgrade path (was 0.2 under the old
        # flat 5% limit).
        snapshot = fixture()
        context = complete_context(snapshot)
        context["verified_risk_inputs"]["post_trade_weight"] = .30
        d = assess_proposal(proposal(), snapshot, context, now=NOW)
        self.assertEqual(d["action"], "hold")
        self.assertEqual(d["risk_checks"]["single_name"]["status"], "fail")

    def test_trusted_complete_context_and_stop(self):
        # quantity/target_weight/stop_loss are engine outputs under ADR-0004;
        # this test exercises the full engine-produced-order path through to
        # an actionable decision, so it uses source="engine" throughout.
        snapshot = fixture()
        p = proposal()
        p.update(quantity=10, target_weight=.02, mode="tactical")
        d = assess_proposal(p, snapshot, complete_context(snapshot, p), now=NOW, source="engine")
        self.assertEqual(d["execution_scope"], "research_only")
        p["stop_loss"] = 95
        d = assess_proposal(p, snapshot, complete_context(snapshot, p), now=NOW, source="engine")
        self.assertEqual(d["execution_scope"], "actionable")
        self.assertEqual(d["quantity"], 10)

    def test_index_price_never_an_entry(self):
        # quantity here stands in for an engine-supplied size; the point of
        # the test is that an index instrument never becomes an entry
        # regardless of who supplies the numbers, so use the engine path.
        snapshot = fixture("^NDX")
        p = proposal("^NDX")
        p.update(price=100.0, quantity=10)
        d = assess_proposal(p, snapshot, complete_context(snapshot), now=NOW, source="engine")
        self.assertEqual(d["execution_scope"], "research_only")
        self.assertNotIn("price", d)
        self.assertNotIn("quantity", d)

    def test_gold_without_real_merchant_evidence_has_no_entry(self):
        # quantity here stands in for an engine-supplied size; the point of
        # the test is that gold never gets a concrete entry without a real
        # merchant quote, regardless of who supplies the numbers.
        snapshot = fixture("GOLD.CNY")
        p = proposal("GOLD.CNY")
        p.update(price=100.0, quantity=10, retail_quote={"verified": True, "price": 100})
        d = assess_proposal(p, snapshot, complete_context(snapshot), now=NOW, source="engine")
        self.assertEqual(d["action"], "buy")
        self.assertNotIn("price", d)
        self.assertEqual(d["risk_checks"]["retail_quote"]["status"], "unknown")

    def test_portfolio_update_and_mode_change_change_identity(self):
        snapshot = fixture()
        p = proposal()
        a = assess_proposal(p, snapshot, {"portfolio_version": "v1"}, now=NOW)
        b = assess_proposal(p, snapshot, {"portfolio_version": "v2"}, now=NOW)
        self.assertNotEqual(a["decision_id"], b["decision_id"])
        p["mode"] = "tactical"
        c = assess_proposal(p, snapshot, {"portfolio_version": "v2"}, now=NOW)
        self.assertNotEqual(c["decision_id"], b["decision_id"])
        p["portfolio_version"] = "v1"
        self.assertEqual(assess_proposal(p, snapshot, {"portfolio_version": "v2"}, now=NOW)["action"], "data_insufficient")

    def test_malformed_schema_and_nonfinite_proposal(self):
        for change in ({"quantity": float("nan")}, {"action": "Strong Buy"}, {"horizon": "intraday"}, {"evidence_ids": "market"}):
            p = proposal()
            p.update(change)
            with self.assertRaises(ValueError):
                assess_proposal(p, fixture(), now=NOW)

    def test_short_sample_and_required_indicator_unavailable(self):
        snapshot = fixture()
        snapshot["instruments"]["QQQ"]["indicators"]["sample_count"] = 30
        seal(snapshot)
        self.assertEqual(assess_proposal(proposal(), snapshot, now=NOW)["action"], "data_insufficient")
        p = proposal()
        p["required_indicators"] = ["atr14"]
        self.assertEqual(assess_proposal(p, fixture(), now=NOW)["action"], "data_insufficient")

    def test_determinism_and_inputs_not_mutated(self):
        snapshot, p = fixture(), proposal()
        before = copy.deepcopy((snapshot, p))
        self.assertEqual(assess_proposal(p, snapshot, now=NOW), assess_proposal(p, snapshot, now=NOW))
        self.assertEqual((snapshot, p), before)


class SleeveLimits(unittest.TestCase):
    def test_etf_single_name_limit_is_twenty_five_percent(self):
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("single_name", "etf"), 0.25)

    def test_stock_single_name_limit_is_unchanged(self):
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("single_name", "stock"), 0.05)

    def test_only_single_name_differs_by_sleeve(self):
        from copilot.policy import _LIMITS, limit_for
        for name in _LIMITS:
            if name == "single_name":
                continue
            self.assertAlmostEqual(limit_for(name, "etf"), _LIMITS[name][1], msg=name)
            self.assertAlmostEqual(limit_for(name, "stock"), _LIMITS[name][1], msg=name)

    def test_an_etf_at_twenty_percent_passes(self):
        # MomentumTopN with top_n=5 equal-weighted is 20% a name. Under the old
        # flat 5% ceiling every ETF sleeve configuration failed.
        snapshot = fixture()
        p = proposal(action="buy")
        context = complete_context(snapshot, p)
        context["verified_risk_inputs"]["post_trade_weight"] = 0.20
        decision = assess_proposal(p, snapshot, context, now=NOW)
        self.assertEqual(decision["risk_checks"]["single_name"]["status"], "pass")
        self.assertAlmostEqual(decision["risk_checks"]["single_name"]["limit"], 0.25)

    def test_an_etf_above_twenty_five_percent_fails(self):
        snapshot = fixture()
        p = proposal(action="buy")
        context = complete_context(snapshot, p)
        context["verified_risk_inputs"]["post_trade_weight"] = 0.2500001
        decision = assess_proposal(p, snapshot, context, now=NOW)
        self.assertEqual(decision["risk_checks"]["single_name"]["status"], "fail")
        self.assertEqual(decision["action"], "hold")

    def test_the_five_percent_tier_is_still_reachable(self):
        # The only test that can kill a mutant which hardcodes 0.25 and never
        # reads the sleeve. GOLD.CNY is physical_gold, not etf.
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("single_name", "physical_gold"), 0.05)
        self.assertAlmostEqual(limit_for("single_name", "index"), 0.05)

    def test_correlation_is_not_applicable_for_an_etf(self):
        # Daily-return correlation among broad equity ETFs is structurally
        # 0.85-0.95, so no threshold is informative: 0.7 rejects every basket
        # and anything loose enough to admit one rejects nothing. ADR-0007
        # clause 7 records the decision and its consequence.
        snapshot = fixture()
        p = proposal(action="buy")
        context = complete_context(snapshot, p)
        context["verified_risk_inputs"]["max_correlation"] = 0.95
        decision = assess_proposal(p, snapshot, context, now=NOW)
        self.assertEqual(decision["risk_checks"]["correlation"]["status"], "not_applicable")
        self.assertIn("look-through", decision["risk_checks"]["correlation"]["detail"])

    def test_correlation_still_applies_outside_the_etf_sleeve(self):
        from copilot.policy import limit_for
        self.assertAlmostEqual(limit_for("correlation", "physical_gold"), 0.7)


class SectorMap(unittest.TestCase):
    def test_every_sector_etf_maps_to_one_sector(self):
        from copilot.instruments import sector_of
        for symbol, sector in (("XLK", "technology"), ("XLF", "financials"),
                               ("XLE", "energy"), ("XLV", "health_care"),
                               ("XLY", "consumer_discretionary"), ("XLP", "consumer_staples"),
                               ("XLI", "industrials"), ("XLB", "materials"),
                               ("XLU", "utilities"), ("XLRE", "real_estate"),
                               ("XLC", "communication_services")):
            self.assertEqual(sector_of(symbol), sector, symbol)

    def test_semiconductor_funds_are_a_sector(self):
        from copilot.instruments import sector_of
        self.assertEqual(sector_of("SMH"), "technology")
        self.assertEqual(sector_of("SOXX"), "technology")

    def test_broad_funds_are_diversified_not_a_sector(self):
        from copilot.instruments import sector_of
        for symbol in ("SPY", "VOO", "VTI", "IVV", "QQQ", "IWM", "VEA", "VWO", "IOO", "DIA"):
            self.assertEqual(sector_of(symbol), "diversified", symbol)

    def test_every_registry_symbol_has_a_sector(self):
        # A missing entry would silently make sector concentration uncomputable
        # for that symbol, which turns the check into "unknown" and blocks
        # execution for a reason nobody would find.
        from copilot.instruments import ETF_REGISTRY, sector_of
        for symbol in sorted(ETF_REGISTRY):
            self.assertIsInstance(sector_of(symbol), str, symbol)
            self.assertTrue(sector_of(symbol), symbol)

    def test_an_unregistered_symbol_raises(self):
        from copilot.instruments import sector_of
        with self.assertRaises(ValueError):
            sector_of("NVDA")


class ModelMayNotSupplyNumbers(unittest.TestCase):
    def test_quantity_target_weight_and_stop_are_refused_from_a_model(self):
        # ADR-0004 clause 2 stops being aspirational here.
        for key, value in (("quantity", 10), ("target_weight", 0.5), ("stop_loss", 90.0)):
            p = proposal(action="buy")
            p[key] = value
            with self.assertRaisesRegex(ValueError, "engine"):
                assess_proposal(p, fixture(), None, now=NOW)

    def test_a_model_price_must_equal_the_snapshot_price(self):
        # Observed before this change: a proposal claiming 4242.0 against a
        # snapshot price of 100.0 returned buy/actionable and the 4242.0 was
        # written to an immutable table.
        snapshot = fixture()
        p = proposal(action="buy")
        p["price"] = 4242.0
        with self.assertRaisesRegex(ValueError, "price"):
            assess_proposal(p, snapshot, None, now=NOW)

    def test_a_matching_model_price_is_accepted(self):
        snapshot = fixture()
        p = proposal(action="buy")
        p["price"] = snapshot["instruments"]["QQQ"]["price"]
        decision = assess_proposal(p, snapshot, None, now=NOW)
        self.assertEqual(decision["price"], snapshot["instruments"]["QQQ"]["price"])

    def test_the_engine_may_supply_a_quantity(self):
        p = proposal(action="buy")
        p["quantity"] = 10
        decision = assess_proposal(p, fixture(), None, now=NOW, source="engine")
        self.assertEqual(decision["requested_action"], "buy")

    def test_an_unknown_source_raises(self):
        with self.assertRaisesRegex(ValueError, "source"):
            assess_proposal(proposal(), fixture(), None, now=NOW, source="whatever")


if __name__ == "__main__":
    unittest.main()

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


def complete_context(snapshot, p=None):
    return {"portfolio_version": "v1", "portfolio_complete": True, "base_currency": "USD", "snapshot_id": snapshot["snapshot_id"], "risk_proposal_fingerprint": proposal_fingerprint(p or proposal()),
        "verified_risk_inputs": {"post_trade_weight": .02, "post_trade_sector_weight": .2, "max_correlation": .5, "position_adv_fraction": .001, "drawdown": .01}}


class PolicyTests(unittest.TestCase):
    def test_registered_etf_requires_verified_market_evidence(self):
        # Evidence *identity* fields (instrument_id/asset_class/currency/unit/
        # price_kind) are bound to the requested instrument at collection time by
        # market_data._validate_source, not here; see _test_market_data's
        # test_wrong_currency_is_rejected and
        # test_registered_etf_cannot_be_reclassified_to_stock. What the policy
        # itself still owns is the evidence's verification state and freshness.
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
                           {"latest_session": "2026-09-03"}):
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
        p = proposal()
        p.update(quantity=10, target_weight=.02)
        d = assess_proposal(p, fixture(), now=NOW)
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
        p = proposal()
        p.update(risk_checks={"all": "pass"}, portfolio_complete=True, quantity=10)
        d = assess_proposal(p, fixture(), now=NOW)
        self.assertEqual(d["risk_checks"]["single_name"]["status"], "unknown")
        self.assertNotIn("quantity", d)

    def test_measured_risk_fail_downgrades_purchase(self):
        snapshot = fixture()
        context = complete_context(snapshot)
        context["verified_risk_inputs"]["post_trade_weight"] = .2
        d = assess_proposal(proposal(), snapshot, context, now=NOW)
        self.assertEqual(d["action"], "hold")
        self.assertEqual(d["risk_checks"]["single_name"]["status"], "fail")

    def test_trusted_complete_context_and_stop(self):
        snapshot = fixture()
        p = proposal()
        p.update(quantity=10, target_weight=.02, mode="tactical")
        d = assess_proposal(p, snapshot, complete_context(snapshot, p), now=NOW)
        self.assertEqual(d["execution_scope"], "research_only")
        p["stop_loss"] = 95
        d = assess_proposal(p, snapshot, complete_context(snapshot, p), now=NOW)
        self.assertEqual(d["execution_scope"], "actionable")
        self.assertEqual(d["quantity"], 10)

    def test_index_price_never_an_entry(self):
        snapshot = fixture("^NDX")
        p = proposal("^NDX")
        p.update(price=100.0, quantity=10)
        d = assess_proposal(p, snapshot, complete_context(snapshot), now=NOW)
        self.assertEqual(d["execution_scope"], "research_only")
        self.assertNotIn("price", d)
        self.assertNotIn("quantity", d)

    def test_gold_without_real_merchant_evidence_has_no_entry(self):
        snapshot = fixture("GOLD.CNY")
        p = proposal("GOLD.CNY")
        p.update(price=100.0, quantity=10, retail_quote={"verified": True, "price": 100})
        d = assess_proposal(p, snapshot, complete_context(snapshot), now=NOW)
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


if __name__ == "__main__":
    unittest.main()

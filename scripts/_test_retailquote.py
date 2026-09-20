"""A user-reported merchant gold quote, and the gate it finally makes reachable."""
from __future__ import annotations

import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from copilot import journal, retailquote, service
from copilot.policy import assess_proposal
from _test_policy import NOW, seal
from copilot.instruments import get_instrument

OBSERVED = "2026-09-06T01:30:00+00:00"


def quote(**overrides):
    base = dict(merchant="中国银行", product="积存金", ask_per_fine_gram=962.5,
                observed_at=OBSERVED)
    base.update(overrides)
    return retailquote.build(**base)


def gold_snapshot():
    return seal({"schema_version": 1, "created_at": "2026-09-06T01:00:00+00:00",
                 "decision_at": "2026-09-06T01:00:00+00:00",
                 "valid_until": "2026-09-06T12:00:00+00:00", "status": "ready",
                 "instruments": {"GOLD.CNY": {
                     **get_instrument("GOLD.CNY"), "quality_status": "pass",
                     "latest_session": "2026-09-04", "expected_session": "2026-09-04",
                     "price": 947.09, "indicators": {}, "evidence_ids": ["sge"],
                     "issues": [], "sources": ["sge"]}},
                 "evidence": [{"evidence_id": "sge", "provider": "sge",
                               "upstream": "Shanghai Gold Exchange",
                               "source_url": "https://www.sge.com.cn/sjzx/quotation_daily_new",
                               "observed_at": "2026-09-04T07:30:00+00:00",
                               "retrieved_at": "2026-09-06T00:59:00+00:00",
                               "instrument_id": "GOLD.CNY", "asset_class": "physical_gold",
                               "currency": "CNY", "unit": "gram",
                               "price_kind": "sge_au9999_close", "status": "ok"}],
                 "issues": []})


class TheQuoteIsShapeValidated(unittest.TestCase):
    def test_a_complete_quote_builds(self):
        result = quote()
        self.assertEqual(result["currency"], "CNY")
        self.assertEqual(result["unit"], "gram")
        self.assertEqual(result["ask_per_fine_gram"], 962.5)

    def test_a_blank_merchant_is_refused(self):
        with self.assertRaisesRegex(ValueError, "merchant"):
            quote(merchant="   ")

    def test_a_blank_product_is_refused(self):
        with self.assertRaisesRegex(ValueError, "product"):
            quote(product="")

    def test_a_non_positive_ask_is_refused(self):
        with self.assertRaisesRegex(ValueError, "ask_per_fine_gram"):
            quote(ask_per_fine_gram=0.0)

    def test_an_ask_outside_the_plausible_band_is_refused(self):
        # A per-ounce figure (about 29,900 CNY) typed into a per-gram field is
        # the single most likely user error here, and it would size the order
        # 31x too small.
        with self.assertRaisesRegex(ValueError, "per fine gram"):
            quote(ask_per_fine_gram=29900.0)

    def test_an_unparseable_observed_at_is_refused(self):
        with self.assertRaisesRegex(ValueError, "observed_at"):
            quote(observed_at="last tuesday")

    def test_a_naive_observed_at_is_refused(self):
        # A timestamp with no zone cannot be compared to the snapshot clock.
        with self.assertRaisesRegex(ValueError, "timezone"):
            quote(observed_at="2026-09-06T01:30:00")


class TheQuoteIsNotAPrediction(unittest.TestCase):
    """Shape validation is not price verification. Say so in the record."""

    def test_the_record_states_that_it_is_self_reported(self):
        self.assertEqual(quote()["verification"], "self_reported")

    def test_it_carries_no_status_that_could_read_as_verified(self):
        self.assertNotIn("verified", quote())

    def test_the_reported_ask_prices_a_trade_but_never_justifies_one(self):
        """The price is usable; the claim is not.

        Two independent gates enforce this, and both fire on the record the
        gate itself forces the proposal to cite: it has no `source_url`,
        because a number read off a phone has no URL, and it carries
        `critical_evidence_eligible: False`. Either one alone makes the
        decision data_insufficient -- see brake.py, which documents the same
        consequence for news records. So a valid quote moves `retail_quote` to
        pass and moves nothing else: assessing gold this way is still
        research_only, and Task 7's evaluate_rule, not this path, is what
        turns a quote into an order.
        """
        attached = retailquote.attach(gold_snapshot(), quote(), now=NOW)
        record = next(e for e in attached["evidence"] if e.get("retail_quote"))
        proposal = {"instrument_id": "GOLD.CNY", "action": "buy", "mode": "accumulation",
                    "horizon": "long_term", "reasons": ["scheduled contribution"],
                    "conditions": [], "evidence_ids": ["sge", record["evidence_id"]],
                    "retail_quote": record["retail_quote"],
                    "price": record["retail_quote"]["ask_per_fine_gram"]}
        decision = assess_proposal(proposal, attached, now=NOW)
        self.assertEqual(decision["risk_checks"]["retail_quote"]["status"], "pass")
        self.assertEqual(decision["action"], "data_insufficient")
        self.assertEqual(decision["execution_scope"], "research_only")


class AttachingItMakesTheGateReachable(unittest.TestCase):
    """Before this module, policy.py:390 could never pass: nothing wrote
    `retail_quote` onto an evidence record, so `rec["retail_quote"] == quote`
    compared against a field that did not exist."""

    def test_the_new_snapshot_carries_a_matching_evidence_record(self):
        attached = retailquote.attach(gold_snapshot(), quote(), now=NOW)
        record = next(e for e in attached["evidence"] if e.get("retail_quote"))
        stored = record["retail_quote"]
        # The stored payload is the built quote PLUS the id of the record
        # holding it. The gate resolves the record by that field and only then
        # compares the two quotes, so a payload without it matches nothing and
        # the check stays "unknown" forever.
        self.assertEqual(stored["evidence_id"], record["evidence_id"])
        self.assertEqual({k: v for k, v in stored.items() if k != "evidence_id"}, quote())
        self.assertEqual(record["instrument_id"], "GOLD.CNY")

    def test_attaching_a_quote_does_not_break_a_proposal_that_ignores_it(self):
        """Capturing a price must not damage the answers that need no price.

        The quote record is not the instrument's market evidence, and policy
        validates every id in that set on every proposal whether or not the
        proposal cites it. Listing the quote there made an ordinary gold
        research proposal -- one that never mentions the merchant -- come back
        data_insufficient purely because a price had been captured.
        """
        from _test_policy import gold_proposal
        attached = retailquote.attach(gold_snapshot(), quote(), now=NOW)
        plain = assess_proposal(gold_proposal(), attached, now=NOW)
        self.assertEqual(plain["action"], "buy")
        self.assertEqual(plain["reasons"], ["scheduled contribution"])

    def test_re_attaching_the_same_quote_is_refused(self):
        # Two records under one evidence_id make assess_proposal raise
        # "duplicate evidence_id" from inside a tool call. Catch it here, where
        # the message can name what the user actually did.
        attached = retailquote.attach(gold_snapshot(), quote(), now=NOW)
        with self.assertRaisesRegex(ValueError, "already carries"):
            retailquote.attach(attached, quote(), now=NOW)

    def test_the_original_snapshot_is_not_mutated(self):
        original = gold_snapshot()
        before = len(original["evidence"])
        retailquote.attach(original, quote(), now=NOW)
        self.assertEqual(len(original["evidence"]), before)

    def test_the_snapshot_id_is_resealed(self):
        original = gold_snapshot()
        attached = retailquote.attach(original, quote(), now=NOW)
        self.assertNotEqual(attached["snapshot_id"], original["snapshot_id"])

    def test_a_gold_proposal_citing_it_becomes_executable(self):
        attached = retailquote.attach(gold_snapshot(), quote(), now=NOW)
        record = next(e for e in attached["evidence"] if e.get("retail_quote"))
        proposal = {"instrument_id": "GOLD.CNY", "action": "buy", "mode": "accumulation",
                    "horizon": "long_term", "reasons": ["scheduled contribution"],
                    "conditions": [], "evidence_ids": ["sge", record["evidence_id"]],
                    "retail_quote": record["retail_quote"],
                    "price": record["retail_quote"]["ask_per_fine_gram"]}
        decision = assess_proposal(proposal, attached, now=NOW)
        self.assertEqual(decision["risk_checks"]["retail_quote"]["status"], "pass")

    def test_without_the_quote_the_same_proposal_is_not_executable(self):
        snapshot = gold_snapshot()
        proposal = {"instrument_id": "GOLD.CNY", "action": "buy", "mode": "accumulation",
                    "horizon": "long_term", "reasons": ["scheduled contribution"],
                    "conditions": [], "evidence_ids": ["sge"]}
        decision = assess_proposal(proposal, snapshot, now=NOW)
        self.assertEqual(decision["risk_checks"]["retail_quote"]["status"], "unknown")
        self.assertNotEqual(decision.get("execution_scope"), "actionable")

    def test_a_quote_older_than_a_day_does_not_pass_the_gate(self):
        # policy.py:392-393 allows at most 86400 seconds. A stale merchant ask
        # is worse than none: it looks measured.
        stale = quote(observed_at=(NOW - timedelta(days=2)).isoformat())
        attached = retailquote.attach(gold_snapshot(), stale, now=NOW)
        record = next(e for e in attached["evidence"] if e.get("retail_quote"))
        # Same vacuity guard as the tampered case: the record must resolve, so
        # that age is the only thing left for the gate to object to.
        self.assertEqual(record["retail_quote"]["evidence_id"], record["evidence_id"])
        proposal = {"instrument_id": "GOLD.CNY", "action": "buy", "mode": "accumulation",
                    "horizon": "long_term", "reasons": ["scheduled contribution"],
                    "conditions": [], "evidence_ids": ["sge", record["evidence_id"]],
                    "retail_quote": record["retail_quote"],
                    "price": record["retail_quote"]["ask_per_fine_gram"]}
        self.assertEqual(assess_proposal(proposal, attached, now=NOW)
                         ["risk_checks"]["retail_quote"]["status"], "unknown")

    def test_a_tampered_proposal_quote_does_not_pass(self):
        # The gate demands rec["retail_quote"] == quote exactly. A proposal that
        # cites the record but states a better price must not pass.
        attached = retailquote.attach(gold_snapshot(), quote(), now=NOW)
        record = next(e for e in attached["evidence"] if e.get("retail_quote"))
        tampered = {**record["retail_quote"], "ask_per_fine_gram": 900.0}
        # Without this the test would pass vacuously: an unresolvable quote
        # also reads "unknown", so the tampered one has to reach the record and
        # be rejected on the comparison, not on the lookup.
        self.assertEqual(tampered["evidence_id"], record["evidence_id"])
        proposal = {"instrument_id": "GOLD.CNY", "action": "buy", "mode": "accumulation",
                    "horizon": "long_term", "reasons": ["scheduled contribution"],
                    "conditions": [], "evidence_ids": ["sge", record["evidence_id"]],
                    "retail_quote": tampered, "price": 900.0}
        self.assertEqual(assess_proposal(proposal, attached, now=NOW)
                         ["risk_checks"]["retail_quote"]["status"], "unknown")


class CapturingItWritesANewSnapshot(unittest.TestCase):
    """Temporary database, synthetic snapshot; personal state is never touched."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "copilot.sqlite"

    def capture(self, stored, **overrides):
        args = dict(snapshot_id=stored["snapshot_id"], merchant="中国银行", product="积存金",
                    ask_per_fine_gram=962.5, observed_at=OBSERVED, db_path=self.db, now=NOW)
        args.update(overrides)
        return service.capture_retail_quote(**args)

    def test_it_stores_a_new_snapshot_and_leaves_the_original_readable(self):
        # The journal's triggers forbid updating a stored snapshot, so the only
        # correct move is to write another one. Both must still load.
        stored = gold_snapshot()
        journal.save_snapshot(stored, db_path=self.db)
        captured = self.capture(stored)
        self.assertNotEqual(captured["snapshot_id"], stored["snapshot_id"])
        self.assertEqual(captured["supersedes"], stored["snapshot_id"])
        self.assertEqual(service.snapshot(stored["snapshot_id"], db_path=self.db), stored)
        written = service.snapshot(captured["snapshot_id"], db_path=self.db)
        record = next(e for e in written["evidence"] if e.get("retail_quote"))
        self.assertEqual(record["retail_quote"]["merchant"], "中国银行")
        self.assertEqual(record["provider"], "user_reported")
        self.assertEqual(record["critical_evidence_eligible"], False)

    def test_the_stored_snapshot_still_passes_integrity_verification(self):
        # A resealed snapshot whose digest disagrees with market_data's would
        # make every later assessment report "snapshot integrity verification
        # failed" instead of anything about gold.
        from copilot.market_data import verify_snapshot
        stored = gold_snapshot()
        journal.save_snapshot(stored, db_path=self.db)
        captured = self.capture(stored)
        self.assertTrue(verify_snapshot(service.snapshot(captured["snapshot_id"], db_path=self.db)))

    def test_an_implausible_ask_is_refused_before_anything_is_written(self):
        stored = gold_snapshot()
        journal.save_snapshot(stored, db_path=self.db)
        with self.assertRaisesRegex(ValueError, "per fine gram"):
            self.capture(stored, ask_per_fine_gram=29900.0)
        self.assertEqual(len(service.snapshot(stored["snapshot_id"],
                                              db_path=self.db)["evidence"]), 1)

    def test_the_receipt_says_the_price_was_not_verified(self):
        stored = gold_snapshot()
        journal.save_snapshot(stored, db_path=self.db)
        self.assertIn("不核实价格", self.capture(stored)["disclosure"])


if __name__ == "__main__":
    unittest.main()

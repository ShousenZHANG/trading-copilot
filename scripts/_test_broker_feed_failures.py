"""Feed failures retain account evidence without authorizing quantities."""
from __future__ import annotations
import sys
import io
import logging
import unittest
from unittest.mock import patch

from copilot import broker
from _test_broker import SETTINGS, sdk_double


class FeedFailures(unittest.TestCase):
    def test_sdk_payload_logs_cannot_reach_inherited_or_sdk_handlers(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        root = logging.getLogger()
        sdk = logging.getLogger("ibapi.utils")
        old_level, old_disabled, old_handlers = sdk.level, sdk.disabled, sdk.handlers[:]
        old_parent_level = logging.getLogger("ibapi").level
        old_parent_handlers = logging.getLogger("ibapi").handlers[:]
        old_parent_propagate = logging.getLogger("ibapi").propagate
        root.addHandler(handler)
        sdk.addHandler(handler)
        sdk.setLevel(logging.DEBUG)
        sdk.disabled = False
        try:
            broker._silence_sdk_logs()
            sdk.info("private-account-payload")
            sdk.error("private-account-error")
            self.assertEqual(stream.getvalue(), "")
        finally:
            root.removeHandler(handler)
            sdk.setLevel(old_level)
            sdk.disabled, sdk.handlers = old_disabled, old_handlers
            parent = logging.getLogger("ibapi")
            parent.setLevel(old_parent_level)
            parent.handlers, parent.propagate = old_parent_handlers, old_parent_propagate

    def collect_with_quote_response(self, callback):
        modules, calls, _ = sdk_double()
        def query(client, req_id, contract, generic, snapshot, regulatory, options):
            calls.append(("reqMktData", snapshot, regulatory))
            callback(client, req_id)
        modules["ibapi.client"].EClient.reqMktData = query
        with patch.dict(sys.modules, modules):
            result = broker.collect_execution_snapshot({**SETTINGS, "timeout_seconds": 1}, ["SPY"])
        return result

    def test_entitlement_error_preserves_positions_and_incomplete_quote_end(self):
        result = self.collect_with_quote_response(lambda client, req: client.error(req, 0, 10168, "private details", ""))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("ibkr_not_entitled", result["issues"])
        self.assertTrue(result["positions"])
        self.assertTrue(result["coverage"]["positions"]["request_complete"])
        self.assertFalse(result["coverage"]["quotes"]["request_complete"])
        self.assertNotIn("private details", str(result))

    def test_delayed_fallback_warning_without_snapshot_end_preserves_account(self):
        result = self.collect_with_quote_response(lambda client, req: client.error(req, 0, 2186, "private details", ""))
        self.assertEqual(result["status"], "blocked")
        self.assertIn("ibkr_api_realtime_subscription_required", result["issues"])
        self.assertIn("ibkr_quote_query_timeout", result["issues"])
        self.assertTrue(result["account"])
        self.assertFalse(result["coverage"]["quotes"]["request_complete"])

    def test_farm_disconnect_does_not_masquerade_as_failed_account_connection(self):
        modules, _, _ = sdk_double(error_args=(-1, 0, 2103, "private details", ""))
        with patch.dict(sys.modules, modules):
            result = broker.collect_execution_snapshot(SETTINGS, ["SPY"])
        self.assertEqual(result["status"], "blocked")
        self.assertIn("ibkr_market_data_farm_disconnected", result["issues"])
        self.assertTrue(result["positions"])
        self.assertNotIn("ibkr_connection_or_query_error", result["issues"])


if __name__ == "__main__":
    unittest.main()

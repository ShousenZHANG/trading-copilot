"""Credential loading and redaction contracts. No real secret is ever written here."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
from copilot import service

FIXTURE = "fixture-not-a-real-key"


class CredentialLoadingContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / ".env").write_text(
            f"FINNHUB_API_KEY={FIXTURE}\nFRED_API_KEY=fixture-fred\n"
            "# comment line\nNOT_A_KNOWN_NAME=should-be-ignored\n",
            encoding="utf-8")

    def load(self, environ: dict) -> dict:
        with patch.object(service, "ROOT", self.root), patch.dict(os.environ, environ, clear=True):
            service.load_credentials()
            return dict(os.environ)

    def test_loads_known_names_from_env_file(self):
        result = self.load({})
        self.assertEqual(result["FINNHUB_API_KEY"], FIXTURE)
        self.assertEqual(result["FRED_API_KEY"], "fixture-fred")

    def test_ignores_names_outside_the_allowlist(self):
        self.assertNotIn("NOT_A_KNOWN_NAME", self.load({}))

    def test_empty_injected_value_does_not_block_the_env_file(self):
        """An MCP client expanding ${VAR} against an empty host environment
        injects "", which must not win over a real value in .env."""
        self.assertEqual(self.load({"FINNHUB_API_KEY": ""})["FINNHUB_API_KEY"], FIXTURE)
        self.assertEqual(self.load({"FINNHUB_API_KEY": "   "})["FINNHUB_API_KEY"], FIXTURE)

    def test_real_process_override_still_wins(self):
        self.assertEqual(self.load({"FINNHUB_API_KEY": "explicit"})["FINNHUB_API_KEY"], "explicit")

    def test_missing_env_file_is_not_an_error(self):
        with patch.object(service, "ROOT", self.root / "nope"), patch.dict(os.environ, {}, clear=True):
            service.load_credentials()

    def test_quoted_values_are_unwrapped(self):
        (self.root / ".env").write_text('FINNHUB_API_KEY="quoted-value"\n', encoding="utf-8")
        self.assertEqual(self.load({})["FINNHUB_API_KEY"], "quoted-value")

    def test_secret_values_reports_live_credentials_only(self):
        with patch.object(service, "ROOT", self.root), patch.dict(os.environ, {}, clear=True):
            service.load_credentials()
            values = service.secret_values()
        self.assertIn(FIXTURE, values)
        self.assertIn("fixture-fred", values)
        self.assertNotIn("", values)
        self.assertNotIn("should-be-ignored", values)

    def test_secret_values_is_deduplicated_and_ignores_blank(self):
        with patch.dict(os.environ, {"FINNHUB_API_KEY": "same", "FRED_API_KEY": "same",
                                     "SEC_USER_AGENT": "  "}, clear=True):
            values = service.secret_values()
        self.assertEqual(values.count("same"), 1)
        self.assertNotIn("  ", values)


if __name__ == "__main__":
    unittest.main()

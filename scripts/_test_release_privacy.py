"""Release archive regression fixtures; never read credentials or personal state."""
import tempfile
import unittest
import zipfile
from pathlib import Path

from package_release import REQUIRED_ARTIFACT_FILES, _audit_zip


class ReleasePrivacyTests(unittest.TestCase):
    def audit(self, additions):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            files = {name: "{}" if name.endswith(".json") else "" for name in REQUIRED_ARTIFACT_FILES}
            files.update(additions)
            with zipfile.ZipFile(archive, "w") as output:
                for name, content in files.items():
                    output.writestr("trading-copilot/" + name, content)
            return _audit_zip(archive)

    def test_codex_secret_is_rejected_in_actual_archive(self):
        problems = self.audit({".codex/config.toml": '[mcp_servers.fixture.env]\nAPI_KEY="fixture-only-secret"'})
        self.assertTrue(any("hardcoded secret" in problem for problem in problems))

    def test_mixed_placeholder_rejected_in_actual_archive(self):
        problems = self.audit({".mcp.json": '{"mcpServers":{"fixture":{"env":{"TOKEN":"${TOKEN}fixture-only-secret"}}}}'})
        self.assertTrue(any("hardcoded secret" in problem for problem in problems))

    def test_private_database_and_strategy_never_ship(self):
        for name in ("data/state/copilot.sqlite", "data/state/copilot.sqlite-wal", "docs/strategy.md"):
            self.assertTrue(self.audit({name: "synthetic fixture"}), name)

    def test_variable_names_are_safe(self):
        self.assertEqual(self.audit({".codex/config.toml": '[mcp_servers.fixture]\nenv_vars=["API_KEY"]'}), [])

    def test_third_party_market_history_never_ships(self):
        from package_release import _forbidden_archive_name
        for name in ("trading-copilot-0.6.0/scripts/BXN_History.csv",
                     "trading-copilot-0.6.0/evals/prices/bxnt_history.csv",
                     "trading-copilot-0.6.0/docs/vendor-data/whatever.csv"):
            self.assertTrue(_forbidden_archive_name(name), name)

    def test_our_own_fixtures_still_ship(self):
        from package_release import _forbidden_archive_name
        self.assertFalse(_forbidden_archive_name("trading-copilot-0.6.0/evals/prices/2026.json"))


if __name__ == "__main__":
    unittest.main()

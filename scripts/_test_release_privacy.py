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


if __name__ == "__main__":
    unittest.main()

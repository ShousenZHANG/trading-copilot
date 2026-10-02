"""Release archive regression fixtures; never read credentials or personal state."""
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from package_release import REQUIRED_ARTIFACT_FILES, _audit_zip


class ReleasePrivacyTests(unittest.TestCase):
    def audit(self, additions, *, omissions=()):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            files = {name: "{}" if name.endswith(".json") else "" for name in REQUIRED_ARTIFACT_FILES}
            files.update(additions)
            for name in omissions:
                files.pop(name, None)
            with zipfile.ZipFile(archive, "w") as output:
                for name, content in files.items():
                    output.writestr("trading-copilot/" + name, content)
            return _audit_zip(archive)

    def test_execution_sensitivity_dependency_must_ship(self):
        name = "scripts/copilot/backtest/execution_sensitivity.py"
        self.assertEqual(self.audit({}), [])
        problems = self.audit({}, omissions=(name,))
        self.assertTrue(any(name in problem for problem in problems), problems)

    def test_codex_secret_is_rejected_in_actual_archive(self):
        problems = self.audit({".codex/config.toml": '[mcp_servers.fixture.env]\nAPI_KEY="fixture-only-secret"'})
        self.assertTrue(any("hardcoded secret" in problem for problem in problems))

    def test_mixed_placeholder_rejected_in_actual_archive(self):
        problems = self.audit({".mcp.json": '{"mcpServers":{"fixture":{"env":{"TOKEN":"${TOKEN}fixture-only-secret"}}}}'})
        self.assertTrue(any("hardcoded secret" in problem for problem in problems))

    def test_private_database_and_strategy_never_ship(self):
        for name in ("data/state/copilot.sqlite", "data/state/copilot.sqlite-wal", "docs/strategy.md",
                     "data/watchlist.local.md"):
            self.assertTrue(self.audit({name: "synthetic fixture"}), name)

    def test_variable_names_are_safe(self):
        self.assertEqual(self.audit({".codex/config.toml": '[mcp_servers.fixture]\nenv_vars=["API_KEY"]'}), [])

    def test_http_credential_variable_names_are_safe(self):
        self.assertEqual(self.audit({".codex/config.toml":
            '[mcp_servers.fixture]\nbearer_token_env_var="API_KEY"\n'
            'env_http_headers={"X-Token"="OTHER_KEY"}'}), [])

    def test_literals_cannot_hide_in_http_credential_variable_fields(self):
        for field in ('bearer_token_env_var="fixture-secret"',
                      'env_http_headers={"X-Token"="fixture-secret"}'):
            self.assertTrue(self.audit({".codex/config.toml": '[mcp_servers.fixture]\n' + field}))

    def test_third_party_market_history_never_ships(self):
        from package_release import _forbidden_archive_name
        for name in ("trading-copilot-0.6.0/scripts/BXN_History.csv",
                     "trading-copilot-0.6.0/evals/prices/bxnt_history.csv",
                     "trading-copilot-0.6.0/docs/vendor-data/whatever.csv"):
            self.assertTrue(_forbidden_archive_name(name), name)

    def test_our_own_fixtures_still_ship(self):
        from package_release import _forbidden_archive_name
        self.assertFalse(_forbidden_archive_name("trading-copilot-0.6.0/evals/prices/2026.json"))

    def test_packaging_guard_catches_previously_missed_variants(self):
        # Mutation-testing follow-up: a bare `.endswith("_history.csv")` plus
        # a forward-slash-only "/vendor-data/" substring test missed a
        # hyphenated name, any extension other than .csv, a compressed file,
        # a backslash path, and mixed case.
        from package_release import _forbidden_archive_name
        for name in (
            "trading-copilot-0.6.0/scripts/BXN-History.csv",
            "trading-copilot-0.6.0/evals/bxn_history.txt",
            "trading-copilot-0.6.0/scripts/BXN_History.csv.gz",
            "trading-copilot-0.6.0\\evals\\vendor-data\\bxn.csv",
            "TRADING-COPILOT-0.6.0/SCRIPTS/BXN_HISTORY.CSV",
        ):
            self.assertTrue(_forbidden_archive_name(name), name)
        self.assertFalse(_forbidden_archive_name("trading-copilot-0.6.0/evals/prices/2026.json"))

    def test_build_itself_skips_a_forbidden_market_data_file(self):
        # The audit is a backstop, not the enforcement point: build()'s only
        # callers were _excluded() (path patterns) and a three-name inline
        # leak_guard tuple, neither of which knew about vendor market data.
        # A forbidden file must never enter the zip in the first place, so
        # prove build() itself refuses it -- not only that a post-hoc audit
        # of a zip containing it would flag it.
        from package_release import build
        from sync_runtimes import generated_files
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "evals").mkdir()
            fixture = root / "evals/_pkgtest_admission_review_history.csv"
            fixture.write_text("DATE,BXN\n09/18/2009,298.140000\n", encoding="utf-8")
            (root / "data").mkdir()
            (root / "data/watchlist.local.md").write_text("Private fixture notes", encoding="utf-8")
            (root / ".mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
            for path, content in generated_files(root=root).items():
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")
            with patch("package_release.ROOT", root), patch("sync_runtimes.generated_files",
                    side_effect=lambda: generated_files(root=root)):
                out = build("0.0.0-pkgtest", "buildguard")
            with zipfile.ZipFile(out) as zf:
                names = zf.namelist()
            self.assertFalse(any(n.endswith(fixture.name) for n in names), names)
            self.assertFalse(any(n.endswith("watchlist.local.md") for n in names), names)


if __name__ == "__main__":
    unittest.main()

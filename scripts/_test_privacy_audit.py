"""Offline privacy checks over synthetic files; no personal state is opened."""
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from privacy_audit import scan_archive, scan_tree


class PrivacyAuditTests(unittest.TestCase):
    def tree(self, files, *, denylist=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name, content in files.items():
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
            denylist_path = None
            if denylist is not None:
                denylist_path = root / "local-private.json"
                denylist_path.write_text(json.dumps(denylist), encoding="utf-8")
            return scan_tree(root, paths=list(files), denylist_path=denylist_path)

    def archive(self, files):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name, content in files.items():
                    bundle.writestr(name, content)
            return scan_archive(archive)

    def test_user_home_path_fails_without_disclosing_the_literal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            sensitive = "C:/Users/" + "private-person/portfolio.json"
            (root / "README.md").write_text("Example\n" + sensitive, encoding="utf-8")
            report = scan_tree(root, paths=["README.md"])
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["findings"], [dict(path="README.md", line=2, category="user_home_path")])
            self.assertNotIn(sensitive, json.dumps(report))

    def test_archive_audit_rejects_contact_text_and_never_echoes_it(self):
        from package_release import REQUIRED_ARTIFACT_FILES, _audit_zip
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "fixture.zip"
            contact = "private.person" + "@" + "contact-domain.org"
            with zipfile.ZipFile(archive, "w") as bundle:
                for path in REQUIRED_ARTIFACT_FILES:
                    bundle.writestr("trading-copilot/" + path, "{}" if path.endswith(".json") else "")
                bundle.writestr("trading-copilot/docs/example.md", "Contact: " + contact)
            problems = _audit_zip(archive)
            self.assertTrue(any("contact_email" in problem for problem in problems), problems)
            self.assertNotIn(contact, json.dumps(problems))

    def test_reserved_contacts_and_explicit_home_placeholders_are_public(self):
        contents = "contact@example.com contact@example.invalid\nC:/Users/<USER>/project\n"
        self.assertEqual(self.tree({"README.md": contents})["status"], "pass")

    def test_synthetic_broker_values_only_allowed_in_named_tests(self):
        synthetic = "U" + "1234567"
        self.assertEqual(self.tree({"scripts/_test_fixture.py": synthetic})["status"], "pass")
        self.assertEqual(self.tree({"README.md": synthetic})["findings"][0]["category"],
                         "broker_account_identifier")

    def test_test_filename_does_not_exempt_an_arbitrary_account(self):
        value = "U" + "98765432"
        report = self.tree({"scripts/_test_fixture.py": value})
        self.assertEqual(report["status"], "fail")
        self.assertNotIn(value, json.dumps(report))

    def test_contact_phone_and_residential_address_are_redacted(self):
        number = "+61" + " 412 345 678"
        address = "17" + " Private Avenue"
        report = self.tree({"README.md": "phone: " + number + "\nhome_" + "address: " + address})
        self.assertEqual({finding["category"] for finding in report["findings"]},
                         {"contact_phone", "residential_address"})
        self.assertNotIn(number, json.dumps(report))
        self.assertNotIn(address, json.dumps(report))

    def test_private_denylist_is_case_insensitive_and_does_not_echo_value(self):
        literal = "private fixture portfolio"
        report = self.tree({"docs/example.md": literal.upper()},
                           denylist=dict(schema_version=1, literals=[literal]))
        self.assertEqual(report["findings"], [dict(path="docs/example.md", line=1,
                                                  category="private_denylist_match")])
        self.assertTrue(report["private_denylist_used"])
        self.assertNotIn(literal, json.dumps(report).lower())

    def test_denylist_redacts_sensitive_filename_as_well_as_contents(self):
        literal = "private-person"
        report = self.tree({"docs/" + literal + ".md": literal},
                           denylist=dict(schema_version=1, literals=[literal]))
        self.assertEqual(report["status"], "fail")
        self.assertNotIn(literal, json.dumps(report))
        self.assertTrue(all("[private-literal]" in finding["path"] for finding in report["findings"]))

    def test_json_unicode_escaping_does_not_hide_an_email(self):
        contact = "private.person" + "@" + "contact-domain.org"
        escaped = '"' + "".join("\\u" + format(ord(char), "04x") for char in contact) + '"'
        report = self.tree({"data/example.json": '{"contact":' + escaped + '}'})
        self.assertEqual(report["findings"][0]["category"], "contact_email")

    def test_json_encoded_private_literal_still_matches(self):
        literal = "私有示例字符串"
        report = self.tree({"data/example.json": json.dumps(dict(value=literal))},
                           denylist=dict(schema_version=1, literals=[literal]))
        self.assertEqual(report["findings"][0]["category"], "private_denylist_match")

    def test_missing_denylist_fails_closed_without_reading_public_files(self):
        with tempfile.TemporaryDirectory() as directory:
            report = scan_tree(Path(directory), paths=["missing.md"],
                               denylist_path=Path(directory) / "missing-private.json")
            self.assertEqual(report["findings"], [dict(path="[denylist]", line=None,
                                                      category="invalid_private_denylist")])
            self.assertEqual(report["text_files_scanned"], 0)

    def test_invalid_denylist_does_not_fall_back_to_generic_rules(self):
        for denylist in (dict(schema_version=2, literals=[]), dict(schema_version=1, literals=["x"]),
                         dict(schema_version=1, literals="not-a-list")):
            report = self.tree({"README.md": "public"}, denylist=denylist)
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["text_files_scanned"], 0)

    def test_tracked_private_state_is_rejected_without_opening_it(self):
        with tempfile.TemporaryDirectory() as directory:
            # Intentionally nonexistent: a private entry is rejected at its name,
            # before stat/open, rather than generating an unreadable-file error.
            report = scan_tree(Path(directory), paths=["data/state/not-present.sqlite"])
            self.assertEqual(report["findings"][0]["category"], "private_file")
            self.assertEqual(report["text_files_scanned"], 0)

    @unittest.skipUnless(shutil.which("git"), "git inventory dependency")
    def test_default_inventory_scans_tracked_files_not_untracked_private_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "--quiet", str(root)], check=True, capture_output=True)
            (root / "README.md").write_text("Public", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
            (root / "data/state").mkdir(parents=True)
            (root / "data/state/secret.sqlite").write_bytes(b"\xff\xfe\x00")
            report = scan_tree(root)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["text_files_scanned"], 1)

    def test_unscannable_binary_fails_closed(self):
        report = self.tree({"docs/unknown.bin": b"\x00\xff\x01"})
        self.assertEqual(report["findings"][0]["category"], "unscannable_content")

    def test_utf16_text_cannot_bypass_content_checks(self):
        home = "C:/Users/" + "private-person/file"
        report = self.tree({"docs/encoded.txt": home.encode("utf-16")})
        self.assertEqual(report["findings"][0]["category"], "user_home_path")

    def test_unknown_public_file_error_is_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            report = scan_tree(Path(directory), paths=["docs/missing.md"])
            self.assertEqual(report["findings"][0]["category"], "unreadable_public_file")

    def test_archive_private_paths_cannot_hide_under_an_arbitrary_root(self):
        for name in ("data/state/private.sqlite", "wrapper/data/state/private.json",
                     "WRAPPER\\config\\user.toml", "trading-copilot/data/runs/example.md"):
            report = self.archive({name: "not opened"})
            self.assertEqual(report["findings"][0]["category"], "private_file", name)
            self.assertEqual(report["text_files_scanned"], 0)

    def test_archive_parent_traversal_is_rejected_before_extraction(self):
        report = self.archive({"trading-copilot/../outside.md": "public"})
        self.assertEqual(report["findings"][0]["category"], "unsafe_archive_path")

    def test_duplicate_case_insensitive_archive_names_are_rejected(self):
        report = self.archive({"trading-copilot/README.md": "public", "trading-copilot/readme.md": "public"})
        self.assertEqual(report["findings"][0]["category"], "duplicate_archive_path")

    def test_broker_identifier_in_filename_is_redacted(self):
        account = "U" + "98765432"
        report = self.tree({"docs/" + account + ".md": "public"})
        self.assertEqual(report["findings"][0]["category"], "broker_account_identifier")
        self.assertNotIn(account, json.dumps(report))

    def test_cli_missing_denylist_returns_failure_with_sanitized_json(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, str(Path(__file__).with_name("privacy_audit.py")),
                                     "--root", directory, "--denylist", str(Path(directory) / "missing.json")],
                                    capture_output=True, text=True, check=False)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["findings"][0]["category"], "invalid_private_denylist")
            self.assertEqual(result.stderr, "")

    def test_high_confidence_private_key_and_access_tokens_are_not_shippable(self):
        fixtures = {
            "private_key_header": "-----BEGIN " + "PRIVATE KEY-----",
            "github_access_token": "ghp_" + "a" * 36,
            "aws_access_key_id": "AKIA" + "A" * 16,
            "google_api_key": "AIza" + "a" * 35,
        }
        for category, credential in fixtures.items():
            with self.subTest(category=category):
                report = self.tree({"scripts/_test_fixture.py": credential})
                self.assertEqual(report["status"], "fail")
                self.assertEqual(report["findings"][0]["category"], category)
                self.assertNotIn(credential, json.dumps(report))

    def test_build_rejects_contact_text_before_creating_an_archive(self):
        from package_release import build
        from sync_runtimes import generated_files
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            contact = "private.person" + "@" + "contact-domain.org"
            (root / "README.md").write_text(contact, encoding="utf-8")
            (root / ".mcp.json").write_text('{"mcpServers":{}}', encoding="utf-8")
            for file, content in generated_files(root=root).items():
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content, encoding="utf-8")
            with patch("package_release.ROOT", root), patch("sync_runtimes.generated_files",
                    side_effect=lambda: generated_files(root=root)):
                with self.assertRaises(ValueError) as raised:
                    build("0.0.0-fixture", None)
            self.assertIn("contact_email", str(raised.exception))
            self.assertNotIn(contact, str(raised.exception))
            self.assertFalse((root / "dist").exists())

    def test_private_denylist_reaches_both_archive_audits(self):
        from package_release import REQUIRED_ARTIFACT_FILES, _audit_zip
        from verify_release import verify
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            denylist = root / "private-list.json"
            literal = "private fixture portfolio"
            denylist.write_text(json.dumps(dict(schema_version=1, literals=[literal])), encoding="utf-8")
            archive = root / "fixture.zip"
            with zipfile.ZipFile(archive, "w") as bundle:
                for name in REQUIRED_ARTIFACT_FILES:
                    bundle.writestr("trading-copilot/" + name, "{}" if name.endswith(".json") else "")
                bundle.writestr("trading-copilot/README-extra.md", literal)
            problems = _audit_zip(archive, privacy_denylist=denylist)
            self.assertTrue(any("private_denylist_match" in problem for problem in problems))
            self.assertNotIn(literal, json.dumps(problems))
            with self.assertRaises(ValueError) as raised:
                verify(archive, privacy_denylist=denylist)
            self.assertNotIn(literal, str(raised.exception))

    def test_multiline_private_literal_is_not_missed(self):
        literal = "Private fixture\nportfolio summary"
        report = self.tree({"README.md": literal}, denylist=dict(schema_version=1, literals=[literal]))
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["findings"], [dict(path="README.md", line=1, category="private_denylist_match")])


if __name__ == "__main__":
    unittest.main()

"""Portable MCP/CLI launcher contracts, using only isolated runtime fixtures."""
from __future__ import annotations

import tempfile
import json
import contextlib
import io
import os
import subprocess
import sys
import venv
import tomllib
import unittest
from unittest.mock import patch
from pathlib import Path

from copilot_runtime import build_command, launch, main


class RuntimeLauncherTests(unittest.TestCase):
    def local_python(self, root: Path) -> Path:
        runtime = root / "data/state/runtime"
        interpreter = runtime / "ibkr-venv/Scripts/python.exe"
        interpreter.parent.mkdir(parents=True)
        interpreter.write_bytes(b"fixture interpreter, never executed")
        (runtime / "copilot-runtime.json").write_text(json.dumps({
            "schema_version": 1, "python": "ibkr-venv/Scripts/python.exe",
        }), encoding="utf-8")
        # Hosted Windows TEMP may use an 8.3 alias (RUNNER~1). The production
        # confinement check resolves that alias before choosing the executable.
        return interpreter.resolve()

    def test_without_local_manifest_keeps_the_existing_uv_script_runtime(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            self.assertEqual(build_command(root=root), [
                "uv", "run", "--no-project", "--quiet", "--script",
                str(root / "mcps/copilot_mcp.py"),
            ])
            self.assertEqual(build_command("cli", ["capabilities"], root=root), [
                "uv", "run", "--no-project", "--quiet", "--script",
                str(root / "scripts/copilot_cli.py"), "capabilities",
            ])

    def test_local_manifest_selects_the_confined_python_for_both_facades(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            interpreter = self.local_python(root)
            self.assertEqual(build_command(root=root), [
                str(interpreter), str(root / "mcps/copilot_mcp.py"),
            ])
            self.assertEqual(build_command("cli", ["context", "--sleeve", "gold"], root=root), [
                str(interpreter), str(root / "scripts/copilot_cli.py"), "context", "--sleeve", "gold",
            ])

    def test_bad_manifest_fails_without_fallback_or_revealing_its_contents(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            interpreter = self.local_python(root)
            manifest = root / "data/state/runtime/copilot-runtime.json"
            bad = [
                '{"schema_version":1,"python":"fixture-private-path",',
                "[]",
                json.dumps({"schema_version": True, "python": "ibkr-venv/Scripts/python.exe"}),
                json.dumps({"schema_version": 2, "python": "ibkr-venv/Scripts/python.exe"}),
                json.dumps({"schema_version": 1, "python": "ibkr-venv/Scripts/python.exe", "command": "fixture-secret"}),
                '{"schema_version":1,"python":"fixture-private-path","python":"ibkr-venv/Scripts/python.exe"}',
                json.dumps({"schema_version": 1, "python": "../fixture-private-path"}),
                json.dumps({"schema_version": 1, "python": "C:/fixture-private-path/python.exe"}),
                json.dumps({"schema_version": 1, "python": "C:fixture-private-path/python.exe"}),
                json.dumps({"schema_version": 1, "python": "//fixture-private-path/server/python.exe"}),
                json.dumps({"schema_version": 1, "python": "/fixture-private-path/python"}),
                json.dumps({"schema_version": 1, "python": "ibkr-venv/Scripts/missing-python.exe"}),
                json.dumps({"schema_version": 1, "python": "ibkr-venv/Scripts"}),
                json.dumps({"schema_version": 1, "python": 2}),
                " " * 5000,
            ]
            for text in bad:
                with self.subTest(text=text):
                    manifest.write_text(text, encoding="utf-8")
                    with self.assertRaises(ValueError) as caught:
                        build_command(root=root)
                    self.assertRegex(str(caught.exception), r"^copilot_runtime_manifest_[a-z_]+$")
                    self.assertNotIn("fixture-private-path", str(caught.exception))
                    self.assertNotIn(str(interpreter), str(caught.exception))

    def test_real_child_preserves_stdio_environment_arguments_and_exit_code(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            runtime = root / "data/state/runtime"
            environment = runtime / "fixture-venv"
            venv.EnvBuilder(with_pip=False, symlinks=False).create(environment)
            relative = "fixture-venv/Scripts/python.exe" if os.name == "nt" else "fixture-venv/bin/python"
            (runtime / "copilot-runtime.json").write_text(json.dumps({
                "schema_version": 1, "python": relative,
            }), encoding="utf-8")
            script = root / "scripts/copilot_cli.py"
            script.parent.mkdir()
            script.write_text(
                "import json,os,sys\n"
                "request=json.loads(sys.stdin.buffer.read())\n"
                "response={'received':request,'arguments':sys.argv[1:],'cwd':os.getcwd(),"
                "'environment':os.environ.get('COPILOT_RUNTIME_TEST')}\n"
                "sys.stdout.buffer.write(json.dumps(response,ensure_ascii=False).encode('utf-8'))\n"
                "sys.stderr.buffer.write(b'fixture-child-stderr')\n"
                "sys.exit(7)\n", encoding="utf-8",
            )
            # The public launch interface is called in a child so inherited
            # binary descriptors are exercised instead of mocking subprocess.
            code = ("from pathlib import Path; from copilot_runtime import launch; "
                    "import sys; sys.exit(launch('cli',['fixture argument','quoted $ literal'],"
                    "root=Path(sys.argv[1])))")
            result = subprocess.run([sys.executable, "-c", code, str(root)],
                                    cwd=Path(__file__).resolve().parent,
                                    input=json.dumps({"query": "只读查询"}).encode("utf-8"),
                                    capture_output=True,
                                    env={**os.environ, "COPILOT_RUNTIME_TEST": "fixture-only-value"},
                                    check=False, timeout=30)
            self.assertEqual(result.returncode, 7)
            self.assertEqual(result.stderr, b"fixture-child-stderr")
            response = json.loads(result.stdout)
            self.assertEqual(response, {
                "received": {"query": "只读查询"},
                "arguments": ["fixture argument", "quoted $ literal"],
                "cwd": str(root), "environment": "fixture-only-value",
            })

    def test_manifest_and_process_errors_emit_only_named_stderr_errors(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            self.local_python(root)
            manifest = root / "data/state/runtime/copilot-runtime.json"
            manifest.write_text('{"python":"fixture-private-path",', encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                result = main(["mcp"], root=root)
            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "copilot_runtime_manifest_invalid\n")
            self.assertNotIn("fixture-private-path", stderr.getvalue())
            manifest.write_text(json.dumps({"schema_version": 1, "python": "ibkr-venv/Scripts/python.exe"}), encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with (contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr),
                  patch("copilot_runtime.subprocess.run", side_effect=OSError("fixture-private-path"))):
                result = main(["mcp"], root=root)
            self.assertEqual(result, 2)
            self.assertEqual(stdout.getvalue(), "")
            self.assertEqual(stderr.getvalue(), "copilot_runtime_start_failed\n")

    def test_targets_and_argument_types_cannot_select_arbitrary_programs(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            for target, arguments in (("arbitrary-private-script.py", []), ("mcp", ["--arbitrary"]),
                                      ("cli", "not-an-argument-list"), ("cli", [None])):
                with self.subTest(target=target, arguments=arguments), self.assertRaises(ValueError):
                    build_command(target, arguments, root=root)

    def test_resolved_interpreter_cannot_escape_the_fixed_runtime_directory(self):
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            interpreter = self.local_python(root)
            outside = root / "outside-python.exe"
            outside.write_bytes(b"fixture outside executable")
            interpreter.unlink()
            try:
                interpreter.symlink_to(outside)
            except OSError:
                self.skipTest("OS does not permit creating fixture symlinks")
            with self.assertRaisesRegex(ValueError, "copilot_runtime_manifest_path_invalid"):
                build_command(root=root)

    def test_canonical_catalog_and_generated_config_share_the_portable_entry(self):
        from sync_runtimes import generated_files
        project = Path(__file__).resolve().parent.parent
        active = json.loads((project / ".mcp.json").read_text(encoding="utf-8"))
        catalog = json.loads((project / ".mcp.json.template").read_text(encoding="utf-8"))
        expected = ["run", "--no-project", "--quiet", "--script", "scripts/copilot_runtime.py", "mcp"]
        for configuration in (active, catalog):
            server = configuration["mcpServers"]["trading-copilot"]
            self.assertEqual(server["command"], "uv")
            self.assertEqual(server["args"], expected)
        with tempfile.TemporaryDirectory(prefix="copilot-runtime-test-") as directory:
            root = Path(directory)
            output = generated_files(root=root, config=active)[root / ".codex/config.toml"]
            server = tomllib.loads(output)["mcp_servers"]["trading-copilot"]
            self.assertEqual(server["command"], "uv")
            self.assertEqual(server["args"], expected)
            self.assertNotIn(str(root), output)


if __name__ == "__main__":
    unittest.main()

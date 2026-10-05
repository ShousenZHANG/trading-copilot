# /// script
# requires-python = ">=3.11"
# ///
"""Launch the shared MCP or CLI using an optional, private local Python runtime."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path, PureWindowsPath

ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIRECTORY = Path("data/state/runtime")
MANIFEST_NAME = "copilot-runtime.json"
MAX_MANIFEST_BYTES = 4096


class RuntimeConfigurationError(ValueError):
    """A fixed error code, containing no local path or manifest payload."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest field")
        result[key] = value
    return result


def _runtime_python(root: Path) -> Path | None:
    runtime = root / RUNTIME_DIRECTORY
    manifest = runtime / MANIFEST_NAME
    try:
        manifest.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimeConfigurationError("copilot_runtime_manifest_unreadable") from None
    try:
        directory = runtime.resolve()
        if not directory.is_relative_to(root.resolve()) or not manifest.resolve().is_relative_to(directory):
            raise RuntimeConfigurationError("copilot_runtime_manifest_path_invalid")
        with manifest.open("rb") as stream:
            raw = stream.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise RuntimeConfigurationError("copilot_runtime_manifest_invalid")
        spec = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_object)
    except (OSError, RuntimeError):
        raise RuntimeConfigurationError("copilot_runtime_manifest_unreadable") from None
    except (ValueError, UnicodeError):
        raise RuntimeConfigurationError("copilot_runtime_manifest_invalid") from None
    if (not isinstance(spec, dict) or set(spec) != {"schema_version", "python"}
            or type(spec["schema_version"]) is not int or spec["schema_version"] != 1):
        raise RuntimeConfigurationError("copilot_runtime_manifest_invalid")
    relative = spec["python"]
    if (not isinstance(relative, str) or not relative or relative != relative.strip()
            or any(ord(char) < 32 for char in relative)
            or PureWindowsPath(relative).drive or PureWindowsPath(relative).root
            or ":" in relative or ".." in relative.replace("\\", "/").split("/")):
        raise RuntimeConfigurationError("copilot_runtime_manifest_path_invalid")
    try:
        interpreter = (directory / relative.replace("\\", "/")).resolve()
        if not interpreter.is_relative_to(directory):
            raise RuntimeConfigurationError("copilot_runtime_manifest_path_invalid")
        if not interpreter.is_file():
            raise RuntimeConfigurationError("copilot_runtime_manifest_python_missing")
    except (OSError, RuntimeError):
        raise RuntimeConfigurationError("copilot_runtime_manifest_path_invalid") from None
    return interpreter


def build_command(target: str = "mcp", arguments=(), *, root: Path = ROOT) -> list[str]:
    """Choose one shared runtime; never put a local machine path in MCP config."""
    scripts = {"mcp": "mcps/copilot_mcp.py", "cli": "scripts/copilot_cli.py"}
    if not isinstance(target, str) or target not in scripts:
        raise RuntimeConfigurationError("copilot_runtime_target_invalid")
    if (not isinstance(arguments, (list, tuple))
            or any(not isinstance(value, str) or "\0" in value for value in arguments)
            or (target == "mcp" and arguments)):
        raise RuntimeConfigurationError("copilot_runtime_arguments_invalid")
    interpreter = _runtime_python(root)
    prefix = [str(interpreter)] if interpreter is not None else [
        "uv", "run", "--no-project", "--quiet", "--script",
    ]
    return [*prefix, str(root / scripts[target]), *arguments]


def launch(target: str = "mcp", arguments=(), *, root: Path = ROOT,
           inherited: dict[str, str] | None = None) -> int:
    """Inherit binary stdio and environment, and return the child's exit status."""
    return subprocess.run(build_command(target, arguments, root=root), cwd=root,
                          env=inherited, check=False).returncode


def main(argv: list[str] | None = None, *, root: Path = ROOT) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    target = arguments.pop(0) if arguments else "mcp"
    try:
        return launch(target, arguments, root=root)
    except RuntimeConfigurationError as error:
        print(str(error), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("copilot_runtime_start_failed", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())

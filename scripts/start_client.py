"""Portable launcher. Parse .env as data, never source it as shell code."""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path


def client_command(client):
    """Prefer the newest installed native Codex; never change the user's model."""
    found = shutil.which(client)
    if client != "codex" or os.name != "nt":
        if not found:
            raise FileNotFoundError(f"{client} executable not found")
        return [found]
    candidates = []
    if found and Path(found).suffix.lower() == ".exe":
        candidates.append(Path(found))
    desktop = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin"
    if desktop.is_dir():
        candidates.extend(desktop.glob("*/codex.exe"))
    if found:
        package = Path(found).parent / "node_modules/@openai/codex"
        candidates.extend(package.glob("node_modules/@openai/codex-*/vendor/*/bin/codex.exe"))
    versions = []
    for candidate in set(candidates):
        try:
            result = subprocess.run([str(candidate), "--version"], capture_output=True, text=True,
                                    timeout=5, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
            match = re.search(r"(\d+)\.(\d+)\.(\d+)", result.stdout)
            if match:
                versions.append((tuple(map(int, match.groups())), str(candidate)))
        except (OSError, subprocess.SubprocessError):
            continue
    if versions:
        return [max(versions)[1]]
    if found:
        entry = Path(found).parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if node and entry.exists():
            return [node, str(entry)]
    raise FileNotFoundError("native Codex runtime unavailable; install a current Codex CLI")


def main():
    root = Path(__file__).resolve().parent.parent
    args = sys.argv[1:]
    client = args.pop(0) if args and args[0] in {"claude", "codex"} else "claude"
    path = root / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.fullmatch(r"\s*([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*", line)
            if match:
                key, value = match.groups()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value:
                    os.environ.setdefault(key, value)
    return subprocess.run([*client_command(client), *args], cwd=root, check=False).returncode

if __name__ == "__main__":
    raise SystemExit(main())

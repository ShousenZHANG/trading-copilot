"""Run the explicit, offline self-test contract shared by local checks and CI."""
from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODULES = (
    "evals/financebench/runner.py",
    "evals/scorer.py",
    "evals/stockbench/backtest_engine.py",
    "evals/stockbench/runner.py",
    "mcps/akshare_mcp.py",
    "scripts/backtest_cli.py",
    "scripts/copilot/journal.py",
    "scripts/enable_mcp.py",
    "scripts/mcp_handshake.py",
    "scripts/notify.py",
    "scripts/package_release.py",
    "scripts/parse_rating.py",
)


def exposes_self_test(text: str) -> bool:
    """Detect a flag registration/branch in code, excluding comments/docstrings."""
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "add_argument" and any(
                isinstance(arg, ast.Constant) and arg.value == "--self-test"
                for arg in node.args
            ):
                return True
        if isinstance(node, ast.Compare) and any(
            isinstance(item, ast.Constant) and item.value == "--self-test"
            for item in ast.walk(node)
        ):
            return True
    return False


def validate(root: Path = ROOT, modules: tuple[str, ...] = MODULES) -> list[str]:
    """Refuse stale entries and new self-test interfaces omitted from the list."""
    problems = []
    declared = set(modules)
    if len(declared) != len(modules):
        problems.append("duplicate self-test module in scripts/self_tests.py")
    found = set()
    for folder in ("scripts", "evals", "mcps"):
        for path in (root / folder).rglob("*.py"):
            relative = path.relative_to(root).as_posix()
            if path.name.startswith("_test_") or relative == "scripts/self_tests.py":
                continue
            try:
                if exposes_self_test(path.read_text(encoding="utf-8-sig")):
                    found.add(relative)
            except SyntaxError:
                problems.append(f"cannot inspect self-test interface: {relative}")
    problems += [f"self-test interface missing from MODULES: {name}"
                 for name in sorted(found - declared)]
    problems += [f"listed module has no self-test interface: {name}"
                 for name in sorted(declared - found)]
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="validate the list without execution")
    args = parser.parse_args()
    problems = validate()
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        return 1
    if args.check:
        print(f"Checked {len(MODULES)} offline self-test interfaces.")
        return 0
    failed = []
    interpreter_flags = (["-S"] if sys.flags.no_site else [])
    if sys.dont_write_bytecode:
        interpreter_flags.append("-B")
    for name in MODULES:
        print(f"Running {name}", flush=True)
        try:
            result = subprocess.run([sys.executable, *interpreter_flags, str(ROOT / name), "--self-test"],
                                    cwd=ROOT, timeout=300, check=False)
            if result.returncode:
                failed.append(name)
        except (OSError, subprocess.TimeoutExpired):
            failed.append(name)
    print(f"{len(MODULES) - len(failed)}/{len(MODULES)} self-test modules passed.")
    if failed:
        print("Failed: " + ", ".join(failed), file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

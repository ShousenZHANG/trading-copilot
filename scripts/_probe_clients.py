"""Opt-in real Claude/Codex integration probe using current auth/default models.

Runs model-backed clients, so it consumes account usage. Every ledger/config is
temporary; no production holdings, persistent client settings or transcripts are
written. No permission/sandbox bypass is used. Run manually, never in offline CI:
``python scripts/_probe_clients.py``. Each client is bounded to 150 seconds.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ["get_investment_context", "record_investment_operation"]
CLIENT_EXECUTABLES = {}


def safe_text(text):
    for key, value in os.environ.items():
        if value and len(value) >= 8 and any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY")):
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)(token|api_key|secret|password)=([^\s&\"']+)", r"\1=[redacted]", text)
    return text


def executable(name):
    if name in CLIENT_EXECUTABLES:
        candidate = Path(CLIENT_EXECUTABLES[name]).resolve(strict=True)
        if not candidate.is_file():
            raise FileNotFoundError(f"{name} executable is not a file")
        return [str(candidate)]
    if name == "codex":
        from start_client import client_command
        return client_command(name)
    candidate = shutil.which(name)
    if candidate and Path(candidate).suffix.lower() in {".exe", ""}:
        return [candidate]
    if name == "codex" and candidate:
        base = Path(candidate).parent / "node_modules" / "@openai" / "codex"
        candidates = list(base.glob("node_modules/@openai/codex-*/vendor/*/bin/codex.exe"))
        if len(candidates) == 1:
            return [str(candidates[0])]
        entry = base / "bin" / "codex.js"
        node = shutil.which("node")
        if node and entry.exists():
            return [node, str(entry)]
    raise FileNotFoundError(f"native {name} executable unavailable")


def _walk(value):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def run_client(name, args, prompt, cwd, env, timeout):
    started = time.monotonic()
    process = subprocess.Popen(executable(name) + args, cwd=cwd, env=env,
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace",
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    timed_out = False
    try:
        stdout, stderr = process.communicate(prompt, timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        if os.name == "nt":
            subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/taskkill.exe"), "/PID", str(process.pid), "/T", "/F"], capture_output=True, timeout=15)
        else:
            process.kill()
        stdout, stderr = process.communicate(timeout=15)
    tool_calls, final, errors, models = [], [], [], set()
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        for item in _walk(event):
            kind = item.get("type", "")
            if kind == "tool_use" and item.get("name"):
                tool_calls.append(item["name"])
            if kind == "mcp_tool_call":
                tool_calls.append(str(item.get("server", "")) + ":" + str(item.get("tool", "")))
                if item.get("error") or item.get("status") == "failed":
                    errors.append(str(item.get("error") or "MCP tool call failed"))
            if kind == "result" and isinstance(item.get("result"), str):
                final.append(item["result"])
            if kind == "agent_message" and isinstance(item.get("text"), str):
                final.append(item["text"])
            if kind == "error":
                errors.append(str(item.get("message", item)))
            if item.get("is_error"):
                errors.append(str(item.get("result", item.get("content", "tool error"))))
            if isinstance(item.get("model"), str):
                models.add(item["model"])
    # Only bounded sanitized summaries leave process memory; no raw transcript.
    result = {"client": name, "exit_code": process.returncode, "timed_out": timed_out,
              "seconds": round(time.monotonic() - started, 2), "tools": sorted(set(tool_calls)),
              "models": sorted(models), "final": safe_text("\n".join(final))[-1600:],
              "errors": [safe_text(error)[-800:] for error in errors[:4]]}
    if (process.returncode != 0 or not tool_calls) and not errors and not final:
        result["diagnostic"] = safe_text(stderr + "\n" + stdout)[-2400:]
    return result


def probe(timeout=150, skip_claude=False):
    timeout = min(max(timeout, 10), 150)
    with tempfile.TemporaryDirectory(prefix="copilot-client-probe-") as directory:
        workspace = Path(directory)
        database = workspace / "fixture.sqlite"
        env = dict(os.environ, COPILOT_DB_PATH=str(database), PYTHONUTF8="1")
        uv = shutil.which("uv")
        if not uv:
            raise FileNotFoundError("uv not available")
        server = {"command": uv, "args": ["run", "--no-project", "--quiet", "--script", str(ROOT / "mcps/copilot_mcp.py")], "env": {"COPILOT_DB_PATH": str(database)}}
        config_path = workspace / "mcp.json"
        config_path.write_text(json.dumps({"mcpServers": {"trading-copilot": server}}), encoding="utf-8")
        operation = {"statement": "我已经买了0.1股QQQ，成交价100美元。", "execution_status": "executed",
                     "instrument_id": "QQQ", "side": "buy", "quantity": "0.1", "unit": "share", "price": "100",
                     "currency": "USD", "occurred_at": "2026-01-02", "fees": "0", "account_id": "isolated-client-fixture",
                     "external_trade_id": "fixture-fill-1", "source_message_id": "claude-probe-1"}
        prefix = ("This is an explicitly authorized integration test in a disposable database, not a real investment action. "
                  "The configured trading-copilot MCP server has COPILOT_DB_PATH pointing to an isolated temporary fixture.sqlite. "
                  "Use only its get_investment_context and record_investment_operation tools. Do not use shell, files, web, other servers, or market analysis. ")
        writer = prefix + "Call get_investment_context, then record_investment_operation with idempotency_key='client-fixture-1' and operation=" + json.dumps(operation, ensure_ascii=False) + ". This operation is a synthetic harness fixture; preserve its statement exactly. Then call get_investment_context again and return operation status, operation ID, quantity and gross cost basis from actual tool results. Never claim completion without a successful receipt."
        allowed = ",".join("mcp__trading-copilot__" + tool for tool in TOOLS)
        claude = {"client": "claude", "exit_code": None, "tools": [], "skipped": True} if skip_claude else run_client("claude", ["--print", "--no-session-persistence", "--output-format", "stream-json", "--verbose",
                                      "--strict-mcp-config", "--mcp-config", str(config_path), "--tools", "", "--allowedTools", allowed,
                                      "--permission-mode", "dontAsk"], writer, workspace, env, timeout)
        print(json.dumps(claude, ensure_ascii=False), flush=True)

        # Ignore user MCP/plugin definitions for this invocation. Preserve model
        # preferences explicitly; auth still reads the existing CODEX_HOME.
        codex_home = Path(os.environ.get("CODEX_HOME", str(Path.home() / ".codex")))
        user_path = codex_home / "config.toml"
        config = tomllib.loads(user_path.read_text(encoding="utf-8")) if user_path.exists() else {}
        args = ["exec", "--ephemeral", "--json", "--skip-git-repo-check", "--sandbox", "read-only", "--ignore-user-config", "-C", str(workspace)]
        for name in ("model", "model_reasoning_effort", "model_reasoning_summary"):
            if name in config:
                args += ["-c", name + "=" + json.dumps(config[name])]
        if config.get("model_provider"):
            raise ValueError("non-default model provider requires an explicitly reviewed temporary configuration")
        for field, value in dict(server, enabled=True, required=True, enabled_tools=TOOLS, startup_timeout_sec=45, tool_timeout_sec=45).items():
            if field == "env":
                for variable, content in value.items():
                    args += ["-c", "mcp_servers.trading-copilot.env." + variable + "=" + json.dumps(content)]
            else:
                args += ["-c", "mcp_servers.trading-copilot." + field + "=" + json.dumps(value)]
        # Equivalent to Claude's --allowedTools: only the two explicitly
        # authorized fixture tools are approved for this ephemeral invocation.
        # Noninteractive exec otherwise denies MCP calls under policy=never.
        for tool in TOOLS:
            args += ["-c", "mcp_servers.trading-copilot.tools." + tool + '.approval_mode="approve"']
        reader = prefix + "Call get_investment_context for QQQ. Read the operation previously written by Claude in this shared temporary database. If it is absent because Claude could not authenticate, independently record this exact harness fixture using record_investment_operation with idempotency_key='codex-client-fixture-1', operation=" + json.dumps(operation, ensure_ascii=False) + ". Then get context again. Return actual operation ID, status, quantity, gross cost basis and account_id from tool results; do not attribute a Codex-created fixture to Claude."
        codex = run_client("codex", args, reader, workspace, env, timeout)
        print(json.dumps(codex, ensure_ascii=False), flush=True)
        restart = run_client("codex", args, prefix + "This is a new client process. Call get_investment_context for QQQ and read the existing fixture. Do not write. Return actual operation ID, quantity and cost from the tool response; report absence honestly.", workspace, env, timeout) if codex["exit_code"] == 0 else {"client": "codex-restart", "exit_code": None, "tools": [], "skipped": True}
        print(json.dumps(dict(restart, client="codex-restart"), ensure_ascii=False), flush=True)
        sys.path.insert(0, str(ROOT / "scripts"))
        from copilot.journal import get_context
        context = get_context(db_path=database)
        holdings = context["holdings"]
        committed = len(holdings) == 1 and holdings[0]["quantity"] == "0.1" and holdings[0]["gross_cost_basis"] == "10" and holdings[0]["account_id"] == "isolated-client-fixture"
        read_tool = any("get_investment_context" in tool for tool in codex["tools"])
        write_tool = any("record_investment_operation" in tool for tool in claude["tools"])
        operation_id = context["operations"][0]["operation_id"] if context["operations"] else None
        codex_confirmed = bool(operation_id and operation_id in codex.get("final", "") and not codex.get("errors"))
        restart_confirmed = bool(operation_id and operation_id in restart.get("final", "") and not restart.get("errors"))
        summary = {"dual_client_pass": committed and read_tool and write_tool and codex_confirmed and claude["exit_code"] == codex["exit_code"] == 0,
                   "codex_independent_pass": committed and read_tool and codex_confirmed and restart_confirmed and any("get_investment_context" in tool for tool in restart["tools"]) and codex["exit_code"] == restart["exit_code"] == 0,
                   "fixture_committed": committed, "claude_record_tool_called": write_tool, "codex_context_tool_called": read_tool,
                   "operation_count": len(context["operations"]), "temporary_state_only": True}
        print(json.dumps(summary), flush=True)
        return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=int, default=150)
    parser.add_argument("--skip-claude", action="store_true", help="Skip retrying a known Claude authentication failure")
    parser.add_argument("--codex-executable", help="Use an already-installed newer Codex binary without changing PATH or model")
    options = parser.parse_args()
    if options.codex_executable:
        CLIENT_EXECUTABLES["codex"] = options.codex_executable
    result = probe(options.timeout, options.skip_claude)
    raise SystemExit(0 if result["dual_client_pass"] else 1)

# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp[cli]>=1.2.0,<2", "yfinance==1.7.0", "exchange-calendars==4.13.2", "tzdata==2026.3"]
# ///
"""Real MCP tool/restart smoke test. All writes use a temporary fixture database."""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from copilot import journal
from copilot.data_calendar import MarketCalendar
from copilot.market_data import snapshot_digest
from _test_policy import fixture, proposal

ROOT = Path(__file__).resolve().parent.parent

def decode(result):
    if result.isError:
        raise RuntimeError("MCP tool returned isError")
    if result.structuredContent is not None:
        return result.structuredContent
    return json.loads(next(item.text for item in result.content if item.type == "text"))

async def call(session, name, arguments):
    return decode(await session.call_tool(name, arguments))

async def run(live=False, symbols=None, trace=False, allow_us_stocks=False):
    results = {"fixture_state": "temporary_only", "checks": []}
    with tempfile.TemporaryDirectory(prefix="copilot-probe-") as directory:
        db = Path(directory) / "state/copilot.sqlite"
        now = datetime.now(timezone.utc)
        snapshot = fixture()
        # Fixture prices are synthetic; current timestamps only exercise freshness gates.
        expected, _ = MarketCalendar().window(snapshot["instruments"]["QQQ"], now)
        snapshot.update(created_at=now.isoformat(), decision_at=now.isoformat(),
                        valid_until=(now + timedelta(minutes=10)).isoformat())
        snapshot["instruments"]["QQQ"].update(latest_session=expected, expected_session=expected)
        snapshot["evidence"][0].update(observed_at=MarketCalendar().close(snapshot["instruments"]["QQQ"], expected).isoformat(),
                                       retrieved_at=now.isoformat())
        snapshot["snapshot_id"] = snapshot_digest(snapshot)
        journal.save_snapshot(snapshot, db_path=db)
        server_args = [str(ROOT / "mcps/copilot_mcp.py")]
        if trace:
            server_args = ["-c", "import faulthandler,runpy; faulthandler.dump_traceback_later(30,repeat=True); "
                           + "runpy.run_path(" + repr(server_args[0]) + ",run_name='__main__')"]
        params = StdioServerParameters(command=sys.executable, args=server_args,
                    cwd=str(ROOT), env={**os.environ, "COPILOT_DB_PATH": str(db),
                                        "COPILOT_CONFIG_PATH": str(Path(directory) / "absent.toml")})
        operation = {"statement": "I already bought 0.1 QQQ shares at USD 100.",
                     "execution_status": "executed", "instrument_id": "QQQ", "side": "buy",
                     "quantity": "0.1", "price": "100", "unit": "share", "currency": "USD",
                     "occurred_at": expected, "source_message_id": "probe-fixture-1", "fees": None}
        recommendation_count = 1
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write, read_timeout_seconds=timedelta(seconds=240)) as session:
                await session.initialize()
                listed = await session.list_tools()
                required = {"collect_market_snapshot", "get_evidence_snapshot", "get_investment_context",
                            "assess_investment_proposal", "record_investment_operation", "get_data_capabilities",
                            "declare_holdings_coverage", "evaluate_adopted_rule", "get_operation_context",
                            "analyze_market_signals", "scan_investment_opportunities",
                            "collect_broker_snapshot", "prepare_manual_trade_plan", "get_manual_trade_plan",
                            "confirm_manual_plan_review", "revalidate_manual_trade_plan", "confirm_account_cash_flow",
                            "confirm_holding_modes", "confirm_strategy_execution_state"}
                assert required <= {tool.name for tool in listed.tools}
                results["checks"].append("initialize_and_tools_list")
                empty = await call(session, "get_investment_context", {})
                assert empty["holdings"] == []
                assert "operations" not in empty and empty["view"] == "sanitized_advisor_context"
                broker = await call(session, "collect_broker_snapshot", {"instrument_ids": ["QQQ"]})
                assert broker["status"] == "blocked" and "broker_disabled" in broker["issues"]
                results["checks"].append("sanitized_context_and_disabled_broker")
                capability = await call(session, "get_data_capabilities", {})
                assert capability["broker"]["order_submission"] is False
                assert capability["advisor"]["strategy_templates"] == ["long_term_trend", "swing_breakout"]
                rejected = await session.call_tool("confirm_strategy_execution_state", {
                    "execution_snapshot_id": broker["snapshot_id"], "rule_id": "rule-0000000000000000",
                    "mode": "long_term", "statement": "我确认该策略模式首次启动，此前没有实际成交",
                    "expected_account_version": "fixture-only", "no_prior_executions": True})
                assert rejected.isError
                results["checks"].append("capabilities_and_unadopted_strategy_state_rejection")
                signal = await call(session, "analyze_market_signals", {"snapshot_id": snapshot["snapshot_id"]})
                assert signal["signals"]["QQQ"]["execution_scope"] == "research_only"
                plan = await call(session, "prepare_manual_trade_plan", {
                    "research_snapshot_id": snapshot["snapshot_id"], "execution_snapshot_id": broker["snapshot_id"]})
                assert plan["plan"]["orders"] == [] and plan["plan"]["committed"]
                assert plan["plan"]["status"] == "blocked"
                results["checks"].append("research_signal_and_committed_blocked_plan")
                rejected = await session.call_tool("confirm_manual_plan_review", {
                    "plan_id": plan["plan"]["plan_id"], "statement": "我确认批准此方案",
                    "expected_version": plan["plan"]["version"]})
                assert rejected.isError
                results["checks"].append("blocked_card_review_rejection")
                decision = await call(session, "assess_investment_proposal",
                                      {"snapshot_id": snapshot["snapshot_id"], "proposal": proposal()})
                assert decision["decision"]["action"] == "buy", decision["decision"]["reasons"]
                assert decision["decision"]["execution_scope"] == "research_only"
                results["checks"].append("stored_evidence_assessment")
                args = {"operation": operation, "idempotency_key": "probe-key"}
                first = await call(session, "record_investment_operation", args)
                retry = await call(session, "record_investment_operation", args)
                assert first["status"] == "executed" and retry["replayed"]
                assert first["operation_id"] == retry["operation_id"]
                results["checks"].append("commit_and_idempotent_retry")
                coverage = await call(session, "declare_holdings_coverage", {})
                assert coverage["history"][0]["current"]
                results["checks"].append("declare_coverage")
                if live:
                    started = time.monotonic()
                    data = await call(session, "collect_market_snapshot",
                                      {"instrument_ids": symbols or ["QQQ", "^NDX", "^IXIC", "GOLD.CNY"],
                                       "allow_us_stocks": allow_us_stocks})
                    results["live"] = {"snapshot_id": data["snapshot_id"], "status": data["status"],
                        "elapsed_seconds": round(time.monotonic() - started, 2),
                        "instruments": {k: {"quality": v["quality_status"], "session": v.get("latest_session"),
                            "sample_count": v.get("indicators", {}).get("sample_count"),
                            "issues": v["issues"]} for k,v in data["instruments"].items()},
                        "research_issues": data.get("research_issues", [])}
                    results["live"]["assessments"] = {}
                    for symbol, item in data["instruments"].items():
                        p = proposal(symbol, "hold")
                        p["reasons"] = ["Technical evidence only; missing research coverage stays unknown."]
                        p["evidence_ids"] = [source["evidence_id"] for source in item["sources"] if source.get("evidence_id")]
                        reviewed = await call(session, "assess_investment_proposal",
                                              {"snapshot_id": data["snapshot_id"], "proposal": p})
                        results["live"]["assessments"][symbol] = {k: reviewed["decision"].get(k)
                            for k in ("action", "data_status", "execution_scope", "reasons")}
                        recommendation_count += 1
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                current = await call(session, "get_investment_context", {})
                assert current["holdings"][0]["quantity"] == "0.1"
                assert "operations" not in current and current["operations_available_locally"]
                operation_view = await call(session, "get_operation_context", {"operation_id": first["operation_id"]})
                assert operation_view["operation"]["quantity"] == "0.1"
                assert "statement" not in operation_view["operation"]
                assert len(current["recommendations"]) == recommendation_count
                assert any("portfolio_changed" in d["invalidation_reasons"] for d in current["recommendations"])
                results["checks"].append("restart_persistence_and_stale_decision_marking")
    return results

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="also fetch real free providers; still temporary state")
    parser.add_argument("--symbols", nargs="+", help="optional live canary symbols")
    parser.add_argument("--trace", action="store_true", help="dump isolated server stacks to stderr after 30s")
    parser.add_argument("--allow-us-stocks", action="store_true", help="explicitly include common-stock research in live canaries")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.live, args.symbols, args.trace, args.allow_us_stocks)), ensure_ascii=False, indent=2))

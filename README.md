# Trading Copilot

Free daily investment research in Claude Code and Codex, with short Chinese answers and a shared local operation journal. Covers registered US ETFs (a closed whitelist in scripts/copilot/instruments.py), the Nasdaq-100 and Composite indexes as benchmarks, and Shanghai Gold Exchange Au99.99 gold in RMB.

Ask naturally: “QQQ 适合长期持有吗？” “现在适合积累金条吗？” “我已经买了两股 QQQ……” The assistant collects a fresh evidence snapshot, checks its quality, assesses a proposal and returns a brief sourced answer. An explicit completed purchase/sale is recorded; an intention or incomplete execution stays separate. No broker order is sent.

[中文说明](README_zh.md) · [Setup](docs/INSTALL.md) · [Data limits](docs/mcp-fallback.md) · [Design and source research](docs/research/conversation-copilot-plan-2026-09-06.md)

## Start

Install Python 3.11+, uv, and your chosen Claude Code/Codex CLI. Open this checkout, copy .env.example to .env if it does not already exist, and add free provider credentials there.

Windows:
```powershell
.\scripts\start.ps1 -Client claude
.\scripts\start.ps1 -Client codex
```

macOS/Linux:
```sh
sh scripts/start.sh claude
sh scripts/start.sh codex
```

Both clients use scripts/copilot through the trading-copilot MCP server and data/state/copilot.sqlite. A newly configured MCP server requires a new client session. CLI fallback uses the same core:
```sh
uv run --no-project --quiet --script scripts/copilot_cli.py capabilities
uv run --no-project --quiet --script scripts/copilot_cli.py snapshot QQQ
python scripts/copilot_cli.py context
```

## Data contract

| Research | Free sources / boundary |
|---|---|
| Registered US ETFs | Yahoo history + Nasdaq official latest close; eligible Alpaca SIP optional. Unknown tickers are rejected, not provisionally accepted. Corporate actions constrain comparisons |
| Nasdaq indexes | Yahoo plus official Nasdaq historical corroboration |
| China investment bullion | Official SGE Au99.99 daily and SHAU benchmark; retail quote remains separate |
| Company facts | SEC submissions/companyfacts with acceptance-time matching |
| News | Finnhub returned articles with publication cutoffs; empty response is not proof of no events |
| Macro | FRED series plus release/vintage metadata; unresolved freshness stays unknown |

The current free-source network and account entitlements determine coverage. Single-source or conflicting prices can return unknown; expired/failed evidence pauses dependent recommendations. No service can guarantee every future upstream quote. Raw and adjusted data stay distinct; 200-session indicators require sufficient valid daily history.

## State and outputs

Snapshots retain source lineage, timestamps and bars. Assessed decisions and actual operations have different records. Decimal accounting, idempotent retries, pending duplicate checks, corrections and reversals preserve history. Portfolio completeness and unknown fees stay visible; USD/CNY totals are not mixed without valid conversion.

Conversation and /gold produce concise answers from one validated snapshot. Order quantities and limit prices come from the policy engine evaluating rules you adopt in config/user.toml (see ADR-0004); nothing here promises a return.

Private state lives in gitignored data/state, data/runs and data/decisions. Back up a live journal with `python scripts/copilot_cli.py backup <destination>`. Credentials and private state are excluded from release archives.

## Development

Canonical prompts are in .claude. Run `python scripts/sync_runtimes.py` to generate .agents/skills, skills/ and .codex/config.toml. Drift checks, contract tests and runtime probes cover different guarantees:
```sh
python scripts/check.py
python -m unittest discover -s scripts -p "_test_*.py"
uv run --no-project --quiet --script scripts/copilot_probe.py
python scripts/package_release.py
```

Architecture concepts informed by [TradingAgents](https://github.com/TauricResearch/TradingAgents), [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund), [OpenBB](https://github.com/OpenBB-finance/OpenBB), and [Qlib](https://github.com/microsoft/qlib); implementation and licenses remain separate. See the dated source review for verified commits and adopted ideas.

[Educational/informational use; terms](DISCLAIMER.md).

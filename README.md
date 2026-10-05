# Trading Copilot

Investment research in Claude Code and Codex, short Chinese answers and a shared local operation journal. Covers registered US ETFs, explicitly provider-verified ordinary US stocks, Nasdaq benchmark indexes and SGE Au99.99 gold in RMB. Public research uses free sources; optional IBKR execution evidence may require market-data entitlements. Your chosen model/client may have its own usage costs.

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
| Ordinary US stock research | Explicit research route, Nasdaq common-share identity and supported US venue; unknown/preferred/ADR identity is refused |
| Nasdaq indexes | Yahoo plus official Nasdaq historical corroboration |
| China investment bullion | Official SGE Au99.99 daily and SHAU benchmark; retail quote remains separate |
| Company facts | SEC submissions/companyfacts with acceptance-time matching |
| News | Finnhub returned articles with publication cutoffs; empty response is not proof of no events |
| Macro | FRED series plus release/vintage metadata; unresolved freshness stays unknown |

The current free-source network and account entitlements determine coverage. Single-source or conflicting prices can return unknown; expired/failed evidence pauses dependent recommendations. No service can guarantee every future upstream quote. Raw and adjusted data stay distinct; 200-session indicators require sufficient valid daily history.

Research signals provide reproducible trend/momentum, prior-session volume and
reference trigger/invalidation levels for long-term and swing horizons. A bounded
candidate scan combines those observations with available filings/news/macro
evidence. Default model context is a sanitized summary; raw operations stay local.

Optional [IBKR read-only snapshots](docs/IBKR.md) supply actual quotes, settled
currency cash, positions and open-order reservations for adopted-rule manual
cards. The user Reviews, runs a fresh preflight and submits manually. Separate
stock/ETF long-term and swing templates require frozen history, cost/holdout
validation and actual user adoption; see [strategy validation](docs/STRATEGY_VALIDATION.md).
Research alone creates no funded orders, and no personal strategy was adopted
by this code upgrade.
Neither Review nor card expiry places or cancels broker orders.

## State and outputs

Snapshots retain source lineage, timestamps and bars. Assessed decisions and actual operations have different records. Decimal accounting, idempotent retries, pending duplicate checks, corrections and reversals preserve history. Portfolio completeness and unknown fees stay visible; USD/CNY totals are not mixed without valid conversion.

Conversation and /gold produce concise answers from validated evidence. Order quantities and limit prices come from the shared compiler: either an adopted strategy or an explicitly confirmed one-off ETF instruction. Neither path promises a return. See [the two paths and opening holdings](docs/adr/0010-user-directed-calculations-and-opening-holdings.md).

Private state lives in gitignored data/state, data/runs and data/decisions. Back up a live journal with `python scripts/copilot_cli.py backup <destination>`. Credentials and private state are excluded from release archives.

`/watchlist` stores personal choices in gitignored data/watchlist.local.md;
data/watchlist.md is a public supported example. For engine orders, follow the
[configuration, adoption, coverage and evaluation walkthrough](docs/INSTALL.md#adopt-a-rule-and-request-engine-orders).
Research works without an adopted rule. IBKR integration is read-only; there is no unattended scan runner or automatic order submission.

Version 0.6.0 adds confirmed opening holdings, one-off ETF budgets/target shares,
issuer/exchange QQQI/JEPQ distribution research, explicit withholding/FX scenarios
and constrained income comparisons. Research targets are not executable orders.
Run `python scripts/copilot_runtime.py cli doctor` for prerequisite gaps in the
selected local runtime (including the optional IBKR SDK).
See [workflow and release acceptance](docs/DELIVERY.md) for commands and limits.
Legacy adoption schema 4 requires recomputation with initial-cost and continuous
calendar coverage checks; older records remain readable but cannot fund new cards.

## Development

Canonical prompts are in .claude. Run `python scripts/sync_runtimes.py` to generate .agents/skills, skills/ and .codex/config.toml. Drift checks, contract tests and runtime probes cover different guarantees:
```sh
python scripts/check.py
python scripts/self_tests.py
python -m unittest discover -s scripts -p "_test_*.py"
uv run --no-project --quiet --script scripts/copilot_probe.py
python scripts/package_release.py
```

Architecture concepts informed by [TradingAgents](https://github.com/TauricResearch/TradingAgents), [ai-hedge-fund](https://github.com/virattt/ai-hedge-fund), [OpenBB](https://github.com/OpenBB-finance/OpenBB), and [Qlib](https://github.com/microsoft/qlib); implementation and licenses remain separate. See the dated source review for verified commits and adopted ideas.

[Educational/informational use; terms](DISCLAIMER.md).

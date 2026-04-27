# Trading Copilot

> Multi-agent trading research copilot, native to Claude Code. Stocks + Gold + Macro analysis with adversarial Bull/Bear debate, 3-way risk analysis, and self-reflection loop.

**Architecture**: Direct port of [TradingAgents](https://github.com/TauricResearch/TradingAgents) (53k+ stars) to Claude Code subagents + slash commands + MCP servers. No separate backend service.

> ⚠️ **Educational and informational use only. Not investment advice.** See [DISCLAIMER.md](./DISCLAIMER.md).

---

## Quick Start

```bash
git clone https://github.com/ShousenZHANG/trading-copilot.git D:/trading-copilot
cd D:/trading-copilot
cp .env.example .env   # fill in API keys for the MCP servers you want
```

Then in Claude Code:

```
/analyze NVDA       # Run full pipeline on a stock
/gold               # Analyze XAU/USD with macro emphasis
/scan               # Run watchlist
/watchlist add TSLA
/weekly-review      # Sunday deep review with Opus
/debate AAPL        # Force multi-round Bull/Bear debate
```

---

## Pipeline

```
Market Analyst   -> Social Analyst   -> News Analyst   -> Fundamentals Analyst
                                                                 |
                                          Bull Researcher <-> Bear Researcher
                                                                 |
                                                   Research Manager (Opus)
                                                                 |
                                                              Trader
                                                                 |
                              Aggressive -> Conservative -> Neutral Risk Debators
                                                                 |
                                                  Portfolio Manager (Opus)
                                                                 |
                                                Decision logged + reflected at T+5d
```

11 agents. 2-tier LLM (Opus for 2 managers, Sonnet for 9 others). 5-tier rating (Buy/Overweight/Hold/Underweight/Sell).

See [docs/tradingagents-deep-dive.md](./docs/tradingagents-deep-dive.md) for architecture details.

---

## Data Sources (MCP servers)

| Role | MCP | Cost |
|------|-----|------|
| Primary market data | [Polygon.io](https://github.com/polygon-io/mcp_polygon) | Free 5/min |
| Backup / quotes | [Yahoo Finance](https://github.com/Alex2Yang97/yahoo-finance-mcp) | Free |
| News + financials | [Finnhub](https://github.com/NimbleBrainInc/mcp-finnhub) | Free 60/min |
| Macro (Fed/CPI/yields) | [FRED](https://github.com/stefanoamorelli/fred-mcp-server) | Free unlimited |
| Gold spot | [metal-price MCP](https://github.com/isdaniel/mcp-metal-price) | Free 100/mo |
| Web research | [Exa](https://github.com/exa-labs/exa-mcp-server) | $10 free credits |
| Memory | [claude-mem](https://github.com/thedotmack/claude-mem) | Free local |

Configure in `.claude/settings.json` after copying `.env.example` -> `.env`.

---

## Project Layout

```
trading-copilot/
|-- .claude-plugin/plugin.json         # plugin manifest
|-- .claude/
|   |-- settings.json                  # MCP servers + permissions
|   `-- commands/                      # slash commands (/analyze, /gold, ...)
|-- agents/                            # 11 subagents
|   |-- analysts/                      #   market, social, news, fundamentals
|   |-- researchers/                   #   bull, bear
|   |-- managers/                      #   research-manager, portfolio-manager (Opus)
|   |-- risk-debators/                 #   aggressive, conservative, neutral
|   `-- trader.md
|-- skills/trading-copilot/SKILL.md    # methodology
|-- data/                              # human-readable persistence
|   |-- watchlist.md
|   |-- positions.md
|   |-- decisions/<TICKER>-<DATE>.md
|   |-- runs/<TICKER>-<DATE>/          # intermediate per-step outputs
|   `-- memory/trading_memory.md       # append-only decision log
|-- docs/                              # methodology + samples
|-- evals/                             # FinanceBench + StockBench harness
|-- reference/TradingAgents/           # upstream source for reference
`-- .github/workflows/                 # cron jobs (premarket scan, weekly review)
```

---

## Status

**Phase 0 (skeleton) - in progress.** See `TodoWrite` for live phase tracking.

Roadmap: 8 phases, ~4-5 days to ship a complete institutional-grade trading agent.

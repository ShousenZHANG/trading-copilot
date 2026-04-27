---
name: trading-copilot
description: Multi-agent trading research methodology. Use when the user asks for stock/gold/macro analysis, a buy/sell recommendation, watchlist scan, or weekly portfolio review. Triggers a sequential 4-analyst -> Bull/Bear debate -> Trader -> 3-way Risk debate -> Portfolio Manager pipeline based on TradingAgents (53k+ stars).
---

# Trading Copilot — Methodology

## When to invoke

Trigger this skill when the user:
- Asks to analyze a specific ticker (`/analyze NVDA`, "analyze TSLA", "what about AAPL?")
- Asks about gold or commodities (`/gold`, "what's the gold setup?")
- Asks for a watchlist scan or batch analysis
- Asks for a weekly portfolio review
- Mentions buy/sell/hold decision for any tradable instrument

Do **not** invoke for general market commentary or news questions — those don't need the full pipeline. Just the slash commands trigger the pipeline.

## Pipeline (strict order)

```
1. Setup
   - Resolve ticker; verify exchange suffix preserved (e.g. .HK, .T, =F)
   - Create run dir: data/runs/<TICKER>-<YYYY-MM-DD>/
   - Load past_context from data/memory/trading_memory.md (same-ticker entries + cross-ticker reflections)
   - Resolve any pending entries for this ticker (T+5d outcome reflection job)

2. Analysts (SEQUENTIAL, in order — each writes a markdown report)
   a. market-analyst       -> 01-market.md
   b. social-analyst       -> 02-social.md
   c. news-analyst         -> 03-news.md
   d. fundamentals-analyst -> 04-fundamentals.md
      (for /gold: replace fundamentals with macro-analyst -> 05-macro.md)

3. Bull/Bear debate (alternating, max_debate_rounds × 2 turns; default 1 round = 1 Bull + 1 Bear)
   - bull-researcher reads all analyst reports + prior bear arg
   - bear-researcher reads all analyst reports + prior bull arg
   - Append each turn to a debate_history.md

4. research-manager (Opus)
   - Reads debate_history
   - Outputs ResearchPlan: Recommendation (5-tier) + Rationale + Strategic Actions
   -> 06-research-plan.md

5. trader
   - Reads research_plan + analyst reports
   - Outputs TraderProposal: Action (3-tier) + Reasoning + Entry/Stop/Sizing
   -> 07-trader-proposal.md

6. Risk debate (3-way, fixed order: Aggressive -> Conservative -> Neutral, max_risk_rounds × 3 turns; default 1 round = 3 turns)
   - Each debator reads all analyst reports + trader_proposal + risk_debate_history
   - Append each turn to risk_debate_history.md

7. portfolio-manager (Opus)
   - Reads everything + past_context + positions.md
   - Runs pre-trade risk gate (concentration / correlation / liquidity / freshness / stop / drawdown)
   - Outputs PortfolioDecision: Rating (5-tier) + Exec Summary + Thesis + Target + Horizon
   -> 08-portfolio-decision.md
   - Appends pending entry to data/memory/trading_memory.md
   - Writes user-facing report to data/decisions/<TICKER>-<YYYY-MM-DD>.md

8. Output to user
   - Final markdown report with:
     - Headline rating + price target
     - Executive summary
     - Bull/Bear debate transcript (collapsible)
     - Trader proposal
     - Risk debate transcript (collapsible)
     - Portfolio Manager decision + thesis
     - Disclaimer footer
```

## Rating scales (DO NOT MIX)

- **Research Manager + Portfolio Manager** use 5-tier: `Buy / Overweight / Hold / Underweight / Sell`
- **Trader** uses 3-tier: `Buy / Hold / Sell`
  - Mapping: 5-tier `Buy/Overweight` → 3-tier `Buy`; `Hold` → `Hold`; `Underweight/Sell` → `Sell`

## Model assignment (cost optimization)

- **Opus** (deep_thinking): only `research-manager` + `portfolio-manager`. Highest stakes, structured output.
- **Sonnet** (quick_thinking): all 9 other agents (analysts, researchers, trader, risk debators).
- **Haiku** (background): reflection summarization, signal extraction.

## Memory contract

- `data/memory/trading_memory.md` is **append-only**. Never edit by hand. Two phases:
  - **Phase A (sync)** — Portfolio Manager appends `[date | ticker | rating | pending]` block at end of each run.
  - **Phase B (async, T+5 days)** — reflection job pulls real returns from yfinance, computes raw + alpha vs SPY, generates 2-4 sentence reflection, atomically replaces pending tag with `[date | ticker | rating | raw% | alpha% | days]` and appends `REFLECTION:` section.
- Past context format injected to Portfolio Manager prompt:
  ```
  Past analyses of <TICKER> (most recent first): <last N=5 same-ticker resolved entries>
  Recent cross-ticker lessons: <last N=3 other-ticker reflections>
  ```

## Language protocol

- **Internal debate** (Bull/Bear/Risk debators) stays in **English** for reasoning quality.
- **User-facing output** (analyst reports, Research Manager rationale, Trader reasoning, Portfolio Manager thesis) in **Chinese (中文)**.
- **Always preserve** ticker symbols, indicator names (RSI, MACD, ATR), price numbers, FRED series IDs in English.

## Pre-trade risk gate (Portfolio Manager enforces)

Before issuing **Buy** or **Overweight**, all checks must pass. Failure → downgrade rating with explicit reason.

| Check | Threshold |
|-------|-----------|
| Single-name concentration | ≤ 5% of portfolio |
| Sector concentration | ≤ 25% |
| Correlation to existing book | < 0.7 |
| Position size vs ADV | ≤ 1% |
| Data freshness (all reports) | ≤ 24h for daily horizon |
| Stop-loss is set | required |
| Portfolio max-drawdown | not in > 15% drawdown (else half-size all positions) |

## Disclaimer (always append to user-facing decisions)

> ⚠️ Educational and informational use only. Not investment advice. See [DISCLAIMER.md](../../DISCLAIMER.md) for full terms.

## File-system layout (per run)

```
data/runs/NVDA-2026-04-27/
├── 01-market.md
├── 02-social.md
├── 03-news.md
├── 04-fundamentals.md       (or 05-macro.md for /gold)
├── debate_history.md         (Bull/Bear transcript)
├── 06-research-plan.md
├── 07-trader-proposal.md
├── risk_debate_history.md   (3-way transcript)
└── 08-portfolio-decision.md

data/decisions/NVDA-2026-04-27.md   ← user-facing assembled report
data/memory/trading_memory.md        ← appended pending entry
```

## Reference

Architecture and prompt patterns derived from upstream [TradingAgents](https://github.com/TauricResearch/TradingAgents). See [docs/tradingagents-deep-dive.md](../../docs/tradingagents-deep-dive.md) for source-level analysis.

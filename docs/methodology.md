# Methodology

> Everything Trading Copilot does and why. The "what we believe" doc — built on TradingAgents (53k stars), big-tech standards (Goldman / BlackRock / Bloomberg patterns), and academic results (FinMem, StockBench, FinanceBench).

## Core beliefs

1. **LLMs are not price predictors.** They're synthesizers. Best use is reading lots of context (10-Ks, news, social, indicators) and surfacing the strongest narratives — both bull and bear. We bet on synthesis, not prediction.

2. **Adversarial debate beats single oracle.** A Bull/Bear pair forced to refute each other catches more failure modes than any single agent. Source: TradingAgents arXiv 2412.20138.

3. **Multiple specialist agents beat one generalist.** Splitting fundamentals / technical / sentiment / news / macro lets each have its own context, tools, and prompt. No single context bloat.

4. **The risk gate is non-bypassable.** Position sizing, concentration, correlation, liquidity, freshness checks BEFORE the recommendation is surfaced. A great thesis on an over-concentrated book is still a bad trade.

5. **Memory + reflection > stateless decisions.** Append-only decision log, T+5d outcome resolution, reflection re-injected into future PM prompts. The system gets less wrong over time.

6. **Layered LLM cost optimization.** Opus only for the 2 deciders (Research Manager + Portfolio Manager). Sonnet for the 9 analysts/researchers/trader/risk debators. Haiku for reflection summarization. ~50%+ cost reduction vs all-Opus.

7. **Provenance over prose.** Every claim must trace to a source: an analyst report, a tool result, a prior decision. No floating opinions.

8. **Educational use only.** Not advisory. Not registered. Output is a starting point for human judgment, not a substitute for it.

---

## Pipeline (12 steps)

```
0.  Setup (resolve ticker, load past_context, read positions)

ANALYSTS (sequential)
1.  market-analyst              -> 01-market.md       [Sonnet]
2.  social-analyst              -> 02-social.md       [Sonnet]
3.  news-analyst                -> 03-news.md         [Sonnet]
4.  fundamentals-analyst        -> 04-fundamentals.md [Sonnet]
    (or macro-analyst for /gold -> 05-macro.md       [Sonnet])

INVESTMENT DEBATE (1+ rounds)
5.  bull-researcher    -> append to debate_history.md  [Sonnet]
6.  bear-researcher    -> append to debate_history.md  [Sonnet]
    (loop steps 5-6 for max_debate_rounds × 2 turns)

7.  research-manager   -> 06-research-plan.md          [Opus]   ResearchPlan
8.  trader             -> 07-trader-proposal.md        [Sonnet] TraderProposal

RISK DEBATE (3-way, 1+ rounds)
9.  aggressive-debator    -> append to risk_debate     [Sonnet]
10. conservative-debator  -> append to risk_debate     [Sonnet]
11. neutral-debator       -> append to risk_debate     [Sonnet]
    (loop steps 9-11 for max_risk_rounds × 3 turns)

12. portfolio-manager  -> 08-portfolio-decision.md     [Opus]   PortfolioDecision
                       -> appends pending entry to memory log
                       -> writes user-facing decisions/<TICKER>-<DATE>.md
```

---

## Why sequential analysts (not parallel)

Each analyst's prompt focuses narrowly on its domain. Sequential means the news analyst can reference what the market analyst surfaced (e.g., "this technical breakdown coincides with the earnings date next week"). LangGraph state passes through. The cost of waiting is dominated by the cost of better cross-domain synthesis.

Parallel would be ~4× faster but the analysts would each work in isolation, missing material cross-references.

---

## Why 5-tier rating, not 3-tier

`Buy / Sell / Hold` is too coarse. Real institutional ratings use 5 tiers:

- **Buy** — strong conviction, full size
- **Overweight** — favorable, half size
- **Hold** — maintain
- **Underweight** — trim
- **Sell** — exit

The Trader downsamples to 3 (Buy/Hold/Sell) for execution, but the Research Manager and Portfolio Manager preserve the nuance.

---

## Why English debate, Chinese reports

TradingAgents finding (preserved in our `get_language_instruction`): **internal debate quality degrades** when the model is forced to reason in non-English. So:

- Internal Bull/Bear/Risk debate stays in English (best reasoning)
- User-facing analyst reports + final decision in target language (we default to Chinese, configurable)
- Tickers, indicator names, FRED series IDs, prices stay in English everywhere

---

## Memory log (append-only markdown)

Direct port of TradingAgents' design. **No vector DB.** Reasons:

- Personal trading volume is small (10s of decisions/month, not millions)
- Markdown is human-readable, git-diffable, grep-able, survives any tooling change
- Two-phase resolution maps cleanly to file-based atomic writes (write to `.tmp`, rename)

Schema (per entry):

```
[YYYY-MM-DD | TICKER | RATING | pending]   # initial
[YYYY-MM-DD | TICKER | RATING | +X.X% | +Y.Y% | Nd]   # after T+5 resolution

DECISION:
<full Portfolio Manager output>

REFLECTION:
<2-4 sentence retrospective from reflection prompt>

<!-- ENTRY_END -->
```

The `<!-- ENTRY_END -->` HTML comment is a hard delimiter that LLM output cannot accidentally produce.

---

## Reflection prompt (T+5d retrospective)

```
You are a trading analyst reviewing your own past decision now that the outcome is known.
Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).

Cover in order:
1. Was the directional call correct? (cite the alpha figure)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis.
```

Run with **Haiku** (cost). 2-4 sentences keeps the log compact so injecting last-N reflections into future Portfolio Manager prompts doesn't bloat context.

---

## past_context injection

Before the Portfolio Manager runs, the orchestrator pulls:

- Last 5 same-ticker resolved entries (full DECISION + REFLECTION)
- Last 3 cross-ticker reflections (REFLECTION only, brief)

Format:

```
Past analyses of <TICKER> (most recent first):
[2026-04-10 | NVDA | Overweight | +5.2% | +1.8% | 5d]
DECISION: ...
REFLECTION: 方向正确 (+5.2%, alpha+1.8%). 数据中心论点成立. 下次类似setup加大仓位.

Recent cross-ticker lessons:
[2026-04-12 | AMD | Buy | +3.1%]
半导体板块整体beta>1, 单一名建仓时记得check correlation.
```

The Portfolio Manager prompt explicitly instructs: "If past_context is provided, cite the lesson and how it informs this round."

---

## Pre-trade risk gate

Portfolio Manager enforces. Failure → automatic downgrade with explicit explanation.

| Check | Threshold | Why |
|-------|-----------|-----|
| Single-name | ≤ 5% portfolio | Concentration risk |
| Sector | ≤ 25% | Sector concentration |
| Correlation to book | < 0.7 | Hidden duplicate exposure |
| Position vs ADV | ≤ 1% | Liquidity / exit cost |
| Data freshness | ≤ 24h (daily horizon) | Avoid stale-data decisions |
| Stop-loss set | required | Define max loss before entry |
| Portfolio drawdown | not in > 15% DD | Halve sizes when drawdown active |

---

## Cost optimization

| Lever | Saving |
|-------|--------|
| Layered LLM (Opus 2 / Sonnet 9 / Haiku 1) | 40-60% vs all-Opus |
| Prompt caching (system prompts + tool defs) | 60-70% on repeat calls |
| Sequential analysts (vs parallel + redundant tool fetch) | 20-30% on tools |
| Skip-recent rule in /scan (24h gate) | Variable |
| Default 1 debate round (vs 3) | 60% on debate cost |

Personal MVP cost: $0.30-0.50 per `/analyze` run. Monthly $30-100 with daily scan + weekly review.

---

## Anti-patterns (consciously avoided)

- ❌ Single-LLM-call "oracle" decisions — always debate.
- ❌ Backtests on today's index constituents — survivorship bias.
- ❌ Close-of-day inputs to "intraday" signals — look-ahead bias.
- ❌ Free-form ticker generation — LLMs invent symbols. Always whitelist via run brief.
- ❌ Auto-execution without human confirmation or paper-account boundary — Reg BI exposure.
- ❌ Reporting only cumulative return — Sharpe / Sortino / max-DD are required.
- ❌ Vector DB for small-N personal use — append-only markdown is simpler and grep-able.
- ❌ Mocking the database in tests — same for data sources; eval against real APIs.

---

## Sources

### Architecture
- [TradingAgents](https://github.com/TauricResearch/TradingAgents) — direct port basis (53k stars)
- [TradingAgents arXiv 2412.20138](https://arxiv.org/abs/2412.20138)
- [FinMem arXiv 2311.13743](https://arxiv.org/abs/2311.13743) — layered memory pattern

### Risk + standards
- [FINRA Notice 24-09 (AI)](https://www.finra.org/rules-guidance/notices/24-09)
- [BlackRock Aladdin](https://www.blackrock.com/aladdin) — scenario stress patterns
- [FINOS Agent Decision Audit](https://air-governance-framework.finos.org/mitigations/mi-21_agent-decision-audit-and-explainability.html)

### Evaluation
- [FinanceBench](https://github.com/patronus-ai/financebench)
- [StockBench](https://stockbench.github.io/)

### Big-tech LLM patterns
- [Anthropic Claude Cookbook](https://github.com/anthropics/claude-cookbooks)
- [Anthropic prompt engineering best practices](https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices)

### Plugin ecosystem (related work)
- [tradermonty/claude-trading-skills](https://github.com/tradermonty/claude-trading-skills) (1.1k stars) — taxonomy
- [quant-sentiment-ai/claude-equity-research](https://github.com/quant-sentiment-ai/claude-equity-research) — Goldman-style report format
- [anthropics/financial-services-plugins](https://github.com/anthropics/financial-services-plugins) — official manifest format

# Trading Copilot

**English** · [中文](./README_zh.md)

> A multi-agent trading-research plugin for **Claude Code**. Four analysts → Bull/Bear debate → Trader → 3-way risk debate → Portfolio Manager. Stocks, ETFs, gold, macro.

A faithful port of [TradingAgents](https://github.com/TauricResearch/TradingAgents) (53k★) to native Claude Code subagents + slash commands + MCP data servers. No backend, no build step.

> ⚠️ **Educational use only. Not investment advice.** See [DISCLAIMER.md](./DISCLAIMER.md).

---

## Install (60 seconds)

1. **Download** the latest `trading-copilot-x.y.z.zip` from [Releases](https://github.com/ShousenZHANG/trading-copilot/releases).
2. **Unzip** anywhere, e.g. `~/trading-copilot`.
3. **Open the folder in [Claude Code](https://claude.com/claude-code)** (`claude` in that directory).
4. `cp .env.example .env` — add a free [Finnhub](https://finnhub.io) key (Yahoo Finance needs none).
5. Type `/advise NVDA`.

That's it. No install scripts, no dependencies beyond Claude Code + Python 3.

---

## Use it

| Command | What it does | Time / cost |
|---------|--------------|-------------|
| `/advise NVDA` | One Opus agent: full read + Buy/Hold/Sell call | ~5 min · $0.20–0.50 |
| `/analyze NVDA` | Full 12-agent pipeline (debate + risk + PM) | ~30 min · $1–3 |
| `/gold` | Gold pipeline (macro-driven) | ~30 min |
| `/scan` | Run the whole watchlist | varies |
| `/watchlist add TSLA` | Manage tickers | instant |
| `/weekly-review` | Resolve past calls, compute alpha, learn | ~10 min |

### Example

```
You:   /advise NVDA
Claude: NVDA — Buy (medium conviction)
        Entry $182–188 · Stop $171 · Target $230 (12mo) · Size ≤5%
        Why: data-center demand + reasonable forward P/E; RSI not overbought.
        Risk gate: all pass. Full report → data/decisions/NVDA-2026-06-01.md
```

Every run also writes a full markdown report under `data/decisions/`.

---

## Pipeline (`/analyze`)

```
Market · Social · News · Fundamentals   (4 analysts, parallel)
                  │
        Bull  ⇄  Bear   debate
                  │
        Research Manager (Opus)  → 5-tier rating
                  │
              Trader            → entry / stop / size
                  │
   Aggressive → Conservative → Neutral   (risk debate)
                  │
       Portfolio Manager (Opus)  → final decision + risk gate
                  │
        Logged → reflected at T+5 days
```

Opus runs the 2 deciders + `/advise`; Sonnet runs the rest. Adversarial debate surfaces failure modes a single oracle misses; the memory log makes it learn from past calls.

---

## Other runtimes (Claude.ai / ChatGPT)

The full pipeline needs Claude Code (subagents + MCP + filesystem). For **claude.ai** or **ChatGPT**, the agent prompts in `.claude/agents/` are portable — paste one (e.g. `investment-advisor.md`) as a system prompt / Custom GPT instruction, supply market data manually, and you get the reasoning framework without the automation. See [docs/INSTALL.md](./docs/INSTALL.md).

---

## Data sources (MCP)

Configured in `.mcp.json` (only `yahoo-finance` + `finnhub` active by default; rest ship disabled). Keys live in `.env` (gitignored). Toggle with `python scripts/enable_mcp.py <name>`.

| Data | Server | Cost |
|------|--------|------|
| Quotes / history | Yahoo Finance | free |
| News / financials / sentiment | Finnhub | free 60/min |
| Macro (Fed/CPI/yields) | FRED | free |
| Web research | Exa | free credits |

---

## Verify your install

```bash
python scripts/check.py        # repo health → prints "OK"
/advise NVDA                   # in Claude Code
```

---

## Links

- [docs/INSTALL.md](./docs/INSTALL.md) — full install (Claude Code / claude.ai / ChatGPT)
- [docs/methodology.md](./docs/methodology.md) — why adversarial debate + reflection
- [.claude/skills/trading-copilot/SKILL.md](./.claude/skills/trading-copilot/SKILL.md) — pipeline spec
- [LICENSE](./LICENSE) (MIT) · [DISCLAIMER.md](./DISCLAIMER.md)

> ⚠️ AI-generated research. Verify before acting. You bear all responsibility for your decisions.

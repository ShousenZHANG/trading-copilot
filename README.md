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

## Security & data provenance

Plugin directories do not vet the MCP servers a plugin ships. This section is the
audit surface — every claim below is checkable in this repo.

**1. Untrusted input is data, never instructions.**
All five analyst prompts (`market`, `social`, `news`, `fundamentals`, `macro`) and
`investment-advisor` carry an explicit policy: fetched news, social posts, filings,
and FOMC text are material **to extract from**, never directives to obey. A prompt
that tries to issue orders gets tagged `[suspicious directive content in <source>]`
and ignored. No agent may originate a buy/sell call from injected text.
→ `.claude/agents/analysts/*.md`, `.claude/agents/investment-advisor.md`

**2. `[UNSOURCED]` provenance tagging, machine-counted.**
Any number an agent cites that did **not** come from a tool result in that run must
be tagged `[UNSOURCED]`. `scripts/validate_outputs.py` counts the markers per
artifact and across the whole run, and warns past a soft cap of 3 so weak provenance
is visible to the Portfolio Manager before it rates anything.

**3. Keyless by design — it works with zero paid keys.**
Yahoo Finance needs no key. Event probabilities come from real-money markets via the
keyless Polymarket Gamma API (`scripts/polymarket_odds.py`), so agents cite
`market-implied P(x) = y%` instead of guessing. Reddit `.json` returns 403 to
bots, so the social analyst falls back to keyless Reddit RSS. Finnhub is the only
default server wanting a key, and its free tier is enough.

**4. No hardcoded secrets, anywhere.**
Keys live only in `.env` (gitignored) and are referenced as `${VAR}` inside
`.mcp.json`. `.env.example` is the sole committed template and holds placeholders.
`.claude/settings.json` additionally denies `Write`/`Edit` on `.env`.

**5. Your trading state never leaves your machine.**
`.gitignore` blocks `data/positions.md`, `data/runs/`, `data/memory/trading_memory.md`,
`data/decisions/`, `data/audit/`, `docs/strategy.md`, and `evals/results/`.
`scripts/check.py` **fails** if any of it becomes git-tracked. The release zip is
built from a fail-closed allow-list plus a post-build leak scan
(`scripts/package_release.py`) — unlisted paths are never shipped.
No telemetry: the only scripts that talk to the network are `polymarket_odds.py`
(public Polymarket API) and `notify.py` (opt-in Telegram push, inert unless you set
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`).

**6. Which MCP servers actually run.**
Only two ship enabled. Everything else is disabled behind a `_` name prefix and must
be turned on deliberately with `python scripts/enable_mcp.py <name>`.

| Server | Default | What it is | Key |
|--------|---------|-----------|-----|
| `yahoo-finance` | **enabled** | third-party `uvx yahoo-finance-mcp` | none |
| `finnhub` | **enabled** | `mcps/finnhub_mcp.py` — in this repo, readable in full | free Finnhub key |
| `_akshare` | disabled | `mcps/akshare_mcp.py` — in this repo, A-share/HK data | none |
| `_polygon` `_alpha-vantage` `_fred` `_gold` `_exa` `_tushare` `_claude-mem` | disabled | third-party servers | per-service |

`finnhub` and `akshare` are single-file Python wrappers we ship and you can read;
`yahoo-finance` and the remaining disabled entries are third-party code you should
evaluate yourself before enabling.

**7. It is not advice.** Outputs are AI-generated research for education only, with
no guarantee of accuracy. Read [DISCLAIMER.md](./DISCLAIMER.md) before acting on
anything this plugin prints.

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

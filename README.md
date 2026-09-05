# Trading Copilot

**English** · [中文](./README_zh.md)

> A multi-agent trading-research plugin for **Claude Code**. Four analysts → Bull/Bear debate → Trader → 3-way risk debate → Portfolio Manager. Stocks, ETFs, gold, macro.

A faithful port of [TradingAgents](https://github.com/TauricResearch/TradingAgents) [![upstream stars](https://img.shields.io/github/stars/TauricResearch/TradingAgents?style=flat&label=%E2%98%85&color=555)](https://github.com/TauricResearch/TradingAgents/stargazers) to native Claude Code subagents + slash commands + MCP data servers. No backend, no build step.

> ⚠️ **Educational use only. Not investment advice.** See [DISCLAIMER.md](./DISCLAIMER.md).

---

## Install (60 seconds)

1. **Download** the latest `trading-copilot-x.y.z.zip` from [Releases](https://github.com/ShousenZHANG/trading-copilot/releases).
2. **Unzip** anywhere, e.g. `~/trading-copilot`.
3. `cp .env.example .env` — add a free [Finnhub](https://finnhub.io) key (Yahoo Finance needs none).
4. **Start [Claude Code](https://claude.com/claude-code) from that folder** with the
   launcher, so `.env` reaches the MCP servers: `sh scripts/start.sh` (macOS /
   Linux / WSL) or `.\scripts\start.ps1` (Windows). Plain `claude` also works,
   but then `${VAR}` in `.mcp.json` resolves to nothing and Finnhub is keyless
   and broken.
5. Type `/advise NVDA`.

Requirements: Claude Code, Python 3, and [`uv`](https://docs.astral.sh/uv/) on
your PATH — both default MCP servers are spawned through `uv`/`uvx`. No install
script, no build step, nothing else.

Verify data actually flows: `python scripts/mcp_handshake.py --all` should print
`2/2 server(s) completed the handshake.`

---

## Security & data provenance

Plugin directories do not vet the MCP servers a plugin ships. This section is the
audit surface — every claim below is checkable in this repo.

**1. Untrusted input is data, never instructions.**
All five analyst prompts (`market`, `social`, `news`, `fundamentals`, `macro`) and
`investment-advisor` carry an explicit policy: fetched news, social posts, filings,
and FOMC text are material **to extract from**, never directives to obey. Text that
tries to issue orders is tagged and ignored, and no agent may originate a buy/sell
call from injected content — the analysts do not issue ratings at all.
→ `.claude/agents/analysts/*.md`, `.claude/agents/investment-advisor.md`

*Known gap*: `validate_outputs.py` counts the marker spelled
`[suspicious directive content …]`. Five of the six prompts emit that;
`market-analyst` emits `[suspicious content detected]` instead, so an injection
attempt caught by that one agent is ignored correctly but **is not counted** in
the run summary. Tracked, not yet fixed.

**2. `[UNSOURCED]` provenance tagging, machine-counted.**
Any number an agent cites that did **not** come from a tool result in that run must
be tagged `[UNSOURCED]`. `scripts/validate_outputs.py` counts the markers per
artifact and across the whole run, and warns past a soft cap of 3
(`UNSOURCED_SOFT_CAP = 3`) so weak provenance is visible to the Portfolio Manager
before it rates anything. It is a **warning**, not a hard gate.

**3. Runs on free tiers — but not keyless, and not dependency-free.**
Yahoo Finance needs no key, and event probabilities come from the keyless
Polymarket Gamma API (`scripts/polymarket_odds.py`) so agents cite
`market-implied P(x) = y%` instead of guessing; Reddit `.json` returns 403 to bots,
so the social analyst falls back to keyless Reddit RSS. But be precise about the
cost of entry: **`finnhub` is active by default and does not work without a
`FINNHUB_API_KEY`** (free tier, 60 req/min), and **both default servers are
launched through `uv`/`uvx`, which must be installed and on your PATH.** Free, yes.
Zero-setup, no.

**4. No hardcoded secrets, anywhere.**
Keys live only in `.env` (gitignored) and are referenced as `${VAR}` inside
`.mcp.json`. `.env.example` is the sole committed template and holds placeholders.
`.claude/settings.json` additionally denies `Read`/`Write`/`Edit` on `.env` and
`.env.*` with project-relative rules, and ships a least-privilege allow-list (no
blanket `Bash`, no bare `WebFetch`, no pre-approved `git commit`). Treat that as a
guardrail against an agent wandering into the file, not as a sandbox — anything you
approve at the permission prompt still runs.

**5. Your trading state never leaves your machine.**
`.gitignore` blocks `data/positions.md`, `data/runs/`, `data/memory/trading_memory.md`,
`data/decisions/`, `data/audit/`, `data/state/`, `docs/strategy.md`, and
`evals/results/`. `scripts/check.py` **fails** if any of it becomes git-tracked. The
release zip is built from a fail-closed allow-list plus a post-build leak scan
(`scripts/package_release.py`) — unlisted paths are never shipped, and `.github/` is
deliberately excluded. There are no scheduled workflows: recurring runs happen on
your machine, where the state and keys already live
([ADR-0002](./docs/adr/0002-local-scheduling-and-evidence-only-stubs.md)).
No telemetry. Scripts that touch the network at all, exhaustively:
`prices.py` (Yahoo quotes), `polymarket_odds.py` (public Polymarket API),
`mcp_handshake.py` (spawns the MCP servers to test them), and `notify.py` (opt-in
Telegram push, inert unless you set `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`).

**6. Which MCP servers actually run.**
`.mcp.json` contains exactly the servers Claude Code launches — **absence is the off
switch**; there is no disable prefix (an earlier release used a `_` prefix that
disabled nothing and started every server anyway). `.mcp.json.template` is the
catalog; `python scripts/enable_mcp.py <name>` copies an entry across, `--disable`
removes it. `python scripts/enable_mcp.py` with no argument prints the true state.

| Server | Default | What it is | Key |
|--------|---------|-----------|-----|
| `yahoo-finance` | **active** | third-party `uvx yahoo-finance-mcp` | none |
| `finnhub` | **active** | `mcps/finnhub_mcp.py` — in this repo, readable in full | free Finnhub key, **required** |
| `akshare` | catalog only | `mcps/akshare_mcp.py` — in this repo, A-share/HK data | none |
| `polygon` `alpha-vantage` `fred` `gold` `exa` `tushare` | catalog only | third-party servers | per-service |

That is the whole catalog — nine entries, two of them running.

`finnhub` and `akshare` are single-file Python wrappers we ship and you can read;
`yahoo-finance` and the rest are third-party code you should evaluate yourself
before enabling.

*Honest status on A-share / HK*: `akshare`'s 59-test `--self-test` passes, but it
covers **symbol parsing only and makes zero network calls**, and the server has
never been verified against live AkShare endpoints from this repo. Its endpoints
are mainland-China hosted and may be unreachable from your network. Run
`python mcps/akshare_mcp.py --probe` before trusting any `.SS` / `.SZ` / `.HK`
number it returns. Yahoo and Finnhub coverage of those listings is
thin-to-absent, so an empty result there means *not covered*, not *nothing
happened*.

**7. It is not advice.** Outputs are AI-generated research for education only, with
no guarantee of accuracy. Read [DISCLAIMER.md](./DISCLAIMER.md) before acting on
anything this plugin prints.

---

## Use it

Ten commands ship. Eight work; two are specifications that are **not implemented**
and are labelled as such in the command picker.

| Command | What it does | Time / cost |
|---------|--------------|-------------|
| `/portfolio` | Deterministic check of your actual holdings — P&L, drift, look-through, trigger lines, all in Python. Dispatches an agent **only** if a trigger fires | seconds · $0 on the no-trigger path |
| `/advise NVDA` | One Opus agent: full read + a rated call | ~5–10 min · $0.20–0.50 |
| `/analyze NVDA` | Full 12-agent pipeline (debate + risk + PM) | ~30–60 min · $1–3 |
| `/gold` | Gold pipeline (macro-analyst replaces fundamentals) | ~30–60 min · $1–3 |
| `/debate NVDA` | Force a multi-round Bull/Bear on existing analyst reports | ~10 min |
| `/scan` | Run the pipeline across the whole watchlist | varies with watchlist size |
| `/watchlist add TSLA` | Manage tickers (add / remove / list / tag) | instant |
| `/weekly-review` | Resolve past calls at T+5d, compute alpha, write lessons | ~10 min |
| `/earnings` | ⛔ **inactive — not implemented.** No earnings agent exists; the file is the activation spec | — |
| `/screen` | ⛔ **inactive — not implemented.** No screen agent exists; the file is the activation spec | — |

`/portfolio` is the cheap daily habit; `/advise` is the normal answer to "should I
buy this"; `/analyze` is for a position large enough to justify an hour and a few
dollars.

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

`.mcp.json` is the active set; `.mcp.json.template` is the catalog. Keys live in
`.env` (gitignored). Toggle with `python scripts/enable_mcp.py <name>`.

| Data | Server | Default | Cost |
|------|--------|---------|------|
| Quotes / history | Yahoo Finance | active | free, no key |
| News / financials / earnings | Finnhub | active | free 60/min, **key required** |
| Macro (Fed / CPI / yields) | FRED | catalog only | free key |
| Web + social research | Exa | catalog only | free starting credit |
| A-share / HK / index | AkShare | catalog only | free, no key — see the status note above |
| Options / forex, backup quotes | Polygon, Alpha Vantage | catalog only | free tiers |
| Spot gold | GoldAPI | catalog only | free 100/mo (Yahoo `GC=F` is a free substitute) |
| A-share (token-gated) | Tushare | catalog only | points quota |

Full signup links, free-tier limits and troubleshooting: [docs/mcp-setup.md](./docs/mcp-setup.md).
Fallback chains when a server is down: [docs/mcp-fallback.md](./docs/mcp-fallback.md).

---

## Verify your install

```bash
python scripts/check.py                  # repo health → prints "OK"
python scripts/mcp_handshake.py --all    # every MCP server answers a real handshake
/advise NVDA                             # in Claude Code
```

`check.py` checks shape; `mcp_handshake.py` checks that the data servers can
actually start and speak the protocol. Run both — a shape check cannot catch a
server that dies on import, which is exactly how a broken dependency pin once
went unnoticed.

---

## Links

- [docs/INSTALL.md](./docs/INSTALL.md) — full install (Claude Code / claude.ai / ChatGPT)
- [docs/mcp-setup.md](./docs/mcp-setup.md) — wire up a data server
- [docs/methodology.md](./docs/methodology.md) — why adversarial debate + reflection
- [CONTEXT.md](./CONTEXT.md) — domain glossary (ticker, run, decision, alpha, risk gate…)
- [docs/adr/](./docs/adr/) — architecture decisions, numbered and append-only
- [.claude/skills/trading-copilot/SKILL.md](./.claude/skills/trading-copilot/SKILL.md) — pipeline spec
- [LICENSE](./LICENSE) (MIT) · [DISCLAIMER.md](./DISCLAIMER.md)

> ⚠️ AI-generated research. Verify before acting. You bear all responsibility for your decisions.

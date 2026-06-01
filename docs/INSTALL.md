# Install Guide

Trading Copilot is a **Claude Code plugin**. The 12-agent pipeline relies on
three Claude Code capabilities that ordinary chat UIs do not have:

1. **Subagent dispatch** (the `Agent` tool) — the analysts, debaters, and
   managers run as isolated subagents.
2. **MCP tool servers** — live market data (yahoo-finance, finnhub, …).
3. **Slash commands + filesystem** — `/analyze`, `/advise`, run artifacts.

Pick the install path that matches where you want to run it.

---

## Path A — Claude Code (native, full pipeline) ✅ recommended

Everything works: all slash commands, all 12 agents, live MCP data.

### From the release zip

1. Download `trading-copilot-<version>.zip` from the GitHub Releases page.
2. Unzip into a folder, e.g. `D:/trading-copilot`.
3. `cp .env.example .env` and fill in the API keys for the MCP servers you want
   (Finnhub is free; yahoo-finance needs no key).
4. Open the folder in Claude Code.
5. Run `/advise NVDA` to smoke-test, or `/analyze NVDA` for the full pipeline.

### From git

```bash
git clone https://github.com/ShousenZHANG/trading-copilot.git
cd trading-copilot
cp .env.example .env   # add keys
# open in Claude Code
```

### As a Claude Code plugin (marketplace-style)

The repo ships `.claude-plugin/plugin.json`. If you maintain a plugin
marketplace, point it at this repo; otherwise the clone/unzip paths above are
equivalent — Claude Code reads `.claude/` directly.

**Windows note**: launch via `scripts/start.ps1` so `.env` is loaded into the
PowerShell session before `claude` starts (MCP servers read keys via `${VAR}`
substitution in `.mcp.json`).

---

## Path B — claude.ai Projects (prompts only, no live pipeline) ⚠️

claude.ai chat has **no subagents, no MCP, no filesystem**. You cannot run
`/analyze` there. What you *can* do:

1. Create a Project on claude.ai.
2. Upload the agent prompts from `.claude/agents/` and the methodology from
   `.claude/skills/trading-copilot/SKILL.md` as Project knowledge.
3. Paste market data **manually** (you fetch it yourself) and ask Claude to
   apply one agent's role at a time.

This gives you the *reasoning framework* but not the automation. Treat it as a
structured thinking aid, not the pipeline.

---

## Path C — OpenAI / ChatGPT (portable prompt pack, manual) ⚠️

ChatGPT has no plugin format compatible with this repo. The markdown prompts are
model-agnostic, so you can:

1. Copy an agent prompt (e.g. `.claude/agents/investment-advisor.md`) into a
   ChatGPT Custom GPT "Instructions" field, or paste it as a system message via
   the OpenAI API.
2. Supply market data manually in the conversation (ChatGPT cannot call the MCP
   servers).
3. Run agents sequentially by hand, pasting each output into the next.

The orchestration (`analyze.md` step sequencing, resume, memory log, validators)
will **not** run — those are Claude Code scripts. You get the prompts, not the
machine.

> **Honest expectation**: Paths B and C give you the *prompt library*. Only
> Path A (Claude Code) runs the actual multi-agent pipeline with live data,
> resume, validation, and the memory/reflection loop.

---

## Verify your install (Path A)

```bash
python scripts/check.py                       # repo health: should print OK
python scripts/montecarlo.py --price 100 --vol 0.2 --days 14   # physics sanity
/advise NVDA                                  # in Claude Code
```

If `check.py` prints `OK` and `/advise` returns a rated report, you are good.

---

## What you must configure before real use

- **`.env`** — API keys (never commit this; it is gitignored).
- **`docs/strategy.md`** — your personal strategy/risk profile (not shipped;
  create your own. It is gitignored so it stays private).
- **`data/watchlist.md`** — tickers you actually track.
- **`data/positions.md`** — your holdings (gitignored; create from scratch).

See [README.md](../README.md) for the command table and pipeline diagram.

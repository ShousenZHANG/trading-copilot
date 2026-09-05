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

The repo ships `.claude-plugin/plugin.json`. To load it as a plugin without
installing it anywhere:

```bash
claude --plugin-dir /path/to/trading-copilot
```

If you maintain a plugin marketplace, point it at this repo; otherwise the
clone/unzip paths above are equivalent — Claude Code reads `.claude/` directly.

### Launch it so the MCP servers get their keys

`${VAR}` in `.mcp.json` is substituted from the environment of the process that
starts Claude Code. Claude Code does **not** read `.env` itself, so starting
`claude` directly leaves every keyed server (finnhub) with an empty key.

```powershell
.\scripts\start.ps1     # Windows PowerShell
```

```bash
sh scripts/start.sh     # macOS / Linux / WSL / Git Bash
```

Both load `.env` into the session, then exec `claude`. Neither prints a key.

`uv` must be on your PATH — both default MCP servers are spawned through it.
Install from <https://docs.astral.sh/uv/>, then check with `uv --version`.

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
### Run the handshake once before your first session (it pre-warms `uv`)

Both default servers are provisioned on demand by `uv`, and Claude Code gives a
stdio MCP server **30 seconds** to answer before reporting `CONNECT_TIMEOUT`.
Measured on an empty `uv` cache, `uvx --with mcp<2 yahoo-finance-mcp` takes
**26.3s** to complete its first handshake — inside the budget, but only just, and
two servers starting at once can push it over. Once the packages are cached it
answers in 5–9s.

So run the handshake once, from a shell, before you start Claude Code:

```bash
python scripts/mcp_handshake.py --all
```

It reports `SLOW` and exits non-zero for any server that answers but exceeds the
client's budget, and warns when one is inside it without headroom — a server that
works from a shell and fails inside Claude Code is the exact failure this repo
spent a release not noticing.


```bash
python scripts/check.py                       # repo health: should print OK
python scripts/mcp_handshake.py --all         # each MCP server answers a real handshake
python scripts/montecarlo.py --price 100 --vol 0.2 --days 14   # physics sanity
/advise NVDA                                  # in Claude Code
```

The handshake is the one that matters for data: it spawns each server the way
`.mcp.json` tells Claude Code to and demands a JSON-RPC `initialize` reply, so a
dependency break or a crash-on-import surfaces as a non-zero exit instead of a
silent "Connection closed" during a run. Expect:

```
PASS     2.8s  finnhub          finnhub 1.28.1
PASS     6.0s  yahoo-finance    yfinance 1.29.1

2/2 server(s) completed the handshake.
```

If `check.py` prints `OK`, the handshake is 2/2, and `/advise` returns a rated
report, you are good.

---

## What you must configure before real use

- **`.env`** — API keys (never commit this; it is gitignored).
- **`docs/strategy.md`** — your personal strategy/risk profile (not shipped;
  create your own. It is gitignored so it stays private).
- **`data/watchlist.md`** — tickers you actually track.
- **`data/positions.md`** — your holdings (gitignored; create from scratch).

See [README.md](../README.md) for the command table and pipeline diagram.

---

## Submitting to a Claude Code marketplace (maintainers)

Verified against the official docs on 2026-09-05
([plugins](https://code.claude.com/docs/en/plugins),
[plugin-marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)).
Re-check before submitting — this process has already changed once, and the
version of this section that preceded it described a submission route that no
longer exists.

There are **two** Anthropic-run public marketplaces, and they are not the same
thing:

| Marketplace | How a plugin gets in |
|-------------|----------------------|
| `claude-community` — the public community marketplace, added by users with `/plugin marketplace add anthropics/claude-plugins-community` | Submit through an in-app form; entries land after review. **This is the route open to us.** |
| `claude-plugins-official` — curated by Anthropic, registered automatically on first interactive launch | **No application process.** Anthropic decides what to include; the submission form does not add anything here. |

Submission forms (pick the one that matches your account):

- **claude.ai** — <https://claude.ai/admin-settings/directory/submissions/plugins/new>
  (requires a Team or Enterprise organization plus directory-management access;
  organization Owners have it by default)
- **Console** — <https://platform.claude.com/plugins/submit> (the route for an
  individual author with no Team/Enterprise org)

What to expect after approval, per the docs: the plugin is **pinned to a commit
SHA** in the [`anthropics/claude-plugins-community`](https://github.com/anthropics/claude-plugins-community)
catalog, and **CI bumps that pin automatically** as you push new commits — so a
fix does propagate without re-submitting. The public catalog syncs nightly, so
there is a lag between approval and the plugin being installable. Check by
searching for the name in the community catalog's `marketplace.json`.

Two properties still make this worth getting right the first time:

| Property | Consequence |
|----------|-------------|
| The plugin **name is the skill namespace** and users type it | `trading-copilot` should be considered permanent. Decide before submitting. |
| The review pipeline runs `claude plugin validate` plus automated safety screening; **nobody audits the MCP servers a plugin ships** | Our `README.md` is the de-facto security-review surface. Keep the "Security & data provenance" section accurate. |

### Pre-submission checklist

- [ ] `claude plugin validate .` passes. Plain `claude plugin validate .`
      is what the review pipeline runs; `--strict` promotes its warnings to
      errors, so clear it first. **Known open warning**: `CLAUDE.md at the plugin
      root is not loaded as project context` — plain validate passes (exit 0),
      `--strict` currently fails (exit 1) on that one warning alone.
- [ ] `python scripts/check.py` prints `OK` (repo-level invariants the official
      validator does not cover: prompt drift, model tiers, private-state tracking).
- [ ] `python scripts/mcp_handshake.py --all` — every shipped server completes a
      real JSON-RPC handshake. A shape check cannot catch a server that dies on
      import; this can.
- [ ] **Checked on something that is not Windows** (macOS, Linux, or WSL): the
      launcher (`scripts/start.sh`), the relative `--script` paths in `.mcp.json`,
      and every documented path. This repo is developed on Windows, which is the
      one platform whose breakage we would notice by accident.
- [ ] `.claude-plugin/plugin.json` version bumped; `homepage` reachable.
- [ ] `README.md` opens with the educational-use disclaimer and carries the
      security/provenance section in its top third.
- [ ] No personal state tracked: `git ls-files` shows no `data/positions.md`,
      `data/runs/`, `data/memory/trading_memory.md`, `data/decisions/`,
      `data/state/`, `docs/strategy.md`, `.env`.
- [ ] `python scripts/package_release.py` builds and its post-build leak check
      passes.
- [ ] Tag and publish a GitHub release.

You can also distribute without any Anthropic marketplace at all: publish your
own `.claude-plugin/marketplace.json` in a git repo and have users run
`/plugin marketplace add <owner>/<repo>`. That path needs no review and no form.

# Continuous Tracking

> **Partly historical (0.5.0).** <!-- historical --> The reasoning about why scheduled runs
> belong on the local machine still holds and is recorded in
> [ADR-0002](adr/0002-local-scheduling-and-evidence-only-stubs.md). The concrete commands
> below invoke `scripts/memory.py`, deleted in 0.5.0 with the multi-agent pipeline; the
> replacement scan and notification runner is not built yet.

> Scheduled runs belong on the machine that holds the state. This document
> explains why the GitHub Actions cron workflows were retired, and what replaced
> them.

## The workflows are gone

`premarket.yml` and `weekly-review.yml` were deleted. They had failed every
scheduled run for weeks before anyone noticed, and fixing the immediate error
would not have made them useful. Four independent reasons, any one of which is
disqualifying:

**1. The state they operate on is not in the checkout.** `/weekly-review`
resolves pending entries in `data/memory/trading_memory.md`. `/scan` dedups
against `data/decisions/`. Both are gitignored personal state, so a CI checkout
contains neither. The weekly job had nothing to resolve and the scan job
re-analysed everything from scratch daily. `git add data/decisions` in the
commit step could never stage a file.

**2. Making them work would require a backend.** The only way to give a runner
the memory log is an encrypted state store — a private gist, an object store, a
sibling private repo. This project's architecture is "no backend, no build
step". A state service is exactly the thing it declines to be.

**3. They leaked private state into a public repo.** The artifact upload path
included `data/runs/`, publishing full analysis runs as downloadable artifacts.
The commit step pushed `data/state/pushed_alerts.json` — `sha256(ticker|date|rating)`
truncated to 16 hex chars — to the public default branch. That preimage space is
small enough to enumerate, so the file publishes the signal history it exists to
deduplicate. `data/state/` is now gitignored.

**4. The economics were upside down.** Both workflows declared two cron lines to
cover EST and EDT. GitHub's cron has no DST awareness, so both fired every
scheduled day and every run was billed twice. A full `/scan` over an 11-name
watchlist is $11–33 per pass.

One more defect worth recording, because it is a pattern to avoid rather than a
one-off: `${{ inputs.filter }}` was interpolated directly into a `run:` block in
a job that held every API secret. Any workflow that accepts `workflow_dispatch`
input must pass it through `env:` and reference it as `"$FILTER"`, never splice
it into the script text.

`scripts/check.py` now enforces the mechanical half of these lessons
(`check_workflows`): no `${{ inputs.* }}` inside `run:`, no unpinned tool
installs, at most one `cron:` per schedule, and no `upload-artifact` path or
`git add` target that `.gitignore` covers.

## What runs instead

Everything scheduled runs locally, where the state, the keys, and the MCP
servers already work.

### Daily — the cheap habit

```bash
python scripts/portfolio_check.py --help    # see the trigger-line flags
```

`/portfolio` is deterministic and spends zero tokens unless a trigger fires. It
already dedups across runs via `data/portfolio_state.json` and
`--state-ttl-hours`, which is the same job the CI dedup state was trying to do —
except it works, and it stays on your machine.

### Weekly — resolve the memory loop

The T+5d resolution is deterministic. It needs prices and arithmetic, not a
model:

```bash
python scripts/prices.py --tickers NVDA,GC=F,SPY --start 2026-04-01 --end 2026-09-30 \
    --out evals/prices/2026.json
python scripts/memory.py resolve-pending --prices evals/prices/2026.json --dry-run
python scripts/memory.py resolve-pending --prices evals/prices/2026.json
```

Then run `/weekly-review --no-resolve` in Claude Code for the reflection and
lessons synthesis, which is the part that genuinely wants a model.

### Scheduling it

Whatever your OS already offers. On Windows, Task Scheduler pointed at
`scripts/start.ps1` (it loads `.env` before invoking `claude`); on macOS or
Linux, `cron` plus `scripts/start.sh`. Claude Code's own `/schedule` also works
and keeps the run inside a session that has the MCP servers configured.

## What CI still does

`ci.yml` is the only workflow left, and it deliberately never calls `claude`:

- byte-compiles every module on Python 3.11 / 3.12 / 3.13
- runs `scripts/check.py`
- discovers and runs every `--self-test` module
- runs the memory and validation integration suites
- **starts every shipped MCP server and requires a JSON-RPC `initialize` reply**
  (`scripts/mcp_handshake.py`) — the check that would have caught the SDK break
- validates the plugin manifest with `claude plugin validate --strict`
- builds the release zip and asserts its payload

That is CI's whole job: prove the repo works on a clean machine. Anything that
needs your positions, your memory log, or your API keys runs at home.

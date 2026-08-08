---
description: Deterministic check of YOUR actual holdings (parses data/positions.md). Zero-LLM math first (P&L, allocation drift, NVDA look-through, trigger lines, Monte Carlo), then dispatches agents ONLY if a price/VIX trigger fired. The cheap daily/weekly habit — /analyze and /advise are for new research.
argument-hint: [--force-agents]
---

# /portfolio — your-holdings check (math first, agents only on trigger)

Run a deterministic portfolio check on the holdings in `data/positions.md`.

**Why this exists**: the weekly routine was burning 3 Opus agents (~$1.50, ~20 min)
even when nothing changed. All of the arithmetic is deterministic — an LLM should
never do it. Agents are dispatched **only when a pre-committed trigger fires**.

## Steps

### 1. Get live prices (main thread)

Pull via MCP (fall back per [docs/mcp-fallback.md](../../docs/mcp-fallback.md) if down):

- `mcp__yahoo-finance__get_stock_info` for **NDQ.AX** → `regularMarketPrice`
- same for **IOO.AX**
- optional: `^VIX` (or WebFetch a quote page) for the VIX gate

### 2. Run the deterministic check

```bash
python scripts/portfolio_check.py --ndq <NDQ_PRICE> --ioo <IOO_PRICE> --vix <VIX>
```

Exit code contract (load-bearing — this command branches on it):
- **0 = HOLD** — nothing to dispatch. Either no trigger fired, **or** every
  fired trigger was already dispatched inside the dedup window (see below).
- **1 = TRIGGER** — at least one *new* pre-committed line crossed.
- 2 = input error (missing positions.md / price)

Trigger lines default to the latest PM decision (NDQ stop A$60 / 50d A$56.61,
IOO 50d A$186, VIX 25). **The 50d SMAs drift — refresh the flags weekly** from
the latest market data (`--ndq-50d`, `--ioo-50d`).

#### Dedup: don't pay twice for the same breach

The check is stateful. A trigger that already dispatched agents within
`--state-ttl-hours` (default 24) is **suppressed**: still printed, but it does
not flip the exit code. Without this, running the check twice in one day
re-dispatched the same paid Opus agents for the same unchanged breach.

The dedup key is the trigger's *semantic kind* (`ndq_below_50d`,
`vix_systemic`, …), not the formatted message — so the same breach at a new
price still dedupes. State lives in `data/portfolio_state.json` (gitignored,
personal). A corrupt or missing state file fails **open** (dispatches).

```bash
python scripts/portfolio_check.py --ndq 63.05 --ioo 192.76 --no-state        # bypass dedup
python scripts/portfolio_check.py --ndq 63.05 --ioo 192.76 --reset-state     # clear history, then run
python scripts/portfolio_check.py --ndq 63.05 --ioo 192.76 --state-ttl-hours 48
```

#### ATR-adaptive lines (optional)

Pass the daily ATR as a percentage of price to stop a stale-tight line firing
on ordinary noise:

```bash
python scripts/portfolio_check.py --ndq 63.05 --ioo 192.76 --ndq-atr-pct 1.8 --ioo-atr-pct 1.1
```

ATR only ever **loosens** a line (pushes it to at most `price − 1.5×ATR`); it
never tightens one. Omit the flags and the effective lines are identical to the
fixed ones — this is purely opt-in.

### 3a. Exit 0 (HOLD) → report and STOP

Show the script output table to the user. Do **NOT** dispatch agents — the
verdict is hold; the cost discipline is the point. Append the standing note if
the NVDA look-through breach is listed (new money → diversifier, not tech).

If the verdict says triggers were **suppressed**, say so plainly: the breach is
still live, it just already got its decision inside the dedup window. Point at
that earlier decision rather than paying for a new one.

### 3b. Exit 1 (TRIGGER) → dispatch agents for a re-decision

Only now spin up the pipeline, scoped to what fired:

| Trigger | Dispatch |
|---------|----------|
| NDQ below stop / 50d | `investment-advisor` on NDQ.AX (mode: accumulation, question: trim/hold) |
| IOO below 50d | `investment-advisor` on IOO.AX |
| VIX > 25 | `macro-analyst` (systemic check) + `portfolio-manager` synthesis |

Pass the script's JSON (`--json`) into the agent brief as verified embedded data.
Finish with a `portfolio-manager` decision if more than one trigger fired.

### 4. Reply

- The script table (verbatim — it is the ground truth)
- Verdict line + what (if anything) the user should do
- Disclaimer footer

## Notes

- `--force-agents` in `$ARGUMENTS`: dispatch the advisor pass even on HOLD
  (e.g. the Sunday deep review). Default is the cheap path.
- `data/positions.md` is the single source of truth for holdings — keep it
  updated after every fill, or this command lies to you.
- This command **never** writes to the memory log (no new decision is created
  on a HOLD verdict). TRIGGER-path PM decisions go through the normal
  validate → memory → assemble flow.

> ⚠️ Educational use only. Not investment advice. See [DISCLAIMER.md](../../DISCLAIMER.md).

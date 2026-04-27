---
description: Force a multi-round Bull/Bear debate (skips full pipeline). Useful when you already have analyst reports and want to stress-test the thesis.
argument-hint: <TICKER> [--rounds=3]
---

Force a multi-round Bull/Bear debate on `$ARGUMENTS`.

**Ticker**: first token of `$ARGUMENTS`.
**Rounds**: default 3 (vs 1 in `/analyze`). Override via `--rounds=N`. Each round = 1 Bull + 1 Bear turn.

## When to use this

- After `/analyze` ran with default 1 round and you want deeper adversarial scrutiny on a specific call
- When two analysts strongly disagreed and you want the researchers to settle it
- For high-stakes positions before sizing up

## Execution plan

### 1. Locate analyst reports

Look for the most recent `data/runs/<TICKER>-*/` dir. If found, reuse:
- `01-market.md`
- `02-social.md`
- `03-news.md`
- `04-fundamentals.md` (or `05-macro.md`)

If no recent run exists (no dir, or older than 24h), tell the user to run `/analyze <TICKER>` first.

### 2. Initialize debate

Create `data/runs/<TICKER>-<DATE>/debate_extended.md` (separate from the original `debate_history.md` so we don't overwrite).

### 3. Run N rounds

For each round (1..N):
1. Dispatch **`bull-researcher`** with: 4 analyst reports + current debate_extended.md + last bear arg.
2. Append to debate_extended.md.
3. Dispatch **`bear-researcher`** with: same context + the new Bull arg.
4. Append to debate_extended.md.

### 4. Re-run Research Manager

Dispatch **`research-manager`** (Opus) with: ticker context + full `debate_extended.md`. Produces an **updated** ResearchPlan to `06-research-plan-extended.md`.

### 5. Diff vs original

Read the original `06-research-plan.md` (if exists). Compare:
- Did the rating change?
- Did the rationale strengthen or weaken?
- Did sizing recommendation change?

### 6. Output

Write `data/decisions/<TICKER>-<DATE>-extended.md`:

```markdown
# <TICKER> 扩展辩论结果 — <DATE> (rounds=<N>)

## 评级变化
- 原评级 (1轮): <X>
- 新评级 (N轮): <Y>
- 变化: <unchanged | upgraded | downgraded>

## 新研究主管摘要
<paste 06-research-plan-extended.md>

## 完整辩论纪要
<paste debate_extended.md>

⚠️ 教育用途, 非投资建议.
```

### 7. Reply

Show:
- Rating change (or "unchanged")
- Top 1-2 new arguments that emerged in extended rounds
- Path to extended decision file

**Do not** trigger the Trader / Risk / PM steps — `/debate` is purely a research-stress-test, not a new decision.

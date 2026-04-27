---
description: Run the full Trading Copilot pipeline on one instrument (4 analysts -> Bull/Bear debate -> Trader -> 3-way Risk debate -> Portfolio Manager). Use for stocks, ETFs, futures.
argument-hint: <TICKER> [--debate-rounds=1] [--risk-rounds=1]
---

Run the full Trading Copilot pipeline on `$ARGUMENTS`.

**Ticker**: parse from `$ARGUMENTS` — first token. Preserve any exchange suffix exactly (`.HK`, `.T`, `.L`, `.TO`, `=F`, `=X`).
**Date**: today (read from system clock, format `YYYY-MM-DD`).
**Debate rounds**: default 1 (= 1 Bull + 1 Bear). Override via `--debate-rounds=N`.
**Risk rounds**: default 1 (= Aggressive + Conservative + Neutral). Override via `--risk-rounds=N`.

## Execution plan

Follow this order **strictly**. Use the Agent tool with the named subagent for each step. Wait for each subagent to finish and save its report before starting the next.

### Step 0: Setup + RESUME DETECTION

1. Create run dir: `data/runs/<TICKER>-<DATE>/` (or reuse if exists).

2. **RESUME CHECK** — list existing files in the run dir. For each step below, if its output file already exists with non-trivial size (> 50 bytes), **SKIP that step** and use the existing file. Resume from the first incomplete step.

   Decision table:

   | File found | Action |
   |------------|--------|
   | `00-past-context.md` exists | Skip Step 0 step 2-3 |
   | `01-market.md` (>500 bytes) exists | Skip market-analyst |
   | `02-social.md` (>500 bytes) exists | Skip social-analyst |
   | `03-news.md` (>500 bytes) exists | Skip news-analyst |
   | `04-fundamentals.md` (>500 bytes) exists | Skip fundamentals-analyst |
   | `debate_history.md` exists | Inspect content: count `## Bull (round N)` and `## Bear (round N)` headers. Resume from the next-needed turn (e.g. Bull done, Bear missing → start Step 2 at Bear) |
   | `06-research-plan.md` (>200 bytes) exists | Skip Research Manager |
   | `07-trader-proposal.md` (>200 bytes) exists | Skip Trader |
   | `risk_debate_history.md` exists | Count Aggressive/Conservative/Neutral headers, resume next-needed |
   | `08-portfolio-decision.md` (>200 bytes) exists | Skip Portfolio Manager — go to Step 7 (assemble final report) |
   | `data/decisions/<TICKER>-<DATE>.md` exists | Pipeline already complete — print summary and return |

3. Tell the user explicitly which steps you're skipping (e.g. "Resuming /analyze NVDA — skipping completed: market-analyst, social-analyst, news-analyst, fundamentals-analyst, Bull researcher. Continuing from: Bear researcher.").

4. If 00-past-context.md does not exist: read `data/memory/trading_memory.md` and extract:
   - Last 5 resolved entries for `<TICKER>` (full DECISION + REFLECTION)
   - Last 3 resolved entries for OTHER tickers (REFLECTION only)
   Save as `00-past-context.md`. If memory log empty, write `(no past context)`.

5. Read `data/positions.md` for the Portfolio Manager risk gate (don't skip even on resume).

### Step 1: Analysts (PARALLEL — fan out 4 in a single message)

The 4 analysts have **no inter-dependencies** (each writes a different report field, none reads another analyst's output). Dispatch all four in **a single message containing 4 Agent tool calls**, so Claude Code runs them concurrently. This cuts wall-clock time from ~15-20 min (serial) to ~5-7 min (parallel).

In ONE message, fan out:
- **`market-analyst`** with run brief `{ticker, date, run_dir}` → writes `01-market.md`
- **`social-analyst`** with same brief → writes `02-social.md`
- **`news-analyst`** with same brief → writes `03-news.md`
- **`fundamentals-analyst`** with same brief → writes `04-fundamentals.md`

**Wait for all four to complete** before proceeding to Step 2. Verify each file exists. If any failed, log to `data/runs/<TICKER>-<DATE>/_errors.md` and continue with the analysts that succeeded — Bull/Bear can still debate from partial reports.

**Rate-limit guardrail**: if Claude Code returns rate-limit errors during parallel dispatch, fall back to serial (a → b → c → d). Document the fallback in `_errors.md`.

**Cost note**: parallel does not increase total tokens — same total work, different timing. Peak concurrent context is 4× higher (4 analyst contexts in flight), still well within Sonnet limits.

### Step 2: Bull/Bear debate

Maintain a `data/runs/<TICKER>-<DATE>/debate_history.md` file.

For each round (default 1):
1. Dispatch **`bull-researcher`** with: all 4 analyst reports + current `debate_history.md` + last bear arg (empty on round 1). Append the Bull's response to `debate_history.md` as `## Bull (round N)\n<text>`.
2. Dispatch **`bear-researcher`** with: all 4 analyst reports + current `debate_history.md` + the Bull arg just produced. Append `## Bear (round N)\n<text>`.

### Step 3: Research Manager

Dispatch **`research-manager`** (Opus tier) with: ticker context + full `debate_history.md`. It produces the structured ResearchPlan to `06-research-plan.md`.

### Step 4: Trader

Dispatch **`trader`** with: ticker context + `06-research-plan.md` + all 4 analyst reports. Produces `07-trader-proposal.md`.

### Step 5: Risk debate (3-way, fixed order)

Maintain `data/runs/<TICKER>-<DATE>/risk_debate_history.md`.

For each round (default 1), dispatch in order:
1. **`aggressive-debator`** with: all analyst reports + trader proposal + current `risk_debate_history.md`. Append `## Aggressive (round N)\n<text>`.
2. **`conservative-debator`** — same context. Append `## Conservative (round N)\n<text>`.
3. **`neutral-debator`** — same context. Append `## Neutral (round N)\n<text>`.

### Step 6: Portfolio Manager

Dispatch **`portfolio-manager`** (Opus tier) with:
- ticker context
- `06-research-plan.md`
- `07-trader-proposal.md`
- `risk_debate_history.md`
- `00-past-context.md`
- `data/positions.md`

It runs the pre-trade risk gate, applies past lessons, and produces `08-portfolio-decision.md`. It also appends a pending entry to `data/memory/trading_memory.md`.

### Step 7: Assemble final user-facing report

Write `data/decisions/<TICKER>-<DATE>.md` using this template:

```markdown
# <TICKER> 决策报告 — <DATE>

> ⚠️ 教育与研究用途. 非投资建议. 详见 [DISCLAIMER.md](../../DISCLAIMER.md).

## 最终结论 (Portfolio Manager)
<paste 08-portfolio-decision.md content>

## 交易员方案 (Trader)
<paste 07-trader-proposal.md content>

## 研究主管摘要 (Research Manager)
<paste 06-research-plan.md content>

---

<details>
<summary>📊 4位分析师报告 (展开查看)</summary>

### 技术面 (Market)
<paste 01-market.md>

### 情绪面 (Social)
<paste 02-social.md>

### 新闻面 (News)
<paste 03-news.md>

### 基本面 (Fundamentals)
<paste 04-fundamentals.md>

</details>

<details>
<summary>🥊 Bull/Bear 辩论纪要 (英文, 展开查看)</summary>

<paste debate_history.md>

</details>

<details>
<summary>⚖️ 风险辩论纪要 (Aggressive/Conservative/Neutral, 英文)</summary>

<paste risk_debate_history.md>

</details>

---

**过往决策上下文** (用于本次复盘):

<paste 00-past-context.md>

---

⚠️ **免责声明**: 本报告由 trading-copilot 多agent生成. 仅供教育研究, 非投资建议. 模型可能产生幻觉/错误/遗漏. 你对所有投资决定负全责. 详见 [DISCLAIMER.md](../../DISCLAIMER.md).
```

### Step 8: Reply to user

Show the user:
- The headline rating + price target (1 line)
- The executive summary (2-4 sentences)
- The path to the full report: `data/decisions/<TICKER>-<DATE>.md`
- Brief warning: "教育用途, 不是投资建议"

Do NOT dump the full report into chat — it's long. The user can open the markdown file or ask to see specific sections.

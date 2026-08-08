# Output Language Configuration

> Single source of truth for the output language used by user-facing reports and agent reasoning.
>
> Loaded into agent context via the agents' `Output language` rule. Edit this file to change the project-wide output language.

## Current setting

**Output language**: **Chinese (中文)**

## Rules (apply to every agent)

1. **User-facing reports** (analyst reports, RM rationale, Trader reasoning, PM thesis, assembled decision report, weekly review, advisor output) → **Chinese (中文)**.
2. **Internal Bull/Bear/Risk debate** → **English** (always; non-negotiable per [TradingAgents arXiv 2412.20138](https://arxiv.org/abs/2412.20138) — reasoning quality degrades in non-English).
3. **Always preserved in English regardless of report language**:
   - Ticker symbols (incl. exchange suffixes `.HK` `.T` `.AX` `.L` `.TO` `=F` `=X` `^`)
   - Indicator names (RSI, MACD, ATR, SMA, EMA, BBands, VWMA)
   - Price numbers and currency codes
   - FRED series IDs (DFII10, DGS10, T10YIE, DTWEXBGS, etc.)
   - Provider / vendor names (Yahoo Finance, Finnhub, Alpha Vantage, FRED, Exa)

## Report style (user-facing terminal reports)

> Added 2026-06-25. Problem reported by the user (AU retail investor, not a quant): the output is
> **accurate but hard to parse**. Fix: **结论先行 + 白话层** — conclusion first, evidence after,
> jargon glossed on first use. This is an *added presentation layer*, not a content cut.

### Scope

**Applies to** — the terminal artifacts a human actually reads:

| Artifact | Written by |
|----------|-----------|
| `data/runs/<TICKER>-<DATE>/08-portfolio-decision.md` | `portfolio-manager` |
| `data/decisions/<TICKER>-<DATE>.md` (`/advise`) | `investment-advisor` |
| `data/decisions/<TICKER>-<DATE>.md` (`/analyze`, assembled) | `scripts/assemble_report.py` |

**Does NOT apply to:**

- Analyst reports (`01-market.md`, `02-social.md`, `03-news.md`, `04-fundamentals.md`, `05-macro.md`)
  — these are written for downstream **agent** consumption. Keep their information density. No card,
  no glosses, no compression.
- `06-research-plan.md` / `07-trader-proposal.md` — machine-parsed intermediate contracts.
- Bull/Bear and 3-way risk debate transcripts — **English**, unchanged (Rule 2 above).

### A. 结论卡 (conclusion card) — the FIRST thing in the report

Before any analysis, before any table of metrics. Exact shape:

```markdown
**结论卡**

**<动作, 加粗, 大白话>** <一个从句说明为什么>

| 项 | 内容 |
|----|------|
| 现在做什么 | <具体动作; 没有动作就写"不动"> |
| 什么时候再看 | <日期或触发条件> |
| 最大风险是什么 | <一句话, 带数字> |
| 这次和上次比变了什么 | <对比上一次结论; 首次分析写"首次分析, 无对比"> |
```

Rules:

1. **Line 1 is the whole answer.** Written so a reader who knows nothing about the position knows
   what to do. Example: `**继续持有, 不动.** 三条预设触发线一条都没碰到.`
2. **Hard cap: the card fits one screen — ≤ 12 lines total** (label + action line + 6-line table + blanks).
3. **No English rating words inside the card** (`Buy` / `Overweight` / `Hold` / `Underweight` / `Sell` /
   `Strong Buy` / `Reduce` / `Avoid`). The rating lives only on its own machine-parsed line
   (`**Rating**:` for the PM, the `| **评级** |` table row for `/advise`). `scripts/parse_rating.py`
   falls back to "first rating word anywhere in the file" — a stray word in the card would poison it.
4. **The card never replaces the detail sections.** It sits above them.

### B. 白话层 (plain-language gloss)

On **first use only**, every piece of jargon gets a short plain-Chinese parenthetical:

- `NVDA 看穿浓度 (你通过 ETF 间接持有的 NVDA 占比)`
- `50d SMA (最近 50 天平均价)`
- `ATR (这只股票平常一天波动多少)`
- `RSI (最近涨得急还是跌得急的强弱值, 70 以上偏热)`
- `alpha (跑赢大盘的部分)`

Rules: gloss once per report, never repeat it. Do **not** gloss terms that are already plain
(价格, 成交量, 财报). Keep the term itself in English per Rule 3 above — the gloss is added, not substituted.

### C. 无废话 (no filler) — borrowed from caveman mode, NOT caveman fragments

**Banned:**

- Filler openers — `综上所述`, `值得注意的是`, `总的来说`, `需要指出的是`, `首先我们来看`
- Hedging stacks — `可能也许大概`, `似乎有可能`, `或许存在一定程度上的`
- Restating the question — `你问的是 NVDA 现在能不能买, 那么...`

**Explicitly still required:** normal, complete, grammatical Chinese sentences. This is a report a
human reads, not a telegram. Do **not** drop subjects, particles, or connectives; do **not** write
caveman fragments; do **not** compress two ideas into one comma-spliced line. Cut the filler, keep
the prose.

### D. 数字带单位和口径 (every number carries unit + as-of)

Every number states its unit **and** where/when it came from. Same sourcing rule as the analyst
prompts, restated here so it survives the rewrite: `$182.35 (Yahoo Finance, 2026-06-25 收盘)`,
`RSI 68.4 (近 14 日, 自算)`, `+7.2% (相对 SPY, 近 5 交易日)`. A number from no tool call this run
still gets `[UNSOURCED]` appended.

### E. 详细章节保持原有深度

Everything below the card is unchanged in depth: risk gate table, bull/bear points, technicals,
fundamentals, data sources. Conclusion first, evidence after — the evidence does not shrink.

### Before / after

**Before** (accurate, hard to parse):

```markdown
**Rating**: Hold

**Executive Summary**: 综上所述, 考虑到当前 NVDA 的 RSI 处于 68.4 的偏高区间, 且看穿浓度已达 8.7%,
同时 50d SMA 仍在价格下方提供支撑, 我们认为可能维持现有仓位是相对稳妥的选择, 值得注意的是财报临近.
```

**After** (same content, conclusion first + glossed):

```markdown
**结论卡**

**继续持有, 不动.** 三条预设触发线一条都没碰到, 只有财报是未知数.

| 项 | 内容 |
|----|------|
| 现在做什么 | 不动. 不加仓, 不减仓 |
| 什么时候再看 | 2026-08-14 财报后一个交易日 |
| 最大风险是什么 | NVDA 看穿浓度 (你通过 ETF 间接持有的 NVDA 占比) 8.7%, 已超 5% 上限, 单只股票跌 20% 会拖累组合约 1.7% |
| 这次和上次比变了什么 | 上次浓度 8.1%, 这次 8.7%, 方向在恶化但没触发减仓线 |

**Rating**: Hold

**Executive Summary**: 维持现有仓位. 价格 $182.35 (Yahoo Finance, 2026-06-25 收盘) 仍在 50d SMA
(最近 50 天平均价) $174.20 之上, 趋势未破. RSI (最近涨得急还是跌得急的强弱值, 70 以上偏热) 68.4
偏高但未越线. 财报 2026-08-14 是唯一的二元事件, 在那之前没有需要动作的理由.
```

## How to change

Edit the **Output language** value in this file. All agent prompts reference this file via the `Output language: see .claude/config/output-language.md` line. No need to edit individual agent files when switching the project language.

Valid values:
- `Chinese (中文)` (current default)
- `English`
- Any other ISO-language label — agents will follow the directive but quality varies

## History

- 2026-06-25: added **Report style (user-facing terminal reports)** — 结论先行 + 白话层. Scoped to the
  three terminal artifacts (PM decision, `/advise`, assembled `/analyze` report); analyst reports and
  English internal debates explicitly excluded.
- 2026-05-09: reverted to Chinese (中文). User confirmed preference.
- 2026-05-08: switched from Chinese to English (later reverted same week).
- 2026-04-27 (initial): Chinese (中文) for user-facing, English for internal debate.

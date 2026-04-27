---
description: Run the Trading Copilot pipeline specialized for gold (XAU/USD or GC=F). Replaces fundamentals-analyst with macro-analyst (real yields + DXY are the dominant drivers).
argument-hint: [GC=F | XAUUSD=X | XAU/USD]   default: GC=F
---

Run the gold-specialized Trading Copilot pipeline.

**Ticker**: parse from `$ARGUMENTS`. If empty, default to `GC=F` (gold futures, free via Yahoo). Other valid choices: `XAUUSD=X` (spot via Yahoo), or call out a specific GoldAPI symbol if the gold MCP is enabled.

**Date**: today.

## Differences from `/analyze`

- Replace **fundamentals-analyst** with **macro-analyst** — gold has no income statement, no balance sheet. Real yields, DXY, and inflation expectations are the actual drivers.
- Reweight Bull/Bear/Risk debate to focus on:
  - Real yield direction (DFII10)
  - DXY direction
  - Geopolitical / safe-haven flow
  - Central-bank buying narrative
  - Inflation regime
- Use the same 5-tier rating but adjust the **Trader** sizing template:
  - Buy/Overweight on gold → typically 5-15% portfolio (vs 1-5% for single equity)
  - Stops based on weekly ATR, not daily

## Execution plan

Same 8 steps as `/analyze`, with these substitutions:

- **Step 1 (analysts, PARALLEL)**: Fan out **4 analysts** in a single message — `market-analyst`, `social-analyst`, `news-analyst`, **`macro-analyst`** (replaces fundamentals-analyst). The macro-analyst writes to `05-macro.md` instead of `04-fundamentals.md`. Wait for all four; on rate-limit fall back to serial.
- **Step 7 template**: Replace "基本面" section with "宏观面 (Macro)" pasting `05-macro.md`. Update the analyst-report list to read "技术面 / 情绪面 / 新闻面 / 宏观面".

Everything else (debate, risk, portfolio manager, memory log, disclaimer) is identical to `/analyze`.

## Reply to user

Headline + thesis + report path. Plus one extra line specific to gold:

> 🪙 当前实际利率: <DFII10值> | DXY: <值> | 这两个是黄金最重要的两个驱动.

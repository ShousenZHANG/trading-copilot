---
description: Single comprehensive investment advisor — runs ONE agent that combines technical, fundamental, news, sentiment, and macro analysis in a single pass. Faster + cheaper than the 13-agent /analyze. Outputs an actionable Buy/Hold/Reduce recommendation with entry/stop/sizing.
argument-hint: <TICKER>
---

# /advise — Single-agent comprehensive investment recommendation

Run a comprehensive investment analysis on `$ARGUMENTS` using **ONE** super-agent.

**Difference vs `/analyze`**:
- `/analyze` = 13 subagents in serial pipeline (Bull/Bear debate + 3-way risk debate + Portfolio Manager). Deep, slow (30-60 min), expensive. Use for high-conviction positions.
- `/advise` = 1 agent (Opus) doing everything in one pass. Fast (5-10 min), focused, cheaper. Use for **daily decision-making**.

## Args

- **`$ARGUMENTS`** — first token is the ticker. Preserve exchange suffix exactly:
  - US stocks: `AAPL`, `NVDA`, `MSFT`
  - ASX (Australia): `VAS.AX`, `VGS.AX`, `BHP.AX`, `CBA.AX`
  - HK: `0700.HK`, `9988.HK`
  - Japan: `7203.T`
  - UK: `BARC.L`
  - Canada: `SHOP.TO`
  - Gold: `GC=F` (futures) or `XAUUSD=X` (spot)
  - Index: `^GSPC` (S&P 500), `^AXJO` (ASX 200)

If user gave just a name ("Tesla", "苹果"), resolve to ticker first by asking or inferring (TSLA, AAPL).

## Execution

1. **Resolve ticker + today's date** (system clock, format `YYYY-MM-DD`).

2. **Resume check** — if `data/decisions/<TICKER>-<DATE>.md` already exists with size > 1KB, the user already ran this today. Show them the existing path and ask if they want to re-run (use **fresh data**) or read existing.

3. **Single dispatch**: invoke the **`investment-advisor`** subagent with the run brief:

   ```
   Ticker: <TICKER>
   Date: <YYYY-MM-DD>
   Output path: D:/trading-copilot/data/decisions/<TICKER>-<DATE>.md
   User context: 澳洲零售投资者, manual buy at Pearler/CommSec, mid-term hold (3-12 months typical), conservative bias.
   ```

   The agent runs the full analysis (technical + fundamental + news + sentiment + risk gate + recommendation) using yahoo-finance + finnhub + exa + fred MCPs in one pass.

4. **Wait** for the agent to return the saved file path.

5. **Reply to user** (do NOT dump full report into chat — too long). Show:
   - Headline rating + confidence
   - Entry / stop / target / sizing (1 line)
   - 1-sentence reason
   - Path to full report
   - 1-line warning: "教育用途, 非投资建议"

   Example reply:

   ```
   ✅ TSLA — Buy (中等确信)
   入场 $245-$252 | 止损 $228 | 目标 12个月 $310 | 仓位 ≤8%

   理由: 4Q交付超预期 + Robotaxi 5月发布会催化剂 + 50日线突破, 但 RSI 偏高需小心追高.

   📄 完整报告: data/decisions/TSLA-2026-04-27.md (含技术/基本/情绪/风控/操作步骤)

   ⚠️ 教育用途, 非投资建议. 你对所有决定负责.
   ```

6. **Optional**: append a pending entry to `data/memory/trading_memory.md` for T+5 day reflection (only if rating is Buy or Strong Buy or Reduce/Sell — Hold doesn't generate a memory entry since no action was taken).

## Speed expectation

- **3-10 minutes** typical (vs 30-60 min for /analyze)
- 6-14 tool calls (vs 50+ for /analyze)
- ~$0.20-0.50 per run (vs $1-3 for /analyze)

## When to use which

| Scenario | Use |
|---------|-----|
| 日常筛选 watchlist | **`/advise`** |
| 想买某只前快速 sanity check | **`/advise`** |
| ETF / 大盘指数评估 | **`/advise`** |
| 黄金 / 大宗 | **`/advise`** |
| **大仓位**前最终尽调 (>10% portfolio) | `/analyze` (13-agent 深度) |
| 评级和你直觉冲突想要"二次意见" | `/analyze` |
| 教学 / 学习多视角分析 | `/analyze` |
| 复盘历史决策 | 都不需要, 直接读 data/memory/trading_memory.md |

## Notes

- Ticker not found / data unavailable → agent should report "无法获取 <TICKER> 数据, 检查 ticker 拼写或 MCP 状态". Do not fabricate.
- 数据陈旧 (>24h) → agent must downgrade to Hold and explain why.
- 用户指定特殊上下文 (e.g. "我是新手只想要保守的" or "我已持有 5%, 该不该加仓") → 把这段加到 `User context` 里传给 agent.

# TradingAgents Deep-Dive (源码级分析)

**目的**: 我们的 trading-copilot 插件直接抄这套架构. 这份doc固化所有设计决策.

**源码位置**: `reference/TradingAgents/` (commit截至2026-04-25, v0.2.4)

> Current repo note: upstream TradingAgents keeps analysts sequential inside LangGraph. Trading Copilot intentionally diverges by running the four analysts in parallel as Claude Code subagents, then validating and assembling durable markdown artifacts with `scripts/validate_outputs.py` and `scripts/assemble_report.py`.

---

## 1. 真实架构 (修正之前的错误)

### 错误纠正

我之前以为 analysts **并行**, 实际是 **严格串行**. 原架构:

```
START
  ↓
[Market Analyst]   ⇄ tools_market   (loop直到无tool_call)
  ↓ Msg Clear
[Social Analyst]   ⇄ tools_social
  ↓ Msg Clear
[News Analyst]     ⇄ tools_news
  ↓ Msg Clear
[Fundamentals Analyst] ⇄ tools_fundamentals
  ↓ Msg Clear
[Bull Researcher] ⇄ [Bear Researcher]   (辩论 max_debate_rounds 轮)
  ↓
[Research Manager]   (deep_thinking_llm, 出ResearchPlan结构化)
  ↓
[Trader]   (出TraderProposal结构化)
  ↓
[Aggressive] → [Conservative] → [Neutral]   (轮转 max_risk_discuss_rounds 轮)
  ↓
[Portfolio Manager]   (deep_thinking_llm, 出PortfolioDecision结构化)
  ↓
END
```

**11个agent节点, 不是9个**:
1. Market Analyst
2. Social Media Analyst
3. News Analyst
4. Fundamentals Analyst
5. Bull Researcher
6. Bear Researcher
7. **Research Manager** (我之前漏了)
8. Trader
9. Aggressive Risk Debator
10. Conservative Risk Debator
11. Neutral Risk Debator
12. **Portfolio Manager** (与Risk Manager不同)

---

## 2. 核心设计模式 (5个)

### 2.1 双层LLM (cost优化)

```python
deep_thinking_llm    # 仅2个agent用: Research Manager + Portfolio Manager
quick_thinking_llm   # 其余9个agent用 (analysts/researchers/trader/risk debators)
```

→ 我们映射: **Opus只给2个managers**, **Sonnet给其他9个**, **Haiku跑signal_processing/reflection**.

### 2.2 Tool-Loop Pattern

每个analyst是一个node. LangGraph条件边逻辑:

```python
def should_continue_market(state):
    if last_message.tool_calls:
        return "tools_market"  # → 调工具 → 回analyst
    return "Msg Clear Market"   # → 清消息 → 下一个analyst
```

Analyst节点内部:
```python
if len(result.tool_calls) == 0:
    report = result.content   # 写入state["market_report"]
return {"messages": [result], "market_report": report}
```

→ 我们映射: **Claude Code subagent天然支持tool loop**. 但**我们的subagent间无法共享state**, 改用**markdown中转** (subagent输出 → 主orchestrator读 → 注入下一个subagent prompt).

### 2.3 Msg Clear (上下文卫生)

每个analyst完成后, **删除所有intermediate messages**, 只留一个 "Continue" placeholder. 防止messages累积爆炸context.

```python
def delete_messages(state):
    removal_operations = [RemoveMessage(id=m.id) for m in messages]
    placeholder = HumanMessage(content="Continue")
    return {"messages": removal_operations + [placeholder]}
```

→ 我们映射: 每个subagent返回**只保存最终report markdown**, 不传messages.

### 2.4 结构化输出 (仅3个agent)

只有**3个agent**用Pydantic结构化输出 (LangChain `with_structured_output`):

| Agent | Schema | 字段 |
|-------|--------|------|
| Research Manager | `ResearchPlan` | recommendation(5-tier), rationale, strategic_actions |
| Trader | `TraderProposal` | action(3-tier), reasoning, entry_price?, stop_loss?, position_sizing? |
| Portfolio Manager | `PortfolioDecision` | rating(5-tier), executive_summary, investment_thesis, price_target?, time_horizon? |

其余8个agent输出**自由markdown** (但analyst强制要求"末尾追加markdown表格").

→ 我们映射: 用**Claude tool use的JSON schema**或要求**严格markdown格式**.

### 2.5 评级scale (固定枚举)

```python
PortfolioRating = Buy | Overweight | Hold | Underweight | Sell   # 5-tier
TraderAction    = Buy | Hold | Sell                              # 3-tier
```

提示中明确: "Reserve Hold for situations where evidence on both sides is genuinely balanced; otherwise commit to the side with stronger arguments."

---

## 3. 辩论协议 (重要)

### 3.1 投资辩论 (Bull vs Bear)

**State**: `InvestDebateState`
```python
{
    "bull_history": "",       # Bull累积发言
    "bear_history": "",       # Bear累积发言  
    "history": "",            # 合并轮次
    "current_response": "",   # 最新一条
    "judge_decision": "",     # Research Manager最终裁决
    "count": 0,               # 总发言数
}
```

**路由**:
```python
def should_continue_debate(state):
    if count >= 2 * max_debate_rounds:   # 默认1轮 = 2条 (1Bull+1Bear)
        return "Research Manager"
    if current_response.startswith("Bull"):
        return "Bear Researcher"
    return "Bull Researcher"
```

**默认配置**: `max_debate_rounds=1` → 实际只有 1Bull + 1Bear → ResearchManager.
**配置3轮就是**: 1B+1Bear+2B+2Bear+3B+3Bear → ResearchManager.

### 3.2 风险辩论 (3-way轮转)

**State**: `RiskDebateState` (类似但有3个history)

**路由**:
```python
def should_continue_risk_analysis(state):
    if count >= 3 * max_risk_discuss_rounds:   # 默认1轮 = 3条 (A+C+N)
        return "Portfolio Manager"
    if latest_speaker.startswith("Aggressive"):    return "Conservative Analyst"
    if latest_speaker.startswith("Conservative"):  return "Neutral Analyst"
    return "Aggressive Analyst"
```

固定顺序: **Aggressive → Conservative → Neutral → Aggressive → ...**

### 3.3 max_recur_limit

LangGraph硬上限: `max_recur_limit=100` (防死循环).

---

## 4. Memory Log (持久化, 极简设计)

**关键发现**: TradingAgents **不用vector DB**, 不用ChromaDB. 用**append-only markdown**.

### 4.1 文件格式

`~/.tradingagents/memory/trading_memory.md`:

```markdown
[2026-04-15 | NVDA | Buy | pending]

DECISION:
**Rating**: Buy
**Executive Summary**: Strong AI demand...
**Investment Thesis**: ...

<!-- ENTRY_END -->

[2026-04-10 | NVDA | Overweight | +5.2% | +1.8% | 5d]

DECISION:
**Rating**: Overweight
...

REFLECTION:
The directional Overweight call captured most of the upside (+5.2% raw, +1.8% alpha vs SPY). 
The thesis on data-center growth held up well, but the price target was too conservative. 
Lesson: when sell-side estimates are accelerating into a print, lean Buy not Overweight.

<!-- ENTRY_END -->
```

**Tag格式**:
- Pending: `[date | ticker | rating | pending]`
- Resolved: `[date | ticker | rating | raw% | alpha% | days]`

**分隔符**: `<!-- ENTRY_END -->` (HTML注释, LLM输出绝不会产生, 安全hard delimiter).

### 4.2 两阶段写入

**Phase A (同步, 决策后立即)**:
```python
store_decision(ticker, trade_date, final_decision)  # 追加pending entry
```

**Phase B (异步, 下次同ticker运行时)**:
```python
_resolve_pending_entries(ticker)  # 1. 用yfinance取T+5天真实回报
                                  # 2. raw_return + alpha vs SPY
                                  # 3. 调reflection prompt生成2-4句反思
                                  # 4. 原子替换pending tag + 追加REFLECTION段
```

### 4.3 注入PortfolioManager的past_context

```python
get_past_context(ticker, n_same=5, n_cross=3)
```

返回:
```
Past analyses of NVDA (most recent first):
[2026-04-10 | NVDA | Overweight | +5.2% | +1.8% | 5d]
DECISION: ...
REFLECTION: ...

[2026-03-25 | NVDA | Buy | -2.1% | -3.0% | 5d]
...

Recent cross-ticker lessons:
[2026-04-12 | AMD | Buy | +3.1%]
当前AI热度仍强, 半导体板块整体beta>1...
```

注入到PortfolioManager prompt, 让模型看到**自己过去的对错**.

### 4.4 Reflection Prompt (复用)

```
You are a trading analyst reviewing your own past decision now that the outcome is known.
Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).

Cover in order:
1. Was the directional call correct? (cite the alpha figure)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis.

Be specific and terse. Your output will be stored verbatim in a decision log
and re-read by future analysts, so every word must earn its place.
```

→ 我们映射: **直接抄, 翻译成中文版本**.

---

## 5. 关键Prompt精华 (我们抄)

### 5.1 公共system prompt前缀 (所有analyst)

```
You are a helpful AI assistant, collaborating with other assistants.
Use the provided tools to progress towards answering the question.
If you are unable to fully answer, that's OK; another assistant with different tools
will help where you left off. Execute what you can to make progress.
If you or any other assistant has the FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** or deliverable,
prefix your response with FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL** so the team knows to stop.
You have access to the following tools: {tool_names}.
{system_message}
For your reference, the current date is {current_date}. {instrument_context}
```

`instrument_context`:
```
The instrument to analyze is `NVDA`. Use this exact ticker in every tool call, 
report, and recommendation, preserving any exchange suffix (e.g. `.TO`, `.L`, `.HK`, `.T`).
```

### 5.2 Market Analyst (技术分析) — 11个候选指标硬约束

明确列出11个ta-lib指标, 强制选 **最多8个**, 避免冗余:

```
Moving Averages: close_50_sma, close_200_sma, close_10_ema
MACD Related:    macd, macds, macdh
Momentum:        rsi
Volatility:      boll, boll_ub, boll_lb, atr
Volume:          vwma

Avoid redundancy (e.g. do not select both rsi and stochrsi).
```

### 5.3 Bull Researcher

```
You are a Bull Analyst advocating for investing in the stock. 
Build a strong, evidence-based case emphasizing:
- Growth Potential
- Competitive Advantages
- Positive Indicators
- Bear Counterpoints (critically analyze with specific data)
- Engagement (conversational, debate the bear analyst directly)

Resources available:
Market: {market_report}
Sentiment: {sentiment_report}
News: {news_report}
Fundamentals: {fundamentals_report}
Conversation history: {history}
Last bear argument: {current_response}
```

(Bear Researcher mirror)

### 5.4 Risk Debators (3个角色)

**Aggressive**: "actively champion high-reward, high-risk opportunities, focus on potential upside, growth potential, even when these come with elevated risk"

**Conservative**: "protect assets, minimize volatility, ensure steady reliable growth"

**Neutral**: "balanced perspective, weighing both benefits and risks, factor in broader market trends, diversification"

### 5.5 Research Manager (5-tier rating prompt)

```
**Rating Scale** (use exactly one):
- **Buy**: Strong conviction in the bull thesis
- **Overweight**: Constructive view; gradually increasing exposure
- **Hold**: Balanced view
- **Underweight**: Cautious view; trim exposure
- **Sell**: Strong conviction in the bear thesis

Commit to a clear stance whenever the debate's strongest arguments warrant one;
reserve Hold for situations where the evidence on both sides is genuinely balanced.
```

### 5.6 多语言机制 (重要!)

```python
def get_language_instruction() -> str:
    lang = config.get("output_language", "English")
    if lang == "English":
        return ""  # 省token
    return f" Write your entire response in {lang}."
```

**关键设计**: 内部辩论(Bull/Bear/Research/Risk Debators) **保持英文** (推理质量更好). 只有**面向用户的analyst报告 + PM最终决策**用配置语言.

→ 我们直接抄: **辩论英文, 输出中文**.

---

## 6. Tools (数据流)

11个工具, 4类:

| 类别 | 工具 | analyst |
|------|------|---------|
| Stock | `get_stock_data` (OHLCV CSV) | market |
| Indicators | `get_indicators` (MACD/RSI/BB/...) | market |
| Fundamentals | `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, `get_income_statement` | fundamentals |
| News | `get_news` (公司), `get_global_news` (宏观), `get_insider_transactions` | news + social |

**数据vendor配置** (category-level):
```python
"data_vendors": {
    "core_stock_apis": "yfinance",       # or alpha_vantage
    "technical_indicators": "yfinance",
    "fundamental_data": "yfinance",
    "news_data": "yfinance",
}
```

→ 我们映射: 这些 = **MCP servers** (yahoo-finance / alpha-vantage / finnhub).

---

## 7. Checkpoint/Resume (生产用)

`checkpoint_enabled=True` 时:
- LangGraph SqliteSaver保存每个node完成后的state
- `thread_id = sha(ticker + date)` → 同ticker+date可恢复
- 不同date重新开始
- 完成后清checkpoint防止stale

→ 我们映射: 每个subagent完成后, **写中间结果到 `data/runs/<ticker>-<date>/<step>.md`**, 失败可resume.

---

## 8. 反思学习闭环

**完整闭环**:
```
1. 决策 → store_decision (pending)
2. T+5 days后, 下次同ticker运行
3. _resolve_pending_entries 跑:
   - yfinance拉真实价格
   - 算 raw_return + alpha vs SPY
   - reflect prompt生成反思
   - 替换pending tag + 追加REFLECTION
4. 下次决策, get_past_context注入PM prompt
5. PM看着历史对错决策
```

→ 我们映射: GitHub Actions cron每周日跑resolve, **trading-copilot自动学习**.

---

## 9. 我们要修正的设计 (vs 之前的方案)

| 维度 | 之前方案 | 修正后 (基于源码) |
|------|---------|------------------|
| Analyst执行 | 并行 ❌ | **串行** (4个) ✅ |
| Agent数 | 9个 | **11个** (加Research Manager + 3个risk debators) |
| LLM分层 | 全Sonnet | **Opus(2) + Sonnet(7) + Haiku(2)** |
| Risk Manager | 1个网关 | **3个角色辩论 + Portfolio Manager裁决** |
| 结构化输出 | 全agent | **仅3个managers** (其余markdown) |
| 评级 | Buy/Sell/Hold | **5-tier (B/O/H/U/S) + 3-tier (B/H/S)** |
| Memory | SQLite/Supabase | **append-only markdown** (TradingAgents原生模式更优) |
| 反思 | 无 | **T+5天自动反思 + 注入PM** |
| 多语言 | 全agent中文 | **辩论英文 + 报告中文** (推理质量) |
| Bull/Bear辩论 | 不明 | **2 × max_rounds条 (默认2)** |
| Risk辩论 | 不明 | **3 × max_rounds条 (默认3)** |
| Tool loop | 不明 | **每analyst loop到无tool call再切换** |
| Msg Clear | 无 | **每analyst间清消息防爆context** |

---

## 10. 直接复用清单

✅ **直接搬到我们项目的文件**:
- `agents/utils/agent_states.py` → 翻译成markdown schema文档
- `agents/schemas.py` → Claude tool input_schema (JSON)
- `agents/utils/memory.py` → 翻译成bash/python脚本管理 `data/decisions/*.md`
- `graph/reflection.py` → reflection prompt直接抄, 翻译成中文
- 11个agent的prompt → 翻译/精简后写进Claude subagent markdown

✅ **架构原样照搬**:
- Sequential pipeline
- Bull/Bear → Research Manager → Trader → 3-way Risk → Portfolio Manager
- 双层LLM (deep + quick)
- 5-tier + 3-tier rating
- Memory pending → resolved 两阶段

✅ **Claude Code原生增强**:
- LangGraph state → Claude subagent + markdown中转
- SqliteSaver checkpoint → `data/runs/<ticker>-<date>/` 中间产物
- yfinance/alpha_vantage tools → MCP servers

❌ **不要的**:
- LangGraph框架 (用Claude Code orchestrator代替)
- Python运行时 (除非写少量MCP)
- ChromaDB (本来就没用)

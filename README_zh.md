# Trading Copilot

[English](./README.md) · **中文**

> 面向 **Claude Code** 的多 agent 交易研究插件。4 位分析师 → 多空辩论 → 交易员 → 三方风险辩论 → 投资组合经理。覆盖股票、ETF、黄金、宏观。

忠实移植自 [TradingAgents](https://github.com/TauricResearch/TradingAgents)（53k★）到 Claude Code 原生 subagent + slash 命令 + MCP 数据源。无后端，无构建步骤。

> ⚠️ **仅供教育研究，非投资建议。** 详见 [DISCLAIMER.md](./DISCLAIMER.md)。

---

## 安装（60 秒）

1. 从 [Releases](https://github.com/ShousenZHANG/trading-copilot/releases) **下载**最新 `trading-copilot-x.y.z.zip`。
2. **解压**到任意目录，例如 `~/trading-copilot`。
3. 用 **[Claude Code](https://claude.com/claude-code) 打开该文件夹**（在目录里运行 `claude`）。
4. `cp .env.example .env` — 填一个免费 [Finnhub](https://finnhub.io) key（Yahoo Finance 无需 key）。
5. 输入 `/advise NVDA`。

完成。除 Claude Code + Python 3 外无任何依赖。

---

## 安全与数据溯源

插件目录不会审计插件所带的 MCP 服务器。本节就是审计面——下面每一条都可在本仓库中核对。

**1. 外部输入是数据，不是指令。**
五个分析师 prompt（`market`、`social`、`news`、`fundamentals`、`macro`）以及
`investment-advisor` 都写明了同一条策略：抓取到的新闻、社交帖、财报文件、FOMC 文本
只是**待提取的材料**，绝不是要服从的指令。任何试图下达命令的文本会被标记为
`[suspicious directive content in <source>]` 并忽略。任何 agent 都不得基于注入文本
发出买/卖决策。
→ `.claude/agents/analysts/*.md`、`.claude/agents/investment-advisor.md`

**2. `[UNSOURCED]` 溯源标记，由脚本计数。**
agent 引用的任何数字，只要不是本次运行中由工具返回的，就必须打上 `[UNSOURCED]`。
`scripts/validate_outputs.py` 会按产物和整轮运行分别统计这些标记，超过软上限 3 就告警，
让投资组合经理在评级前先看到溯源薄弱的地方。

**3. 无密钥即可运行（keyless by design）。**
Yahoo Finance 无需 key。事件概率来自真金白银的预测市场——无密钥的 Polymarket Gamma API
（`scripts/polymarket_odds.py`），因此 agent 引用的是 `market-implied P(x) = y%` 而不是
主观猜测。Reddit `.json` 对爬虫返回 403，社交分析师会退回到无密钥的 Reddit RSS。默认启用的
服务器中只有 Finnhub 需要 key，而免费额度已经够用。

**4. 绝不硬编码任何密钥。**
key 只存在于 `.env`（已 gitignore），在 `.mcp.json` 中以 `${VAR}` 形式引用。
`.env.example` 是唯一提交进仓库的模板，里面只有占位符。
`.claude/settings.json` 额外禁止对 `.env` 的 `Write`/`Edit`。

**5. 你的交易状态不出本机。**
`.gitignore` 屏蔽 `data/positions.md`、`data/runs/`、`data/memory/trading_memory.md`、
`data/decisions/`、`data/audit/`、`docs/strategy.md`、`evals/results/`。
一旦这些被 git 跟踪，`scripts/check.py` 会**直接失败**。发布 zip 由 fail-closed 白名单构建，
并在打包后做泄漏扫描（`scripts/package_release.py`）——不在白名单里的路径永远不会被打包。
无遥测：唯一会发起网络请求的脚本是 `polymarket_odds.py`（公开 Polymarket API）和
`notify.py`（可选 Telegram 推送，不设置 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 就完全不动）。

**6. 实际会运行哪些 MCP 服务器。**
默认只启用两个。其余全部以 `_` 前缀禁用，必须用
`python scripts/enable_mcp.py <name>` 手动开启。

| 服务器 | 默认 | 是什么 | 密钥 |
|--------|------|--------|------|
| `yahoo-finance` | **启用** | 第三方 `uvx yahoo-finance-mcp` | 无 |
| `finnhub` | **启用** | `mcps/finnhub_mcp.py` — 本仓库内，可完整审阅 | 免费 Finnhub key |
| `_akshare` | 禁用 | `mcps/akshare_mcp.py` — 本仓库内，A股/港股数据 | 无 |
| `_polygon` `_alpha-vantage` `_fred` `_gold` `_exa` `_tushare` `_claude-mem` | 禁用 | 第三方服务器 | 各服务自备 |

`finnhub` 和 `akshare` 都是我们自带的单文件 Python 包装器，你可以直接读源码；
`yahoo-finance` 和其余被禁用的条目都是第三方代码，启用前请自行评估。

**7. 这不是投资建议。** 输出是 AI 生成的研究材料，仅供教育用途，不保证准确。
在依据本插件的任何输出行动之前，请先读 [DISCLAIMER.md](./DISCLAIMER.md)。

---

## 使用

| 命令 | 作用 | 时间 / 成本 |
|------|------|------------|
| `/advise NVDA` | 单 Opus agent：综合分析 + 买/持/卖 | ~5 分钟 · $0.20–0.50 |
| `/analyze NVDA` | 完整 12-agent 流水线（辩论 + 风险 + PM） | ~30 分钟 · $1–3 |
| `/gold` | 黄金流水线（宏观驱动） | ~30 分钟 |
| `/scan` | 跑整个 watchlist | 视数量 |
| `/watchlist add TSLA` | 管理标的 | 即时 |
| `/weekly-review` | 复盘历史决策、算 alpha、学习 | ~10 分钟 |

### 示例

```
你:    /advise NVDA
Claude: NVDA — Buy（中等确信）
        入场 $182–188 · 止损 $171 · 目标 $230（12月）· 仓位 ≤5%
        理由：数据中心需求 + 前瞻 P/E 合理；RSI 未超买。
        风险门：全通过。完整报告 → data/decisions/NVDA-2026-06-01.md
```

每次运行都会在 `data/decisions/` 写一份完整 markdown 报告。

---

## 流水线（`/analyze`）

```
技术面 · 情绪面 · 新闻面 · 基本面   （4 分析师，并行）
                  │
        多头  ⇄  空头   辩论
                  │
        研究经理（Opus）  → 5 档评级
                  │
              交易员          → 入场 / 止损 / 仓位
                  │
   激进 → 保守 → 中性   （风险辩论）
                  │
       投资组合经理（Opus）  → 最终决策 + 风险门
                  │
        记录 → T+5 天后反思
```

Opus 跑 2 个决策者 + `/advise`；Sonnet 跑其余。对抗式辩论能暴露单一模型遗漏的失败模式；记忆日志让系统从历史决策中学习。

---

## 其他运行环境（Claude.ai / ChatGPT）

完整流水线需要 Claude Code（subagent + MCP + 文件系统）。在 **claude.ai** 或 **ChatGPT** 上，`.claude/agents/` 里的 prompt 可移植——把某个（如 `investment-advisor.md`）粘贴为 system prompt / 自定义 GPT 指令，手动提供行情数据，即可获得推理框架（但无自动编排）。详见 [docs/INSTALL.md](./docs/INSTALL.md)。

---

## 数据源（MCP）

配置在 `.mcp.json`（默认仅 `yahoo-finance` + `finnhub` 启用，其余禁用）。key 存 `.env`（已 gitignore）。用 `python scripts/enable_mcp.py <name>` 开关。

| 数据 | 服务 | 成本 |
|------|------|------|
| 报价 / 历史 | Yahoo Finance | 免费 |
| 新闻 / 财报 / 情绪 | Finnhub | 免费 60/分钟 |
| 宏观（利率/CPI/收益率） | FRED | 免费 |
| 网络研究 | Exa | 免费额度 |

---

## 验证安装

```bash
python scripts/check.py        # 仓库健康检查 → 打印 "OK"
/advise NVDA                   # 在 Claude Code 内
```

---

## 链接

- [docs/INSTALL.md](./docs/INSTALL.md) — 完整安装（Claude Code / claude.ai / ChatGPT）
- [docs/methodology.md](./docs/methodology.md) — 为何用对抗辩论 + 反思
- [.claude/skills/trading-copilot/SKILL.md](./.claude/skills/trading-copilot/SKILL.md) — 流水线规格
- [LICENSE](./LICENSE)（MIT）· [DISCLAIMER.md](./DISCLAIMER.md)

> ⚠️ AI 生成研究，行动前请自行核实。你对所有决定负全责。

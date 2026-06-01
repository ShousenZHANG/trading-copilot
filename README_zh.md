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

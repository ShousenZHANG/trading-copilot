# Trading Copilot

[English](./README.md) · **中文**

> 面向 **Claude Code** 的多 agent 交易研究插件。4 位分析师 → 多空辩论 → 交易员 → 三方风险辩论 → 投资组合经理。覆盖股票、ETF、黄金、宏观。

忠实移植自 [TradingAgents](https://github.com/TauricResearch/TradingAgents) [![upstream stars](https://img.shields.io/github/stars/TauricResearch/TradingAgents?style=flat&label=%E2%98%85&color=555)](https://github.com/TauricResearch/TradingAgents/stargazers) 到 Claude Code 原生 subagent + slash 命令 + MCP 数据源。无后端，无构建步骤。

> ⚠️ **仅供教育研究，非投资建议。** 详见 [DISCLAIMER.md](./DISCLAIMER.md)。

---

## 安装（60 秒）

1. 从 [Releases](https://github.com/ShousenZHANG/trading-copilot/releases) **下载**最新 `trading-copilot-x.y.z.zip`。
2. **解压**到任意目录，例如 `~/trading-copilot`。
3. `cp .env.example .env` — 填一个免费 [Finnhub](https://finnhub.io) key（Yahoo Finance 无需 key）。
4. 用启动脚本从该目录启动 **[Claude Code](https://claude.com/claude-code)**，这样 `.env` 才会传给 MCP 服务器：
   `sh scripts/start.sh`（macOS / Linux / WSL）或 `.\scripts\start.ps1`（Windows）。
   直接运行 `claude` 也能起，但 `.mcp.json` 里的 `${VAR}` 会解析成空值，Finnhub 就是没有 key 的坏状态。
5. 输入 `/advise NVDA`。

依赖：Claude Code、Python 3，以及 PATH 上的 [`uv`](https://docs.astral.sh/uv/) —— 两个默认 MCP 服务器都由 `uv`/`uvx` 拉起。除此之外没有安装脚本，没有构建步骤。

验证数据链路真的通了：`python scripts/mcp_handshake.py --all` 应打印
`2/2 server(s) completed the handshake.`

---

## 安全与数据溯源

插件目录不会审计插件所带的 MCP 服务器。本节就是审计面——下面每一条都可在本仓库中核对。

**1. 外部输入是数据，不是指令。**
五个分析师 prompt（`market`、`social`、`news`、`fundamentals`、`macro`）以及
`investment-advisor` 都写明了同一条策略：抓取到的新闻、社交帖、财报文件、FOMC 文本
只是**待提取的材料**，绝不是要服从的指令。试图下达命令的文本会被标记并忽略；
任何 agent 都不得基于注入文本发出买/卖决策——分析师本身根本不出评级。
→ `.claude/agents/analysts/*.md`、`.claude/agents/investment-advisor.md`

*已知缺口*：`validate_outputs.py` 统计的标记写法是
`[suspicious directive content …]`。六个 prompt 里有五个用这个写法，
`market-analyst` 用的是 `[suspicious content detected]`——所以它抓到的注入会被正确忽略，
但**不会**被计入本轮汇总。已记录，尚未修复。

**2. `[UNSOURCED]` 溯源标记，由脚本计数。**
agent 引用的任何数字，只要不是本次运行中由工具返回的，就必须打上 `[UNSOURCED]`。
`scripts/validate_outputs.py` 会按产物和整轮运行分别统计这些标记，超过软上限 3
（`UNSOURCED_SOFT_CAP = 3`）就告警，让投资组合经理在评级前先看到溯源薄弱的地方。
注意这是**告警**，不是硬门禁。

**3. 跑在免费额度上——但不是"无密钥"，也不是"零安装"。**
Yahoo Finance 无需 key；事件概率来自真金白银的预测市场——无密钥的 Polymarket Gamma API
（`scripts/polymarket_odds.py`），因此 agent 引用的是 `market-implied P(x) = y%` 而不是
主观猜测；Reddit `.json` 对爬虫返回 403，社交分析师会退回到无密钥的 Reddit RSS。
但入门成本要说清楚：**`finnhub` 默认启用，没有 `FINNHUB_API_KEY` 就不工作**（免费额度 60 次/分钟），
而且**两个默认服务器都由 `uv`/`uvx` 拉起，`uv` 必须装好并在 PATH 上**。免费，是；开箱即用，不是。

**4. 绝不硬编码任何密钥。**
key 只存在于 `.env`（已 gitignore），在 `.mcp.json` 中以 `${VAR}` 形式引用。
`.env.example` 是唯一提交进仓库的模板，里面只有占位符。
`.claude/settings.json` 另外用**项目相对路径**规则禁止对 `.env` / `.env.*` 的
`Read`/`Write`/`Edit`，并且带一份最小权限白名单（没有裸 `Bash`、没有裸 `WebFetch`、
不预授权 `git commit`）。请把它当作"防止 agent 顺手翻到密钥"的护栏，而不是沙箱——
你在权限弹窗里放行的东西照样会执行。

**5. 你的交易状态不出本机。**
`.gitignore` 屏蔽 `data/positions.md`、`data/runs/`、`data/memory/trading_memory.md`、
`data/decisions/`、`data/audit/`、`data/state/`、`docs/strategy.md`、`evals/results/`。
一旦这些被 git 跟踪，`scripts/check.py` 会**直接失败**。发布 zip 由 fail-closed 白名单构建，
并在打包后做泄漏扫描（`scripts/package_release.py`）——不在白名单里的路径永远不会被打包，
`.github/` 也刻意不打进去。仓库里没有任何定时工作流：周期性运行放在你自己的机器上跑，
因为状态和 key 本来就只在那里（[ADR-0002](./docs/adr/0002-local-scheduling-and-evidence-only-stubs.md)）。
无遥测。会发起网络请求的脚本，穷举如下：`prices.py`（Yahoo 报价）、
`polymarket_odds.py`（公开 Polymarket API）、`mcp_handshake.py`（为了测试而拉起 MCP 服务器）、
`notify.py`（可选 Telegram 推送，不设置 `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` 就完全不动）。

**6. 实际会运行哪些 MCP 服务器。**
`.mcp.json` 里有什么，Claude Code 就启动什么——**"不在文件里"才是关闭开关**，
没有所谓的禁用前缀（早期版本用 `_` 前缀，它什么也没禁用，所有服务器照常启动）。
`.mcp.json.template` 是目录清单；`python scripts/enable_mcp.py <name>` 把条目复制过去，
`--disable` 移除。不带参数运行 `python scripts/enable_mcp.py` 会打印真实状态。

| 服务器 | 默认 | 是什么 | 密钥 |
|--------|------|--------|------|
| `yahoo-finance` | **启用** | 第三方 `uvx yahoo-finance-mcp` | 无 |
| `finnhub` | **启用** | `mcps/finnhub_mcp.py` — 本仓库内，可完整审阅 | 免费 Finnhub key，**必需** |
| `akshare` | 仅在清单中 | `mcps/akshare_mcp.py` — 本仓库内，A股/港股数据 | 无 |
| `polygon` `alpha-vantage` `fred` `gold` `exa` `tushare` | 仅在清单中 | 第三方服务器 | 各服务自备 |

以上就是全部清单——九个条目，其中两个在运行。

`finnhub` 和 `akshare` 都是我们自带的单文件 Python 包装器，你可以直接读源码；
`yahoo-finance` 和其余条目都是第三方代码，启用前请自行评估。

*A股/港股的真实状态*：`akshare` 的 39 项 `--self-test` 全部通过，但它**只覆盖符号解析，
零网络调用**，而且这个 server 从未在本仓库中对着真实 AkShare 端点验证过。
它的端点在中国大陆，某些网络下不可达。在相信任何 `.SS` / `.SZ` / `.HK` 数字之前，
先跑 `python mcps/akshare_mcp.py --probe`。Yahoo 和 Finnhub 对这些标的的覆盖是
"很薄到没有"，所以它们返回空结果意味着*未覆盖*，而不是*什么都没发生*。

**7. 这不是投资建议。** 输出是 AI 生成的研究材料，仅供教育用途，不保证准确。
在依据本插件的任何输出行动之前，请先读 [DISCLAIMER.md](./DISCLAIMER.md)。

---

## 使用

共 10 个命令。8 个可用；2 个只是设计规格，**尚未实现**，在命令选择器里已明确标注。

| 命令 | 作用 | 时间 / 成本 |
|------|------|------------|
| `/portfolio` | 确定性地检查你的真实持仓——盈亏、偏离、看穿持仓、触发线，全部由 Python 算。只有触发线被击穿才会调用 agent | 数秒 · 未触发时 $0 |
| `/advise NVDA` | 单 Opus agent：综合分析 + 明确评级 | ~5–10 分钟 · $0.20–0.50 |
| `/analyze NVDA` | 完整 12-agent 流水线（辩论 + 风险 + PM） | ~30–60 分钟 · $1–3 |
| `/gold` | 黄金流水线（macro-analyst 替换 fundamentals） | ~30–60 分钟 · $1–3 |
| `/debate NVDA` | 基于已有分析师报告，强制多轮多空辩论 | ~10 分钟 |
| `/scan` | 对整个 watchlist 跑流水线 | 视数量 |
| `/watchlist add TSLA` | 管理标的（add / remove / list / tag） | 即时 |
| `/weekly-review` | T+5 天复盘历史决策、算 alpha、写教训 | ~10 分钟 |
| `/earnings` | ⛔ **未激活——尚未实现。** 没有对应 agent，文件本身就是激活规格 | — |
| `/screen` | ⛔ **未激活——尚未实现。** 没有对应 agent，文件本身就是激活规格 | — |

`/portfolio` 是每天低成本的习惯动作；"这只该不该买" 的常规答案是 `/advise`；
`/analyze` 留给值得花一小时和几美元的大仓位。

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

`.mcp.json` 是活跃集合，`.mcp.json.template` 是目录清单。key 存 `.env`（已 gitignore）。
用 `python scripts/enable_mcp.py <name>` 开关。

| 数据 | 服务 | 默认 | 成本 |
|------|------|------|------|
| 报价 / 历史 | Yahoo Finance | 启用 | 免费，无需 key |
| 新闻 / 财报 / 业绩 | Finnhub | 启用 | 免费 60/分钟，**需要 key** |
| 宏观（利率/CPI/收益率） | FRED | 仅在清单中 | 免费 key |
| 网络 + 社交研究 | Exa | 仅在清单中 | 免费起始额度 |
| A股 / 港股 / 指数 | AkShare | 仅在清单中 | 免费无 key —— 见上文真实状态说明 |
| 期权 / 外汇、备用报价 | Polygon、Alpha Vantage | 仅在清单中 | 免费额度 |
| 现货黄金 | GoldAPI | 仅在清单中 | 免费 100/月（Yahoo `GC=F` 是免费替代） |
| A股（需 token） | Tushare | 仅在清单中 | 积分额度 |

注册链接、免费额度、排障：[docs/mcp-setup.md](./docs/mcp-setup.md)。
服务器挂掉时的降级链路：[docs/mcp-fallback.md](./docs/mcp-fallback.md)。

---

## 验证安装

```bash
python scripts/check.py                  # 仓库健康检查 → 打印 "OK"
python scripts/mcp_handshake.py --all    # 每个 MCP 服务器完成一次真实握手
/advise NVDA                             # 在 Claude Code 内
```

`check.py` 查的是形状；`mcp_handshake.py` 查的是数据服务器能不能真的起来并把协议说完。
两个都要跑——形状检查抓不到"import 就崩"的服务器，而依赖 pin 坏掉那次正是这样溜过去的。

---

## 链接

- [docs/INSTALL.md](./docs/INSTALL.md) — 完整安装（Claude Code / claude.ai / ChatGPT）
- [docs/mcp-setup.md](./docs/mcp-setup.md) — 接一个数据服务器
- [docs/methodology.md](./docs/methodology.md) — 为何用对抗辩论 + 反思
- [CONTEXT.md](./CONTEXT.md) — 领域术语表（ticker、run、decision、alpha、risk gate…）
- [docs/adr/](./docs/adr/) — 架构决策记录，编号且只追加
- [.claude/skills/trading-copilot/SKILL.md](./.claude/skills/trading-copilot/SKILL.md) — 流水线规格
- [LICENSE](./LICENSE)（MIT）· [DISCLAIMER.md](./DISCLAIMER.md)

> ⚠️ AI 生成研究，行动前请自行核实。你对所有决定负全责。

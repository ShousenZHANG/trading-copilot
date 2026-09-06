# GitHub 上游方案核验：免费日线研究的数据可靠性

调研日期：2026-09-06。适用范围：美股、美国上市 ETF、纳斯达克相关指数与 ETF；黄金指中国购买的人民币计价投资金条/金币，非黄金 ETF 或首饰；日线、波段和长期配置；数据预算为零，允许注册免费 API key。产品在 Claude Code 与 Codex 中共用核心，会话给简短中文建议，后台保留数据依据和真实操作账本。本文是后台源码抽样与架构建议，不是行情实测报告或收益证明；人民币实物黄金的价格源与买卖价差由总体方案单独核验。

## 结论

最适合本仓库的是保留轻量插件，把“取数、校验、计算、风险约束”做成一个确定性的本地共享核心，Claude Code 与 Codex 只保留薄入口；所有分析角色读取同一个有版本的证据快照。优先参考 TradingAgents 的验证快照和显式供应商链、OpenBB 的适配器契约、ai-hedge-fund 的代码风控、yfinance 的参数语义、exchange_calendars 的交易时段模型，再借鉴 Qlib/LEAN 的历史可见性与评估方法。详细材料供后台审计，会话默认只给行动、主要依据、最大风险和下次检查条件。

“顶级”在本文指与问题匹配、源码可核验、维护可观察，不按 Stars 推断准确率。增加角色数量、换更大模型或把多个 MCP 装齐，均不能单独保证最新数据、预测正确或盈利。免费方案的合理承诺是：明确数据截至时间和来源，识别缺失及冲突，关键事实不能验证时停止相应推荐。

## 证据范围与版本

查询了 GitHub 官方 REST API 的 `GET /repos/{owner}/{repo}`、`GET /repos/{owner}/{repo}/commits/{default_branch}`，并使用固定 SHA 的 Git tree 和 raw 文件检查实现。首批元数据取回时间为 **2026-09-06 03:27:45 UTC**；yfinance 和 exchange_calendars 在随后同一次调研中取回。

所有下列仓库在查询时 `archived=false`。Stars 是取回时快照，之后会变化。`pushed_at` 是仓库推送活动时间，并不等于默认分支 HEAD 提交时间；例如 OpenBB、Qlib、zipline-reloaded 两者不同，不能把推送活动写成某功能的发布日期。本次未发现取回时间之后的提交日期。网页检索有旧抓取结果，因此版本判断以直接 API 和 SHA 文件为准。

| 仓库与官方元数据 | Stars | 默认分支 | HEAD SHA | pushed_at UTC | HEAD commit UTC | 许可快照 |
|---|---:|---|---|---|---|---|
| [TauricResearch/TradingAgents](https://api.github.com/repos/TauricResearch/TradingAgents) | 102,627 | main | `9dee508c44662702281a8dbaad1f7b42179b5ba7` | 2026-09-01 05:38:45 | 2026-09-01 05:38:45 | Apache-2.0 |
| [virattt/ai-hedge-fund](https://api.github.com/repos/virattt/ai-hedge-fund) | 63,253 | main | `fc1bf250ead209ae5f02c39c3d0062c4bb554505` | 2026-09-03 20:28:11 | 2026-09-03 20:28:07 | MIT |
| [OpenBB-finance/OpenBB](https://api.github.com/repos/OpenBB-finance/OpenBB) | 72,706 | develop | `3e071fcc2cd9f891cac6040ae60296dba76dab46` | 2026-07-30 17:33:42 | 2026-07-20 17:45:28 | API: NOASSERTION；根 LICENSE: AGPL-3.0 |
| [microsoft/qlib](https://api.github.com/repos/microsoft/qlib) | 48,330 | main | `79633dd9506ea689e5400dea0197717b5b3d74b7` | 2026-09-02 18:23:05 | 2026-07-23 08:15:29 | MIT |
| [QuantConnect/Lean](https://api.github.com/repos/QuantConnect/Lean) | 21,491 | master | `23b735d99a357807dc0df9f4c51d30f05fe0d277` | 2026-09-05 00:00:23 | 2026-09-04 14:52:27 | Apache-2.0 |
| [ranaroussi/yfinance](https://api.github.com/repos/ranaroussi/yfinance) | 25,176 | main | `3d9d2f0cacb662bff689874cd6113bae3a30a885` | 2026-08-27 11:23:24 | 2026-08-26 17:20:38 | Apache-2.0 |
| [gerrymanoim/exchange_calendars](https://api.github.com/repos/gerrymanoim/exchange_calendars) | 666 | master | `5de07333a58052eee033246bfe63f24e71da958f` | 2026-09-02 21:29:23 | 2026-09-02 21:29:23 | Apache-2.0 |

另外核验了三个候选，因范围或成本匹配度较低，不作为默认依赖：

| 仓库 | Stars | 默认分支 / HEAD SHA | pushed_at / HEAD commit UTC | 许可快照 |
|---|---:|---|---|---|
| [stefan-jansen/zipline-reloaded](https://api.github.com/repos/stefan-jansen/zipline-reloaded) | 1,933 | main / `943010b9da848e317fc520de87edade2b884d329` | 2026-01-06 13:01:54 / 2025-11-13 15:14:32 | Apache-2.0 |
| [AI4Finance-Foundation/FinRobot](https://api.github.com/repos/AI4Finance-Foundation/FinRobot) | 7,921 | master / `d221910096de87579b02f8f0674652bf1a175f51` | 2026-08-23 12:06:08 / 同左 | Apache-2.0 |
| [anthropics/financial-services](https://api.github.com/repos/anthropics/financial-services) | 34,703 | main / `69cbc81467a5dced793eee03dec4658aa24ef856` | 2026-08-25 18:23:23 / 2026-08-25 01:04:28 | Apache-2.0 |

`zipline-reloaded` 的当前 owner 是 **stefan-jansen**。`anthropics/financial-services-plugins` 的 API 请求重定向至上表的 `anthropics/financial-services`。这些名称以本次 API `full_name` 为准。

## 核心项目：采用什么，避免什么

### 1. TradingAgents：直接上游，优先补数据契约

已读源码显示，`route_to_vendor` 把显式配置的供应商列表视为完整回退链，不再在失败后偷偷添加其他来源。它区分限流、未配置、无数据和普通异常，最终无可用数据时要求分析角色不要估算价格。实际代码见 [interface.py L168-L262](https://github.com/TauricResearch/TradingAgents/blob/9dee508c44662702281a8dbaad1f7b42179b5ba7/tradingagents/dataflows/interface.py#L168)。

`build_verified_market_snapshot` 在代码中按分析日期过滤未来 OHLCV 行，计算固定指标集合，缺失值展示为 `N/A`，并将最近价格与指标作为精确数值的证据。见 [market_data_validator.py](https://github.com/TauricResearch/TradingAgents/blob/9dee508c44662702281a8dbaad1f7b42179b5ba7/tradingagents/dataflows/market_data_validator.py)。抽查测试覆盖未来行、周末用上一交易日、空数据；resume 测试包括图配置变化后开始新任务。见 [market_data_validator tests](https://github.com/TauricResearch/TradingAgents/blob/9dee508c44662702281a8dbaad1f7b42179b5ba7/tests/test_market_data_validator.py)、[checkpoint resume tests](https://github.com/TauricResearch/TradingAgents/blob/9dee508c44662702281a8dbaad1f7b42179b5ba7/tests/test_checkpoint_resume.py)。本次读取了测试源码，没有运行其测试。

**采用建议：** 让本仓库的分析、辩论、评级复用一次构建的快照；供应商回退必须显式配置，结果记录实际来源；resume 的依据包含数据快照、参数、提示词和模型配置的摘要。

**边界：** 这里的 `verified` 是内部过滤和计算，不是交易所保证；当前抽查函数未提供独立多源认证。其“某供应商无数据 + 另一供应商报错”的处理也不应直接压缩成本项目的全局“不存在”：应保留每个来源的错误状态。无需迁入完整 LangGraph，先补可用本地 Python 实现的能力。

### 2. OpenBB：借鉴适配器契约，不把连接器当免费数据

`Fetcher` 明确分为查询转换、提取、结果转换，并提供逐阶段类型与空结果检查；`OBBject` 有 `provider`、`warnings`、`extra`。见 [fetcher.py L36-L85](https://github.com/OpenBB-finance/OpenBB/blob/3e071fcc2cd9f891cac6040ae60296dba76dab46/openbb_platform/core/openbb_core/provider/abstract/fetcher.py#L36)、[obbject.py L36-L62](https://github.com/OpenBB-finance/OpenBB/blob/3e071fcc2cd9f891cac6040ae60296dba76dab46/openbb_platform/core/openbb_core/app/model/obbject.py#L36)。

**采用建议：** 一个小接口 `fetch -> normalize -> validate`，统一价格、基本面、新闻、宏观数据的元数据外壳，但允许不同资产类别有不同结果结构。增加 `source_url`、`retrieved_at`、`as_of`、`available_at`、`currency`、`adjustment`、`status` 和 `evidence_id`。

**边界：** 连接多个供应商不代表有独立数据，也不改变上游授权与配额。根 [LICENSE](https://github.com/OpenBB-finance/OpenBB/blob/3e071fcc2cd9f891cac6040ae60296dba76dab46/LICENSE#L1) 写明 AGPL-3.0，而本仓库根 LICENSE 为 MIT；本方案选择参考抽象并自行实现小接口，不直接复制 OpenBB 源码，也不默认安装整个平台。许可证结论限于读到的文件，具体组件另有授权时应按组件核实。

### 3. ai-hedge-fund：代码风控与披露时间，比更多人格代理有用

当前代码已经在 `hedge_fund/`，旧文章常见的 `src/agents/` 路径不能当本次版本证据。`apply_limits` 按顺序执行单标的绝对权重上限和组合总敞口上限，每次裁剪产生可解释的 `ClampEvent`；减少的权重不再分配到其他资产。见 [risk/limits.py](https://github.com/virattt/ai-hedge-fund/blob/fc1bf250ead209ae5f02c39c3d0062c4bb554505/hedge_fund/risk/limits.py)。

数据模型有可空的 `filing_date`、`filing_datetime`，注释将其解释为财务信息公开时间；文件整体绑定 Financial Datasets 响应。见 [data/models.py L36-L53](https://github.com/virattt/ai-hedge-fund/blob/fc1bf250ead209ae5f02c39c3d0062c4bb554505/hedge_fund/data/models.py#L36)。回测引擎抽查部分以入场/出场收盘价成交，见 [backtesting/engine.py L161](https://github.com/virattt/ai-hedge-fund/blob/fc1bf250ead209ae5f02c39c3d0062c4bb554505/hedge_fund/backtesting/engine.py#L161)。

**采用建议：** 组合限制由确定性程序决定，LLM 只提交候选建议；记录每项风险判定、缺失输入和裁剪前后值。保存财报的报告期与公开时间，避免把“所属季度”误当“当时已知”。

**边界：** 不移植供应商作为免费默认，不把声明了 PIT 字段等同于端到端无前视。也不照搬同一根收盘价同时生成信号和成交：本项目日线实验应规定信号何时可见、最早何时可以执行。

### 4. yfinance：直接使用可测的日线接口，所有关键参数显式设置

当前 `history` 的默认值包括 `interval="1d"`、`auto_adjust=True`、`repair=False`、`actions=True`；`start` 包含起始日，`end` 不包含结束日；修复选项会处理价格量级、缺失及分红复权问题。见 [history.py L104-L142](https://github.com/ranaroussi/yfinance/blob/3d9d2f0cacb662bff689874cd6113bae3a30a885/yfinance/scrapers/history.py#L104)。

**采用建议：** 把 `interval`、`start/end`、`auto_adjust`、`back_adjust`、`actions`、`repair` 写入配置和快照；保留原始可交易价、调整价与企业行动。不要让“最新值”在复权前后偷偷换口径。修复结果必须带标记和原始数据摘要，不能静默覆盖。长窗口可以显式分段、缓存和验证末行，不因旧 MCP 对 `1y` 的故障就永远限制所有指标为三个月；需要 200 个交易日的指标必须有足够历史。

**边界：** 项目 README 明确其并非 Yahoo 官方支持/审核的客户端，并把使用定位在研究和个人用途；软件开源许可证不替代行情使用条款。见 [README L18-L26](https://github.com/ranaroussi/yfinance/blob/3d9d2f0cacb662bff689874cd6113bae3a30a885/README.md#L18)。不能承诺实时 SLA。Yahoo MCP、yfinance、Yahoo 页面不能算三个独立行情源。

### 5. exchange_calendars：用交易时段判断“最新”，而非墙上时钟

其 XNYS 实现指定 `America/New_York`，列出常规休假和特殊收盘日；接口支持交易日集合、前一交易日和前一次收盘查询。见 [XNYS calendar](https://github.com/gerrymanoim/exchange_calendars/blob/5de07333a58052eee033246bfe63f24e71da958f/exchange_calendars/exchange_calendar_xnys.py#L161)、[README session examples](https://github.com/gerrymanoim/exchange_calendars/blob/5de07333a58052eee033246bfe63f24e71da958f/README.md#L51)。

**采用建议：** `expected_session` 取“在分析时间前已经结束、且已过供应商发布缓冲的最近交易时段”，与末根完整日 K 的 session 对比。交易日历、供应商延迟、价格时间戳分别记录。悉尼本机日期不能直接当美股交易日。官方节假日、早收盘和夏令时列入固定样例。

**边界：** README 说明日历由社区维护且一般只把常规交易计为开市；仍需按官方交易所表验证重要日期，期货和盘前盘后不能默认套股票日历。见 [README L184-L198](https://github.com/gerrymanoim/exchange_calendars/blob/5de07333a58052eee033246bfe63f24e71da958f/README.md#L184)。

### 6. Qlib：采用 PIT 和滚动评估，不把数据集当实时供给

官方 PIT 文档区分财报所属期和发布日期，并保存修订链；`qlib/data/pit.py` 按观察时点取特征并拒绝未来期间引用。`RollingGen` 可按测试开始时间裁剪其他分段，避免标签泄漏。见 [PIT 文档](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/docs/advanced/PIT.rst#L11)、[pit.py](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/qlib/data/pit.py)、[task/gen.py L126-L176](https://github.com/microsoft/qlib/blob/79633dd9506ea689e5400dea0197717b5b3d74b7/qlib/workflow/task/gen.py#L126)。

**采用建议：** 数据记录增加 `period_end`、`published_at/accepted_at`、`retrieved_at`、`revision_id`；实验冻结信息截止时点，按时间划分验证集，留出覆盖持有期的间隔。先积累未来真实运行的归档数据，再检验推荐质量。

**边界：** 当前仓库默认不需要机器学习训练平台。仅下载历史价格和最新财报，无法还原历史新闻、旧修订值、退市标的与历史成分股；这类缺口应限制回测结论，不能由模型补齐。

### 7. LEAN：借鉴资产语义和成交成本，不引入完整运行引擎

`SecurityExchangeHours` 显式管理时区、假期、早收盘、晚开盘；`DefaultBrokerageModel` 按资产类型提供成交、手续费、滑点和交割模型。见 [SecurityExchangeHours.cs](https://github.com/QuantConnect/Lean/blob/23b735d99a357807dc0df9f4c51d30f05fe0d277/Common/Securities/SecurityExchangeHours.cs#L27)、[DefaultBrokerageModel.cs L222](https://github.com/QuantConnect/Lean/blob/23b735d99a357807dc0df9f4c51d30f05fe0d277/Common/Brokerages/DefaultBrokerageModel.cs#L222)。

**采用建议：** 资产登记表区分股票、ETF、指数与实物黄金；研究基准和实际交易载体有单独 ID。人民币金条/金币另有纯度、重量、商家卖价、回购价和交易时间记录，不能拿期货或 ETF 收盘价替代真实可成交价。评估纳入滑点、买卖价差、费用及执行时点；用与建议周期对应的基准和总回报口径。

**边界：** 完整 LEAN 对目前 Markdown + Python 插件过重。代码可用不等于其数据服务或每个数据集免费。默认券商模型只是默认假设，不能视为用户真实成交成本。

## 次优先候选

- **zipline-reloaded**：Git tree 确认有 `src/zipline/data/bundles/core.py`、`finance/slippage.py`、`finance/commission.py` 和企业行动调整测试。本次只核验路径和元数据，没有详细审阅这些实现；不据此主张其适合所有黄金工具。已有 Qlib/LEAN 对所需设计的参考覆盖，暂不加第三个回测框架。
- **FinRobot**：抽查的 SEC 适配器实际导入第三方 `sec_api` 并要求 `SEC_API_KEY`，不能把名字中的 SEC 当成无需 key 的官方 EDGAR 直连。见 [sec_utils.py L3-L24](https://github.com/AI4Finance-Foundation/FinRobot/blob/d221910096de87579b02f8f0674652bf1a175f51/finrobot/data_source/sec_utils.py#L3)。当前零数据预算下，优先自己对接所需官方公开接口。
- **anthropics/financial-services**：官方 README 展示研究、财报更新、论点跟踪与集中连接器，适合作为报告与插件组织参考；其连接器包括 FactSet、LSEG、S&P Global 等，并不提供对应的数据授权。见 [README workflow / connectors](https://github.com/anthropics/financial-services/blob/69cbc81467a5dced793eee03dec4658aa24ef856/README.md#L117)。本次只把文档作为研究材料，没有安装或启用其中 skills/插件。

## 本仓库应该落地的能力顺序

以下是基于上述源码证据的设计建议，不是上游实现已经自动赋予本项目的能力。

| 优先级 | 小模块/契约 | 借鉴来源 | 验收重点 |
|---|---|---|---|
| P0 | 资产登记表 + 分资产能力表 | LEAN | 指数与 ETF、实物黄金成交价与参考行情不混用；不支持项目有明确状态 |
| P0 | 标准化数据记录、快照 ID、原始证据摘要 | OpenBB + TradingAgents | 同一次运行各角色用同一数据版本；来源和时间完整 |
| P0 | 日历感知的末根完整日线校验 | exchange_calendars + yfinance | 周末、假日、早收盘、夏令时、未收盘当日不误判 |
| P0 | 确定性指标 + 风险决策 | TradingAgents + ai-hedge-fund | 数值重算可重复；模型不能覆盖失败项；仓位输入不足时不输出精确仓位 |
| P0 | 不可用与冲突状态 + 显式回退链 | TradingAgents + OpenBB | 缺失不变零、无覆盖不变无事件；未知错误不伪装为无数据 |
| P1 | 版本化恢复和数据依赖检查 | TradingAgents | 快照或策略参数变化后旧结果失效；不能只判断文件大小 |
| P1 | 财报/宏观公开时间与修订档案 | Qlib | 观察时点前不可见的信息不进入历史实验 |
| P1 | 按时间冻结的评估、执行成本与基准 | Qlib + LEAN | 与简单基准和同快照单代理比较；保留区间与不确定性 |
| P1 | 独立的真实操作账本 | 本项目设计建议 | 建议和真实买卖分开记录；用户未确认成交时不能自动视为已执行 |

推荐的本地处理顺序为：

```text
用户范围/持仓约束
  -> 资产身份与免费能力检查
  -> 显式供应商适配器
  -> 原始数据归档 + 标准化 + 交易时段/口径/完整性检查
  -> 固定证据快照 + 确定性指标
  -> 单代理日常研究 / 有需要时多代理复核
  -> 确定性风险闸门
  -> 会话简短建议 + 后台证据 ID/截至时间/缺口/再评估条件
  -> 用户实际执行后，单独记入真实操作账本
```

源码中的可测机制优先于 README 的“多代理胜率更高”等宣传。本次没有审计全部仓库、执行任何上游回测、验证所有数据供应商在线覆盖，或独立复现论文收益；也没有证明本仓库现有多代理方案优于单代理。验收应分为数据正确性、推荐证据忠实度、风险一致性和事后结果四层，不能用一个“准确率”把它们混在一起。

⚠️ Educational and informational use only. Not investment advice. See [DISCLAIMER.md](../../DISCLAIMER.md) for full terms.

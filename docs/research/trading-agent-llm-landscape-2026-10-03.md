# LLM 投资 Agent：官方源码与评测证据核查

研究日期：2026-10-03。适用需求：AI 研究机会，确定性代码计算价格与资金约束，用户手动 Review 和交易；同时支持数日到数周、允许隔夜的波段模式，以及以总回报和再投资为目标的长期模式；研究范围包含 ETF 和美股个股。这里记录系统设计证据，不记录用户资产金额、账户凭据或具体投资建议。

## 结论与证据等级

最适合借鉴的是组合：TradingAgents 的研究证据和持仓上下文；ai-hedge-fund v2 的多策略净额聚合、共用账户和确定性数量接口；FinRobot 当前 desktop 的确定性估值覆盖与币种校验。这个判断来自下述固定源码，表示与需求的接口匹配度，不表示谁更赚钱。三个项目均不能提供同账户双模式、净成本、含分红再投资的持续盈利保证。

证据分为：**S**＝固定提交的实现和测试源码，静态可复核；**P**＝作者论文或官方产品声明；**I**＝基于这些材料的设计推论。本轮没有安装依赖、执行外部项目、调用其模型、运行其测试、连接账户或验证 broker 成交。测试存在只证明有相应测试意图，不能写成“本轮测试已通过”。

## 固定快照与复现边界

通过官方 Git URL 获取完整主仓库工作树，固定 HEAD；不是只读取 README，也未使用 Stars 排序。扫描了核心 source、tests、数据接口、配置与依赖声明；直接引用并逐项核实 38 个核心证据文件（TradingAgents 12、ai-hedge-fund 15、FinRobot 11，包含子模块配置）。此外做了更广的源码关键词扫描；并非逐行审阅所有 tracked 文件。

| 官方仓库 | 固定提交 / 提交时间 | Git tree / 主仓库 tracked entries |
|---|---|---|
| [TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents/tree/8b22d43d01d9ddda5d686d093d5385884622f3de) | 8b22d43d01d9ddda5d686d093d5385884622f3de / 2026-09-29T01:15:09-05:00 | e3466a68d15f48b219e269ccd48a4beed53e51cc / 204 |
| [AI4Finance-Foundation/FinRobot](https://github.com/AI4Finance-Foundation/FinRobot/tree/2717499b8e30f242640af08c4ad9afd1113c2d45) | 2717499b8e30f242640af08c4ad9afd1113c2d45 / 2026-09-28T19:24:51+08:00 | eee135451d83398acd925f061483f0c864331749 / 1049 |
| [virattt/ai-hedge-fund](https://github.com/virattt/ai-hedge-fund/tree/78b779c1389e2d1452dc29606d2c4126d859b964) | 78b779c1389e2d1452dc29606d2c4126d859b964 / 2026-10-02T10:18:57-04:00 | ecd4e52e8d527b96da874b8198d653e5444b300e / 115 |

快照保留在系统 Temp 下的 trading-agent-research-20261003-0f815bc019054f94be5002181e1dfd48，另有 snapshot-manifest.json。前后检查主仓库工作树均无源码修改。FinRobot 还获取了其 FinNLP 子模块的指定提交 587f04f473507ddea6453e43796797fce17155ce；FinNLP 内两个嵌套数据仓库 Astock 与 stocknet-dataset 没有下载，manifest 保留了其 gitlink SHA。因此“完整”指上述主仓库树，不能声称包含所有递归数据集、LFS 实体、付费 API 数据和模型权重。[子模块声明](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/.gitmodules)

复核时应固定提交而不是再次取 main。例如：

~~~powershell
$snapshotDir = Join-Path $env:TEMP 'trading-agent-source-review'
git clone --no-checkout https://github.com/virattt/ai-hedge-fund.git $snapshotDir
git -C $snapshotDir checkout --detach 78b779c1389e2d1452dc29606d2c4126d859b964
git -C $snapshotDir rev-parse HEAD
git -C $snapshotDir rev-parse 'HEAD^{tree}'
git -C $snapshotDir ls-files
~~~

另外两个仓库按表中 URL 和 SHA 复现。复核 FinRobot 子模块时使用 git submodule update --init --depth 1 -- finrobot_autogen/FinNLP；无需运行安装或第三方脚本。

## 三个框架实际具备的能力

### TradingAgents：研究和建议接口，持仓已经接通

**S：确实接收持仓，而不是默认空仓。** PortfolioContext 包含 cash、currency 和 Position(ticker, quantity, average_price)，区分“不提供上下文”“明确空仓”“已有持仓”；渲染后进入决策层。持仓 fingerprint 也能改变 checkpoint 身份。测试分别检查持仓进入 trader/risk/portfolio manager，以及 research bull/bear 层不接收账户上下文。[portfolio.py:23–63](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/portfolio.py#L23-L63)，[状态注入:305–324](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/graph/trading_graph.py#L305-L324)，[持仓测试:96–160](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tests/test_portfolio_context.py#L96-L160)

**S：有具体价格字段，但数量还不是确定性结果。** TraderProposal 有 entry_price、stop_loss，均为可空的绝对价格；position_sizing 是字符串，例如“5% of portfolio”。Trader 提示模型根据技术报告中的 current price、ATR、支撑阻力生成价位。最终 PortfolioDecision 有 rating、摘要、论据、可空 price_target 和 time_horizon；结构化输出失败时还能退回自由文本。这些字段不等于通过现金、手续费和账户净额验证的限价/股数。[schemas.py:146–187](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/agents/schemas.py#L146-L187)，[最终输出:221–263](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/agents/schemas.py#L221-L263)，[trader.py:28–61](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/agents/trader/trader.py#L28-L61)，[fallback:78–86](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/agents/managers/portfolio_manager.py#L78-L86)

**S：数值和时间证据比原始论文版更谨慎。** 当前 Yahoo snapshot 由代码计算截止日内的 OHLCV 和指标；历史日期的 Yahoo live profile 会被撤回。SEC EDGAR 路径依据 filed 日期和当时已公开的修订值返回报表，有“期末已到但尚未申报”和后续重述的测试。默认供应商还包含 Yahoo、Alpha Vantage、FRED 等选择。这里核实的是源码保护措施，没有验证所有网络源都能提供历史 vintage，也没有验证盘中报价延迟。[snapshot.py:1–51](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/dataflows/vendors/yahoo/snapshot.py#L1-L51)，[Yahoo 历史 guard:21–39](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/dataflows/vendors/yahoo/fundamentals.py#L21-L39)，[SEC vintage:158–184](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/dataflows/vendors/sec_edgar.py#L158-L184)，[SEC 测试:74–109](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tests/test_sec_edgar.py#L74-L109)，[vendor 配置:143–159](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/default_config.py#L143-L159)

**S：当前 backtest 不是资金账户回测。** 模块明写只评估 decision quality；不同 ticker/date cell 相互独立，传入的持仓是每个 cell 相同的 standing book，没有数量、成交价和现金账。不能把其决策评分当成用户账户净收益、可持续执行或真实 broker 记录。[backtest.py:1–15](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/backtest.py#L1-L15)

**I：适合借鉴**证据组织、账户未知状态、输入 snapshot 身份、技术指标的代码核验。波段和长期应成为显式的研究 mandate；不能直接相信模型填出的 entry_price，也不能从 prose 中抽取股数当成执行建议。

### ai-hedge-fund v2.5.0：最接近双模式共享账户的计算结构

**S：多个逻辑 sleeve 共用一个物理 book。** StrategySpec.weight 是相对资金 slice；assess_fund 归一各 slice，先聚合重叠标的，再经过整个 fund 的风险限制。execute_decision 从同一个 broker 的持仓与现金标记总权益，然后计算最终 delta orders；不会给每个 strategy 各复制一份完整账户资金。注意这是目标贡献归因，尚不等于按实际部分成交追踪每个 sleeve 的 lot 和已实现盈亏。[spec.py:42–104](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/fund/spec.py#L42-L104)，[净额聚合:55–100](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/stages.py#L55-L100)，[共用 book:120–165](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/stages.py#L120-L165)

**S：共享现金有明确测试。** test_netting_math_two_strategies_unequal_slices 手算 3:1、重叠 ticker 的净额；test_second_cycle_rebalances_not_restarts 验证持仓延续后不重复下单；flat sleeve 测试验证其资本留在现金；offset 测试保留互相抵消的贡献。上述是 fake analyst/data 和 SimBroker 的确定性测试，不是实盘验证。[test_stages.py:131–203](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/test_stages.py#L131-L203)，[现金与抵消:325–372](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/test_stages.py#L325-L372)

**S：数量和价格确实计算，但价格是模拟执行参考 close。** build_orders 使用 int(weight × equity / mark)，减已有股数得 delta，零股不发单，先卖后买。risk 代码设单标的和总 gross exposure 上限；conviction 的归一化明确不代表预期收益校准。不能把 persona 投票或 [-1,1] conviction 当作盈利概率。[execution.py:16–53](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/execution.py#L16-L53)，[limits.py:21–31、52–102](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/risk/limits.py#L21-L102)，[conviction 限制:28–45](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/portfolio/construction.py#L28-L45)

**S：当前已实现持续纸账户，旧 ROADMAP 不能代表现状。** PaperBroker 将独立模拟持仓文件原子持久化；SessionRecord 有 hash 链、code/mandate 版本、pending decision，advance 先核对 broker 与 ledger 的现金和股数。持仓核对有实际实现，而不是同一个文件与自己比较；paper tests 用 temp 目录和 fake clock/data。[paper.py:1–73](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/brokers/paper.py#L1-L73)，[session 状态:64–109、185–199](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/session.py#L64-L199)，[paper 测试](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/paper/test_tick.py#L1-L72)

**S：时间和真实成交限制。** 评估与执行分开，pending 在后续 session 的 close 成交；周末等间隔会以执行日前一天重新 assess。completed_through 甚至排除当前纽约日期，实际循环是已完成 daily 数据，不是盘中实时交易。财务接口按 filing_date_lte 查询，另有测试。SimBroker 全量按 reference close 填单，明示未建模手续费、滑点与 margin；Broker 协议只接受完全成交或异常，真实 broker 是后续扩展，不能据此声称支持 IBKR 的异步和部分成交。[时序:1–26、129–139](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/session.py#L1-L139)，[刷新:120–133](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/stages.py#L120-L133)，[daily 截止:13–29](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/data/sessions.py#L13-L29)，[财报 PIT:98–122](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/data/client.py#L98-L122)，[模拟限制](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/brokers/sim.py#L1-L60)，[Broker 契约](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/brokers/protocol.py#L1-L34)

**S/I：不能确认 ETF 总回报和多币种现金正确。** Price 仅 OHLCV/time，没有分红或 adjustment 标志；模拟现金只因买卖变化，NAV 为 cash + shares × close，benchmark 也是 close 比例。扫描没有发现显式分红到账、DRIP 或拆股调整持仓事件；本轮未核实 FD 返回 close 的调整定义，因此不能断言它已包含或完全排除分红。book 是单一 cash 数值、整数 shares，未见账户现金币种向量。研究资产的含分红收益与真实每股执行价格需要独立定义。[Price:19–29](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/data/models.py#L19-L29)，[现金计算:38–60](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/brokers/sim.py#L38-L60)，[NAV:141–149](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/session.py#L141-L149)，[benchmark:132–145](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/backtesting/fund.py#L132-L145)

**I：最值得借接口，而不是整仓替换。** 可借 StrategySpec → assessment snapshot → combined targets → deterministic risk/sizing；在本项目终止于 ReviewRecommendation，不能调用模拟 fill 来更新真实账本。需另补真实现金、费用、pending 建议占用、sleeve 成交归属及分红事件，避免短线建议把长期持仓视为隐式零目标而卖出。

### FinRobot：确定性价格校验强，用户账户数量不是核心接口

**S：当前不是只有旧 AutoGen demo。** 主仓库含 finrobot_autogen、finrobot_equity 与 finrobot_desktop；desktop 有独立 typed data/compute/artifact/backtest 层。估值聚合 leaf 明确不调用 LLM。apply_canonical_override 在模型生成后强制 price_target、recommendation、basis 与确定性 canonical 对齐，并处理自由文本中的漂移价格；可用目标缺失时撤回 point。[仓库目录](https://github.com/AI4Finance-Foundation/FinRobot/tree/2717499b8e30f242640af08c4ad9afd1113c2d45)，[纯估值聚合:1–25](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/compute/operators/valuation_aggregator.py#L1-L25)，[canonical override:1307–1405](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/pipelines/equity_research.py#L1307-L1405)

**S：币种校验值得借鉴。** artifact contract 在跨币种数值 audit 被阻断，或 quote/reporting currency 不完整时，撤回每股目标值。tests 复核 TWD 报表、USD 报价的 ADR 数量级，也检查拆股未调整的双源价格分歧。这比只要求 LLM 输出 JSON 更接近可信金额约束。[币种 contract:286–337](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/artifact/contract.py#L286-L337)，[跨币种测试:69–145](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/tests/unit/test_cross_currency_valuation.py#L69-L145)，[价格测试:39–75](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/tests/unit/test_price_cross_validate.py#L39-L75)

**S：有保留尾部 OOS 的策略选择，但仍是历史研究。** strategy_agent 规定最多三次 IS 选择、70/30 日期划分，OOS 不足 60 日时标注无 holdout；最终参数在 OOS 运行一次。BackTrader adapter 明示零手续费、零滑点。这个切分避免优化循环直接查看 holdout，却不能消除 pretrained LLM 对历史 ticker/date 的记忆，也不能证明当前 desktop 真实账户净收益。[切分设置:34–55、130–147](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/backtest/strategy_agent.py#L34-L147)，[OOS 单次运行:316–331](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/backtest/strategy_agent.py#L316-L331)，[成本披露:188–193](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/backtest/backtrader_adapter.py#L188-L193)

**S/I：不能把估值区间当成交易胜率。** DCF 的 ±20% band 明确是 placeholder，不是模型分布。核心 backtest 的输入是单 ticker、日期、参数、initial_cash；所检查的研究入口也是 ticker/pipeline，并未给出用户持仓→可负担 delta shares 的完整执行接口。搜索到的 institutional_holdings 是 SEC 13F 机构持仓，不是用户 book。没有从这些路径核实 IBKR/paper broker 账户集成。这里的估值 target 也不能直接解释为今日推荐挂单价。[placeholder:498–511](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/compute/operators/valuation_aggregator.py#L498-L511)，[BacktestConfig:20–37](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/backtest/engine.py#L20-L37)，[研究入口:176–191](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/orchestrator.py#L176-L191)，[机构持仓模型:133–145](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/models/sec.py#L133-L145)

**I：适合长期个股研究的数字来源、币种、估值敏感性与报告一致性。** 波段可借技术数据核验；账户 sizing 仍应由本项目独立完成。可借“模型文字服从代码值”的边界，不必复制其庞大 desktop UI 或所有估值假设。

## 论文与评测：哪些结果能迁移

以下摘要均来自论文作者或官方站点；本轮不独立复现论文收益。新预印本只读摘要的项目明确标注，不能将其作者结论升级为实盘证明。

| 一手来源 / 日期 | 核实内容与本项目价值 | 证据边界 |
|---|---|---|
| [TradingAgents, arXiv 2412.20138v2](https://arxiv.org/html/2412.20138v2) / 初稿 2024-12 | 模拟 2024-01-01 至 03-29，报告若干个股收益、Sharpe、回撤；角色辩论适合研究组织。 | 窗口短；论文的 executed signals 不能替代当前源码明确的 decision-quality backtest；未在本轮确认严格冻结的未来 OOS、净费用和用户账户迁移。 |
| [FinRobot 平台论文 2405.14767](https://arxiv.org/abs/2405.14767)；[equity research 2411.08804](https://arxiv.org/abs/2411.08804) / 2024-05、11 | 金融研究/报告与估值平台的背景。 | 2024 论文不自动覆盖 2026 desktop 新实现；报告质量不是账户 alpha。本轮主要依据上面的固定源码，而非论文效果宣传。 |
| [FinMem 2311.13743v2](https://arxiv.org/abs/2311.13743v2)；[作者仓库](https://github.com/pipiku915/FinMem-LLM-StockTrading) / 2023-12 | Profiling、分层记忆、decision-making；官方程序区分 train/test，test 使用已训练 memory。可借研究线索的时间分层。 | 本轮未取得该项目完整固定快照；不是已核实的 broker/账户执行组件。记忆的训练区间、时间隔离和实际成交事实需要另验。 |
| [FinAgent 2402.18485v3](https://arxiv.org/abs/2402.18485v3) / 2024-06 | 数值、新闻、图表、双层 reflection、工具和记忆；论文覆盖六个股票/crypto 数据集。 | 论文中高收益是特定实验，不是未来保证；本轮没有复现其代码或成本。不要将同名 GitHub 项目自动视为该论文官方实现。 |
| [StockBench 2510.02209](https://arxiv.org/html/2510.02209)；[作者代码](https://github.com/ChenYXxxx/stockbench) / 2025-10 | 20 个预选高权重 DJIA 股票，方法章节窗口 2025-03-03 至 06-30；每日 portfolio overview → research → dollar targets → 股数/现金校验。多数模型未胜过持有基准。 | 不检验完全自主发现投资标的；作者的 contamination-free 标签需按每个新模型重新审查，不能在 2026 直接沿用旧窗口。 |
| [AMA: When Agents Trade, 2510.11695](https://arxiv.org/html/2510.11695) / 2025-10 | 作者报告 2025-08-01 至 09-30，TSLA/BMRN/BTC/ETH 的实时输入、统一 daily actions；适合统一 prompt、日历、费用与轨迹的评测接口。 | 明确更新 simulated portfolio，不是 broker 实盘；仅两个月四资产。框架封装与当时模型结果不能当作当前 2026 模型排名。 |
| [TradingArena 官方说明](https://www.tradingarena.ai/leaderboard) / 访问 2026-10-03 | 每模型纸账户、相同 market snapshot、固定风险比例、按 target 先于 stop 命中评分。 | 官方明确 simulated paper 与非真实回报；目标命中率不等于净收益。没有固定源码快照/完整重跑数据，不能据动态榜单选择“最赚钱 Agent”。 |
| [FINSABER 2505.07078v6](https://arxiv.org/abs/2505.07078v6) / 最新 2026-06-26 | 作者在两十年、100+ symbols 中发现先前 LLM 优势随更广时间和截面退化；最新修正 FinAgent 结果，结论不变。 | 本轮核实摘要/版本记录，未重跑数据；长期窗口也需单独处理模型历史记忆污染。适合要求多 regime、更多资产和被动基准。 |
| [KTD-Fin 2605.28359v1](https://arxiv.org/abs/2605.28359v1) / 2026-05-27 | 对 ticker/date 等一致 masking，结合 Barra 风格归因；作者在 CSI300 2024–2026 的十个模型中发现收益多由市场/style 暴露解释。 | 本轮只复核摘要；不能直接外推美国 ETF，但说明赚钱与独立选股能力需要不同评价。 |
| [EvolveTrade 2609.17632v1](https://arxiv.org/abs/2609.17632v1) / 2026-09-15 | 用已实现反馈和 traces 按更新 interval 改研究 prompt，固定 backbone；可借研究 prompt 的版本化评估。 | 新预印本，本轮只读摘要；未复核实验代码/split。不能据此让生产自动修改资金规则，也不能边改 prompt 边宣称冻结 OOS。 |
| [FARSIGHT/SoK 2609.19705v1](https://arxiv.org/abs/2609.19705v1) / 2026-09-17 | 作者调查 15 个 academic trading schemes 的市场压力和信息/Agent 攻击。提醒测试新闻污染与崩盘情境。 | 只复核摘要，作者实验结论不代表本项目已被攻击；手动交易仍需要研究证据和风险规则的独立边界。 |

“AgentMarket”不是足以唯一识别论文/产品的名称。本笔记采用已核实标题的 **Agent Market Arena（AMA）**；不把其他同名 Agent API marketplace、链上代理商店或排行榜混为交易评测。

## 与本项目最匹配的接口草案（设计推论，尚未实现）

研究模型输出 ResearchIdea：symbol、mode、as_of、catalyst、evidence_refs、invalidation、research_horizon。这些是可被质询的研究观点；evidence_ref 必须能落回时间戳和来源，model confidence 只作为模型自评，不充当已校准胜率。

由确定性模块读取同一 AccountSnapshot 和不同 logical sleeve 的 mandate，使用 QuoteSnapshot 与固定 PricingPolicy / SizingPolicy，输出 ReviewRecommendation：

~~~text
recommendation_id
account_snapshot_id, quote_snapshot_id, research_version, policy_version
symbol, sleeve_id, mode
observed_quote, quote_currency, quote_as_of
valuation_target / technical_reference         # 研究参考，不冒充成交价
suggested_limit_price, quantity, delta_quantity # 通过规则与资金验证
fee_estimate, projected_cash_by_currency, aggregate_risk_after
valid_until, invalidation_conditions, state=review_required
evidence_refs, unresolved_issues
~~~

这组字段借鉴上述项目的深接口并补齐已发现的缺口，不是复制某个库 API。具体规则与账户参数仍需用户确认。最小不变量：

1. 物理账户只保留一份真实持仓/各币种现金；波段与长期 sleeve 有归属和预算，两者的总建议同时接受全账户风险与可用现金约束。逻辑调拨不冒充真实买卖。
2. 建议不修改真实 shares/cash、不计成实际执行；同一资金的未解决建议需要占用/冲突规则和到期失效规则。只有用户报告的真实成交才进入真实账本，记录部分成交及 sleeve 归属。
3. observed quote、研究 fair value、建议 limit 和真实 fill price 四者分开。明确 timestamp、session、币种和有效期；新行情/新账户状态变化需要重新校验，历史 next-close 模拟价格不变成今日可成交报价。
4. 长期总回报要验证分红到账、税费/成本、再投资建议和实际执行链；未显式验证 adjustment 定义前，不将调整价格与现金分红重复计入。分红率、价格涨幅、总回报和个人账户资金流分别记录。
5. 无可靠 quote、关键账户值、足够数据或可通过资金约束的 quantity 时，允许 research_only 或暂不建议交易；不能强迫每次 research 都产生买卖动作。

以上不变量是从共用 book、模拟 fill 与 canonical override 的实际边界推导，不能依靠多角色“风险经理”文字承诺代替。

## 有效性验收建议（设计推论）

为手动 Review 的波段/长期分别冻结标的 universe、mandate、数据截止、prompt/model/code/policy 版本及执行假设。先评研究和金额正确性，再评策略效果：

| 验收层 | 必需证据 | 不能替代的证据 |
|---|---|---|
| 事实/数值 | source/timestamp/currency/units、stale/缺数降级、同账户不同 sleeve 同时建议、手续费后现金、部分成交和分红 ledger | JSON 可解析、多 persona 一致、单元测试数量 |
| 历史策略 | PIT 财报与新闻；参数只在训练/验证集选；多 regime/标的；延迟执行、费用/滑点/换汇与分红处理；多个 seed/重复运行 | 单一上涨窗口、一条最好的 equity curve、模型金融 QA 分数 |
| 严格未来样本 | 在未来时段开始前冻结全部版本/筛选规则；不可借未来结果修 prompt 后仍沿用同一 OOS 标签；保存真实调用与失败覆盖率 | 对模型可能记忆过的历史窗口仅做日期切分 |
| 前瞻纸面建议 | 当时行情快照、建议有效期、可成交/未成交条件、同账户预算冲突、建议与用户真实操作分别评估 | 新闻是实时的、纸面 fill 被写为真实持仓 |
| 收益解释 | 被动持有、不交易、同预算确定性规则基准；净收益/风险/周转/资金使用、LLM 成本；market/style 与选股贡献 | Target 命中率、毛收益、把行情 beta 当研究 alpha |

以上是可检验的研究设计，不意味着这些框架或本项目已经通过验收。持续盈利保证不存在；工程成熟度、研究质量和未来投资回报应分别呈现。

# Investment Chat：固定源码的功能迁移与最低研究信号合同

核验日期：2026-10-03。项目基线：`a20da36ae486160cb8acd47fdb7fe25714a6e0cf`。本次仅静态读取本项目和上一轮固定源码；未重新扩大网络搜索、未执行第三方代码、未测试模型或策略收益、未读取私人配置和账户状态。本文不包含用户资产金额。

此文保留实施前的功能迁移判断；新增代码与验收状态见[实现记录](trading-skill-implementation-2026-10-03.md)。下文“尚未实现”描述原设计基线，不能当作当前运行能力清单。

结论：先补一个基于已校验日线的**确定性研究信号**，再让 Skill 强制完成市场背景、量化卡、反证和触发/失效条件。研究信号必须 `research_only`，不输出股数、委托限价或止损订单；精确交易建议继续走现有已采用规则及资金校验。加入更多指标或多角色投票不能替代这条合同。

这是研究层的中间交付，不是用户完整目标的完成标准。完整升级须在账户、报价和已采用策略验证通过时，自动给出规则计算的买卖价格与股数，并完成手动成交后的对账。完整的五仓库对应与交易卡验收见 [Skill 升级合同](trading-skill-upgrade-2026-10-03.md)。

## 固定证据范围

复用上一轮完整顶层 Git 快照，源码引用均绑定以下 SHA；这里新增核查了指标输出、PEAD、基本面快照、组合净额、Sniper 算法和测试，没有依赖 README 的功能宣称。

| 仓库 | 完整 commit SHA | 本次直接核查的主要范围 |
|---|---|---|
| TauricResearch/TradingAgents | `8b22d43d01d9ddda5d686d093d5385884622f3de` | Yahoo verified snapshot、trader、bear researcher |
| AI4Finance-Foundation/FinRobot | `2717499b8e30f242640af08c4ad9afd1113c2d45` | Sniper 数学、technical payload 协调层、Sniper 单元测试；前轮已核估值 synthesis 与 canonical 输出 |
| virattt/ai-hedge-fund | `78b779c1389e2d1452dc29606d2c4126d859b964` | fundamentals snapshot、numeric helpers、PEAD、signals tests、construction、pipeline stages/execution |

完整获取范围、子模块边界和前轮论文证据见 [上一轮研究](trading-agent-llm-landscape-2026-10-03.md)。这些项目的静态源码和测试结构证明接口或算法存在；不能证明策略赚钱。

## 本项目已经有什么

`compute_indicators` 已输出 SMA20/50/200、Wilder RSI14/ATR14、20/252-session 收益率、252-session 高低和20-session均量，并保存价基、样本数和公式版本。收益率需要额外一个起点；ATR 使用同价基 OHLC。历史缺口不会被当作连续有效样本。现有数据主张限于已完成 session close，交叉来源主要核对价格，不证明量价全字段被双源认证。[指标公式](../../scripts/copilot/market_data.py#L42)、[快照取值](../../scripts/copilot/market_data.py#L306)

已采用规则另有固定权重 bands、63-session 逆波动、12-1 top-N 动量。逆波动和动量主要决定权益资产之间的权重，不是识别熊市后退出现金的择时策略。当前同收盘回测明确包含当前 bar；next-session-close 仅为研究敏感性，不能采用、不能当 next-open 或收益下界。[规则](../../scripts/copilot/backtest/rules.py#L124)、[动量](../../scripts/copilot/backtest/rules.py#L205)、[执行假设](../../scripts/copilot/backtest/engine.py#L199)、[敏感性](../../scripts/copilot/backtest/execution_sensitivity.py#L78)

已有 SEC 接受时间与 accession 约束、FRED vintage/release 边界、新闻时间窗；SEC 目前保存部分财务原始事实，并不生成 TTM、EPS、FCF、P/E 或 DCF。缺新闻不能推断无事件，日期可见性不能冒充历史日内可见性。`valuation.py` 是组合市值历史，不是公司内在价值估算。[SEC facts](../../scripts/copilot/research_data.py#L274)、[宏观边界](../../scripts/copilot/research_data.py#L197)、[新闻](../../scripts/copilot/research_data.py#L315)

已有单笔/组合风险、ADV 流动性、组合观察回撤、成本与 cash floor 的确定性 sizing；新闻 brake 只能减少或跳过，不能放大交易。完整组合和币种条件仍是资金建议前提。Skill 当前支持注册 ETF、指数背景与人民币黄金，不能仅改提示词就宣称任意美股个股已接入。[风险输入](../../scripts/copilot/riskinputs.py#L78)、[资金计算](../../scripts/copilot/sizing.py#L95)、[brake](../../scripts/copilot/brake.py#L58)、[身份注册](../../scripts/copilot/instruments.py#L54)

## 可迁移功能与边界

| 功能 | 固定源码确实实现的内容 | 本项目迁移合同与优先级 |
|---|---|---|
| 市场 regime | TradingAgents verified snapshot 输出均线、RSI、ATR、MACD/Bollinger 等；指标集合本身不是经过盈利验证的 regime 策略。ai-hedge-fund 的 QuantModel 注释允许 regime 子类，不代表已有可靠 regime 模型。 | **先做**同价基均线排列与动量的观察状态；unknown/mixed 必须存在。宏观信息解释背景，不用单次利率变化自动决定买卖。 |
| 波动、动量、量价 | TradingAgents 把指标变为工具事实后再交模型；ai-hedge-fund 的 RSI 使用简单滚动均值，其缺失默认值与本项目 Wilder/unknown 合同不同。 | **先做** ATR相对价格、距均线比例、量比；保留本项目算法，不替换为对方同名 RSI。数据不足返回 None，不补0或50。 |
| 基本面估值 | ai-hedge-fund 有 filed/PIT fundamentals snapshot；FinRobot 有确定性估值和技术输出协调层，并在估值点被撤回时隐藏方向性 Sniper。 | **后做**个股财务标准化及估值范围；必须区分报告期、公开时间、币种、修订、股数与市场价格。ETF 不套公司 DCF。可靠估值缺失时只展示已验证事实。 |
| 候选发现 | ai-hedge-fund 在策略定义的 universe 中逐标的生成信号、组合；这不同于已验证的全市场机会发现。TradingAgents 核查路径是给定标的分析。 | **先限定**注册候选池，输出入选/拒绝/缺失原因和覆盖率。个股需后端身份、数据、风险支持；不能用 LLM 搜到 ticker 就产生可执行建议。 |
| 反证 | TradingAgents bear researcher 明确使用财务、趋势、新闻反驳 bull argument，但仍为模型提示而非真假裁决器。 | **立即改 Skill**：每个机会至少一个最强反证、可推翻的条件、缺数据；反证必须引用同一快照。无需先搭多 agent 辩论或多数票。 |
| 组合净额 | ai-hedge-fund 合并 strategy slice 后统一风险缩放，再与实际持仓计算 delta。 | **保留本项目资金核心**：同一物理账户、逻辑用途、共同 cash/cost/集中度约束；研究候选仅为局部意图。缺席候选不意味着现有长期仓位归零。 |
| 成本与定价 | FinRobot 有数学价格梯度与取整不变量；ai-hedge-fund delta orders 是整股目标差额。 | **不直接照搬订单层**：成本、价差、FX、结算现金、成交回填由确定性本地层处理。手动 review/实际成交与 research intent 分开。 |
| 信号有效性 | PEAD 默认只用8-K BEAT/MISS、在最近 filing 后短窗口生成固定±1 conviction；各仓的算术/不变量测试不等于未来收益验证。 | **先验算，再验证策略**：版本化输入、公式与状态可重现；冻结样本外、净成本、基准与前瞻 paper evidence 单独报告。confidence 不叫胜率。 |

源码依据：[TradingAgents snapshot L23–90](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/dataflows/vendors/yahoo/snapshot.py#L23-L90)、[bear prompt L31–50](https://github.com/TauricResearch/TradingAgents/blob/8b22d43d01d9ddda5d686d093d5385884622f3de/tradingagents/agents/researchers/bear_researcher.py#L31-L50)、[numeric helpers L56–97](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/signals/base.py#L56-L97)、[PIT fundamentals L178–234](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/features/snapshot.py#L178-L234)、[组合净额 L55–100](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/stages.py#L55-L100)、[PEAD L25–100](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/signals/pead.py#L25-L100)。

## 最小确定性研究分析：建议实现，尚未实现

复用 `compute_indicators` 接收的已校验连续 bars；不另抓一份行情，不让模型计算百分比。建议加在 `snapshot.instruments[symbol].indicators`，以便继续走已有 computed-claim 路径。

| 建议字段 | 确切计算合同 | 样本与缺失合同 |
|---|---|---|
| `reference_close` | 与 SMA/ATR 一致的最后一个收盘；total_return_adjusted 时为 adjusted_close | 有效末 bar；不是可成交报价 |
| `atr14_percent` | `100 * atr14 / reference_close` | 继承15个可比 OHLC 条件；缺 ATR 则 None |
| `distance_sma50_percent` / `distance_sma200_percent` | `100 * (reference_close / SMA - 1)` | 对应均线有效、分母正；不拿 raw price 除 adjusted SMA |
| `average_volume_previous_20_sessions` | 最新 session **之前**20个 session 的均量 | 至少21 bars，单位一致、有限非负；任何缺量则 None |
| `volume_ratio_20_sessions` | 最新 session volume / 上述此前均量 | 此前均量>0；0分母或无 volume 则 None。不是成交活跃的独立认证 |
| `trend_state` | `reference_close > sma50 > sma200` 为 up；严格反向为 down；其余 mixed | 任一缺失则 unknown；只是观察分类，没有胜率含义 |
| `momentum_state` | 20-session return 为正/负/零，分别 positive/negative/flat | 缺20-session return 则 unknown；与 trend 冲突时展示冲突 |

保留已有 basis、sample_count、missing；新增公式应升级 `formula_version`，保存规则版本和实际 session 日期。阈值若以后改变，旧快照不能被静默重算。ATR%衡量幅度，不是下跌概率；量比衡量相对历史量，不能单独证实资金流入。

第一批不必增加 MACD、Bollinger、EMA 或几十个高度相近的特征。相对 benchmark 强弱可留第二批：必须使用两个标的同起止交易日、同收益定义；“各取最后20个bar”在缺交易日时并不保证同一窗口。若真要数字触发水平，再增加同价基的**此前**20-session close high/low，而非让模型推支撑阻力；它们只是历史收盘极值，不能冒称市场已验证支撑、盘口价格或当日 OHLC 突破。

claims 的兼容点：当前白名单允许本标的 `price` 与 `indicators/` 下已存 scalar，但不允许模型供应 quantity/target_weight/stop_loss，model price 只能等于已捕获价格。百分比文本当前仅识别 `return_*` 或末字段 `_percent` 等有限合同；`*_pct` 新字段会不兼容。显式“倍”文本目前始终拒绝，新增量比若展示为倍，需增设路径级 typed unit 合同及测试，不能只改 Skill 心算绕过。[claim 核验](../../scripts/copilot/policy.py#L177)、[模型交易字段约束](../../scripts/copilot/policy.py#L304)

建议研究对象包含 `snapshot_id`、`instrument_id`、`horizon`、`formula_version`、`signal_rule_version`、`observed_state`、`trigger_condition`、`invalidation_condition`、`counter_evidence`、`missing`、`scope="research_only"`，不包含 shares 或订单价格。未增加后端前，Skill 只能定性描述已存指标之间的关系，不能宣称这些新字段已经计算。

最小状态逻辑只负责描述：数据质量失败 → insufficient；trend/momentum 一致 → 有方向的研究条件；冲突 → mixed/watch；不存在完整组合 → 仍 research_only。up 不自动等于 buy，down 不自动等于 sell。反证、已知事件与组合约束仍可以降低研究结论。不得给这些状态附未经校准的收益概率。

## Skill 输出的最低集

1. **身份、模式、证据**：明确波段（日到周、可隔夜）或长期（总回报）；标的身份、snapshot、末有效 session、价基、质量和缺项。宏观与公司研究缺失明示 unknown。
2. **量化卡**：只引用实际返回的趋势、动量、波动、量比与样本；必要时 full snapshot。所有数字附 exact scalar claims；背景与事实区分。
3. **研究结论**：已满足研究条件/等待条件/结论失效/资料不足。它们是展示语义，不能偷偷增加未实现的 proposal action enum，也不能跳过 assess。
4. **明确触发与失效**：例如“后续已完成交易日的同价基收盘重新站上 SMA50 后再评估；跌回或证据失效则撤销研究结论”。必须说明需要新快照重新评估；当前快照里的已满足条件不能冒充未来成交。数值水平若没有后端字段不编造。
5. **最强反证**：至少一个能推翻结论的事实/条件，指向证据和可能的缺数据；不能拿同一模型的反方措辞当第二个独立来源。
6. **账户适用性与下一动作**：研究信号无订单；要求精确股数/价格时调用已采用规则、成本与账户校验，报告返回的 action/scope。没有采用规则或缺完整现金/FX时维持 research_only。

可用五至六行中文模板：

```text
结论：{已满足研究条件/等待/失效/资料不足}；{波段/长期}；research_only。
量化：{同价基趋势与动量}；{已存波动/量比}；{缺项}。
背景：{可见宏观/公司事件事实}；{哪些仍未知}。
触发/失效：{可重新检验的条件}；后续新快照确认，不是订单。
反证：{最强相反证据及如何推翻结论}。
证据：{session、snapshot、来源}；精确交易计划须另走已采用规则。
```

长期与波段共享账户时，这个模板不分配两份可用现金；新增波段机会不改变长期持仓归属。长期评估要以总回报和可解释的公司/ETF证据为主，不能让日线噪声自动触发清仓；波段可重视价量状态，但不能因此把它称为盈利已验证的策略。

## 两个不能照搬的源码合同

FinRobot 的 Sniper 算法确实使用最后20个收盘的 min/max、DCF折扣、波动缓冲和取整后的方向不变量；测试覆盖 rounded-collapse、不可成立的次级买入位和 levels-only。可迁移的是“可靠目标点缺失时，交易字段全部 None、仅显示历史水平”和对已发布取整数字做不变量校验。不能直接采用其1–5%仓位启发式、默认波动或 DCF→SHORT：其 R/R 使用 current 而非 ideal_buy 计算，估值可靠性、费用、借券和成交合同还需另验。[Sniper 数学 L109–240](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/compute/operators/sniper.py#L109-L240)、[取整不变量 L242–310](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/compute/operators/sniper.py#L242-L310)、[levels-only L314–348](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/finrobot/engine/compute/operators/sniper.py#L314-L348)、[测试 L628–724](https://github.com/AI4Finance-Foundation/FinRobot/blob/2717499b8e30f242640af08c4ad9afd1113c2d45/finrobot_desktop/tests/unit/test_sniper.py#L628-L724)。

ai-hedge-fund 的完整 target book 里，已有持仓未出现在 targets 会隐含目标0而生成平仓。这适合声明完整目标组合的基金流水线，不能用于“本次只发现几个新机会”的局部建议；其 strategy slice 是贡献归因，不是独立现金账户。PEAD 以 filing 的日期可见性和固定±1形成观点，不能当作 earnings 公布后的分钟级反应或胜率。既不能把遗漏候选当清仓，也不能让 persona confidence 直接越过本项目风险/资金规则。[目标差额 L16–53](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/pipeline/execution.py#L16-L53)、[组合信号 L29–79](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/portfolio/construction.py#L29-L79)、[PEAD 窗口 L25–100](https://github.com/virattt/ai-hedge-fund/blob/78b779c1389e2d1452dc29606d2c4126d859b964/hedge_fund/signals/pead.py#L25-L100)。

## 验收分两层

**算法与证据正确性**：人工已知序列验算百分比/均线/量比；21-bar量比排除当前bar分母；缺量、零分母、NaN、样本不足保持 unknown；raw/adjusted切换、拆股/分红前后价基一致；前缀快照不受未来新增bar影响；不同标的或过期 evidence 的 claims 失败；百分比与倍单位准确；信号只有 research scope；prompt 文本或云输出不能改变资金计算。这些测试只证明合同。

**策略有效性**：若以后把研究条件升级为可采用策略，另行冻结特征、阈值、规则与模型版本，使用时间上严格隔离且未调参的样本外，比较适合该 universe 的基准，计入成本/FX/分红/再投资及执行时点；覆盖不同市场状态和种子，报告覆盖率、无信号期、失败、最大回撤与不确定性。历史 LLM 已见到的事件、survivorship、研究者挑选标的和短样本都限制收益结论。前瞻 paper review/真实手动成交日志与算术测试分开。任何代码仓库、测试通过或一次回测均不能保证赚钱。

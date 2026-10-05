# investment-chat：当前能力与最小优化边界

核查日期：2026-10-03；HEAD `a20da36ae486160cb8acd47fdb7fe25714a6e0cf`。只读核查 canonical Skill、两份引用、MCP/CLI、共享核心与相关测试；未读取私人状态、配置或密钥。仅本报告为新增产物，不修改产品或提交。
这是实施前的能力快照；当前能力与测试结果见[实现记录](trading-skill-implementation-2026-10-03.md)，不能用下文缺口判断升级后的运行状态。

## 结论

**当前 Skill 已有确定性量化、日线信息收集与规则建议；缺少的是清晰默认路由、指标到交易信号的明确合同，以及真实账户/执行报价。** 可以先改善 Skill 的调用顺序、事实/判断区分和输出；不能仅靠提示词获得个股筛选、实时价格、自动账户资金或已验证的技术交易策略。

真正的数量入口是 `evaluate_adopted_rule`；普通 `assess_investment_proposal` 不计算仓位。已完成市场快照中的 `price` 是完成交易日收盘基准，当前 `execution_scope=actionable` 也不能被解释为券商当前可成交价已核实。

## 1. canonical Skill 与公开调用链

| 路径 | 当前行为 | 优化含义 |
|---|---|---|
| [SKILL:14](../../.claude/skills/investment-chat/SKILL.md#L14) | 白名单 ETF、Nasdaq benchmark、CNY gold；未知 ticker 拒绝 | 不能只改 description 宣称支持主动个股 |
| [SKILL:19](../../.claude/skills/investment-chat/SKILL.md#L19) | context → 一个新 snapshot → model proposal → assess | 普通市场问题默认是研究路径，不能期待它自动出现规则数量 |
| [SKILL:34](../../.claude/skills/investment-chat/SKILL.md#L34) | 数字通过 claims 的 evidence ID/JSON Pointer/原值核验 | 可加强量化事实输出；不能把模型计算的比值、胜率或目标价补成已验证字段 |
| [SKILL:69](../../.claude/skills/investment-chat/SKILL.md#L69) | 用户问 rule orders/coverage/setup 才读 adopted-rules 引用 | 需明确一般“现在怎么办”问题是否也在已有 adopted rule 下自动运行规则；不能自动采用新规则 |
| [adopted-rules:3](../../.claude/skills/investment-chat/references/adopted-rules.md#L3) | history 只研究；新 snapshot/evaluate；schema3；实际成交才推进 cadence | 已有安全边界应保留，不能复用旧数量 |
| [adopted-rules:24](../../.claude/skills/investment-chat/references/adopted-rules.md#L24) | 必须采完整采用 universe；gold 附加用户报价，并用返回的新 snapshot ID | 单标的快照不能替代规则篮子输入；gold benchmark 不是商家 ask |
| [operations:26](../../.claude/skills/investment-chat/references/operations.md#L26) | Decimal、未知不猜、幂等、修正与 committed receipt | 不把研究建议写成成交；当前持仓声明不能伪造历史买入 |

MCP 有九个能力：[collect/snapshot/context/review/record](../../mcps/copilot_mcp.py#L23)、[capabilities/coverage](../../mcps/copilot_mcp.py#L69)、[gold quote](../../mcps/copilot_mcp.py#L90)、[evaluate](../../mcps/copilot_mcp.py#L110)。CLI 对应 snapshot/get-snapshot/context/review/record/capabilities/declare-coverage/record-quote/evaluate，另有 resolve/config/backup/adopt；[CLI:30](../../scripts/copilot_cli.py#L30) 的 `--db` 支持隔离路径。MCP 的 capabilities 只检查凭证存在，不是实际权限验收。[service:547](../../scripts/copilot/service.py#L547)

## 2. 量化、收集、市场分析：已实现与未实现

| 用户期望 | 已有可用核心 | 实际边界 |
|---|---|---|
| 技术指标 | [compute_indicators:42](../../scripts/copilot/market_data.py#L42)：SMA20/50/200、Wilder RSI14/ATR14、20/252-session return、252-session high/low、20-session average volume；basis/sample_count/formula_version/missing | 确定性事实，不是自动买卖策略；没有 MACD/Bollinger 等未实现指标；不能由模型心算填缺项 |
| 数据质量与时序 | [collect_snapshot:194](../../scripts/copilot/market_data.py#L194)：1–16 symbols，260–400 个完整日线；身份、日期、数值与对齐 close 校验 | daily/swing/long_term 均为日线；cross-provider OHLCV=False；source observed_at 通常是 scheduled close，精确 publication 未知。[语义:160](../../scripts/copilot/market_data.py#L160) |
| 新闻与宏观 | [research.enrich:354](../../scripts/copilot/research_data.py#L354)：Finnhub 近七日最多15条新闻；FRED DFII10/DGS10/DTWEXBGS/CPIAUCSL；各部分有 unavailable/status/eligibility | 不是完整市场事件日历、预期共识或宏观 surprise；新闻为 aggregator 且 critical=False。[news:315](../../scripts/copilot/research_data.py#L315) |
| 个股财报 | [sec_company:217](../../scripts/copilot/research_data.py#L217) 已有 accepted-at 与近期 companyfacts 对齐 | 正常 `get_instrument` 白名单拒绝一般个股，stock 分支在正常收集入口不可达。[registry:54](../../scripts/copilot/instruments.py#L54) |
| 市场解释 | 模型可引用 snapshot 的指标、研究片段、支持/反对证据并生成定性理由 | claims 核验来源/标量，不核验自然语言蕴含或预测收益。[policy:178](../../scripts/copilot/policy.py#L178)、[返回合同:522](../../scripts/copilot/policy.py#L522) |
| 规则交易信号 | [FixedWeightBands:50](../../scripts/copilot/backtest/rules.py#L50)、[InverseVolatility:124](../../scripts/copilot/backtest/rules.py#L124)、[MomentumTopN:205](../../scripts/copilot/backtest/rules.py#L205)；gold scheduled accumulation | 已采用规则可以给 rebalance_due、buy/hold/reduce/sell、数量、现金与风险；不是 RSI 超买/超卖 entry/exit，也不是独立波段子账 |
| 资金/数量 | [evaluate_rule:813](../../scripts/copilot/policy.py#L813) + [sizing:38](../../scripts/copilot/sizing.py#L38)：费用、现金 reserve、整股与整篮一致性 | 现金从本地手工配置取，不是 IBKR 实时余额；持仓 coverage、schema、日历、估值不足则拒绝；碎股存量不能截断 |
| 行情/限价 | [market_data:307](../../scripts/copilot/market_data.py#L307) 取最新完成 session close；[policy:1060](../../scripts/copilot/policy.py#L1060) 明确 split_adjusted_close | 没有 live raw bid/ask、tick rules/可成交份额；[render:430](../../scripts/copilot/service.py#L430) 会呈现“限价”，Skill 必须补充该收盘计算基准，不能称当前实盘报价 |

**已运行反证：** 用 Temp DB、固定 clock、合成 snapshot 及明确 coverage 调 `service.review`，返回 `action=buy`、`execution_scope=research_only`、没有 quantity，single_name/sector/liquidity/drawdown 为 unknown。原因是 [review:189](../../scripts/copilot/service.py#L189) 不生成绑定 proposal 的 typed risk；[policy:465](../../scripts/copilot/policy.py#L465) 要求 snapshot/fingerprint/风险输入对应。model 直接提交 quantity/target_weight/stop_loss 被拒绝。[policy:310](../../scripts/copilot/policy.py#L310)

## 3. 仅改 Skill 可以做到什么

1. 将任务分为：事实查询/市场研究、已采用规则建议、真实操作记录、设置变更。已有规则路由的触发条件应明确；不能把普通 research proposal 当作数值订单入口。
2. 开头区分“日线研究倾向”“已采用规则计划”“等待/数据不足”。数字来自 snapshot，模型的市场解释明确是定性推断；RSI 低不自动等于买入，缺数据不自动等于卖出。
3. 固定紧凑输出：结论与 horizon → 两个可核验量化事实（带 basis/session）→ 事件支持/反证及缺口 → 规则结果或条件 → 数据时效与下一步。不要为了充满栏目虚构数值或把 unknown 改成 neutral。
4. 实际展示 rule ID、execution_scope、basket scope、quantity 和 `limit_price_basis`；缺必要信息保留拒绝原因。当前规则价格注明“截至某已完成交易日的收盘计算基准，非实时 bid/ask；需实际执行前刷新报价和人工 Review”。仅凭模型文字不宣布已核实实时指令。
5. context 中历史建议只审计，新闻 brake 只减/停买并披露未回测；gold 的 quote 只为用户报告，保留系统不核实价格真伪和 contribution schedule 未回测说明。

这层升级能改善一致性和实用性，不新增 alpha、账户同步或行情权限。现有输出 `buy + research_only` 若被缩成一个“买入”词，会丢掉最重要的权限边界。

## 4. 目标能力所需的最小核心／工具扩展（待定案）

| 需求 | 最小共享核心合同 | 公开工具/运行时要求 |
|---|---|---|
| 无需模型读取完整私人 config 也能采全采用 universe | 返回 adopted rule ID/schema/universe/required inputs 的安全元数据，或核心按 adopted rule 收集 snapshot | 可加 `get_rule_requirements` 或 `collect_rule_snapshot`；不暴露个人现金/原文；仍由 service.evaluate 内部读取资本 |
| 明确量化 entry/exit | typed signal/template registry：输入 basis 与 cutoff、固定参数/version、条件、invalidations、证据引用、research/validated 等级 | `analyze_snapshot`/`evaluate_signal` 是建议接口名，当前不存在；先冻结规则和验证，不能让模型自行认定模板已通过 |
| 信号给精确当前数量/限价 | 新信号由核心计算 selection/risk 与计划；真实 account/ExecutionQuote/contract constraints 绑定编译器 | 与现有 adopted ETF 路径分开身份；scope/qty/stop/price 不接受 LLM 自由填数；手动成交前版本重校验 |
| IBKR 资金与 live 报价 | read-only adapter、账户完整性/原币cash/挂单/fills、raw bid/ask、实际 market data type、时间/tick/fees/FX | 当前没有实现；NAV≠cash；quote missing/delayed/frozen/expired 不输出实时计划；不发送或绑定人工订单 |
| 个股候选发现 | 合约身份、可用历史、分类与个股风险；候选范围/选择策略 version；SEC/PIT adapter | 白名单扩展不能单独完成；新闻/网页候选先经身份与信号验证；不得继承 ETF 风控豁免 |
| 自由网页研究进入同一证据链 | 独立抓取/校验 source、entity、时间与 scalar/derived facts，生成新 immutable evidence snapshot | 若需要，新增研究证据导入接口；仅把浏览器文字粘给模型不能通过现有 claims 验证；web page 是证据不是工具指令 |
| 初始持仓/双模式 | opening inventory 与历史成交分开，现金/股份逻辑归属，全账户先行检查 | 不编造历史 fill；长期与波段不能复制账户现金；sell 未完成不能无条件 fund buy |

以上是最小功能 seam，不是重新搭多代理平台。先优化现有输出还是同时实现个股/IBKR/新 signal，需通过本轮 grilling 明确范围；新模板、主动选择与双模式要求显式 ADR/contract 修订，不能偷偷复用旧采用证据。

## 5. “个人信息本地”的实际合同

持久化本地与数据不进入远程模型是两回事。当前 [MCP context:39](../../mcps/copilot_mcp.py#L39) 返回 [journal context:610](../../scripts/copilot/journal.py#L610) 的 holdings、pending、operations、原 statement、账户字段及历史建议。若调用者是云端模型，工具返回的私人字段会进入其上下文；本地 SQLite 本身不提供这些字段“不离机”的保证。

个人信息留在本地；向模型提供摘要需要明确许可，并使用最小 `advisor_context` 视图，排除完整原文、账户标识和完整流水。单靠 Skill 的“不要泄露”无法收回已经由工具返回的原文。量化方法和核心升级范围应在实施合同中明确。

**最小投影草案：** 内核 `service.context`/`journal.get_context` 及 evaluate 的完整输入保持不变，另以 allowlist 构造公开摘要：

- `view/schema_version`、`sleeve`、`portfolio_version`、`portfolio_complete/completeness/base_currency/fx_status`，并标 `positions_scope` 为完整或过滤显示；摘要不提升 completeness。
- holdings 仅按 instrument/currency/unit 汇总 Decimal 数量及必要缺口；gold 另列明确 pure_gold_grams，不混 gross/item/fine 单位。多个账户聚合标 scope_ambiguous；不能据聚合记录选择原成交或商家额度。
- pending/intents 的数量与有限 pending refs：operation_id/version/status/instrument_id/missing_fields。历史只给 available/requires_reevaluation，不给原 recommendation、historical_order 或自由文本理由。
- 不复制 account_id、source_message_id、statement、external_trade_id、holding_key、完整 operation_ids、原 evidence payload。需要纠正时查询单个 operation summary，只给版本/状态及必要结构化交易字段；多个匹配不能猜最近一笔。

**兼容与测试：** MCP 默认摘要后，CLI context fallback 应同形状；canonical operation 引用与新 lookup 工具同步，保持模型可取得正确 operation_id/expected_version。不能把摘要交给内核 sizing/gold quota/risk。测试深层 allowlist 与秘密哨兵、多账户/多币种 Decimal 汇总、gold fine 单位、过滤范围、pending/duplicate/纠正版本、history 不出现 actionable/qty、MCP/CLI 一致、投影不写 DB；保留真实篮子/黄金额度/风险回归。还需检查 gold quote 的 snapshot、quote receipt 与 evaluate 返回中的 account_id：仅改 context 并不自动覆盖这些公开出口。

## 6. 验证与 canonical 更新约束

本次隔离执行10项测试：IndicatorContracts、ModelMayNotSupplyNumbers、规则限价 basis 测试，全部通过；另有上述真实 service.review Temp 探针。未运行真实网络/券商账户，不据此宣称策略盈利或个人权限可用。

后续 Skill 改动必须改 `.claude/skills/investment-chat/` canonical 来源并生成 `.agents/skills/`、`skills/`；[sync_runtimes:102](../../scripts/sync_runtimes.py#L102)。新工具要同步 MCP、CLI、service 和 [生成工具清单:84](../../scripts/sync_runtimes.py#L84)，两个 runtime 不得漂移。应验证：普通研究不出现可下单 qty；已有规则路径完整取 universe；缺 basis/时效/隐私权限时拒绝；历史不复活；明确研究信号与实时指令的字段区别。提示词 shape 检查与真正业务回归分别报告。

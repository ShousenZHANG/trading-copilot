# 手动投资建议 Agent：执行与券商数据架构研究

研究日期：2026-10-03。已确认场景：IBKR、美股 ETF 与个股、数日到数周的隔夜波段和中长期投资、AI 研究加确定性价格/数量、用户手动下单、长期按总回报与分派再投资评价。用户所述总额已明确为 NAV，不能视为现金；现金构成、账户类型和行情权限尚未核实。本文不记录私人金额，没有读取私人配置、持仓、账本或密钥，没有连接账户，没有执行下载的项目代码。

## 结论与证据层级

**最匹配的是现有确定性策略核心 + 小型 IBKR 只读适配器 + 手动成交对账。** 先把账户、各币种资金、未成交订单、合约身份和实时 bid/ask 接入可靠快照，再输出有期限的限价与数量。当前需求无需迁入 NautilusTrader、LEAN 或 Lumibot 的完整自动交易运行时。这是基于下述接口、源码和需求规模的设计判断，不是收益结论。

证据分三层：A 为本次完整取回并固定 SHA 的 Nautilus/Lumibot 源码与测试；B 为其他框架和券商的当前官方文档；C 为本项目适配建议。A 证明代码具备某种实现或测试，B 证明发行方声明的接口/限制；本次未安装框架、运行其测试、验证用户账户权限或进行实盘成交，因此均不能证明本账户的连通性、成交质量或盈利。

## 可复现的完整源码快照

两份完整 HEAD 工作树都在独立 Temp 目录，未采用只读 README 的替代扫描。浅克隆仅省去历史，不省略当前提交的 tracked 文件；未初始化额外子模块、安装依赖或执行安装脚本。取回及扫描时 Git 工作树均干净。

| 仓库 | 固定 SHA | HEAD 提交时间 | tracked 文件 | 版本/许可证据 |
|---|---|---|---:|---|
| [NautilusTrader](https://github.com/nautechsystems/nautilus_trader/tree/6f3a91818174bbaafcfc8306e38a476c48e032d4) | `6f3a91818174bbaafcfc8306e38a476c48e032d4` | 2026-10-03 11:56:09 +10:00 | 5026 | 当前默认分支为 `2.0.0rc6`，Rust 原生核心、Python >=3.12,<3.15；LGPL-3.0-only。[pyproject](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/python/pyproject.toml#L1) |
| [Lumibot](https://github.com/Lumiwealth/lumibot/tree/fc05c8ed32cf77336b7405609a42f05759c43634) | `fc05c8ed32cf77336b7405609a42f05759c43634` | 2026-10-01 12:52:19 -04:00 | 1941 | `4.6.3`、Python >=3.10、GPL-3.0；安装依赖包含多个 broker/data SDK、数据分析库与 AI 组件。[setup.py](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/setup.py#L45) |

实际快照目录：本机 `%TEMP%/trading-execution-research-89b1697cb7e54ac0ace109002dbd665a/` 下的 `nautilus_trader` 与 `lumibot`。Nautilus 首次 Windows checkout 遇到长路径，设置该临时仓库 `core.longpaths=true` 后按 HEAD 恢复，复核零改动。上述 rc 源码不代表已发布稳定版本。

以下 PowerShell 命令可重新获得同一完整树，仅执行 Git，不运行上游代码：

```powershell
$executionResearch = Join-Path $env:TEMP ('execution-source-review-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $executionResearch | Out-Null
$executionRepos = @(
    @{Name='nautilus_trader'; Url='https://github.com/nautechsystems/nautilus_trader.git'; Sha='6f3a91818174bbaafcfc8306e38a476c48e032d4'},
    @{Name='lumibot'; Url='https://github.com/Lumiwealth/lumibot.git'; Sha='fc05c8ed32cf77336b7405609a42f05759c43634'}
)
foreach ($executionRepo in $executionRepos) {
    $executionCheckout = Join-Path $executionResearch $executionRepo.Name
    git init $executionCheckout
    git -C $executionCheckout config core.longpaths true
    git -C $executionCheckout remote add origin $executionRepo.Url
    git -C $executionCheckout fetch --depth 1 origin $executionRepo.Sha
    git -C $executionCheckout checkout --detach FETCH_HEAD
    git -C $executionCheckout rev-parse HEAD
    git -C $executionCheckout status --porcelain
    git -C $executionCheckout ls-files | Measure-Object
}
```

## 框架比较：哪些能力值得借鉴

| 方案 | 账户、资金与报价 | 订单/成交与 paper/live | 本需求匹配度（判断） |
|---|---|---|---|
| NautilusTrader | IBKR 数据与执行 client 分离；账户 summary 保留 tag/currency，等待初始结束事件；QuoteTick 区分 bid/ask、event/init time。 | 订单状态、外部订单与成交对账、重复 fill、重启恢复均有明确代码和测试。相同模型不是相同真实成交。 | **最佳状态模型参考；整栈偏重。** 当前源码为 Rust+Python rc。只读建议没有必要接入自动提交/恢复运行时。 |
| Lumibot | Python broker/strategy API 较直接，IBKR/Alpaca 能读持仓与报价；但余额 tuple、strategy 持仓归属、报价时间均需补领域合同，详见下面。 | new/cancel/fill/partial-fill 事件；broker 同步测试覆盖更新中到达的 fill。可复用策略不保证行情权限和模拟成交等同。 | **易读的实现参考；不建议直接当本账户资金权威。** 仍是完整自动交易栈，依赖面不小。 |
| QuantConnect LEAN | live portfolio 从 broker 持仓及 open orders 加载；遇不能建模的资产会停止。broker/data provider 分开。 | OrderEvent/OrderTicket 生命周期，回测 reality models；官方解释 stale fills、手续费、流动性和重启导致的 live 差异。 | **完整研究到部署候选；本阶段过重。** 若将来要求多市场统一自动执行再评估。 |
| OpenBB Python V5 | provider 数据接入与标准化；可通过 Python/REST/MCP 使用。不能据此推定真实账户、资金或下单权限。 | 本次核验的能力重点是数据分发与研究接口。 | **可选研究数据层。** 不承担资金/成交权威。V5 破坏性变化，旧 V4 命令不能照搬。 |
| vectorbt | `Portfolio.from_orders/from_signals` 是数组式组合模拟，包含现金、fees/slippage/quantity 等参数。 | 模拟 order records 不等于 broker 未成交订单或真实 executions。 | **适合快速离线敏感性比较。** 不能直接给当前账户可买数量。 |
| Qlib / FinRL | Qlib 面向量化 ML 研究和数据处理；FinRL 当前原仓库明确经典 DRL 的教育、实验与研究原型范围。 | 有研究/交易示例不等于本账户经过验收的只读快照与手动对账。 | **后续研究工具。** 先解决真实账户与报价，暂不增加模型训练栈。 |

比较的官方证据：[LEAN live portfolio](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/key-concepts#05-Algorithm-Portfolio)、[LEAN order events](https://www.quantconnect.com/docs/v2/writing-algorithms/trading-and-orders/order-events)、[LEAN reconciliation](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation)、[OpenBB V5](https://docs.openbb.co/odp/python)、[vectorbt Portfolio](https://vectorbt.dev/api/portfolio/base/)、[Qlib](https://github.com/microsoft/qlib)、[FinRL](https://github.com/AI4Finance-Foundation/FinRL)。这些方案不以 Stars、角色数或测试数评估收益。

OpenBB 官方 V5 页面于2026-09-29更新，明确 shared routers 迁到 provider namespaces、移除15个 V4 provider packages、许可从 AGPL-3.0 改为 Apache-2.0。采用前应固定实际 package/source 版本；旧仓库 V4 的接口或许可记录不能代表 V5。[V5 变更原文](https://docs.openbb.co/odp/python)

### Nautilus：借账户完成边界和成交去重，保持自己的资金约束

固定 SHA 的 [IBKR account.rs L57](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/adapters/interactive_brokers/src/execution/account.rs#L57) 请求 NetLiquidation、TotalCashValue、SettledCash、BuyingPower、AvailableFunds、保证金等 tags，按账户过滤，并到 `AccountSummaryResult::End` 才完成初始收集。[QuoteTick L51](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/model/src/data/quote.rs#L51) 显式保存 bid/ask、size 和两个时间字段；模型有字段不证明上游总提供可信交易所时间。

其 [执行管理配置](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/live/src/execution/config.rs#L35) 有外部订单、inflight 检查和持仓对账；`generate_missing_orders` 是从报告补建内部状态，不能误解为自动下单买回差额。[manager 测试 L2377](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/live/tests/integration/manager.rs#L2377) 覆盖重复 reconciliation fill，[L2571](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/live/tests/integration/manager.rs#L2571) 覆盖 dispatch 拒绝后不 commit，[L11188](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/live/tests/integration/manager.rs#L11188) 覆盖重启时闭合记录。

这里值得借的是明确状态与证据绑定。资金映射仍要自己审核：例如 [account.rs L204](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/adapters/interactive_brokers/src/execution/account.rs#L204) 的 SettledCash 映射不自动解决本项目保留现金、未成交订单和不使用杠杆的要求。[DockerizedIBGatewayConfig L318](https://github.com/nautechsystems/nautilus_trader/blob/6f3a91818174bbaafcfc8306e38a476c48e032d4/crates/adapters/interactive_brokers/src/config.rs#L318) 有默认 `read_only_api=true`；这是容器配置边界，不代表任意 Strategy 实例都只能读取。

### Lumibot：三个不能照搬的接口假设

1. **BASE 汇总现金不是 USD 可用资金。** [IBKR balances L420-L468](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/lumibot/brokers/interactive_brokers.py#L420) 返回 BASE 的 TotalCashBalance 与 NetLiquidationByCurrency。Alpaca [balances L677](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/lumibot/brokers/alpaca.py#L677) 返回 cash/positions value/portfolio value，tuple 未携带 buying_power。`get_cash()` 也不应被当作已经核对挂单、结算、FX 的保守资金额度。
2. **本地接收时间不是市场报价时间。** [IBKR get_quote L386-L431](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/lumibot/data_sources/interactive_brokers_data.py#L386) 把 `-1` bid/ask 转 None，但返回 timestamp 为本地 `datetime.now(UTC)`；[IBKR tick handlers L685](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/lumibot/brokers/interactive_brokers.py#L685) 分开收 bid/ask，并允许 last 回退前日 close。不能仅以“timestamp 很新”把 close 当可手动成交的 ask。
3. **strategy 归属不等于真实账户隔离。** [broker.py L1090](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/lumibot/brokers/broker.py#L1090) 将 broker 持仓分配给当前 strategy；[Strategy.get_positions L1559](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/lumibot/strategies/strategy.py#L1559) 最终按 strategy 过滤内部持仓。同账户两个投资期限必须共享真实余额与持仓，另行记录用途分配，不能让各 sleeve 都认领全仓。

静态读取了 [test_broker_sync_positions.py](https://github.com/Lumiwealth/lumibot/blob/fc05c8ed32cf77336b7405609a42f05759c43634/tests/test_broker_sync_positions.py)：现有持仓 mark 更新、缺失字段清理、读取中发生的新 fill 优先于旧 snapshot。还读取了 broker/strategy、order entity、credentials/setup 及相关 IBKR/Alpaca test 源码；没有执行带 apitest/live 标记的脚本，也没有把这些测试当作本账户验收。

## IBKR 只读路径：账户事实先于建议数量

**TWS 或 IB Gateway socket API 是优先路径。** 用户继续手动下单时，Agent 只暴露查询工具；保持 TWS 的 Read-Only 边界，不提供 place/modify/cancel 方法。当前官方教程说明 Read-Only 默认启用并阻止 API 下单；默认 TWS live/paper 端口为 7496/7497，Gateway 为 4001/4002，但实际配置可改。[官方配置教程](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)

“连接成功”不能被当成完整账户：IBKR 的 `positionEnd` 标记初始持仓接收结束，`accountSummaryEnd` 标记账户 summary 收集结束；每项带 account/currency。适配器需分别记录 positions/cash/orders 的 complete/unknown、请求范围、时间和账户身份，超时不能当空仓。[Receive Positions](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/positions/receive-positions)、[Receiving Account Summary](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-summary/receiving-account-summary)

资金输入应保存原始 NetLiquidation、TotalCashValue、SettledCash、BuyingPower、AvailableFunds，而不是压成一个 cash。官方 `$LEDGER:ALL` / `$LEDGER:USD` 能请求各币种现金 tags；BASE 总数只供报告，不能当 USD 购买余额。BuyingPower 也不是已授权借款额度。新设计默认不使用杠杆，按已核实资金、费用、保留额及挂单影响计算；必须避免对已在 broker 额度里扣除的占用重复扣减。[Account Summary Tags](https://www.interactivebrokers.com/docs/tws-api/doc/account-portfolio-data/account-summary/account-summary-tags)

不同 API client 的订单可见性不同，Master Client ID 与 client 0 对其他 API/TWS/FIX 成交的接收范围有特殊规则，不能凭“当前 client 无 open orders”认定账户无挂单。[官方 client ID 规则](https://www.interactivebrokers.com/docs/tws-api/doc/order-management/client-id-0-and-the-master-client-id) 旧官方页还称 Read-Only 时订单信息不可见；当前教程只明确阻止下单，未确认该旧限制是否仍适用。因此要按用户实际 TWS/IBG 版本做只读 capability 探针，验证手工挂单/成交覆盖；不可见时请求人工核对并保留 unknown，不能静默关闭 Read-Only。[旧版官方说明，需版本复核](https://interactivebrokers.github.io/tws-api/initial_setup.html)

**只读查询也要核对是否产生 binding。** 官方说明 client 0 的 `reqOpenOrders` 与 `reqAutoOpenOrders` 可绑定手工订单；binding 会取消/重新提交交易所 working order，可能改变排队顺序，而 `reqAllOpenOrders` 不绑定。适配器应禁止 auto-bind，检查 SDK 连接时是否隐式调用这些方法，优先使用不绑定的查询；不能为获得“更完整状态”触发手工订单改动。[官方 Modifying Orders](https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders)

Web API 是备选，不能把 read-only 外层 session 与可获取实时 `/iserver` 行情的 brokerage session 混同；官方说明 username 同时仅能有一个 brokerage session。[Web API session 模型](https://www.interactivebrokers.com/campus/ibkr-api-page/webapi-doc/) 对当前本地手动下单，先验证 TWS 同一会话读取更直接；这属于适配选择判断。

Flex Web Service 用于生成/获取预先配置的报告，本设计把 Activity Flex 用于日结/历史对账，Trade Confirmation Flex 用于成交核对。不能断言所有 Flex 都只能每日：官方明确实时 trade confirmations 为 executions 生成。但它们都不应替代盘中 bid/ask、当前挂单和资金的决策快照。[Flex 介绍](https://www.interactivebrokers.com/docs/web-api/flex-web-service/introduction)、[Trade Confirmations](https://www.ibkrguides.com/brokerportal/performanceandstatements/tradeconfirm.htm)

### AUD 基准与 USD 交易

Base currency 是报表和汇总币种，账户仍可持有/交易其他币种。需明确账户所属 IBKR entity、cash/margin 类型、实际 USD/AUD 余额和 AutoFX 规则；“AUD 总资产”不能直接换算成 USD 可买额度。[Base Currency](https://www.interactivebrokers.com/campus/glossary-terms/base-currency/)、[账户币种说明](https://gdcdyn.interactivebrokers.com/Universal/servlet/Registration_v3.formHelp?s=p2181)

澳洲官方价表列 AutoFX 通常对汇率加减 0.03%，不另收该转换佣金；手动 Spot FX 小额档为 0.20 bp、最低 USD2（澳洲居民 GST 适用时为 0.22 bp、最低 USD2.20）。美股整股 Tiered 小额档 USD0.0035/股、最低0.35，Fixed USD0.005/股、最低1，另有相应费用。分派再投资也有佣金规则，不能写为零成本。[FX 费率](https://www.interactivebrokers.com.au/en/pricing/commissions-spot-currencies.php)、[美股与分派再投资费率](https://www.interactivebrokers.com.au/en/pricing/commissions-stocks.php) 实际账户方案和 AutoFX 资格必须核实，AUD 计价不证明用户属于澳洲实体或居民。

**设计要求：** USD 下单建议保留费用和 FX buffer；未知是否会自动换汇时先输出需要换汇/确认资金的条件，不用 margin buying power 掩盖 USD 不足。真实分派、预扣税、再投资 fill、FX 与佣金分开入账；total-return 回测的分派再投资假设不能直接创建用户持仓。

## 行情权限、最小成本与报价有效期

IBKR API 官方当前最低条件包含已开通 PRO 账户、USD500 资金加订阅成本、对应订阅与 API access。Market Data API Acknowledgement 未完成会出现“未订阅”错误。TWS 界面免费数据属于 on-platform，API 属 off-platform，**界面看得到不证明 API 能合法收到同一实时流**。[API Requirements](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/market-data-requirements)、[API Acknowledgement](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/compliance-requirements-for-api-market-data/market-data-api-acknowledgement)、[TWS vs API](https://www.interactivebrokers.com/docs/general/market-data-subscriptions/tws-data-vs-api-data)

按 2026-10-03 官方报价、假设用户确实符合 Non-Professional 分类：

| 路径 | 官方成本/限制 | 对本场景的判断 |
|---|---|---|
| 平台免费 Cboe One/IEX streaming | 非综合报价，不是 NBBO；API 权限需单独核验。 | 研究观察可标明覆盖范围，不能冒充全市场最佳价格。 |
| 股票/ETF 的 Network A/B/C L1 | 每网 USD1.50/月；三网合计 USD4.50/月。 | 少量股票/ETF、持续刷新时优先比较此股票方案；无须默认购买期货/期权组合包。 |
| US regulatory snapshot | 每次 USD0.01；TWS API 每秒最多1次，需相应 snapshot 权限。 | 低频按需 NBBO 候选；不会自动刷新。应显示费用预算、权限与实际收到的字段。 |

前两行及一般 snapshot 的 USD1/月费用减免见 [IBKR Market Data Pricing](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)。TWS API 文档要求相应 snapshot bundle，不能把每次 USD0.01 当作已经证明无需基础订阅的总成本；该 bundle 官方价表基础费 USD10/月，存在活动减免。API 计费和 pacing 见 [Regulatory Snapshots](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/top-of-book-l-1/regulatory-snapshots)。**不要把普通界面的免费 snapshot 次数直接承诺为任意账户的 API 配额**；订阅前以 Client Portal 对该 username 的报价/权限为准。

IBKR `marketDataType` 有 live/frozen/delayed/delayed-frozen；实际 callback 才证明收到的类型。当前 TWS 文档描述 delayed 为15–20分钟，frozen 是闭市最后报价，不是当前可成交价格。[Delayed Data](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-delayed/introduction) 普通 snapshot 在11秒窗口里收集可用 ticks，结束时未必有全部字段，不能把 `tickSnapshotEnd` 当 bid/ask 均有效。[Streaming Snapshots](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/top-of-book-l-1/streaming-data-snapshots)

**建议合同（设计推断）：** 每个 quote 绑定 conId、currency、bid/ask、size、source/feed、实际 data type、是否综合、market session、各字段 observed/received time、expiry。协议没有市场 timestamp 时保留 null 并记录 received_at，不伪造交易所时间；异步到达的两个方向需检查收集窗口。任何无权限、unknown、缺单边、过期、crossed quote、停牌或 session 不符都不给精确下单建议。可以先用60秒有效期作待验收原型参数，但它不是 broker 保证，需按流动性和实际刷新过程调整。

隔夜波段的 thesis 可以持续数日，中长期目标可持续数月；**具体数量和限价仍绑定分钟级新账户/报价快照**。账户、挂单、fill、资金、价格边界或配置变化即失效。买入 ask / 卖出 bid 用于当前成本与价差评估，不承诺成交；限价须经过合约 tick size 规则取整。[IBKR Market Rule](https://www.interactivebrokers.com/docs/tws-api/doc/orders/minimum-price-increment/request-market-rule)

Alpaca 用作架构交叉验证，不作为开户建议：Basic 实时 equities 仅 IEX；SIP 是综合流，最新 SIP 权限与历史15分钟访问限制不同。latest quotes 支持 delayed_sip，不能看到“quote endpoint”就视为实时。[Market Data API](https://docs.alpaca.markets/us/docs/about-market-data-api)、[Market Data FAQ](https://docs.alpaca.markets/us/docs/market-data-faq)、[Latest Quotes](https://docs.alpaca.markets/us/reference/stocklatestquotes-1) 其官方订单说明也明确 open buy orders 占用 buying power，卖出持仓在执行前不释放该额度；这验证了必须读取挂单资金状态的要求。[Orders](https://docs.alpaca.markets/us/docs/orders-at-alpaca)

## paper/live 能验证什么

IBKR paper 支持同类 API，但成交只模拟 top-of-book，没有 deep-book；部分订单类型不支持，stop/复杂订单模拟行为可能不同，部分成交后的剩余处理也可能不同。[IBKR Paper](https://www.interactivebrokers.com/campus/glossary-terms/paper-trading-account/) Alpaca paper 不包含市场冲击、延迟滑点或排队位置，paper/live 共用 API spec 仍不证明经济结果相同。[Alpaca Paper](https://docs.alpaca.markets/us/docs/paper-trading)

因此 paper 适合验证身份、字段、状态转换、断线、重复事件和拒绝路径。建议价格可手动成交、费用可承受及未来策略表现，需要另行验证；不能用 paper fill 让真实账本记成交，也不能用 paper 现金替代真实资金。LEAN 官方也明确回测手续费/fill model 不一定匹配 live，限价触及不保证真实填单。[LEAN reconciliation](https://www.quantconnect.com/docs/v2/writing-algorithms/live-trading/reconciliation)

## 建议落地合同与验收

以下全部是 C 层设计建议，本研究没有实现这些能力：

1. **读取边界。** 本地 adapter 仅允许账户、持仓、合约、quote、open orders/executions 查询；Agent 工具层不存在提交/改撤单。接口始终回传 account、currency、completeness、时间与来源；错误只报状态，不报 credentials。
2. **组合边界。** 长期与波段共享真实账户总资金/持仓约束，sleeve 只分配用途。研究候选与 adopted deterministic rule 分开；没有经验证的规则不能由 LLM 自由生成可执行数量。
3. **手动计划。** 输出 instrument/conId、side、quantity、limit、USD 最大成本、fee/FX buffer、信号截至时间、quote截至时间、失效时间、失效条件；多标的必须整体资金可行。未成交卖出不当作已收到现金。
4. **真实对账。** 只有用户明确成交报告或已授权且完整的 broker execution 才改变持仓。以 account+execution id 去重，区分 pending/partial/filled/cancelled/rejected；commission 后到时追加，不能覆盖原收据。账户 snapshot 与本地 ledger 不一致先显示差异，不自动补造 fill。
5. **必要拒绝测试。** 空仓成功与持仓超时有不同结果；AUD NAV 足但 USD 现金不足；同股被两个 sleeve 分配；外部挂单只对另一 client 可见；部分成交中旧 snapshot 到达；重复 fill；纸面账户误接真实资金；实时权限失败但延迟字段返回；快照完成却缺 ask；本地 received_at 新而市场时间未知；quote 过期；FX/费用超出预留；重连后账户身份改变。
6. **两阶段验收。** 先以隔离 fixture/paper 验证上述合同，再由用户授权只读 live 探针，逐账户与 TWS 人工显示核对。没有真实账户覆盖验收，产品只能称研究与条件式建议，不能称已能按真实持仓给可靠买卖数量。

接入须确认：账户所属 entity 与 cash/margin 类型；各持仓和原币现金；外部挂单；非专业行情分类及 A/B/C/API entitlement；碎股许可；模式资金分配、现金保留和最大损失；分派是否实际 DRIP 或手动再投。账户净资产（NAV）不能用于推算可用现金。这些是独立账户证据，不应从仓库、币种或开户平台猜测。

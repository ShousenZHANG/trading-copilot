# 会话投资助手优化方案

方案日期：2026-09-06。审计基线：`2f779238bb0a9b6f25d02905bd2e1fb40798dd5f`。

状态：用户已确认并指示“执行”；核心实现与自动化验收已落地，客户端登录及未配置数据源的实测边界见 [实施验收记录](implementation-validation-2026-09-06.md)。本文保留批准时的设计及审计基线，下面的“当前证据”表是实施前快照。日常产品不要求生成报告文件。

## 1. 已确认的产品目标

- 只覆盖美股/ETF、纳斯达克100与综合指数、QQQ/QQQM，以及中国人民币购买的投资金条/金币。
- 服务日线研究、波段及长期积累；数据费用为零，接受免费注册与 API Key。
- 在 Claude Code 和 Codex 会话里直接给简短中文建议。Caveman 只压缩表达，不删数据时点、证据与不确定性。
- 后台保存数据依据、建议、实际操作，两个运行环境共享本地状态。
- 用户明确说已经买入/卖出，自动记录；意向和建议不算成交；成交信息不全先记录待补全，不猜成交价或数量。
- 日常默认单 advisor，复杂分歧才升级分析；保留现有深度分析作为显式入口。

“最新可靠”定义为：**在该数据源实际发布节奏下，取得本次决策应当可获得的最新合格数据，并能核对来源、时间、口径和异常。** 免费数据不能保证永不断流，也不能保证投资预测正确。断流或冲突时，系统必须明确缺口，暂停依赖该数据的具体建议。

## 2. 当前证据与验证边界

本次读过权威 CLAUDE.md、CONTEXT、ADR、数据包装器、价格归档、命令/代理、验证器、记忆与评估路径。不是全部代码和全部上游仓库的形式化审计。

| 本次检查 | 结果 | 能证明什么 |
|---|---|---|
| `python scripts/check.py` | 72 文件，0 errors，0 warnings | 当前静态/合同规则通过 |
| `python scripts/mcp_handshake.py --all --timeout 45` | Finnhub 2.6s、Yahoo 5.2s，2/2 | 两个 stdio 进程能够 initialize，不代表免费权限或行情正确 |
| `claude plugin validate .` | 通过，1 warning | manifest 可接受；根 CLAUDE.md 不随插件作为项目上下文加载 |
| `prices.py --self-test` | 20/20 | 现有离线案例通过 |
| `akshare_mcp.py --self-test` | 59/59 | 离线符号等规则通过；不代表当前目标市场需要启用它 |
| 当前 Yahoo MCP `tools/list` 和真实 `get_historical_stock_prices` | QQQ、`^NDX` 的 5d/1d 返回截至 2026-09-04 的记录 | 本机这一调用取得数据；尚未用第二供应商核对价格 |
| 同一 MCP 调用 `XAUUSD=X` | 空数组，但 `isError=false` | 调用成功不等于取得可用行情，必须检查数据内容 |
| [SGE 官方每日行情](https://www.sge.com.cn/sjzx/quotation_daily_new?start_date=2026-09-04&end_date=2026-09-04)及[上海金定价行情](https://www.sge.com.cn/sjzx/shanghaiAuAuto?start_date=2026-09-04&end_date=2026-09-04) | 网页取得 2026-09-04 记录 | 官方公开基准可读；尚未完成本地自动采集适配器长期验收 |

Alpaca 的免费账户权限、SEC/FRED 在用户账户和网络上的完整链路、商家具体金条报价、两个运行环境的完整会话行为均未宣称已验收。

### 必须优先修正的现有问题

| 优先级 | 代码证据（当前基线） | 已确认的问题 |
|---|---|---|
| P0 | `scripts/validate_outputs.py:124-162` | 缺风险输入或极旧数据的买入文本仍能通过；目前主要验格式，不能作为风险硬门 |
| P0 | `.claude/commands/analyze.md:19-46` | 按 ticker+日期+文件大小恢复，已有 final 直接返回；同日新数据/新持仓可能仍复用旧决定 |
| P0 | `.claude/commands/advise.md:75`、`scripts/memory.py:698-714`、`scripts/parse_rating.py:105-119` | advisor `Reduce` 可被正文的 `Buy` 解析成买入；不同评级尺度污染记忆 |
| P0 | `.claude/agents/analysts/macro-analyst.md:40-43` | CPIAUCSL 指数写成同比；广义美元指数写成 DXY；黄金 FRED fallback 已移除 |
| P0 | `docs/mcp-fallback.md:91-96`、`.claude/commands/gold.md:8` | 现货、连续期货和实物购买缺少明确身份区分 |
| P1 | `.claude/agents/analysts/market-analyst.md:24-36` | 只取 3mo 日线却要求 SMA200；指标需要确定性计算和足够样本 |
| P1 | `scripts/prices.py:136-195` | 可接收 NaN；复权缺失可退回原价；局部合并可能拼接不同复权基准 |
| P1 | `mcps/finnhub_mcp.py:83-102`、`docs/mcp-fallback.md:24-29` | 无共享配额/有界重试；基本财务数据不是 OHLCV fallback；免费权限不能凭工具名称认定 |
| P1 | `evals/stockbench/backtest_engine.py:110-115,231-237,257-301,320-322` | 决策只有日期、同日收盘成交；特定 Buy/Hold/Buy 序列仍重叠持仓 |
| P1 | `evals/scorer.py:70-135`、`.claude/commands/weekly-review.md:58-66` | 年份可能抢占数值匹配，否定句可能误通过；统一 alpha>0 不适合衡量卖出或长期积累 |

官方核对：[CPIAUCSL 的单位与频率](https://fred.stlouisfed.org/series/CPIAUCSL)、[FRED 移除 IBA 黄金日价公告](https://news.research.stlouisfed.org/2022/01/ice-benchmark-administration-ltd-iba-data-to-be-removed-from-fred/)、[DTWEXBGS 的实际含义](https://fred.stlouisfed.org/series/DTWEXBGS)。

## 3. 推荐架构：一个共享本地核心

保留插件形态和本地运行决定。新增少量 Python 模块，通过同一 CLI/MCP 接口供 Claude Code 与 Codex 调用。无需另建网页或常驻云服务。

1. **`market_data` 模块**：取得、规范化、验证和存储 EvidenceSnapshot；供应商差异、交易日历、配额、缓存、复权与证据都留在其实现内部。
2. **`decision_policy` 模块**：接收快照、持仓状态、策略模式及 advisor 的结构化提案，确定性检查质量、风险和可执行范围。
3. **`trade_journal` 模块**：保存用户操作事件并生成可重建的持仓视图；同时提供建议与证据记录的关联查询。
4. **运行环境接入**：共用工具接口和核心政策文本；分别维护原生 Claude/Codex 入口、权限与代理定义，自动检查漂移。

建议接口草案（名称在实现时统一）：

```text
collect_snapshot(instrument_ids, decision_at, horizon) -> EvidenceSnapshot
assess_proposal(proposal, snapshot_id, portfolio_version, policy_version) -> Decision
record_operation(statement, idempotency_key, expected_version?) -> JournalReceipt
get_context(instrument_ids, as_of) -> HoldingsAndPriorDecisions
```

同一快照、多次解释不重新抓取数据。若新增必要证据，生成新快照并使依赖它的决定失效。记录成功只在数据库提交之后回复。

## 4. 免费数据组合与接入顺序

| 内容 | 首选与交叉核验 | 使用边界 |
|---|---|---|
| 美股、QQQ/QQQM 等 ETF 日线 | 先以当前 Yahoo 链路建基线；接入 Alpaca Basic 的合格历史数据作为独立核验及可用时的首选 | 显式指定 feed、完整 session、币种、复权；先验证账户权限。免费 IEX 实时数据不能冒充全市场 SIP |
| 纳指100、综合指数 | Yahoo `^NDX`、`^IXIC`；Nasdaq 官方方法与公开数据辅助核对 | 指数点位不能填入 ETF 买入价格；没有可比第二源就标明核验范围 |
| 公司财务与重大公告 | SEC submissions/companyfacts、发行人 IR；Finnhub 免费且实测有权限的字段补充 | 保存披露时点、期间、单位和更正版本；当前财报不能用于伪造历史时点回测 |
| ETF 持仓、费用、跟踪对象 | 发行商官方资料；行情供应商补充 | 披露日期和基金身份必备；不把 ETF 当普通公司估值 |
| 中国投资金条/金币 | SGE Au99.99 与上海金基准价；实际银行/商家同产品卖价及回购价 | SGE 是市场基准，不是商家成交价；SHAU 与 Au99.99 不是同一价格字段，不能以差价自动判数据冲突 |
| 黄金宏观 | FRED/ALFRED、Fed、BLS 等官方发布 | 实际利率、美元指数、通胀按各自发布周期判断；不依赖已删除的 FRED 黄金序列 |
| 新闻与事件 | 公司公告、SEC、Fed/BLS 发布；Finnhub 免费新闻补充 | 记录 published_at 和首次取得时间，去重和辨别转载；搜索只负责发现来源 |

供应商条件依据：[Alpaca 当前免费方案](https://docs.alpaca.markets/us/docs/about-market-data-api)、[历史行情](https://docs.alpaca.markets/us/docs/historical-stock-data-1)、[Finnhub 文档](https://finnhub.io/docs/api/quote)、[SEC 数据接口](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)、[FRED Key](https://fred.stlouisfed.org/docs/api/api_key.html)、[yfinance 自身定位](https://ranaroussi.github.io/yfinance/)。

Alpaca 官方列免费方案历史数据有最近15分钟限制。这适合完整日线研究，但实际 SIP 权限仍需用户账户 canary 验收，不在验证前承诺主链可用。若只能取得 IEX，明确标注，不能用来“验证”全市场成交量。

不把数据供应商总量当覆盖率。Yahoo API、Yahoo 网页、基于 Yahoo 的其他包装器都属于相同上游。SGE 与其转发网站同样不能算两个独立来源。

不启用与确认范围无关的 A股、港股、加密数据模块；不强依赖付费 sentiment、分析师目标价或付费搜索。

## 5. 数据可靠性合同

每条重要数据带 `instrument_id、provider、upstream、source_url、observed_at、available_at、retrieved_at、session、currency、unit、adjustment、status、evidence_id`。来源未提供精确发布时间时保留未知，不能用抓取时间伪造。

- **交易时段**：美股按 America/New_York 与官方休市/早收安排；黄金按 SGE 交易日及夜盘归属。周末最新完整周五日线可合格；日线不混入尚未收盘 bar。
- **发布周期**：财报看最新已披露报告，宏观看发布日历与 vintage；月度 CPI 不套行情的24小时规则。休市期间仍刷新重大新闻及事件。
- **输入检查**：拒绝空数组、NaN/Inf、错误币种/单位、未来记录、重复冲突记录、错误资产身份和缺失必要时间。停牌/零成交等按工具类别解释，不一律误删。
- **交叉核验**：先对齐同一资产、完整 session、价格类型、调整口径和时间；误差阈值按字段/精度确定并经 canary 校准。不同不能解释时暂停相关价位建议，不盲目取平均或少数服从多数。
- **指标计算**：SMA、RSI、ATR、收益与仓位全部用代码，固定公式/参数。SMA200 至少200条有效日线；其他指标按其暖启动规则加余量。采用明确 start/end 分页及末条验证解决长窗口问题。
- **复权**：原价、拆股调整、分红/总回报分开存；缺失不得静默切换。公司行动或历史修订发生时版本化受影响窗口，禁止把不同复权基准拼接。
- **能力与错误**：区分 `not_covered/not_entitled/rate_limited/stale/malformed/unavailable`；403 不当作无新闻；429 尊重 Retry-After，共享配额且重试有上限。
- **缓存**：按数据类型和发布/session失效；问建议时检查当前水位。旧合格数据可留档，但不能因上游失败自动升级成最新。失败不覆盖上一份好数据。

质量状态与投资观点分离：数据故障不能自动推出卖出。必需数据不达标，回复“当前无法核验，暂停具体建议”；足够证据支持的部分可以单独解释。

## 6. 实物黄金专用判断

人民币本地基准优先。国际现货和期货只作为宏观背景，并保留品种身份。没有可信现货就不把 `GC=F` 改名为现货。

实际购买前采集：商家/产品、克重、纯度、报价单位、卖价、回购价、费用、报价时间与适用门店/条件。未指定商家时可分析基准与趋势；不能宣称已取得该商家的实时可成交价。可接收用户现场报价，明确标为用户提供、未独立核验。

计算本币每克总成本、相对同时间基准溢价、回购折价和往返成本。金币收藏溢价与黄金投资价值分别说明。报价按每枚/每根计价时先核对重量及纯度；毛重与纯金重不能混用。跨产品成本统一为含费总额除以纯金克重，同时保留原始报价及计价单位；比较基准时也必须核对该基准的含金量和单位定义。

如展示国际金价折算，明确 `USD/金衡盎司 × CNY/USD ÷ 31.1034768 = CNY/克纯金`，仅是同时间参考值，不等于零售报价。当地基准存在时无需强行依赖汇率才能给人民币基准分析。

积累模式关注总预算、购入节奏、渠道溢价、已持有黄金暴露；波段模式关注价位与风险。新高或短期 RSI 不自动推翻长期积累计划；任何模式都不能绕过必要数据质量门。

## 7. 会话输出与决策

自然语言请求触发分析，不要求用户记住斜杠命令。默认会话输出控制在约4–6行，复杂问题按需展开：

```text
建议：行动 + 适用周期。
理由：最多两条关键依据。
条件：改变建议的事件或价格条件。
数据：最新完整交易日/发布时间 + 关键来源链接。
缺口：有则说明；没有持仓信息就不给伪精确仓位。
```

这是格式模板，不是当前资产建议。后台记录完整证据与结构化决定，前台不展示 JSON、长辩论或模板表格。

Decision 保存 `action、instrument_id、mode、horizon、evidence_ids、quality_status、risk_checks、conditions、created_at、valid_until、snapshot_id、portfolio_version、policy_version、model/prompt_version`。证据质量、方向判断与概率不是同一字段；没有校准的概率不展示为精确胜率。

风险检查使用 `pass/fail/unknown/not_applicable`。组合信息不完整时仍可做一般研究，但不能宣称单品种或总仓位比例已通过，也不能给基于虚构总资产的金额。

跨美元和人民币资产汇总前，必须有用户指定的组合本位币及与估值时点匹配的 FX 证据。原始成本继续按成交币种保留；没有合格汇率时只给分币种视图，组合占比标记 unknown。首次需要组合精确仓位时再补齐期初持仓、现金、目标配置及本位币，不能从时区或购买地区推断。

三套历史评级保留原 scale 与枚举，内部 action 显式映射。新记录不再依赖在全文搜索 Buy/Hold 的启发式。历史解析只供迁移审计，无法确定的旧条目标记歧义。

Run 使用唯一ID，身份包含快照、持仓版本、模式、策略与代码/提示词版本。相同输入可以复用计算；数据更新、新交易、模式变更必须使相关建议失效。不能只靠文件存在判断完成。

## 8. 后台记账与跨会话记忆

使用本地 SQLite，建议落在已忽略的 `data/state/`；数据库、WAL/SHM、备份、原始证据均不进入 Git 或发布包。SQLite 提供本地持久化，不引入额外托管费用。

- `journal_events` 追加原始操作、补充、修正、撤销事件；持仓是可重建投影。
- `recommendations` 与 `evidence_snapshots` 分开保存，通过ID关联；建议绝不自动变成真实成交。
- 已成交声明缺资产/方向/数量及单位/价格及币种/日期等关键项时记 pending，暂不当成完整仓位或成本。
- 金额用 Decimal/定点；USD/股、CNY/克、每枚总价分别表示。手续费未知保持未知，不默认为零。实际成交时间与系统记录时间分开。
- 同一请求稳定幂等键，优先采用真实订单/成交ID。相同消息重试不重复入账；不同成交ID即使同价同量也保留两笔。跨会话相似文字只标为疑似重复，不自动吞并。
- 写入事件和更新投影在同一事务完成，处理数据库忙与版本冲突。成功提交后才回复“已记录”，并回传简短操作ID。
- 修改通过追加修正/撤销事件，不抹原始记录。卖出超过已知持仓时标记期初持仓待核对，不捏造历史买入成本。
- 旧 `trading_memory.md` 是建议/复盘资料，只通过现有 memory 工具读取与管理；不可迁移成真实持仓。
- 通过 SQLite backup API 做可恢复备份。检索上下文按 as_of 截断，跨会话读取账本而非依赖模型记忆。

## 9. Claude Code / Codex 支持

保留原生接入差异，共享数据/政策/记账实现。修复 `.codex/config.toml` 中过时条目、硬编码路径及运行依赖；从统一配置源生成并检查两套环境的有效配置。

Codex 的项目 MCP 使用 `.codex/config.toml`，自定义代理用 `.codex/agents/*.toml`；需按实际客户端版本验收加载、工具可见性和执行结果。[官方 MCP 文档](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)、[官方 subagents 文档](https://learn.chatgpt.com/docs/agent-configuration/subagents)。

不要求两个模型输出逐字一致，要求同一数据、同一硬性风险门、同一事务账本。自然语言触发、建议返回、成交记录、重启后检索都分别在两个环境验证。

## 10. 上游借鉴原则

详见同目录 `github-data-reliability-2026-09-06.md`，包含当前 GitHub metadata、commit 固定证据及许可证核对。

- TradingAgents：数据验证与恢复生命周期；不把多代理数量当准确率。
- ai-hedge-fund：确定性风险限制及审计；不照搬其依赖付费数据的默认供应商。
- OpenBB：规范化模型和供应商能力边界；不为此引入整个平台，也不把开源软件等同免费数据授权。
- Qlib/LEAN：历史时点、公司行动、成交时间与组合净值评估纪律；首版不重做交易引擎。
- yfinance/exchange_calendars：显式价格参数、交易日历与数据修订处理；引用版本并独立验收。

## 11. 实施顺序与完成条件

| 阶段 | 实施内容 | 完成条件 |
|---|---|---|
| P0a 数据与语义 | instrument registry、免费 capability canary、快照、交易日/发布周期、SGE、黄金口径、CPI/美元系列修正、复权混用拒绝与修订失效检查 | 真正取得合格数据或明确拒绝；空数据不再被当成功；复权受影响且未完成重取的窗口不能用于建议 |
| P0b 建议正确性 | 结构化 Decision、机器风险门、严格评级尺度、snapshot/portfolio版本失效 | 无证据买入不能通过；陈旧/冲突不自动卖出；同日更新不复用旧结论 |
| P0c 会话与账本 | 自然语言入口、Caveman输出、SQLite事件及幂等、Claude/Codex共享接入 | 两环境真实会话完成建议、自动记账、待补全、修正和重启检索 |
| P1 抗故障 | 有界重试/配额、复权自动回补与版本管理优化、备份恢复、崩溃恢复、前瞻记录 | 断流/429/磁盘写入失败不伪报成功，回放账本不重复 |
| P2 推荐质量优化 | 修复评分器、按周期的前瞻对照、必要时改善多代理 | 同一快照和成本预算下比较增益，证据不足不宣称更优策略 |

核心建议合同、数据质量与账本一起构成可用版本；不把只改提示词或只连通 MCP 称作完成。

必要验收案例：

1. 周末、节假日、早收、DST、SGE夜盘归属；未来/未收盘bar、空数组、NaN与无时间戳数据。
2. 不足200条请求SMA200时明确样本不足；拆股/分红/历史更正不制造虚假收益。
3. 同源数据不被计作独立核验；不同价格口径不取平均；403/429与空覆盖分开处理。
4. Reduce/Avoid/Strong Buy等往返记录正确；正文否定句及评级词不能改写机器action。
5. 更新快照或新增一笔真实成交，旧建议失效；新运行不命中同日旧文件。
6. 同一记账请求跨环境重试一次入账；两笔同价同量实际成交保留两笔；待补全不冒充完整持仓。
7. 数据库提交失败不能说已记录；修正保留历史，恢复备份后账本一致。
8. Gold CNY/g与USD/oz、毛重/纯金重、基准/商家报价不会混算；缺商家报价不生成确定的零售买价结论。
9. Claude与Codex各执行完整会话，核对工具可见、共享记录和硬门一致。
10. USD股票与CNY实物黄金共存但汇率缺失时，展示分币种视图；不给合并资产金额或假称组合风险检查通过。

前瞻评估先积累至少30个交易日作为可靠性观察窗口，这不足以证明收益优势。按5/20/60/120交易日等事先约定窗口分别看波段/长期；收盘后决定不能回测成交在同日收盘。成本、滑点、现金、逐日净值和真实成交时间具备后，才报告组合回测指标。实物黄金以人民币总成本和真实回购净额评价，纸面基准上涨不等于已经实现的收益。

## 12. 整体确认点

方案的产品边界已经由用户逐项确认。最后只需确认本文件描述的组合是否准确：**免费最新合格数据、会话短建议、国内人民币投资黄金、明确成交自动记账、Claude/Codex共享本地核心**。未指定商家不阻碍核心实现，具体零售价建议时必须补齐渠道和产品。

根据用户调用的 grilling 技能，整体理解确认后再实施业务改造。本次研究及方案文件不是已经上线的功能。

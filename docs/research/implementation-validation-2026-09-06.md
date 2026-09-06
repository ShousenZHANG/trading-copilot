# 实施与验收记录

日期：2026-09-06。用户已批准原方案并指示“执行”。本文件是工程验收记录；产品默认在会话中回答，不生成长报告。

## 已实现

- scripts/copilot：共享证据快照、交易日历、免费来源、指标、确定性决策门禁、本地 SQLite 操作账本。
- Claude Code/Codex：同一数据与记账核心；默认 investment-chat 简短中文输出；/analyze 显式调用。由 .claude 自动生成 .agents、.codex；Codex 插件 skills 只暴露会话入口。
- 美股/ETF：Yahoo 日线与 Nasdaq 官方最新收盘核验；有权限时可加入 Alpaca 历史 SIP。未知 ETF 用验证过的供应商身份确认，注册身份冲突拒绝。
- ^NDX/^IXIC：与 Nasdaq 官方近五日收盘点位核对。指数与 QQQ/QQQM 分开。
- 黄金：SGE Au99.99 日线与 SHAU PM 分开处理；保留人民币/克口径，不冒充商家卖价或回购价。
- SEC：财务事实按 accession/accepted_at 可见性筛选，保留单位与期间；FRED：观测日期、release、vintage、实际发布时间分开；Finnhub 新闻标明聚合来源，空响应不代表无事件。
- 用户明确已成交才记为执行；缺字段待补全；意向不加仓；重复检查、幂等重试、更正、撤销、事务提交回执、并发版本检查。历史 Markdown 建议不自动转换成成交。
- 数值事实通过 evidence_id/path/value 校验；百分比、倍率、量级不能借同一个裸数混过校验。模型的定性推理与未来收益不因此得到事实或预测认证。
- 采集共用 110 秒预算，研究补充 30 秒；按剩余时间限制请求/重试。超时返回明确缺口。真实 5 秒预算用例在 5.95 秒返回，说明网络/连接存在少量额外开销。
- 修复旧评级误解析、复权混用/非有限数值、回测成交时间和重叠持仓，以及报告绕过决策校验的问题。
- 发布包包含两套运行时；检查 SQLite/WAL、策略、密钥泄露及运行时漂移。未提交或推送 Git。

## 实测结果

| 验证 | 结果与证明范围 |
|---|---|
| unittest discovery | 121 项中 119 项通过；2 项真实日历用例由下面的固定依赖运行补测 |
| 市场数据专项 + --calendars | 43/43；含 DST、提前收盘、美中假日、拆股、空/NaN、来源冲突、超时、动态 ETF 身份 |
| Policy | 23/23；未知数据、过期/篡改、证据/数值绑定、来源类型、研究范围、持仓版本 |
| Journal / research / service | 27 / 15 / 9 项通过；临时数据库及合成操作 |
| Release privacy | 4 项真实 ZIP 回归 + 34 项打包检查通过；包含 Codex TOML 和混合占位符泄漏检测 |
| 旧辅助模块 | 17 个 --self-test 模块通过；memory/validation 直接 CLI 用例通过 |
| check.py / lint / compile | 95 文件，0 errors/0 warnings；关键错误 lint、字节编译通过 |
| 插件校验 | Codex 本地验证器通过；Claude 官方验证器通过，保留“根 CLAUDE.md 不作为插件上下文加载”的提示 |
| MCP 工具链 | initialize、tools/list、已存证据决策、成交幂等、重启恢复、旧持仓版本标记通过 |
| Codex 实机 | 已安装桌面 CLI 0.153.4：首次会话读/写临时账本，第二次独立会话读取同一笔；最终只有一笔 |
| Claude 实机 | 被 401 OAuth access token has been revoked 阻塞，未声称双客户端完整验收 |

2026-09-06 04:27–04:28 UTC 的真实 MCP 整链，用临时账本完成采集、落库、决策门禁和重启恢复：

| 标的 | 最新完整交易日 | 有效日线 | 行情质量 |
|---|---|---:|---|
| QQQ | 2026-09-04 | 414 | pass，Yahoo + Nasdaq 最新收盘 |
| ^NDX | 2026-09-04 | 414 | pass，官方近期收盘核对 |
| ^IXIC | 2026-09-04 | 414 | pass，官方近期收盘核对 |
| GOLD.CNY | 2026-09-04 | 282 | pass，SGE 基准 |

该批次采集及研究补充耗时 **59.64 秒**。单独 QQQ 的同链路耗时 **6.57 秒**。另行实时验证 QQQM、AAPL 收盘核对通过，Finnhub AAPL 返回 15 条有时间戳新闻。以上是特定时点与标的的验证，不代表全部市场、历史 OHLCV 或未来供应商可用性。

本次发现真实 MCP 先能握手却在取数时卡住：faulthandler 定位到 Windows 下运行中的 MCP 首次加载 NumPy 原生模块。将数值库预加载到服务启动阶段后，真实 QQQ 与四标的整链通过。另把 Yahoo 缓存隔离到核心私有的进程目录，避免共享旧适配器的全局缓存；没有证据把这次卡住单独归因于缓存。

## 尚未验收的外部条件

- Claude 需要用户重新登录。完成后运行 python scripts/_probe_clients.py 复测双客户端；脚本使用临时配置/账本、当前用户认证，不改变全局设置。它会消耗模型用量，未纳入无人值守 CI。
- FRED_API_KEY、SEC_USER_AGENT 当前未配置，因此宏观最新发布与 SEC 实时链路未通过账户实测；返回明确缺口。Alpaca Key 未配置，SIP 权限未验证；现有 Nasdaq 对照可服务本次测试的股票/ETF。
- ETF 发行人持仓/费用与完整前瞻财报日历尚无完整自动采集覆盖，不应据此声称完成基金穿透或事件排除。
- 实物金条/金币仍需具体商家、产品、纯度、含费卖价及回购条款；当前自动源提供市场基准，不是零售报价抓取服务。
- 账本只知道用户记录的操作，组合完整性仍为 unknown；精确仓位、完整组合净值及 USD/CNY 合并估值没有被凭空补全。
- 中国 SGE 假日日历验证覆盖 2025–2026；进入未验证年份时阻断并要求更新。未完成跨年度或长期连续运行验收。
- 未运行真实资金交易、未导入生产持仓、未设置后台定时任务。后台记录指每次会话操作的持久化，不代表无人值守交易。

## 重现

```powershell
.\scripts\start.ps1 -Client codex
.\scripts\start.ps1 -Client claude
python scripts/check.py
python -m unittest discover -s scripts -p '_test_*.py'
uv run --no-project --with yfinance==1.7.0 --with exchange-calendars==4.13.2 --with tzdata==2026.3 python scripts/_test_market_data.py --calendars
uv run --no-project --quiet --script scripts/copilot_probe.py --live
python scripts/package_release.py
```

Windows 启动器选择已安装的较新原生 Codex，不改变用户模型、全局配置或 PATH。本次选择 0.153.4；旧 PATH CLI 0.147.0 对当前模型报兼容错误。不能据旧 CLI 的异常推断用户全局配置损坏。

实施依据及上游取舍见 [GitHub 与官方数据源研究](github-data-reliability-2026-09-06.md) 和 [批准方案](conversation-copilot-plan-2026-09-06.md)。

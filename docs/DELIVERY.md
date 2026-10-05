# 0.6.0 delivery and acceptance

This release extends the manual advisor workflow. It does not place orders,
adopt a personal strategy, change private risk preferences or certify returns.

## Supported workflow

1. Read sanitized context; explicitly establish opening quantities if historical
   fills are unknown. `opening-balance --input file.json` binds a saved broker
   observation, user statement and account/portfolio versions.
2. Collect market research and distribution evidence. `income-snapshot QQQI JEPQ`
   persists primary-source events. `income-report ID --input parameters.json`
   calculates trailing payouts and explicit raw-NAV, tax and FX scenarios.
3. `income-compare ID --input parameters.json` ranks feasible historical income
   scenarios using stated costs/bounds. It never creates executable orders.
4. `doctor --execution-snapshot-id ID --research-snapshot-id ID --route user_directed`
   lists prerequisites without connecting or changing state. Missing facts stay
   unknown; presence of an SDK or credential is not live readiness.
5. Choose the calculation path: adopted-strategy `plan`, or user-confirmed
   `confirm-intent --input file.json` followed by `directed-plan INTENT_ID`.
6. Actual user Review, fresh `preflight`, manual submission, actual-fill report
   and reconciliation. Never spend unfilled sale proceeds. Manual FX conversion
   and settlement precede any USD budget funded from another currency.

CLI global `--db` and `--config-path` precede the command. `config --path` remains
supported; conflicting global/subcommand paths are refused. CLI and MCP share
the same service and persistence. Skill references document the JSON contracts.
For real local IBKR diagnostics use `python scripts/copilot_runtime.py cli doctor`
so the selected private SDK interpreter is tested. A bare system Python can
correctly report its own SDK missing while a separate configured runtime has it.

## Compatibility

The SQLite additions are additive; backup through `copilot_cli.py backup` before
any personal migration. Opening balances preserve unknown basis and do not
reconstruct historical profit. Existing fills must not be double-counted into a
new opening. Amendments are versioned events, never destructive rewrites.

Legacy adopted-rule schema is 4: prior records remain research/audit evidence
but need recomputation to authorize new orders. Initial cash is the metric
anchor, including initial costs. No-fill backtests cannot be adopted. Full
history and out-of-sample coverage are checked against the shipped session table;
unknown ranges require a reviewed calendar update. XNYS coverage is independent
pinned exchange-calendar data; SGE's table is series-derived and is disclosed
as a coverage check, not independent provenance authentication.

## Reproducible release

PEP 723 scripts ship transitive `.py.lock` files, and MCP v1/market dependencies
are pinned. Yahoo runs through a local locked entrypoint. The optional official
IBKR SDK is installed locally under its own distribution terms and is not
bundled. A private runtime override remains the user's responsibility; probes
must run against that actual interpreter as well as a clean package.

Release ZIP entries use fixed timestamps and permissions. Build twice from the
same source and compare SHA-256; checkout timestamps must not change the bytes.
GitHub release assets contain the ZIP and its checksum. Tags identify the exact
commit. No `.env`, personal config, state, raw broker observations or local audit
outputs belong in Git or the release package.

## Verification and remaining live evidence

Run the offline suite, explicit self-tests, shape/drift checks, critical lint,
real MCP handshake/probe, pinned-calendar verification and package leak checks.
Windows CI covers runtime selection and the new public contracts alongside the
existing Linux Python matrix. `evals/advisor_workflow/runner.py` records current
workflow scenario outcomes with code/skill/tool hashes. Its synthetic contract
results are not real model answer accuracy or trading performance.

Real account acceptance still requires locally running read-only TWS/Gateway,
actual market-data permissions, current USD settlement, explicitly confirmed
fees, a regular-session quote, human Review and fresh preflight. Unit fixtures
cannot establish these. The same applies to actual partial fills and distribution
receipts: source pay dates and expected entitlement are not proof of credited cash.
No profitability benchmark or fully automated StockBench model run is claimed.

## Local acceptance, 2026-10-05

| Check | Observed result |
|---|---|
| Full isolated unittest discovery | 1010 tests, 3 optional skips, no failures |
| Explicit offline self-test manifest | 12/12 modules passed |
| Real pinned-calendar suite | 68 tests passed; 8496 XNYS reference sessions matched |
| MCP processes | 3/3 default servers completed initialize/tools handshake |
| Current service persistence probe | 9 checks passed in a temporary database |
| Independent workflow replay | 153 synthetic contracts passed; 135 limited core/service trace events |
| Model paper scenarios | 4 scenario responses reviewed separately; model version unknown, no accuracy claim |
| Repository/drift/lint/plugin | Passed; plugin validator retains the documented root-context warning |
| Release packaging | No private-state leak; clean extracted commands passed; repeated builds have identical bytes |
| Personal live account | Not accepted: TWS port 7496 was not listening; current quotes/settlement and human flow remain unverified |

The three default-suite skips are not three failures. Run the optional calendar
suite in the pinned runtime to exercise its calendar cases. The remaining local
skip is the fixture-symlink test when Windows denies symlink creation; Linux CI
exercises that platform path. It is not silently counted as passed. Account
connectivity and market effectiveness are separate from these program checks.

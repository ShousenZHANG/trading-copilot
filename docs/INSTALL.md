# Run the shared investment copilot

Install Python 3.11+, uv and Claude Code and/or Codex. Open the repository root. The same checkout provides project-scoped skills, prompts and the trading-copilot MCP server for both clients.

1. Keep an existing .env. For a new checkout, copy .env.example to .env and add the free credentials you obtain yourself.
2. Start Claude with `scripts/start.ps1 -Client claude`, or Codex with `scripts/start.ps1 -Client codex`. On macOS/Linux use `sh scripts/start.sh claude` or `codex`. The launchers pass credentials without printing them.
3. On first use, accept your client's project/MCP trust request if shown. Restart the session after changing MCP configuration.
4. Ask “QQQ 适合长期持有吗？” or “我已买入……”. The shared tools collect evidence and return committed receipts. If tools are unavailable, the skill uses the same CLI core.

## Free credentials

Credentials live in .env and nowhere else; config/user.toml holds decisions, never secrets. The loader reads exactly the names below, so a variable absent from this table is a variable the copilot never sees.

| Setting | Obtain/configure | What it unlocks today |
|---|---|---|
| FINNHUB_API_KEY | [Finnhub](https://finnhub.io/register) free account | Company-news evidence. The economic calendar is **not** available on the free tier (HTTP 403); see [ADR-0005](adr/0005-verified-data-constraints.md) |
| FRED_API_KEY | [FRED key](https://fredaccount.stlouisfed.org/apikeys) | Macro series, currently the four in the whitelist: DFII10, DGS10, DTWEXBGS, CPIAUCSL |
| SEC_USER_AGENT | Your real name/application and contact email | SEC filing evidence; the SEC requires a contact string, not an API key |
| APCA_API_KEY_ID / APCA_API_SECRET_KEY | [Alpaca](https://alpaca.markets/) free account | Optional third price source; historical SIP entitlement is probed and IEX is not consolidated SIP |
| ALPACA_API_KEY / ALPACA_SECRET_KEY | The same Alpaca account | Optional aliases, read only when the APCA_ names are unset |

Absent Alpaca keys, the Yahoo + Nasdaq cross-check is the only price path, which is the supported default.

Yahoo, Nasdaq public pages and SGE public pages do not require these keys. They can fail or change; their current responses are always validated. Never paste keys into a conversation or source file. Configure .env locally. Optional legacy paid adapters in .mcp.json.template are not part of the free core.

```sh
uv run --no-project --quiet --script scripts/copilot_cli.py capabilities
uv run --no-project --quiet --script scripts/copilot_probe.py
```

A missing key is a visible gap, not a silent failure: `python scripts/copilot_cli.py capabilities` reports which of the names above are present. Capabilities checks presence only, and credential presence is not an entitlement or live-data test. The probe checks actual MCP calls against isolated temporary state and reports quality statuses; it does not certify unavailable credentials or all live feeds. Add `--live` for public-source fetches.

## Local persistence

Default: data/state/copilot.sqlite, shared by both clients in this checkout. Set process environment COPILOT_DB_PATH to use a different isolated journal. Restart the MCP server after changing it. Copies of this repository on different machines do not synchronize automatically.

`python scripts/copilot_cli.py backup <destination>` uses SQLite's backup API. Keep backups private. Legacy positions.md and research memory are never silently imported as confirmed transactions.

## Private watchlist

`data/watchlist.md` contains public, supported examples. `/scan` reads your
`data/watchlist.local.md` when present, otherwise those examples. `/watchlist`
creates/edits the private file and validates additions against the shared registry.
The local file is gitignored and excluded from release archives. Existing edits
to the old public file are not migrated automatically; move any personal notes
into the private file yourself before sharing a checkout or release.

## Adopt a rule and request engine orders

Research questions work without an adopted rule. Concrete engine orders need
configuration, a real admitted backtest, declared coverage and current evidence.
The examples below demonstrate the workflow, not an investment recommendation.
They write adoption/coverage/snapshot records to your local journal; `--db` on
the CLIs can direct them to a separate isolated journal.

1. Keep an existing private config. For a new one, copy
   `config/user.example.toml` to `config/user.toml` and edit your actual capital,
   universe and limits. On PowerShell:
   `if (-not (Test-Path config/user.toml)) { Copy-Item config/user.example.toml config/user.toml }`.
   On POSIX: `test -e config/user.toml || cp config/user.example.toml config/user.toml`.
   `python scripts/copilot_cli.py config` validates it locally. ETF cash is cash
   available to deploy; gold `investable_total_cny` is the total gold budget,
   including the value already held, and `contribution_cny` is your chosen addition.
2. For the example ETF universe, review one rule's report:

   ```sh
   python scripts/backtest_cli.py --universe SPY,QQQ,IWM,VUG,VTV,VEA,VWO,XLK,XLV,XLF --family bands --cash-floor-pct 0.15
   ```

   Inspect failures, costs, stress windows, drawdowns and data caveats. To adopt
   that same rule after your review, repeat with `--adopt`. The gate refuses an
   inadmissible result. Copy its returned `rule_id` into `[etf].adopted_rule_id`;
   use exactly that universe in `[etf].universe` and set
   `[etf].min_cash_reserve_pct` to the same `--cash-floor-pct` (0.15 in this
   example). Changing either requires a new matching backtest/adoption;
   changing the config alone cannot activate an untested basket or reserve.
   `python scripts/copilot_cli.py adopt <rule_id>` reads the stored record; it
   cannot create a new one. No command edits your private config automatically.
3. Record only real completed trades you report. Review
   `python scripts/copilot_cli.py context --sleeve etf`. When you have explicitly
   confirmed that this is your complete ETF book, including an explicitly empty
   book, declare it with `python scripts/copilot_cli.py declare-coverage --sleeve etf`.
   This records your assertion; it does not independently verify it. A later
   completed trade invalidates the declaration.
4. Collect every member of the configured ETF universe into one fresh snapshot:

   ```sh
   uv run --no-project --quiet --script scripts/copilot_cli.py snapshot SPY QQQ IWM VUG VTV VEA VWO XLK XLV XLF
   ```

   Check the returned quality, issues, dates and snapshot_id. Replace placeholders
   with returned IDs: `python scripts/copilot_cli.py evaluate <snapshot_id> --sleeve etf`.
   Read its orders, scope and refusals. Missing/stale data, inconsistent configuration
   or incomplete coverage produces research_only rather than an invented quantity.
   This is an instruction proposal for you to review; no broker order is submitted.
   Drawdown measurement also needs comparable portfolio valuations: the first
   valid observation saves a baseline; a new market observation with the same
   holdings can establish drawdown. Replaying the same snapshot is not a new
   observation. A missing valuation keeps that risk measurement unknown.

### Chinese gold

1. Review `python scripts/backtest_cli.py --sleeve gold --family scheduled_accumulation`.
   Its plain report shows the short span/missing-2008 failures. `--adopt` applies
   only the two existing historical waivers documented in [ADR-0008](adr/0008-gold-sleeve-evidence-and-admission.md).
   The measured curve is holding Au99.99; the contribution schedule is **not**
   backtested, and `pause_below_trend` cannot be adopted. Put the receipt's rule_id
   in `[gold].adopted_rule_id`, and set your actual total budget/contribution and
   product-specific order limits. The example's bank limits are not every
   merchant's terms.
2. Review `python scripts/copilot_cli.py context --sleeve gold`. After your own
   explicit completeness confirmation, run
   `python scripts/copilot_cli.py declare-coverage --sleeve gold` (CNY).
3. Collect with `uv run --no-project --quiet --script scripts/copilot_cli.py snapshot GOLD.CNY`.
   The SGE benchmark does not represent a purchasable merchant price. Supply your
   actual merchant/product, an all-in CNY ask per fine gram, and its observation
   time including timezone. Replace the placeholders in this command first:

   ```sh
   python scripts/copilot_cli.py record-quote <snapshot_id> --merchant "<merchant>" --product "<product>" --ask-per-fine-gram <actual_ask> --observed-at "<ISO-8601-with-timezone>"
   ```

   The receipt returns a **new snapshot_id**. Quote freshness/shape checks do not
   verify the reported price; unknown purity or fees require clarification.
4. Use that new ID with
   `python scripts/copilot_cli.py evaluate <new_snapshot_id> --sleeve gold`.
   Review CNY amount/grams, refusals and the unbacktested-schedule disclosure.
   In conversation the equivalent tools are `get_investment_context(sleeve="gold")`,
   `declare_holdings_coverage(sleeve="gold", base_currency="CNY")`,
   `record_retail_gold_quote` and `evaluate_adopted_rule(sleeve="gold")`.

## Runtime files and packaging

Claude prompts/skills are authoritative; `python scripts/sync_runtimes.py` generates .codex/config.toml, .agents/skills and distributable skills/. There are no shipped agents. `python scripts/check.py` rejects drift, missing active-document script references and omitted self-test interfaces. Run the same offline entry point locally and in CI: `python scripts/self_tests.py` (`--check` validates its explicit list without running it).

For session-only Claude plugin loading use `claude --plugin-dir .`. The checkout itself already supplies project settings. The release includes both manifests, generated configuration and skills; private state is excluded. Project support does not imply a marketplace installation or remote synchronization.

## If a client fails

Run the probe first. MCP transport, source credentials, entitlement, data quality and model-provider authentication are separate checks. A transport pass is not a passed price. Use the CLI fallback to distinguish provider issues from client discovery issues. See [source failure handling](mcp-fallback.md).

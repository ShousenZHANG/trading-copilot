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

## Runtime files and packaging

Claude prompts/skills are authoritative; `python scripts/sync_runtimes.py` generates .codex/config.toml, all Codex agents and .agents/skills. `python scripts/check.py` rejects drift. Codex agents inherit the user's model preference.

For session-only Claude plugin loading use `claude --plugin-dir .`. The checkout itself already supplies project settings. The release includes both manifests, generated configuration and skills; private state is excluded. Project support does not imply a marketplace installation or remote synchronization.

## If a client fails

Run the probe first. MCP transport, source credentials, entitlement, data quality and model-provider authentication are separate checks. A transport pass is not a passed price. Use the CLI fallback to distinguish provider issues from client discovery issues. See [source failure handling](mcp-fallback.md).

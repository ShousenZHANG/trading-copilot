# MCP setup

The shared `trading-copilot` server provides the evidence, policy and local
journal tools used by the investment skill. `.mcp.json` is the active set;
`.mcp.json.template` is an optional adapter catalog. Any entry in the active
set starts; a renamed entry is still active.

## Default servers

| Server | Purpose | Credential |
|---|---|---|
| `trading-copilot` | Shared snapshots, assessed decisions, operation journal, rule evaluation | Provider settings in [INSTALL](INSTALL.md) |
| `yahoo-finance` | Auxiliary Yahoo tools; the shared core validates its own provider data | None |
| `finnhub` | Auxiliary Finnhub tools; the shared core collects its own news evidence | FINNHUB_API_KEY |

Install Python 3.11+ and uv. Launch from the repository with `scripts/start.ps1`
or `scripts/start.sh` so optional credentials reach the client environment.
Project configuration is generated for Codex from the same active set. There
are no shipped agents; the investment skill calls the shared server directly.
FRED macro coverage in the core requires FRED_API_KEY, not an extra FRED MCP.
Chinese investment gold uses SGE evidence, with a separate observed merchant
quote for a concrete CNY/gram order.

## Optional adapters

```sh
python scripts/enable_mcp.py
python scripts/enable_mcp.py akshare
python scripts/enable_mcp.py akshare --disable
```

Enabling validates the conversion before changing the active set, then updates
both runtimes. Restart Claude Code/Codex afterward. HTTP bearer/custom-header
credentials remain references to process environment variables. A stdio adapter
with a different destination variable name uses `scripts/mcp_env.py` to resolve
the canonical mapping at process start. Generated files contain variable names,
not credential values. The Codex fields follow the [official configuration
reference](https://learn.chatgpt.com/docs/config-file/config-reference).

These catalog adapters are auxiliary integrations, not additions to the core's
instrument whitelist or policy coverage. AkShare/Tushare do not make A-share/HK
symbols valid investment-chat instruments. GoldAPI/futures do not replace SGE
or the merchant quote. Paid or quota-limited adapters are optional and are not
part of the free core. Catalog availability is configuration, not proof of a
live entitlement or a working upstream endpoint.

Each enabled third-party server is another process or remote connection.
Configure only the adapters you use, with their documented environment names;
keep real keys in the local .env. The core's credential allow-list is described
in INSTALL; optional adapters have their own names in the catalog.

## Verify the correct layer

```sh
python scripts/mcp_handshake.py --all
uv run --no-project --quiet --script scripts/copilot_probe.py
```

The handshake starts configured local stdio servers and checks initialize; it
cannot certify source quality or all optional HTTP transports. The default
probe checks actual shared MCP calls and restart persistence using a temporary
database. `--live` additionally fetches providers into that temporary state.
Neither proves all future feeds or model-provider login.

For a source error, read [data failures](mcp-fallback.md); for installation,
credential presence and rule setup, use [INSTALL](INSTALL.md). On Windows,
optional npx .cmd adapters may need a cmd wrapper around that single entry.
Keep uv/uvx as direct executable commands.

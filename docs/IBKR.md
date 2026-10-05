# IBKR read-only execution evidence

The adapter in `scripts/copilot/broker.py` queries an already running local TWS
or IB Gateway. It does not log in, install software, subscribe to a paid feed,
change broker permissions, bind manual orders, or submit/modify/cancel orders.
It is disabled by default. Repository tests never connect to a personal account.

## Install the optional official SDK

Use the [official TWS API download](https://interactivebrokers.github.io/).
On 2026-10-03, Stable is 1050 (the Mac/Linux source archive is
`twsapi_macunix.1050.02.zip`), and Latest is 1051. The implemented client requests
and wrapper callbacks were checked statically against the official Stable
1050.02 Python source, including `error(reqId, errorTime, errorCode, ...)` and
`commissionAndFeesReport`. This is interface verification, not a live-account
compatibility claim. Keep the SDK and TWS/Gateway versions compatible.

Install the downloaded SDK's `source/pythonclient` into the Python environment
that runs the copilot. For example, with the Windows SDK's default location:

```powershell
python -m pip install "C:\TWS_API\source\pythonclient"
python -c "from ibapi.client import EClient; from ibapi.wrapper import EWrapper; print('official SDK import OK')"
```

Substitute the actual installation directory. The default `uv run --script`
MCP/CLI environment is isolated and will not automatically inherit an SDK
installed in another Python. To use that environment, add the local official
SDK package explicitly to the launcher, for example:

```powershell
uv run --with "C:\TWS_API\source\pythonclient" --no-project --quiet --script mcps/copilot_mcp.py
```

The same `--with` package can be used before `--script scripts/copilot_cli.py`.
Do not replace the checked official package with an unverified similarly named
PyPI package. Installation and SDK imports are user setup steps, not performed
automatically by collection. Missing SDK returns `ibapi_sdk_missing`.
See [official Python setup](https://www.interactivebrokers.com/docs/tws-api/doc/quick-start/installation)
and [Windows SDK installation](https://www.interactivebrokers.com/docs/tws-api/doc/download-the-tws-api/install-the-tws-api-on-windows).

### Keep the optional SDK available across MCP restarts

The canonical MCP entry in `.mcp.json` and its catalog uses
`scripts/copilot_runtime.py`. It selects an already installed local Python;
it never installs an SDK, loads credentials, or connects to a broker itself.
Without a manifest it keeps the existing isolated `uv run --script` runtime.

To use a dedicated Windows environment, create it locally and install both the
official SDK downloaded above and the public MCP dependencies:

```powershell
uv venv --python 3.13 data/state/runtime/ibkr-venv
uv pip install --python data/state/runtime/ibkr-venv/Scripts/python.exe "<official-sdk-directory>/source/pythonclient" "mcp[cli]>=1.2.0,<2" "yfinance==1.7.0" "exchange-calendars==4.13.2" "tzdata==2026.3"
```

Replace the SDK placeholder with the actual downloaded source directory. Keep
the SDK version compatible with local TWS/Gateway. PEP 723 dependencies are
not installed when the manifest selects Python directly, so this environment
must contain the full dependency set in `mcps/copilot_mcp.py`.

Create the private file `data/state/runtime/copilot-runtime.json`:

```json
{"schema_version": 1, "python": "ibkr-venv/Scripts/python.exe"}
```

`python` is relative to `data/state/runtime`, must identify an existing file,
and its resolved path must remain inside that directory. Absolute paths,
directory traversal, external symlinks, extra fields and malformed manifests
are rejected. On macOS/Linux use a copied interpreter inside the local runtime
(for example `ibkr-venv/bin/python`); a venv interpreter symlink pointing outside
the runtime is rejected. The manifest and environment remain private and are
excluded from releases. There is no global MCP server entry or machine path to
add to generated configuration.

Use the same selection for CLI checks:

```powershell
uv run --no-project --quiet --script scripts/copilot_runtime.py cli capabilities
```

Restart the MCP server after local runtime changes. If a desktop session keeps
an old server process, close and reopen the Codex project/client once so it
loads the current project entry and manifest; no TWS SDK reinstall or duplicate
global MCP server entry is needed. A present but invalid
manifest fails with a named `copilot_runtime_manifest_*` error on stderr and
does not silently fall back to another Python. Launcher diagnostics do not
print manifest contents or local paths. Child stdin/stdout/stderr, environment
and exit status are inherited. The launcher does not certify SDK provenance,
account access or live-data entitlements; verify those separately below.

## Local settings and permissions

In TWS/Gateway, enable socket clients, check its actual socket port, and **keep
Read-Only API enabled**. A generic vendor tutorial that disables Read-Only is
not the configuration for this project. Use a dedicated client ID greater than
zero; client 0 is rejected. The adapter accepts loopback hosts only.

The private configuration's `[ibkr]` section supplies `enabled`, `host`, `port`,
`client_id`, `account_id`, `account_alias`, `timeout_seconds`,
`quote_ttl_seconds`, `orders_scope_confirmed`, `market_data_feed`, `paper`, and
`regular_hours_only`. Start with `enabled = false`; enable collection only when
the SDK and a logged-in local session are ready. Keep account selection in
private `config/user.toml`, never in a shared example or conversation.

Port 7497 does not prove a paper session, and 7496 does not prove live. `paper`
is an explicit declaration and is labelled `paper_status = "declared"` unless
the transport supplies verified evidence. An unconfirmed declaration is not a
live-account certification.

`reqAllOpenOrders` avoids order binding. Its end callback alone does not certify
that this client can see every manual/external order. Check the actual account's
manual orders and API-client coverage against TWS before declaring
`orders_scope_confirmed = true`. Returned coverage distinguishes a request end
from this user-declared coverage assertion. Unknown coverage blocks quantities.
The client also actively rejects `reqOpenOrders`, `reqAutoOpenOrders`,
`placeOrder`, `cancelOrder`, and `reqGlobalCancel`.
[IBKR binding side effects](https://www.interactivebrokers.com/docs/tws-api/doc/orders/modifying-orders)

## What a collected snapshot proves

- Each section records its request-end marker. Positions, cash and orders are
  separate asynchronous streams, not an atomic transaction. Changes observed
  during collection block the snapshot; changes after collection still require
  a fresh preflight before a manual order.
- After the account query ends, the quote universe expands to every nonzero
  held USD `STK` contract and every active USD `STK` order in the selected
  account. This includes holdings outside the adopted strategy. At most 16
  caller candidates and 64 total account/candidate instruments are supported;
  excess exposure blocks instead of being truncated. Quote coverage names this
  complete required universe, including an omitted contract/quote failure.
- NAV and base-currency `AvailableFunds` are reported separately from each
  currency's gross/settled cash. Margin-aware funds are not USD cash or permission
  to borrow. Known open limit-buy commitments are deducted once from settled
  cash, excluding their fees; a market buy, unknown order scope, or missing cash
  basis leaves reservations unknown. A pending sell adds no buying cash, and a
  pending cancel retains its reservation.
- Current TWS may prepend `$LEDGER-` to per-currency account values. The adapter
  preserves the raw tag, callback source and value scope before normalizing it.
  Account-summary `SettledCash` does not certify native-currency settled cash;
  an absent per-currency value remains unknown. `Currency@BASE = BASE` is a
  sentinel. A unique account-summary NAV currency can establish the account base,
  while conflicting currency evidence blocks collection. The adapter never
  requests a fictitious `BASE.USD` pair.
  [IBKR per-currency account value prefix](https://www.interactivebrokers.com/docs/tws-api/doc/tws-settings/per-currency-account-value-prefix)
- Prices and quantities are decimal strings. A quote needs received bid/ask,
  sizes, actual `marketDataType = 1`, a verified contract/market-rule increment,
  and an applicable liquid-hours interval. Requested type 1 is not proof of live
  data. The declared feed scope is labelled as such; live data alone does not
  prove consolidated NBBO coverage.
- Stock quotes preserve `ContractDetails.industry/category/subcategory` as
  `broker_industry`. A finite exact-label map supplies direct sector attribution
  and its contract/source evidence to matching held contracts; for example,
  `Technology` maps to `technology` and `Financial` to `financials`. Unknown or
  ambiguous labels remain unknown. This is broker industry attribution, not a
  verified GICS classification, and does not measure ETF constituent overlap.
  ETF sector attribution remains the registered project mapping. The fields
  were also statically verified in the official 1050.02 Python SDK.
  [ContractDetails classification fields](https://interactivebrokers.github.io/tws-api/classIBApi_1_1ContractDetails.html)
- Bid/ask `event_time` stays null because L1 snapshots do not establish each
  side's exchange timestamp. Local callback receipt times are labelled
  `bid_received_at`/`ask_received_at`; last-trade time is not promoted to BBO time.
  The oldest side sets quote expiry. Missing/crossed/expired/delayed/frozen data
  is blocked. A successful `tickSnapshotEnd` is not a complete valid quote.
- IBKR's halt tick can be unknown. The official documentation says zero is
  returned only when the instrument is in a TWS watchlist. Unknown is retained
  and blocks a stock/ETF execution quote; add relevant tickers to the user's
  watchlist manually and verify the actual callback rather than inventing zero.
  [Halt tick semantics](https://interactivebrokers.github.io/tws-api/tick_types.html#halted)
- For a non-USD NAV, the adapter requests a read-only `CASH` pair such as
  `AUD.USD` on IDEALPRO. Its dated live midquote is only a NAV/risk-conversion
  reference: it does not imply a completed FX trade or fund a USD purchase.
  Missing conversion evidence remains unknown.
- Executions are deduplicated by account and execution ID. The normal TWS query
  supplies only the broker's available recent executions; `execDetailsEnd` does
  not prove lifetime history or an investment strategy's cadence. Coverage sets
  `history_complete = false`. Explicit UTC/GMT/offset execution times can be
  normalized; ambiguous operator-local times stay unknown. Fees absent from a
  late commission callback remain pending and block new risk, never become zero.

Collection uses an ordinary one-shot market-data snapshot, never the billable
regulatory-snapshot flag. It does not procure entitlements. Existing API access,
feed scope and subscriptions must be checked separately.
[Actual data type](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-delayed/receive-market-data-type),
[snapshot completion](https://www.interactivebrokers.com/docs/tws-api/doc/market-data-live/top-of-book-l-1/streaming-data-snapshots).

A request-specific missing-entitlement error or quote timeout retains independently
completed account, cash and position observations. It does not mark the failed
quote request complete or make the snapshot executable. A delayed-data fallback
warning remains a named subscription diagnostic. Check the API acknowledgement
and actual API-enabled market-data subscriptions in Client Portal; accepting the
agreement alone does not establish a real-time entitlement. Keep Read-Only API
enabled throughout. The SDK's raw request/callback logging is suppressed because
it can serialize account identifiers and full payloads into MCP stderr. Public
diagnostics use fixed codes and sanitized projections.

## Local evidence and verification

`collect_execution_snapshot(settings, instrument_ids, now=None, transport=None)`
returns a sealed `ready` or `blocked` view. `validate_execution_snapshot` checks
its digest and expiry; it does not prove the account has remained unchanged.
`account_version` excludes quotes and receipt times, while `snapshot_id` binds
the entire collected view. Account keys are local linkage identifiers, not
anonymity guarantees. The service must store broker snapshots privately and
send models only an explicit minimum projection; local persistence does not
mean the model is offline.

Run the isolated adapter suite:

```text
python -S -B scripts/_test_broker.py
```

It verifies injected fixtures and the real transport's query sequence using an
in-memory SDK double. It does not install the SDK or certify personal-account
permissions, execution economics, or future investment returns.

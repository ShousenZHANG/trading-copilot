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

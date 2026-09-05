# MCP Server Setup

> How the data servers are wired, and how to turn one on.

## The model: active file + catalog

There are two files and one script.

| File | Role |
|------|------|
| `.mcp.json` | The **active set**. Claude Code launches every server it finds here — nothing else. |
| `.mcp.json.template` | The **catalog**. Every supported server, with its command line and the env var it reads. Nothing here runs. |
| `scripts/enable_mcp.py` | Copies an entry from the catalog into the active file, or removes it again. |

```bash
python scripts/enable_mcp.py                 # list the catalog and what is active
python scripts/enable_mcp.py fred            # copy fred into .mcp.json
python scripts/enable_mcp.py fred --disable  # remove it again
```

Restart Claude Code (or `/reload-plugins`) after a change — the server list is
read at startup.

> **Absence is the off switch.** An earlier version of this repo "disabled" a
> server by renaming its key to `_name`. Claude Code has no such convention: it
> launched all of them anyway, which is why sessions used to open with a wall of
> `_polygon: Connection closed` errors. If a server is in `.mcp.json`, it runs.

## What ships active

Two servers, and only two:

| Server | Key needed | What it covers |
|--------|-----------|----------------|
| `yahoo-finance` | none | quotes, OHLCV history, company info, recommendations |
| `finnhub` | `FINNHUB_API_KEY` (free tier, 60 req/min) | news, financials, earnings surprises, insider data |

Both are spawned through `uv` / `uvx`, so **`uv` must be on your PATH** — neither
server is pure-Python-stdlib and neither runs without it. Install from
<https://docs.astral.sh/uv/>, then check with `uv --version`.

## Verify — the only smoke test that means anything

```bash
python scripts/mcp_handshake.py --all
```

Run this **once from a shell before your first Claude Code session**. Claude Code
allows a stdio MCP server 30 seconds to answer; a cold `uv` cache needs 26.3s just
to provision `yahoo-finance`, so the first launch inside the client can time out
while the same server is perfectly healthy. The handshake pre-warms the cache and
turns that into a one-time 30-second wait you can see.

This spawns each active server exactly the way `.mcp.json` tells Claude Code to,
speaks the JSON-RPC `initialize` handshake, and requires a `serverInfo` back:

```
PASS     2.8s  finnhub          finnhub 1.28.1
PASS     6.0s  yahoo-finance    yfinance 1.29.1

2/2 server(s) completed the handshake.
```

Exit code 0 means every probed server answered; 1 means at least one did not.
Use `--server <name>` to probe one, `--timeout` to raise the 180s default (a cold
`uvx` run resolves dependencies before the server starts).

Do **not** smoke-test with `uvx yahoo-finance-mcp --help`. That was the old
instruction here and it is worthless: the process exits 0 even when the server
dies on import, which is exactly how a broken `mcp` SDK pin went unnoticed for
weeks. Inside Claude Code, `/mcp` shows the same truth interactively.

---

## Catalog: keys and signup

Every key lives in `.env` (gitignored) and is referenced as `${VAR}` from the
config. Copy `.env.example` to `.env` and fill in only what you enable.

### Free, no key

**`akshare`** — A-share / HK / index data (`.SS`, `.SZ`, `.BJ`, `.HK`). No
registration, no token. Fills the gap Yahoo and Finnhub do not cover. Local
single-file wrapper at `mcps/akshare_mcp.py`; `uv` provisions its deps on first
run, which is slow, which is why it is not active by default.

```bash
python scripts/enable_mcp.py akshare
python mcps/akshare_mcp.py --probe      # requires network reachability
```

> **Honest status**: its `--self-test` covers symbol parsing and makes **zero
> network calls**, and the server has never been verified against live AkShare
> endpoints from this repo. The endpoints are mainland-China hosted and may be
> unreachable from your network. Run `--probe` before relying on it.

### Free with signup

| Server | Signup | Free tier | Env var |
|--------|--------|-----------|---------|
| `finnhub` | <https://finnhub.io/register> | 60 req/min | `FINNHUB_API_KEY` |
| `fred` | <https://fred.stlouisfed.org/docs/api/api_key.html> | unlimited | `FRED_API_KEY` |
| `polygon` | <https://polygon.io/dashboard/signup> | 5 req/min | `POLYGON_API_KEY` |
| `alpha-vantage` | <https://www.alphavantage.co/support/#api-key> | 25 req/day | `ALPHA_VANTAGE_API_KEY` |
| `exa` | <https://exa.ai/> | ~$10 starting credit | `EXA_API_KEY` |
| `gold` | <https://www.goldapi.io/> | 100 req/month | `GOLD_API_KEY` |
| `tushare` | <https://tushare.pro/> | points quota | `TUSHARE_TOKEN` |

Notes on the ones with a catch:

- **`fred`** is what `macro-analyst` wants for `/gold` (real yields, DXY, CPI).
  Without it the macro analyst falls back to what Yahoo exposes.
- **`alpha-vantage`** and **`tushare`** are hosted HTTP MCPs — no local install,
  but also nothing in this repo you can audit.
- **`gold`** is optional even for `/gold`: Yahoo's `GC=F` (futures) and
  `XAUUSD=X` (spot) are free and unlimited, at slightly lower precision.
- **`tushare`** sits *below* `akshare` in the A-share fallback chain precisely
  because it needs a token and gates endpoints behind a points quota. Try
  `akshare` first.
- **`exa`** is the social/news fallback. Without it, `social-analyst` degrades to
  keyless Reddit RSS — see [mcp-fallback.md](./mcp-fallback.md).

### Windows note

`fred` and `exa` are launched via `npx`, which ships as a `.cmd` shim on Windows.
If Claude Code cannot spawn one directly, wrap **that one entry** as
`"command": "cmd", "args": ["/c", "npx", "-y", "<pkg>"]`. Never do this for
`uv`/`uvx` (real executables), and never put a `<` inside a cmd-wrapped argv —
`cmd` re-parses it as a redirect, which is how the `mcp<2` pin once became a
broken shell redirection.

---

## Troubleshooting

A server shows `error` in `/mcp`, or `mcp_handshake.py` returns non-zero:

1. **`uv` / `npx` on PATH?** `uv --version`, `npm --version`.
2. **Key actually in the environment?** `${VAR}` is substituted from the
   environment of the process that starts Claude Code, not read from `.env` by
   Claude Code itself. Launch via `scripts/start.ps1` (Windows) or
   `scripts/start.sh` (macOS / Linux / WSL) so `.env` is loaded first.
3. **Env var name matches the catalog exactly?** `GOLD_API_KEY`, not
   `GOLDAPI_KEY`.
4. **Key still valid?** Test it in the vendor's own dashboard.
5. **Run the handshake for the single server**: `python scripts/mcp_handshake.py
   --server <name>` prints the actual failure instead of a generic
   "Connection closed".

## Recommended set

Start with the shipped default — `yahoo-finance` + `finnhub` runs `/advise` and
`/analyze` end to end. Add `fred` before you rely on `/gold`, `exa` if you want
better social/news coverage than keyless RSS, and `akshare` only if you actually
trade `.SS` / `.SZ` / `.HK` names and have verified it reaches its endpoints.

Every server you enable is another third-party process reading your machine's
environment. Enable deliberately.

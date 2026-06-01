# MCP Server Setup

> Wire the MCP servers that power Trading Copilot. Project MCPs live in `.mcp.json`. Servers prefixed with `_` are disabled; use `python scripts/enable_mcp.py` to toggle them.

---

## Tier 0: Zero-config (enable now, no keys needed)

These two work immediately — no signup, no keys.

### 1. Yahoo Finance — primary stock data

```bash
# Test it works
uvx yahoo-finance-mcp --help
```

`yahoo-finance` is already enabled in `.mcp.json` by default. If disabled, run `python scripts/enable_mcp.py yahoo-finance`.

### 2. claude-mem — cross-session memory

```bash
npm i -g claude-mem
claude-mem --version
```

Run `python scripts/enable_mcp.py claude-mem` if you want this optional server.

---

## Tier 1: Free with signup (recommended)

Sign up takes 1-2 min each. All have generous free tiers.

### 3. Polygon.io — market data (35+ tools)

1. Sign up: https://polygon.io/dashboard/signup
2. Get your API key from dashboard
3. Put in `.env`: `POLYGON_API_KEY=pk_xxx`
4. Run `python scripts/enable_mcp.py polygon`
5. Test: ask Claude "use Polygon to get NVDA last quote"

### 4. Alpha Vantage — backup market data (hosted MCP)

1. Sign up (instant): https://www.alphavantage.co/support/#api-key
2. Put key in `.env`: `ALPHA_VANTAGE_API_KEY=xxx`
3. Run `python scripts/enable_mcp.py alpha-vantage`
4. Note: This one is HTTP-hosted (no local install).

### 5. Finnhub — news + financials

1. Sign up: https://finnhub.io/register
2. Free tier: 60 requests/min
3. Put key in `.env`: `FINNHUB_API_KEY=xxx`
4. `finnhub` is already enabled in `.mcp.json` by default. If disabled, run `python scripts/enable_mcp.py finnhub`.

### 6. FRED — Fed economic data

1. Sign up: https://fred.stlouisfed.org/docs/api/api_key.html
2. Free, unlimited
3. Put key in `.env`: `FRED_API_KEY=xxx`
4. Run `python scripts/enable_mcp.py fred`

### 7. Exa — neural web search

1. Sign up: https://exa.ai/
2. Free trial: $10 credits (~1000 searches)
3. Put key in `.env`: `EXA_API_KEY=xxx`
4. Run `python scripts/enable_mcp.py exa`

---

## Tier 2: Optional / commodity-specific

### 8. GoldAPI — spot gold

1. Sign up: https://www.goldapi.io/
2. Free tier: 100 requests/month
3. Put key in `.env`: `GOLD_API_KEY=xxx`
4. Run `python scripts/enable_mcp.py gold`

> **Alternative (no key)**: skip this MCP and use Yahoo Finance ticker `GC=F` (gold futures) or `XAUUSD=X` (spot). Slightly lower precision, but free and unlimited.

---

## Tier 3: Cloud sync (optional, for multi-device)

### Supabase — Postgres for positions/decisions sync

Skip if you only use one machine. The local markdown in `data/` is the source of truth.

If you want sync:
1. Create project at https://supabase.com/
2. Get URL + anon key + project ref
3. Put in `.env`: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_PROJECT_REF`
4. Add Supabase MCP block manually to `.mcp.json` or `.codex/config.toml` (see [Supabase MCP repo](https://github.com/supabase-community/supabase-mcp))

---

## Verification

After enabling MCPs, restart Claude Code and run:

```
/mcp
```

You should see your enabled MCPs listed with status `connected`. If any show `error`, check:
1. Env var name matches `.env` exactly
2. API key is valid (test in vendor's dashboard)
3. `uvx` or `npx` is on PATH (`uv --version`, `npm --version`)

---

## Recommended minimum for getting started

For a working MVP today, enable:
- ✅ yahoo-finance (free, no key)
- ✅ claude-mem (free, no key)
- ✅ FRED (free key)
- ✅ Finnhub (free key, 60/min)
- ✅ Exa ($10 free credits)

That's enough to run `/analyze NVDA` and `/gold` end-to-end with zero ongoing cost.

Add Polygon + Alpha Vantage later when you hit Yahoo's rate limits or want options/forex coverage.

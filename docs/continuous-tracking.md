# Continuous Tracking Setup

> Run Trading Copilot autonomously via GitHub Actions. Free for public repos. Daily premarket scan + Sunday weekly review + Telegram push.

## Architecture

```
GitHub Actions cron (8:27am ET workdays)
  -> headless `claude -p --model sonnet "Run /scan"`
  -> 12-agent staged pipeline per ticker
  -> writes data/decisions/<TICKER>-<DATE>.md + data/decisions/_scan-<DATE>.md
  -> scripts/notify.py picks high-conviction Buy/Sell, pushes Telegram
  -> dedup via data/state/pushed_alerts.json (sha256 of ticker+date+rating)
  -> git commit non-private decision/state artifacts + push back to repo

Same pattern Sunday 6pm ET for /weekly-review (resolves T+5d entries + Opus synthesis).
```

## Setup steps

### 1. GitHub repo

If you haven't already:

```bash
cd D:/trading-copilot
gh repo create trading-copilot --private --source=. --remote=origin
git push -u origin main
```

> **Use `--private`** — `data/decisions/` will get committed back, and you don't want strangers reading your trading research.

### 2. GitHub Secrets

Settings → Secrets and variables → Actions → New repository secret. Add:

| Secret | Required for |
|--------|-------------|
| `ANTHROPIC_API_KEY` | All Claude calls |
| `POLYGON_API_KEY` | Polygon MCP |
| `ALPHA_VANTAGE_API_KEY` | Alpha Vantage MCP |
| `FINNHUB_API_KEY` | Finnhub MCP |
| `FRED_API_KEY` | FRED MCP |
| `EXA_API_KEY` | Exa MCP |
| `GOLD_API_KEY` | Gold MCP (optional) |
| `TELEGRAM_BOT_TOKEN` | Push notifications |
| `TELEGRAM_CHAT_ID` | Push notifications |

### 3. Telegram bot setup

1. Open Telegram → search `@BotFather` → `/newbot` → follow prompts.
2. Copy the bot token. Save as `TELEGRAM_BOT_TOKEN` secret.
3. Get your chat ID:
   - Send any message to your new bot.
   - Visit `https://api.telegram.org/bot<TOKEN>/getUpdates`
   - Copy the `chat.id` value. Save as `TELEGRAM_CHAT_ID` secret.
4. Test locally:
   ```bash
   export TELEGRAM_BOT_TOKEN=...
   export TELEGRAM_CHAT_ID=...
   python scripts/notify.py test
   ```

### 4. Enable workflows

Push the repo. The two workflows in `.github/workflows/` will activate automatically:

- `premarket.yml` — workdays 8:27am ET
- `weekly-review.yml` — Sundays 6pm ET

Cron times account for both EST and EDT. GitHub Actions cron is approximate (~5-15 min jitter is normal).

### 5. Manual trigger (testing)

```bash
gh workflow run premarket.yml --ref main
gh workflow run premarket.yml --ref main -f filter=tech,ai
gh workflow run premarket.yml --ref main -f force=true

gh workflow run weekly-review.yml --ref main
gh workflow run weekly-review.yml --ref main -f no_resolve=true
```

Or via GitHub Actions tab in the UI.

## Cost estimate (per run)

| Run | Tickers | Avg cost | Notes |
|-----|---------|----------|-------|
| Premarket scan | 12 watchlist | ~$12-36 before caching, often lower | 9 Sonnet agents + 2 Opus per ticker, prompt caching can reduce materially |
| Weekly review | 1 portfolio review | ~$2-4 | Mostly Opus synthesis |

Monthly total can exceed $250 if you run a full deep scan across a 12-name watchlist every workday. Keep daily automation on `/advise` or a small filtered watchlist unless you explicitly accept the spend.

To reduce: scan smaller watchlist, skip days when MCPs return stale data, drop debate rounds to 0 (no debate, just analyst → Trader → Risk → PM).

## Dedup logic

`data/state/pushed_alerts.json` stores `sha256(ticker + date + rating)` of every alert ever pushed. Subsequent scans on the same day won't re-push the same call. Resets implicitly each day because the date is in the hash.

To clear: delete `data/state/pushed_alerts.json`.

## What gets committed back

After each successful run, the bot commits non-private artifacts:

- `data/decisions/<TICKER>-<DATE>.md` — full decision report
- `data/decisions/_scan-<DATE>.md` — scan summary
- `data/decisions/_weekly-<DATE>.md` — weekly review (Sundays only)
- `data/state/pushed_alerts.json` — dedup state

`data/positions.md`, `data/runs/`, `data/memory/trading_memory.md`, and `data/audit/` are **gitignored** personal state. Update them locally only. If you want cloud persistence for memory/reflections, use a private state backend or explicitly opt into force-adding that file in a private repository after accepting the privacy tradeoff.

## Failure modes

- **MCP rate limit**: scan slows down or fails partway. State is committed up to that point, next day picks up the missed tickers.
- **Anthropic API down**: workflow retries via GitHub's standard retry, then fails. No state corruption — pending entries stay pending.
- **Telegram rate limit (30 msg/sec, 20 msg/min per chat)**: notify.py is sequential and small-volume; not a real concern unless you watchlist 50+ tickers.
- **Stale market data**: Yahoo's free tier has 15-min delay. Acceptable for daily-horizon decisions; not for intraday.

## Big-tech equivalent pattern

Bloomberg / IB / Goldman use a 3-tier architecture:

1. **Hot path** (sub-ms): exchange direct feeds → Kafka → rule-based filters → human/algo execution.
2. **Warm path** (seconds): filtered events → LLM enrichment → trader desk.
3. **Cold path** (minutes-hours): aggregated signals → research review.

Our personal-scale equivalent:

- Hot path → broker webhook (when wired in Phase 9)
- **Warm path → GitHub Actions cron + headless claude-p (this setup)** ← you are here
- Cold path → `/weekly-review` Opus synthesis

Sufficient for personal research. For higher-frequency / larger-capital use cases, layer Vercel webhooks for sub-second alerts on broker push events.

# Strategy Setup Checklist

> Manual steps you (Eddy) need to do outside Claude Code. Tick as completed.
>
> See [strategy.md](strategy.md) for the full plan.

## Account setup (one-time)

- [ ] **CommSec Pocket** account opened + funded with $4500 AUD — see [ADR-0005](adr/0005-commsec-pocket-with-ioz-ioo.md)
  - Download CommSec Pocket app (iOS / Android)
  - **If you have a CBA bank account**: KYC is automatic via cross-link, ~5 minutes
  - **If no CBA**: standard KYC via Frankie. Risk: same auto-verify failure as CMC. If it fails, choose: (a) walk into a police station for free document certification, or (b) switch to Stake (selfie KYC, accepts non-Anglo names better)
  - $50 minimum first buy
- [ ] Cash buffer **$500** parked in high-interest savings (UBank / ING Savings Maximiser / Macquarie Savings)
- [ ] Confirm total investable capital → record bucket in [strategy.md](strategy.md)

## DCA execution (Mondays for 4 weeks, all via CommSec Pocket)

- [ ] **Week 1 (Mon 2026-05-11 or post-KYC Tue/Wed)**: Buy NDQ.AX $750 + IOO.AX $375. $2 brokerage per trade × 2 = $4 W1 cost.
- [ ] Week 2 (Mon 2026-05-18): Same batch if Sunday `/advise` didn't trigger pause
- [ ] Week 3 (Mon 2026-05-25): Same
- [ ] Week 4 (Mon 2026-06-01): Final batch
- [ ] Pocket UI: select theme **Aussie Top 200** → NDQ. Select theme **Global 100** → IOO.
- [ ] Update `data/positions.md` after each buy with actual shares + cost basis. Pocket may auto-fractional — record exact unit count.

## CMC Invest cleanup (no action needed)

- [ ] **Do NOT** complete CMC's pending KYC (no document upload, no deposit)
- [ ] CMC will auto-lapse the application in 30-90 days
- [ ] If reminder email arrives: reply once "please withdraw my application" — done
- [ ] CMC retains submitted PII for 7 years per AU AML/CTF Act (mandatory regardless of explicit cancellation)

## Telegram push notifications (recommended)

- [ ] Create Telegram bot via [@BotFather](https://t.me/BotFather), get bot token
- [ ] Get your chat ID from [@userinfobot](https://t.me/userinfobot)
- [ ] Add to `.env`:
      ```
      TELEGRAM_BOT_TOKEN=...
      TELEGRAM_CHAT_ID=...
      ```
- [ ] Test: `python scripts/notify.py push-weekly` (after a `/weekly-review` run exists)

## GitHub Actions (optional — for cloud automation)

If you want Sunday runs to happen even when you're not at the computer:

- [ ] Add `ANTHROPIC_API_KEY` to repo secrets (Settings → Secrets → Actions)
- [ ] Add `FINNHUB_API_KEY` to repo secrets
- [ ] Add `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` to repo secrets
- [ ] Enable workflows in `.github/workflows/`:
  - [weekly-review.yml](../.github/workflows/weekly-review.yml) — Sunday 6pm ET, runs `/weekly-review`
  - [premarket.yml](../.github/workflows/premarket.yml) — Mon-Fri 8:27am ET, runs `/scan` — **DISABLE** for current regime (too frequent for buy-and-hold ETF). Either:
    - comment out the `cron:` lines, or
    - leave only `workflow_dispatch` trigger so it runs only on manual click
- [ ] Add a new Sunday cron for `/advise` ETF check (optional, can run locally instead)

## First Sunday routine (to run now or this Sunday)

```bash
cd D:/trading-copilot
.\scripts\start.ps1                      # loads .env, launches Claude Code

# In Claude Code:
/advise VAS.AX                           # core ETF #1 — get baseline
/advise VGS.AX                           # core ETF #2 — get baseline
/advise SPY                              # global macro tell
/scan                                    # paper-trade US watchlist (long, ~30-60 min)
python scripts/memory.py list-pending    # show what's queued for T+5d resolution
```

After 5 trading days have passed since the first `/scan`:
```
/weekly-review                           # resolves T+5d, computes alpha
```

## Things you must NOT do

- Buy individual US stocks (until graduation gates pass)
- Buy crypto / leverage / options
- Sell ETFs based on a single weekly `/advise` Avoid (only 4 consecutive Avoids triggers)
- Time the market by waiting for "the dip"
- Increase position size beyond 60/30/10 ratios
- Look at FinTwit / WSB / Discord pump posts and act on them

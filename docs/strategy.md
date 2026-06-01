# Personal Investment Strategy

> Single source of truth for current investment plan. All `/scan`, `/advise`, `/weekly-review` runs operate against this strategy.
>
> **Status**: Active 2026-05-08. Capital upgraded $5k → **$8k** on 2026-05-17. DCA cadence revised to Option E-2 (6 weeks × A$1,000 per trade) per [ADR-0007](adr/0007-option-e2-dca-1k-rule.md). Review at $30k AUD capital milestone or 2026-11-08 (whichever first).

## Profile

- **Investor**: Eddy Zhang
- **Domicile**: Australia (AU retail)
- **Brokers**: CommSec (ASX), Pearler (ASX + US fractional)
- **Capital regime**: Tier A (<$10k AUD)
- **Horizon**: 3-6 months minimum hold; weekly thesis check
- **Bias**: Conservative

## Allocation (current regime: **$8,000 AUD**, US-heavy via Pocket wrappers)

| Slice | % | $ AUD | Vehicle | Broker |
|-------|---|-------|---------|--------|
| US tech core | 60% | **$4,800** | **NDQ.AX** (BetaShares NASDAQ 100) | **CommSec Pocket** ($2/trade ≤$1000) |
| Global mega-cap | 30% | **$2,400** | **IOO.AX** (iShares Global 100, ~70% US) | **CommSec Pocket** |
| Cash buffer | 10% | **$800** | High-interest savings (UBank / ING / Macquarie) | bank |

**Effective US exposure**: 60% × 95% (NDQ US weight) + 30% × 70% (IOO US weight) ≈ **78% US equity**. The remainder is non-US developed-market mega-cap (mostly EU pharma + AU resources via IOO's diversified slice).

**Broker rationale**: CommSec Pocket retained from prior plan. Pivot 2026-05-10 swaps **IOZ → NDQ** to lift US exposure from ~21% to ~78% within Pocket's whitelist — no broker switch required, no FX cost, no W-8BEN. See [ADR-0006](adr/0006-ndq-pivot-us-heavy.md).

**ETF substitution tradeoff (post-pivot)**:
- **NDQ vs direct US**: NDQ is ASX-listed wrapper of NASDAQ 100. Same underlying exposure (NVDA/MSFT/AAPL/META/GOOGL etc.) without FX conversion friction. MER 0.48% (vs ~0.20% on direct QQQ via Stake) — extra 0.28%/yr drag is the price of avoiding broker switch + FX spread + tax complexity.
- **IOO concentration**: IOO holds top 100 global mega-caps (concentrated, tech-heavy). Compounds NDQ's tech overweight — combined portfolio is **heavily mega-cap US tech**.
- **No broad US**: VTS (CRSP US Total) and IVV (S&P 500) are NOT in Pocket's whitelist. To add broad US exposure later, need to layer in Stake or CommSec full.

⚠️ **Concentration warning**: this allocation is high US-tech beta. NDQ -33% drawdown happened in 2022. Plan tolerates this volatility; if user cannot, revisit allocation before W1 buy.

**No individual stocks. No leverage. No options. No crypto.** Until graduation gates pass (see [ADR-0001](adr/0001-100pct-etf-until-30k.md)).

## Initial entry: DCA 6 weeks × A$1,000 (Option E-2)

See [ADR-0002](adr/0002-dca-4-weeks-initial-entry.md) (original 4-week thesis) and [ADR-0007](adr/0007-option-e2-dca-1k-rule.md) (revision: capital upgraded $5k → $8k, $1,000-per-trade constraint locks into Pocket $2 sweet spot, NDQ W2 deferred 3 days past NVDA earnings).

### Constraints
- Every trade **exactly A$1,000** (Pocket $2 sweet spot; >A$1,000 triggers $10 fee)
- Skip weeks where rule cannot be satisfied
- NDQ W2 deferred to 5/21 Thursday (post-NVDA Q1 FY27 earnings)

### Schedule

| Step | Date (2026) | NDQ ($) | IOO ($) | Brokerage | Cumulative invested | Notes |
|------|-------------|---------|---------|-----------|---------------------|-------|
| W1 ✅ | Mon 2026-05-11 | $773 (13u @ $59.45) | $381 (2u @ $190.50) | $4 | $1,158 | Done. Below $1k each — pre-$8k-upgrade |
| W2 | Mon 2026-05-18 | **skip** | **skip** | $0 | $1,158 | NDQ deferred for NVDA risk window; IOO at 52W high blocks $1k entry |
| W2+ | **Thu 2026-05-21** | $1,000 | 0 | $2 | $2,160 | Post-NVDA, conditional on `/advise` |
| W3 | Mon 2026-05-25 | $1,000 | $1,000 | $4 | $4,164 | |
| W4 | Mon 2026-06-01 | $1,000 | 0 | $2 | $5,166 | NDQ-only |
| W5 | Mon 2026-06-08 | 0 | $1,000 | $2 | $6,168 | IOO-only |
| W6 | Mon 2026-06-15 | $1,000 | 0 | $2 | **$7,170 (final NDQ)** | |

**Final totals**:
- NDQ: $773 + 4 × $1,000 = **$4,773** (59.66%)
- IOO: $381 + 2 × $1,000 = **$2,381** (29.76%)
- Cash: $8,000 - $7,154 = **$846** (10.58%)
- Total brokerage: $4 + $12 = **$16** (0.20% of $8k)

**DCA pause triggers** (suspend that week's batch, retain cash, re-evaluate next week):
- `/advise NDQ.AX` rating = `Reduce` or `Avoid` → pause NDQ only
- `/advise IOO.AX` rating = `Reduce` or `Avoid` → pause IOO only
- `/advise SPY` rating = `Avoid` AND (NDQ or IOO) rating = `Avoid` → pause that ETF (US-correlated signal)
- All 3 (`NDQ` + `IOO` + `SPY`) `Avoid` → pause all DCA, hold cash, wait 4 weeks (US-wide risk)
- Known single-name binary event within 3 days of buy (e.g., NVDA earnings for NDQ): defer that ETF's batch past the event. NOT timing the macro — tactically dodging a known asymmetric variance source. Re-run `/advise` after the event before deploying.

## Weekly routine (Sunday)

```
1. python scripts/memory.py list-pending
2. /weekly-review                     # resolve T+5d entries, compute alpha
3. /advise NDQ.AX                     # core ETF #1 thesis check (Nasdaq 100 wrapper)
4. /advise IOO.AX                     # core ETF #2 thesis check (Global 100)
5. /advise SPY                        # broad US macro tell — flags mega-cap-vs-broad divergence
6. /scan                              # paper-trade US mega-cap watchlist
7. Read data/decisions/_scan-<DATE>.md — for learning, NOT execution
```

Telegram push: configure `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` so high-conviction alerts go to phone.

## Action triggers (pre-committed, anti-emotion)

| Signal (Sunday `/advise` output) | Action |
|---|---|
| All ETFs `Hold` or `Buy` | Continue DCA per schedule |
| Single ETF `Reduce` | Pause that ETF's DCA batch this week |
| Single ETF `Avoid` | Pause DCA, **do NOT sell** existing |
| All 3 (`NDQ` + `IOO` + `SPY`) `Avoid` | Pause all DCA, hold cash, wait 4 weeks |
| Any individual stock with strong rating | **Ignore**. Not in current regime |

## Sell rules

- **ETF core**: never time-sell. Hold through cycles. Only liquidate when needing cash (life event)
- **Rebalance**: 6-month cadence. If allocation drifts >10pct from 60/30 target, rebalance back

## Paper-trade (data-gathering for graduation)

Weekly `/scan` runs against [data/watchlist.md](../data/watchlist.md) US mega-cap tech list. **No real money**. Output:
- `data/decisions/_scan-<DATE>.md` — weekly summary
- `data/memory/trading_memory.md` — append pending entries
- `/weekly-review` resolves pending after T+5 days, computes alpha vs SPY

Goal: accumulate 20+ resolved decisions before graduating. See [ADR-0003](adr/0003-paper-trade-via-scan.md).

## Graduation gates (paper-trade → real satellite)

All 3 must pass:

| Gate | Threshold | Status |
|------|-----------|--------|
| **G1. Capital** | ≥$30k AUD total investable | Not met (currently <$10k) |
| **G2. Decision count** | ≥20 resolved entries in memory log | Not met (0) |
| **G3. Performance** | Hit-rate ≥55% OR mean alpha ≥+0.5% | Not yet measurable |

Until all 3 pass: **stay 100% ETF**. No deviation.

If G1+G2 pass but G3 fails: **don't activate satellite**. Pipeline doesn't have alpha for this regime/universe. Stay 100% ETF terminally.

## When to revisit this doc

- Capital crosses $30k AUD threshold
- 6 months elapsed (2026-11-08)
- Major life event (income change, marriage, house purchase)
- 3+ pause-DCA weeks in a row (something is structurally wrong with assumptions)

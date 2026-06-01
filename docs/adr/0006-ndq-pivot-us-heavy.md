# Pivot to US-heavy via NDQ.AX (replaces IOZ.AX)

**Status**: accepted, 2026-05-10. Supersedes [ADR-0005](0005-commsec-pocket-with-ioz-ioo.md) for the AU equity slice.

## Context

The user requested a strategy pivot: 100% US equity exposure rather than the prior 60% AU (IOZ) + 30% global (IOO) allocation. Hard constraints from prior decisions still apply: CommSec Pocket as broker (no FX, no W-8BEN, no manual KYC re-do), $5000 AUD total, 4-week DCA.

CommSec Pocket's whitelist supports only seven themed ETFs:

- Aussie Top 200 → IOZ.AX
- Global 100 → IOO.AX
- **Tech Savvy → NDQ.AX** (BetaShares NASDAQ 100 wrapper)
- Health Wise → IXJ.AX
- Sustainability → ETHI.AX
- Aussie Dividends → IHD.AX
- Emerging Markets → IEM.AX

Direct US tickers (SPY, VOO, QQQ, AAPL, NVDA) are NOT available on Pocket. Direct broad-US wrappers IVV.AX (S&P 500) and VTS.AX (CRSP US Total) are also NOT in Pocket's whitelist.

## Decision

**Replace IOZ (AU equity core) with NDQ (US tech core)**. IOO retained at 30% to add non-tech mega-cap diversity (financials, healthcare, energy).

| Slice | Pre-pivot | Post-pivot | Net change |
|-------|-----------|-----------|-----------|
| Slot 1 (60%) | IOZ.AX (ASX 200) | **NDQ.AX (Nasdaq 100)** | -100% AU, +95% US tech |
| Slot 2 (30%) | IOO.AX (Global 100, 70% US) | IOO.AX (unchanged) | unchanged |
| Slot 3 (10%) | Cash | Cash | unchanged |

**Effective US equity exposure**: 60% × 95% + 30% × 70% ≈ **78%** (vs ~21% pre-pivot).

**AU equity exposure**: ~0% direct (was ~60% via IOZ).

## Rationale

User requirement is explicit: prioritise US exposure. Within Pocket's whitelist, NDQ is the highest-US-content option (95% US tech vs IOO's 70% US blend, IXJ's ~60% US healthcare, others lower). Combining NDQ + IOO gives ~78% US exposure without broker switch.

Direct VOO / VTI / QQQ via Stake would deliver 100% US but at the cost of:
- 0.7% FX spread × 4 DCA buys = ~$30 friction
- W-8BEN tax form + complex AU return reporting on US dividends/CGT
- Currency mismatch risk vs AUD-denominated living expenses
- Restart KYC (Pocket KYC already submitted)

The marginal 22 percentage points of US exposure (78% NDQ+IOO vs 100% direct) does not justify those operational + tax costs at $5000 starting capital.

## Considered alternatives

- **100% direct US via Stake** (VOO + QQQ): rejected this round. FX + tax friction outweighs the 22pp exposure delta at this scale. Revisit at graduation ($30k AUD).
- **100% NDQ ($4500 in one ETF)**: rejected. Too concentrated — NDQ alone is mega-cap tech, single-sector beta. IOO's non-tech sleeve absorbs sector rotation risk.
- **Stay 60% IOZ + 30% IOO**: rejected per user pivot request. Maintained as fallback if NDQ thesis breaks.
- **50% NDQ + 30% IOO + 20% IOZ (hybrid)**: rejected. Cuts US exposure to ~62%, undermines the pivot's intent.

## Consequences

### Positive
- Aligns with user stated preference for US exposure
- No broker switch, no FX, no W-8BEN, no KYC re-do
- Same DCA mechanics ($2/trade brokerage, $1125/week, 4-week schedule)
- Same trigger table mechanics (Reduce/Avoid → pause that ETF)

### Negative — explicit
- **AU exposure → 0%**. Loss of home-country diversification. Currency mismatch with AUD income/expenses.
- **Concentration**: 78% US + 30% × top-100 + 60% × top-100-Nasdaq → top 10 holdings (NVDA, MSFT, AAPL, META, GOOGL, AMZN, TSLA, BRK-B, JPM, V) likely >40% of total portfolio.
- **NDQ -33% drawdown precedent (2022)**. Same setup risk now. Plan must tolerate this volatility.
- **Loss of franking credits** — IOZ delivered franking-credit-bearing AU dividends; NDQ does not.
- **MER cost increase**: NDQ MER 0.48% vs IOZ MER 0.05% on the 60% slice = ~+$13/year extra fees.

### Trigger table delta

Original trigger table referenced IOZ; updated to NDQ:

| Old | New |
|---|---|
| `/advise IOZ.AX` Reduce/Avoid → pause IOZ batch | `/advise NDQ.AX` Reduce/Avoid → pause NDQ batch |
| All 3 (IOZ + IOO + SPY) Avoid → pause all | All 3 (NDQ + IOO + SPY) Avoid → pause all |
| (no specific divergence rule) | If SPY shows late-cycle top + IOO/NDQ Buy → consider halving the batch (per SPY-2026-05-10 advice) |

### Graduation gate adjustments

ADR-0001 stays unchanged: still 100% ETF until $30k AUD. ADR-0003's paper-trade graduation gate now operates against a US-heavy core, which is closer to the paper-trade watchlist (NVDA / AAPL / MSFT / GOOGL / TSLA / AMD) — so paper-trade hit-rate is more directly comparable to real ETF performance.

## Revisit triggers

- **Capital ≥ $30k AUD**: open Stake or CMC alongside Pocket, layer in real VOO/VTS for broader US diversification beyond top 100 names.
- **NDQ -20% drawdown sustained 4+ weeks**: revisit AU/global rebalance question. Consider trimming NDQ → adding IOZ as ballast.
- **AUD/USD swings >15% in 6 months**: review currency mismatch impact on real wealth.
- **Major US tech regulatory event** (DOJ breakup of MSFT/GOOG, AI moratorium, etc.): immediate `/analyze NDQ.AX` deep dive.

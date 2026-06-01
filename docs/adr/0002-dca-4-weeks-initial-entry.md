# DCA over 4 weeks for initial ETF entry

**Status**: accepted, 2026-05-08

Lump-sum is mathematically optimal 67% of historical periods ([Vanguard 2012 study](https://corporate.vanguard.com/content/dam/corp/research/pdf/dca_final.pdf)) — markets trend up, "waiting" loses on average. Decision-criterion is regret minimization at peak-ish valuations (SPY 2026-04 forward P/E ~22x vs 20-year mean ~17x), not pure expected-value optimization.

DCA over 4 weeks balances:
- 75% of lump-sum's expected upside captured by week 4
- Smooths a 10%+ drawdown across remaining batches if it arrives
- Keeps DCA brokerage cost ~0.8% of $10k portfolio (~$80 across 8 trades), tolerable
- 4 weeks is short enough that opportunity cost vs lump-sum is bounded

Considered alternatives:
- **Lump-sum**: rejected for new investor with first deployment at high-valuation regime
- **DCA 12 weeks**: rejected — too slow, sacrifices 67% lump-sum advantage
- **Wait for correction (timing)**: rejected — "wait for dip" is the worst retail strategy; correction may not arrive for years

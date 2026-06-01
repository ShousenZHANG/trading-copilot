# 100% ETF allocation until $30k AUD capital

**Status**: accepted, 2026-05-08

At <$10k AUD capital, individual US stocks are negative-EV after costs:
- Pearler brokerage $6.50 USD × 2 (buy+sell) ≈ 4% on a 5% ($500) position
- 30% withholding on US dividends
- 1-week alpha noise (±2%) drowns signal at single-share size

Single-name 5% gate (PM rule) at <$10k means <$500 positions, often <1 whole share. Carries no diversification benefit and high transaction friction.

Decision: stay 100% ETF (60% VAS.AX, 30% VGS.AX, 10% cash) until total investable capital reaches **$30k AUD**, at which point satellite (30% × $30k = $9k cap; 5% single-name = $450) becomes barely viable. Real satellite economics start at $50k+.

Considered alternatives:
- **Hybrid 70/30 from day one**: rejected — capital math doesn't support
- **100% individual stocks**: rejected — no diversification at this size
- **100% ETF forever**: deferred to graduation gates (see [ADR-0003](0003-paper-trade-via-scan.md)); long-term ETF-only is the floor outcome if pipeline shows no alpha

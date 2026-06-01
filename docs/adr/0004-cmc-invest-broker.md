# Use CMC Invest as primary broker

**Status**: superseded by [ADR-0005](0005-commsec-pocket-with-ioz-ioo.md) on 2026-05-10. CMC Invest manual KYC failed (Frankie auto-verify miss for non-Anglo name); switched to CommSec Pocket via CBA cross-link.

At $5000 AUD capital with 4-week DCA at $750 VAS + $375 VGS per week, every batch falls under CMC Invest's **$0 brokerage** threshold (≤ $1000 per stock per day on ASX). Total fees: $0 across all 8 trades.

| Broker | Per-trade | 8-trade total | Drag on $5000 |
|--------|-----------|---------------|---------------|
| **CMC Invest** | **$0** | **$0** | **0.0%** |
| Pearler | ~$3 | $24 | 0.48% |
| CommSec Pocket | $2 | $16 | 0.32% |
| Selfwealth | $9.50 | $76 | 1.52% |

CMC Invest also provides:
- CHESS-sponsored holdings (HIN registered) — direct ownership, not custodial
- ASX + US dual market access (no broker switch needed at graduation)
- Online KYC (1-2 business days)

Considered alternatives:
- **Pearler**: rejected — $24 of avoidable fees on a $5000 portfolio is a 0.48% drag with no offsetting benefit at this scale
- **CommSec Pocket**: rejected — $16 fees, plus restricted ETF list (smaller universe than CMC)
- **Stake / Superhero**: rejected — flat fees worse for sub-$1000 trades

Constraints to remember:
- Multiple buys of the same stock in a single day: only the first is $0; subsequent trades are $11+. Mitigation: stick to weekly Monday cadence, one buy per stock per day.
- US trades on CMC Invest are $0 brokerage but include ~0.7% FX spread. Not relevant in the current 100%-ETF regime; revisit at graduation when satellite activates.

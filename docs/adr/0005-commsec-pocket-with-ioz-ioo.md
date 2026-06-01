# Use CommSec Pocket with IOZ + IOO ETF substitution

**Status**: accepted, 2026-05-10. Supersedes [ADR-0004](0004-cmc-invest-broker.md).

## Context

CMC Invest manual KYC required document certification by JP / police / accountant after Frankie auto-verify failed (non-Anglo name + likely missing AU credit history match). User opted to avoid in-person certification step. Switched to CommSec Pocket, which uses CBA banking infrastructure and accepts cross-linked KYC for existing CBA customers.

## Decision

Open **CommSec Pocket**. Buy:
- **IOZ.AX** (iShares Core S&P/ASX 200) — substitutes VAS.AX (Vanguard ASX 300)
- **IOO.AX** (iShares Global 100) — substitutes VGS.AX (Vanguard MSCI World ex-AU)

Brokerage: **$2 per trade ≤$1000**. 8 DCA trades total = **$16 fees** vs $0 on CMC ($16 paid to skip the JP step).

## ETF substitution analysis

### IOZ vs VAS (acceptable)

| Metric | IOZ | VAS | Diff |
|---|---|---|---|
| Holdings | ASX top 200 | ASX top 300 | -100 small caps |
| MER | 0.05% | 0.07% | IOZ cheaper |
| Issuer | iShares (BlackRock) | Vanguard | both top-tier |
| Yield | ~3.8% | ~4.0% | -0.2% |
| 5y correlation | 99.5%+ | — | functionally identical |

Verdict: **near-perfect substitute**. The 100 missing small caps contribute <2% of weight. IOZ is marginally cheaper.

### IOO vs VGS (acceptable but lossy)

| Metric | IOO | VGS | Diff |
|---|---|---|---|
| Holdings | Global top 100 | ~1500 developed-market | massive concentration shift |
| MER | 0.40% | 0.18% | IOO **5x more expensive** |
| Top sectors | Tech ~30%, healthcare ~15% | broader sector mix | tech overweight |
| Geography | US ~75% | US ~70% | similar |
| Yield | ~1.7% | ~2.0% | -0.3% |

Verdict: **lossy substitute**. IOO is more concentrated (top 100 mega-caps vs broad market), more tech-heavy, higher MER (0.40% vs 0.18% drag), lower yield. Diversification gains from "30% global ex-AU" reduced.

For a $5000 starting portfolio, the tradeoff is acceptable: getting any global exposure beats getting none, and the extra 0.22% MER drag = ~$3.30/year on $1500. Revisit at graduation when capital + alternative broker (Stake / CMC after KYC) make full VGS feasible.

## Considered alternatives

- **Walk into police station for JP certification, complete CMC**: rejected — user prefers to avoid in-person step. Cost: $16 extra fees vs $0 + 5 minutes of manual work.
- **Stake (Onfido selfie KYC)**: rejected this round in favor of Pocket. Stake would have allowed VAS/VGS directly at $3/trade ($24 fees) but user chose Pocket.
- **CommSec full**: rejected — $5-19.95 per trade is too expensive at $5000 capital scale.

## Consequences

- Long-term ETF performance closely tracks original VAS/VGS plan, with IOO concentration drag
- 30% global allocation has higher tech beta than VGS would have provided — partially mitigates if AI/tech leadership continues, hurts if rotation away from mega-cap tech occurs
- At graduation ($30k AUD), reassess: switch to Stake/CMC/Pearler with VAS + VGS once capital justifies the broker friction
- Pocket UI doesn't allow buying arbitrary ASX tickers — if user later wants individual stocks (post-graduation), must open a different broker anyway

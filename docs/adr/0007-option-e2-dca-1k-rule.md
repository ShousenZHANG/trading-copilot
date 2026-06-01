# DCA revision to 6 weeks × A$1,000 per trade (Option E-2)

**Status**: accepted, 2026-05-17. Supersedes [ADR-0002](0002-dca-4-weeks-initial-entry.md) DCA cadence (still respects its rationale; only the schedule changes).

## Context

Three signals converged Sun 2026-05-17 forcing a DCA revision:

1. **Capital upgraded**: $5,000 AUD → $8,000 AUD. Per-week DCA size needed scaling.
2. **CommSec Pocket fee inflection at A$1,000**: confirmed Pocket charges A$2 flat ≤ A$1,000, then jumps to A$10 (or 0.20%) at A$1,001+. A$1,000 is the unambiguous fee sweet spot; user pre-committed all future trades = A$1,000 exactly.
3. **NVDA Q1 FY27 earnings 2026-05-20** (T+2 days from W2 Mon): NDQ holds NVDA ~9% plus AVGO/AMD/Micron = ~15% semiconductor concentration. Combined with confirmed late-cycle macro (CPI hot 3.8%, ERP turned negative -100bps, 10Y 4.595%, NDQ at 52W ATH), a known asymmetric variance source sat directly inside W2 buy window.

Sunday `/advise` outputs converged: SPY → **Reduce**, NDQ.AX → Hold-Reduce, IOO.AX → Hold (but at 52W high zone). All three agents recommended deferring NDQ past NVDA earnings.

## Decision

Replace original 4-week DCA (mixed $750/$375 batches) with **Option E-2: 6 weeks × A$1,000 per trade**, with NDQ W2 deferred to Thursday 2026-05-21 post-NVDA.

Schedule:

| Step | Date | NDQ | IOO | Brokerage |
|------|------|-----|-----|-----------|
| W1 ✅ | 2026-05-11 | A$773 | A$381 | A$4 |
| W2 | 2026-05-18 | skip | skip | A$0 |
| W2+ | 2026-05-21 (Thu) | A$1,000 | 0 | A$2 |
| W3 | 2026-05-25 | A$1,000 | A$1,000 | A$4 |
| W4 | 2026-06-01 | A$1,000 | 0 | A$2 |
| W5 | 2026-06-08 | 0 | A$1,000 | A$2 |
| W6 | 2026-06-15 | A$1,000 | 0 | A$2 |
| **Total** | — | **$4,773** | **$2,381** | **$16** |

Final allocation: NDQ 59.66% / IOO 29.76% / Cash 10.58% — all within ±1pp of 60/30/10 target.

## Rationale (5-dimensional optimization vs alternatives)

| Dimension | Option E-2 | Lump-sum $6k | 12 × $500 | 4 × $1,460 | 3 × $2,000 |
|-----------|-----------|--------------|-----------|------------|------------|
| Brokerage | **$12 (0.21%)** | $20 (0.34%) | $24 (0.41%) | $40 (0.68%) | $30 (0.51%) |
| Allocation precision | ±1pp | exact | exact | exact | exact |
| DCA smoothing | 6 wks | none | 12 wks | 4 wks | 3 wks |
| NVDA risk avoidance | full | none | full | partial | partial |
| Operation simplicity | 6 trades | 1 trade | 12 trades | 4 trades | 3 trades |

E-2 wins on brokerage rate (lowest fee-per-dollar excluding lump-sum), captures DCA smoothing across the NVDA + post-NVDA windows, and removes single-name binary-event variance from NDQ entry.

## Considered alternatives

- **Lump-sum $6k now**: rejected. NDQ + IOO at 52W ATH, ERP negative — historically poor entry. 67% lump-sum-beats-DCA literature is conditional on entry NOT being at percentile peak.
- **12 × $500**: rejected. Doubles brokerage cost, doubles operation count, no offsetting benefit at the longer horizon.
- **3 × $2,000**: rejected. $10/trade brokerage; concentrates entry points; lower DCA value.
- **2 × $3,000**: rejected. $19.95/trade plus extreme concentration.
- **Continue 4-week $750/$375 plan**: rejected. Numbers were designed for $5k budget; not refreshed for $8k upgrade, leaving NDQ at 47% (vs 60% target) by W4 — material allocation miss.

## Pre-committed trigger table

Beyond strategy.md generic triggers, this ADR adds one specific rule:

> **Known single-name binary event within 3 days of buy** (e.g., NVDA earnings for NDQ): defer that ETF's batch past the event. This is NOT macro-timing — it is tactically dodging a known asymmetric variance source. Re-run `/advise` after the event before deploying.

This rule will apply automatically to future NVDA earnings cycles (~ every 13 weeks) and any analogous single-name event whose ETF weight exceeds 5%.

## Consequences

### Positive
- 6-week deployment hits 60/30/10 ± 1pp precision
- Lowest fee per dollar in feasible set (0.21%)
- NVDA single-point risk completely sidestepped on the NDQ entry side
- Cash buffer maintained throughout deployment
- DCA discipline preserved (no market timing on macro)

### Negative
- 6 weeks vs 4 weeks = ~2 weeks additional cash drag. If market rallies +5% during W4-W6, opportunity cost ≈ A$100-150. Accepted as part of variance reduction.
- 6 separate Monday operations vs 4 (or 8 in original plan) = slight increase in discipline-fatigue risk. Mitigated by Sunday `/advise` automation + clear trigger table.

### When to revisit
- **NVDA misses by >10% on 2026-05-20**: NDQ likely -3% to -5% intraday → consider opportunistic A$1,000 NDQ entry Friday 5/22 in addition to scheduled Thursday batch.
- **Any `/advise` returns Avoid in any of 3 (NDQ, IOO, SPY) over next 6 weeks**: pause that ETF's next batch, hold cash, re-evaluate next Sunday.
- **Capital crosses $30k AUD**: full DCA revision needed; new vertical (satellite + alternative brokers like Stake) opens up.

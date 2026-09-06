# Evidence failures and fallback

The conversation core returns status and reasons even when a provider request completes successfully. The current shared snapshot, not legacy provider prose, determines usable coverage.

| Failure | Behavior |
|---|---|
| Missing key or forbidden entitlement | not_configured/unauthorized; state which coverage is absent |
| Rate limit/network error | Bounded retry and error; keep last-good data for audit, never label it refreshed |
| Empty/null/NaN/schema mismatch | Reject the observation; empty news does not mean no events |
| Latest completed session missing | unknown/fail for dependent advice, including after holidays and early closes |
| Different currency/unit/adjustment | Reject comparison; never splice adjusted and raw rows |
| Multiple wrappers of one upstream | One source; not independent confirmation |
| Aligned latest closes disagree | Quarantine the affected recommendation; do not choose the more convenient value |
| FRED publication/release unresolved | Macro claim remains unknown; observation date is not publication time |
| SEC accession lacks acceptance timestamp | Exclude that fact from cutoff-safe evidence; disclose partial coverage |
| SGE benchmark exists, retail ask absent | Benchmark research only; actual bar/coin entry requires merchant/product evidence |

US history uses Yahoo, with eligible Alpaca historical SIP optional. Nasdaq official latest closes corroborate US stocks/ETFs only when corporate-action metadata permits a matching basis; this does not verify the entire OHLCV history. Nasdaq index corroboration checks recent official closes. SGE Au99.99 and SHAU are separate price kinds, not interchangeable confirmations. China annual closures must be verified before enabling a new calendar year.

A technical indicator requires enough consecutive valid sessions: use the computed sample count and missing-indicator list. A 3-month response cannot support SMA200. Same-run retrieval does not make restated historical fundamentals point-in-time safe for backtests.

The shared gate can preserve research-only direction while suppressing exact sizing. All-direction data failure produces data_insufficient, not an inferred Sell. A missing merchant quote or complete portfolio also prevents a concrete order instruction. No automatic execution or paid fallback is enabled.

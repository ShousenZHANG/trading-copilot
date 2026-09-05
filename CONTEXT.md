# Context — domain glossary

The vocabulary this repo uses. When naming a function, an issue, or a report section, use the term as defined here rather than a synonym.

- **Ticker** — an instrument symbol in Yahoo Finance spelling, exchange suffix
  included (`NVDA`, `VAS.AX`, `0700.HK`, `GC=F`, `XAUUSD=X`). Always uppercase,
  always English, never translated or localised. A ticker is validated before it
  is ever used to build a path.
- **Run** — one execution of the pipeline for a single ticker on a single date.
  A run owns a directory of numbered markdown artifacts and is resumable: an
  artifact that already exists is not regenerated.
- **Decision** — the assembled, user-facing report a run produces. Deterministic
  layout; the Portfolio Manager supplies the content, an assembler script
  supplies the shape.
- **Rating tier** — the discrete verdict. Two scales coexist deliberately and
  must never be mixed: the deciders use five tiers (Buy / Overweight / Hold /
  Underweight / Sell), the trader uses three (Buy / Hold / Sell), the single-shot
  advisor uses its own (Strong Buy / Buy / Hold / Reduce / Avoid). Rating words
  stay in English so a parser can find them.
- **Conviction** — how strongly a rating is held, distinct from its direction.
  The backtester maps a rating tier onto a conviction number; position sizing and
  entry arming read conviction, not the tier word.
- **Trigger kind** — the *semantic* identity of a portfolio alert (a drawdown
  breach, a concentration breach), as opposed to the sentence that describes it.
  Dedup keys on the kind, because the sentence embeds a price and would change
  every day.
- **Pending vs resolved** — a memory-log entry is *pending* from the moment a
  decision is logged until the outcome window closes; the weekly review then
  *resolves* it by attaching the realised return and a reflection. The log is
  append-only; resolution replaces a tag in place, nothing is deleted.
- **Benchmark** — the index a return is measured against, chosen by the ticker's
  region rather than assumed. A non-US holding compared to a US index measures
  market and currency mismatch, not skill.
- **Alpha** — return minus the region-correct benchmark's return over the same
  window. Meaningless for commodities, FX and indices; quote the raw return there.
- **Look-through** — the true exposure to a name once fund holdings are unwrapped,
  so an ETF position and a direct position in the same company are counted once.
- **Risk gate** — the fixed pre-trade checklist a bullish rating must clear
  (concentration, correlation, liquidity, data freshness, stop-loss, drawdown).
  A failed check downgrades the rating and the reason is stated.

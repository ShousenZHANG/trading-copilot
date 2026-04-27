# Memory Log Format

`trading_memory.md` is the append-only decision log. Managed exclusively by `scripts/memory.py` and the `/analyze` + `/weekly-review` slash commands. **Never edit by hand.**

## Tag format

Each block is delimited by an HTML comment marker (`<!-- ENTRY_END -->`).

**Pending** (just decided, outcome unknown):

    [DATE | TICKER | RATING | pending]

    DECISION:
    <full Portfolio Manager output>

**Resolved** (T+5 days later, after `/weekly-review` runs):

    [DATE | TICKER | RATING | RAW% | ALPHA% | Nd]

    DECISION:
    <unchanged>

    REFLECTION:
    <2-4 sentence retrospective>

Where:
- `DATE` is `YYYY-MM-DD`
- `TICKER` is the instrument symbol with exchange suffix preserved
- `RATING` is one of `Buy / Overweight / Hold / Underweight / Sell`
- `RAW%` is the raw 5-day return, formatted `+5.2%` or `-1.4%`
- `ALPHA%` is the return minus SPY's return over the same window
- `Nd` is the holding period in trading days

## Lifecycle

1. Portfolio Manager finishes a run → `scripts/memory.py append` writes a pending block.
2. T+5 days later, `/weekly-review` resolves the block: pulls actual returns, runs the reflection prompt (Haiku), atomically replaces the pending tag and appends the REFLECTION section.
3. Future `/analyze` runs read the last 5 same-ticker resolved entries + last 3 cross-ticker reflections via `scripts/memory.py past-context` and inject them into the Portfolio Manager prompt.

## Why append-only markdown

Direct port of TradingAgents' `agents/utils/memory.py`. Markdown is human-readable, git-diffable, grep-able, and survives format/tooling changes. No vector DB needed for the small N of personal trading decisions.

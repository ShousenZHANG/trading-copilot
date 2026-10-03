# Research signals and opportunity discovery

Use `scan_investment_opportunities(instrument_ids, horizon)` for new candidates;
otherwise collect the requested instruments then `analyze_market_signals`.
Default candidate pool and benchmark come from local `[advisor]` settings.
Keep rejected/unconfirmed symbols visible. This is not an exhaustive market scan.

The code computes Wilder RSI/ATR, moving averages, completed-session returns,
distance to averages, prior-session volume ratios and prior 20-session close
extrema. Sample counts, formula version, price basis, evidence IDs and JSON
pointers make each observation reproducible. These are deterministic technical
conditions, not a calibrated win probability, expected return or validated alpha.

`conditions_met` means the coded trend/momentum/reference-level conditions match;
`watch` means mixed or untriggered conditions; `data_insufficient` withholds the
direction. Report `reference_trigger_price` and `reference_invalidation_price`
with `reference_basis` and their conditional meaning. Adjusted close levels
cannot become live raw limit orders, broker stop protection or fill prices.

Build the thesis from available financial reports, SEC filings, news and macro
evidence. Explain the strongest opposing observation and thesis invalidation.
Separate missing research from contradictory research. Price-based technical
invalidation is not equivalent to company-thesis invalidation. Research signals
support common stocks and ETF long-term/swing analysis. Exact funded orders
require a separately validated and actually adopted template, confirmed account
state and live quotes; read [template adoption](advisor-templates.md) and
[manual cards](manual-trade-plans.md). Report the actual missing prerequisite
when it blocks the user's request.

AI may rank the researched candidates and explain a conclusion. It cannot
replace missing quotes/cash, turn confidence into position size, create an
adopted strategy or claim profits. The next trading decision uses fresh evidence.

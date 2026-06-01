# Paper-trade via /scan to validate pipeline before satellite activation

**Status**: accepted, 2026-05-08

User's stated requirement is "real multi-agent analysis", but capital regime forces 100% ETF. Resolution: run `/scan` weekly against US mega-cap tech watchlist with **no real money**, accumulate decisions in `data/memory/trading_memory.md`, let `/weekly-review` resolve T+5d returns and compute alpha vs SPY. After 20+ resolved decisions, evaluate whether the multi-agent pipeline has alpha worth deploying real capital against.

Considered alternatives:
- **No paper-trade, just /advise on ETFs**: rejected — fails the "see real multi-agent reasoning" requirement
- **Paper-trade daily premarket scan**: rejected — too noisy at retail hold horizon (3-6 months)
- **Activate satellite immediately with $5k position**: rejected — see [ADR-0001](0001-100pct-etf-until-30k.md)

Consequences:
- Sunday `/scan` adds 30-60 min wall-clock weekly to weekly routine
- MCP API quota usage stays in free tier (6 tickers × weekly << 60/min finnhub limit)
- Graduation gate G3 (performance) measurable after ~8 weeks
- Terminal outcome if G3 fails: stay 100% ETF, accept that pipeline doesn't transfer to retail-scale execution (still useful as education and macro thesis check)

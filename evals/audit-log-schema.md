# Audit Log Schema

> Per-decision append-only JSON record. Required for institutional audit. Aligns with FINOS Agent Decision Audit framework + SOC2 AI trust criteria.

## Location

`data/audit/<YYYY-MM>/<TICKER>-<YYYY-MM-DD>-<run_id>.json`

`run_id` is `sha256(ticker + date + first_analyst_start_timestamp)[:8]`.

## Schema

```jsonc
{
  "decision_id": "NVDA-2026-04-27-a3b8f1c2",
  "schema_version": "1.0",
  "ts_utc_start": "2026-04-27T13:27:14Z",
  "ts_utc_end": "2026-04-27T13:34:51Z",
  "duration_seconds": 457,

  "instrument": {
    "ticker": "NVDA",
    "exchange_suffix": null,
    "asset_class": "equity",          // equity | etf | future | spot | bond | crypto
    "currency": "USD"
  },

  "trade_date": "2026-04-27",
  "run_brief": {
    "command": "/analyze",
    "args_raw": "NVDA",
    "max_debate_rounds": 1,
    "max_risk_rounds": 1
  },

  "agents": [
    {
      "role": "market-analyst",
      "model": "claude-sonnet-4-6",
      "prompt_hash": "sha256:abc123...",     // hash of system prompt at run time
      "ts_start": "2026-04-27T13:27:14Z",
      "ts_end":   "2026-04-27T13:28:32Z",
      "tokens_input": 12450,
      "tokens_output": 1820,
      "tokens_cached_read": 8120,
      "tokens_cached_write": 4330,
      "cost_usd": 0.024,
      "tool_calls": [
        {
          "tool": "mcp__yahoo-finance__get_stock_data",
          "args": {"ticker": "NVDA", "start": "2025-04-27", "end": "2026-04-27"},
          "result_hash": "sha256:def456...",
          "result_truncated_to_log": false,
          "ts": "2026-04-27T13:27:18Z"
        }
        // ... more tool calls
      ],
      "report_path": "data/runs/NVDA-2026-04-27/01-market.md",
      "report_hash": "sha256:..."
    },
    // ... 12 more agent entries (social, news, fundamentals, bull, bear, research-mgr, trader, aggressive, conservative, neutral, portfolio-mgr)
  ],

  "retrievals": [
    {
      "source": "Yahoo Finance",
      "url_or_endpoint": "yfinance::NVDA",
      "data_timestamp": "2026-04-27T13:00:00Z",
      "freshness_seconds": 1634,
      "agent_consumer": "market-analyst",
      "result_hash": "sha256:def456..."
    }
    // ... one per data fetch
  ],

  "risk_checks": [
    {"name": "single_name_concentration", "threshold": 0.05, "actual": 0.03, "passed": true},
    {"name": "sector_concentration",      "threshold": 0.25, "actual": 0.18, "passed": true},
    {"name": "correlation_to_book",       "threshold": 0.70, "actual": 0.41, "passed": true},
    {"name": "position_vs_adv",           "threshold": 0.01, "actual": 0.0008, "passed": true},
    {"name": "data_freshness_max_hours",  "threshold": 24,   "actual": 0.5,  "passed": true},
    {"name": "stop_loss_set",             "threshold": "required", "actual": "set", "passed": true},
    {"name": "portfolio_drawdown",        "threshold": -0.15, "actual": -0.02, "passed": true}
  ],

  "research_plan": {
    "recommendation": "Buy",
    "rationale_excerpt": "...first 200 chars...",
    "rationale_hash": "sha256:..."
  },
  "trader_proposal": {
    "action": "Buy",
    "entry_price": 850.50,
    "stop_loss": 805.00,
    "position_sizing": "5% of portfolio"
  },
  "final_decision": {
    "rating": "Buy",
    "executive_summary_excerpt": "...first 200 chars...",
    "investment_thesis_hash": "sha256:...",
    "price_target": 1100.0,
    "time_horizon": "3-6 months"
  },

  "memory": {
    "past_context_used": true,
    "past_context_hash": "sha256:...",
    "n_same_ticker_entries": 3,
    "n_cross_ticker_lessons": 2
  },

  "totals": {
    "tokens_input":  92340,
    "tokens_output": 18450,
    "cost_usd": 0.412
  },

  "prev_audit_hash": "sha256:...",   // hash of previous audit record — chains the log
  "this_audit_hash": "sha256:..."    // hash of this record (excluding this field)
}
```

## Hash chaining

Every record stores `prev_audit_hash` (the `this_audit_hash` of the previous record in the same `<YYYY-MM>/` directory). Tampering with a past record breaks the chain.

To verify integrity:

```bash
python evals/audit/verify_chain.py --month=2026-04
```

## Retention

7+ years. Compress monthly directories to `.tar.zst` after 90 days. Never delete.

## Privacy

The audit log can contain personal portfolio info via `risk_checks.actual` values. **Do not commit `data/audit/` to a public repo.** It is gitignored by default.

## Status

This is the **schema spec** for Phase 7. The hook to write these records lives in the slash-command orchestration (Phase 4+) and is currently a TODO — pipeline writes per-step markdown to `data/runs/` but does not yet emit the consolidated JSON audit record. Implement when moving to a regulated/multi-user setup.

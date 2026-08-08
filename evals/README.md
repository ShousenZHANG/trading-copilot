# Evaluation Harness

Three layers of evaluation, modeled on institutional standards:

1. **Knowledge benchmarks** — does the analyst output contain hallucinated numbers?
2. **Trading benchmarks** — does the Portfolio Manager beat buy-and-hold on out-of-sample data?
3. **Component evals** — A/B test individual prompts against a held-out reference set.

## Goal

Reduce hallucination rate to < 10% on numeric extractions. Beat SPY on a rolling 3-month bear-market window. Catch prompt regressions before they ship.

## Layout

```
evals/
├── README.md                             ← this file
├── financebench/
│   ├── sample-questions.jsonl            ← 20 Q&A from FinanceBench subset
│   └── runner.py                         ← runs analysts vs reference answers
├── scorer.py                             ← deterministic answer scorer (+ score_batch)
├── stockbench/
│   ├── backtest_engine.py                ← the backtest loop + metrics (stdlib, self-tested)
│   ├── runner.py                         ← replay / signals driver over the engine
│   └── windows.json                      ← bull / bear / choppy regime windows
├── components/
│   └── prompt-ab.py                      ← A/B test two versions of an agent prompt
├── results/                              ← gitignored — eval output
└── audit-log-schema.md                   ← per-decision JSON record format
```

## Status

`scorer.py` and `stockbench/backtest_engine.py` are **implemented and self-tested** —
run `--self-test` on either. The FinanceBench reference set and the live headless
dispatch path are still scaffold: curate the datasets as you accumulate decisions.

## Backtest (replay mode) — the zero-LLM-cost path

The cheapest honest backtest replays decisions **you already paid for**: every
`data/runs/<TICKER>-<DATE>/08-portfolio-decision.md` is parsed by
`scripts/parse_rating.py`, mapped to a conviction, and run through the engine.
No new tokens are spent.

```bash
# Self-tests first (deterministic, no data needed)
python evals/stockbench/backtest_engine.py --self-test
python evals/scorer.py --self-test

# Replay stored decisions against an offline price map
python evals/stockbench/runner.py --replay --prices evals/results/prices.json \
    --holding-days 5 --run-name replay-2026h1

# Or from pre-collected signals (JSONL: {"ticker","date","rating"|"conviction"})
python evals/stockbench/runner.py --signals evals/results/signals.jsonl --yfinance
```

Output lands in `evals/results/<run-name>/` as two separate artifacts —
`predictions.jsonl` (what the pipeline said) and `metrics.json` (how it scored).
Keeping prediction and score apart is deliberate: you can re-score old
predictions with new metrics without re-running anything.

### Conviction mapping (5-tier → position)

| Rating | Conviction | Position |
|--------|-----------|----------|
| Buy | +1.0 | long |
| Overweight | +0.5 | long (at default threshold 0.5) |
| Hold | 0.0 | flat |
| Underweight | −0.5 | short |
| Sell | −1.0 | short |

### Metrics

Computed in `backtest_engine.compute_metrics` (formulas in its docstring):
annualized return `mean(r)·N`, volatility `std(r)·√N`, information ratio
`mean/std·√N`, max drawdown `min(cumsum − running_max)`, hit rate, trade count.
For a fixed-hold strategy `N = 252 / holding_days`. `n < 2` or `std == 0`
returns `None` rather than a fake number.

### Two correctness details that are easy to get wrong

- **Edge-triggered arming** — after a position opens, that ticker is disarmed
  until its conviction falls back below the threshold. Without this, a
  persistently bullish signal stream opens overlapping duplicate positions and
  inflates returns.
- **Tail-data guard** — a signal with fewer than `holding_days` bars remaining
  is recorded in `result.skipped`, never silently dropped. Request roughly
  `holding_days · 2 + 10` extra days of bars.

### Reproducibility caveat

Backtest numbers are **not** guaranteed to reproduce across model versions,
prompt edits, or MCP data revisions. The engine is deterministic; the decisions
feeding it are not. Lower temperature reduces variance but does not remove it.
Treat a replay as a measurement of *this* configuration, not a universal claim.

## Running

```bash
# Knowledge eval (one-off)
python evals/financebench/runner.py --sample-size=20

# Live dispatch is the expensive path — it refuses to run without an explicit ack
python evals/stockbench/runner.py --window=2024-06-01:2024-09-30 \
    --tickers=NVDA,AAPL,MSFT --yes-i-accept-cost

# Component A/B (compare 2 versions of a prompt)
python evals/components/prompt-ab.py --agent=market-analyst \
    --baseline=agents/analysts/market-analyst.md \
    --candidate=agents/analysts/market-analyst.v2.md \
    --test-set=evals/financebench/sample-questions.jsonl
```

## Key sources

- [FinanceBench (Patronus AI)](https://github.com/patronus-ai/financebench) — 10,231 Q&A on real 10-K/10-Q
- [StockBench](https://stockbench.github.io/) — rolling-window LLM trading benchmark
- [TradingAgents tests/](../reference/TradingAgents/tests/) — upstream test suite for reference

## Anti-patterns enforced

The runners check for and FLAG (not pass silently):

- **Hallucinated tickers** — answers that reference symbols not in the test instrument
- **Look-ahead bias** — analysts referencing data with `date > trade_date`
- **Survivorship bias** — backtest universes built from today's constituents
- **Stale data** — input timestamps > 24h before `trade_date` for daily horizon

## Audit log

Every decision the pipeline makes (in production, not just eval) writes a JSON record per `audit-log-schema.md`. Append-only, hash-chained, retained ≥ 7 years. Required for any future regulated use.

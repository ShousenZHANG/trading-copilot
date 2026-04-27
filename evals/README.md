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
├── stockbench/
│   ├── runner.py                         ← rolling-window historical backtest
│   ├── windows.json                      ← bull / bear / choppy regime windows
│   └── benchmarks.py                     ← Sharpe / Sortino / max-DD / Calmar
├── components/
│   └── prompt-ab.py                      ← A/B test two versions of an agent prompt
├── results/                              ← gitignored — eval output
└── audit-log-schema.md                   ← per-decision JSON record format
```

## Phase 7 status

This phase ships the **scaffold + audit-log schema**. The actual eval datasets (FinanceBench subset, StockBench windows) are populated as you accumulate decisions and have time to curate the reference set.

## Running

```bash
# Knowledge eval (one-off)
python evals/financebench/runner.py --sample-size=20

# Trading eval (slow — runs the full pipeline on N historical dates)
python evals/stockbench/runner.py --window=2024-06-01:2024-09-30 --tickers=NVDA,AAPL,MSFT

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

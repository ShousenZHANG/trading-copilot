# Evaluation tools and their evidence boundaries

This directory contains offline scoring tools and a legacy signal-replay
adapter. It does not establish the current copilot's investment accuracy or
future returns. Tests validate implementation behavior, not strategy alpha.

## Current implementations

| Tool | What it measures | Limitation |
|------|------------------|------------|
| `scorer.py` | One numeric amount or an exact text assertion against a reference | Requires matching number/percentage and currency labels; complex answers require manual grading |
| `financebench/runner.py` | Previously collected `model_answer` fields, answer coverage and reference agreement | No model dispatch; the shipped three real questions have no answers; one prefilled demo is excluded |
| `stockbench/backtest_engine.py` | Fixed-horizon returns from supplied rating/conviction signals | Trade-sample statistics, not portfolio NAV; no benchmark leg or brokerage execution |
| `stockbench/runner.py --signals` | Replays a supplied JSONL signal file against an explicit price source | Caller must supply genuinely point-in-time signals and adequate price coverage |
| `stockbench/runner.py --replay` | Parses legacy `data/runs/*/08-portfolio-decision.md` files | The current single-advisor runtime does not generate this old pipeline format |
| `prices/prices.json` | A fixed public price map for reproducible replay | Historical adjusted closes can be revised; this file alone proves neither signal validity nor complete coverage |

`stockbench/windows.json` contains illustrative regime descriptions, not
verified held-out model evaluations. Live model dispatch exits without running;
prompt A/B evaluation and automatic contamination checks are not implemented.
`audit-log-schema.md` is a proposed legacy schema. The current runtime's local
SQLite journal is separate from this evaluation adapter.

## Offline checks

```bash
python evals/scorer.py --self-test
python evals/financebench/runner.py --self-test
python evals/stockbench/backtest_engine.py --self-test
python evals/stockbench/runner.py --self-test
```

To score collected reference answers, use a separate JSONL file rather than
claiming the example answer came from a model:

```bash
python evals/financebench/runner.py --questions questions-with-answers.jsonl --out score.json
```

The runner reports answered/real-question coverage, excludes rows marked
`demo: true` and legacy `fb-demo` IDs, and never passes incomplete coverage.
No real answers means accuracy is **not measured** (exit 2), rather than 100%.
Exit 0 requires complete coverage, at least 80% agreement and no numeric
hallucination verdict. These thresholds are tooling defaults, not investment
validation criteria. Every question should record its filing, reference source,
model/configuration and answer collection date outside the scoring script.

For an existing signal file, with an explicit fixed price map:

```bash
python evals/stockbench/runner.py --signals signals.jsonl --prices evals/prices/prices.json --out-dir evals/results --run-name explicit-signals
```

Signals use `ticker`, `date`, and either `rating` or `conviction`. Insufficient
samples withhold inferential metrics. Replay predicts entry after the signal
date; it does not reconstruct an actual account's fills, cash, FX or fees.

## Current strategy backtests

The current deterministic ETF and gold rule library is in
`scripts/copilot/backtest/`, exposed through `scripts/backtest_cli.py`.
It is distinct from the legacy LLM-rating replay above. Its reports and adoption
records disclose same-bar-close execution and the limitations of using adjusted
prices for synthetic share counts/per-share fees. Cash and all modeled trade
costs share one funded sizing calculation.

An admission verdict checks the declared history/cost/reporting requirements.
The post-2019 segment does not prove that today's parameter choices were never
tuned on that segment. A convincing effectiveness evaluation still needs frozen
strategy/model versions, genuinely held-out signals, point-in-time evidence,
execution realism and a matching benchmark. No committed real model benchmark
result in this directory supplies that evidence today.

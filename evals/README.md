# Evaluation Harness

Three intended layers of evaluation, modeled on institutional standards:

1. **Knowledge benchmarks** — does the analyst output contain hallucinated numbers?
2. **Trading benchmarks** — does the Portfolio Manager's rating make money out of sample?
3. **Component evals** — A/B test individual prompts against a held-out reference set. **Planned (not implemented).**

## Goal

Reduce hallucination rate to < 10% on numeric extractions. Catch prompt
regressions before they ship.

> **"Beat SPY" is a goal, not something this harness currently measures.**
> `backtest_engine.compute_metrics` computes **absolute** per-trade return only.
> There is no benchmark leg, so nothing here reports alpha or excess return over
> SPY (or any regional index). `scripts/benchmarks.py` picks the right benchmark
> for `/weekly-review`'s T+5d alpha, but the backtest engine does not consume it
> yet. Until it does, read the backtest as "what these trades did", never as
> "this beat the market".

## Layout

```
evals/
├── README.md                             ← this file
├── financebench/
│   ├── sample-questions.jsonl            ← 4 rows: 3 curated Q&A + 1 demo row
│   └── runner.py                         ← runs analysts vs reference answers
├── scorer.py                             ← deterministic answer scorer (+ score_batch)
├── stockbench/
│   ├── backtest_engine.py                ← the backtest loop + metrics (stdlib, self-tested)
│   ├── runner.py                         ← replay / signals driver over the engine
│   └── windows.json                      ← bull / bear / choppy regime windows
├── prices/                               ← committed public closing prices (see its README)
│   ├── README.md
│   └── prices.json                       ← built by scripts/prices.py
├── results/                              ← gitignored — eval output
└── audit-log-schema.md                   ← per-decision JSON record format (spec only)
```

## Status — what actually runs

| Piece | Status |
|-------|--------|
| `scorer.py` | **Implemented**, `--self-test` |
| `stockbench/backtest_engine.py` | **Implemented**, `--self-test` |
| `stockbench/runner.py` (`--replay`, `--signals`) | **Implemented**, `--self-test` |
| `scripts/prices.py` (price-map builder) | **Implemented**, `--self-test` |
| `financebench/runner.py` | Scaffold — 3 curated questions, no dispatch |
| Live headless dispatch (`--yes-i-accept-cost`) | **Planned (not implemented)** — exits 3 |
| `components/prompt-ab.py` | **Planned (not implemented)** — no such file |
| Hash-chained audit log | **Planned (not implemented)** — `audit-log-schema.md` is a spec; nothing writes it |
| Automated anti-pattern checks | **Planned (not implemented)** — see below |

## Backtest (replay mode) — the zero-LLM-cost path

The cheapest honest backtest replays decisions **you already paid for**: every
`data/runs/<TICKER>-<DATE>/08-portfolio-decision.md` is parsed by
`scripts/parse_rating.py`, mapped to a conviction, and run through the engine.
No new tokens are spent.

```bash
# 0. Self-tests first (deterministic, offline, no data needed)
python scripts/prices.py --self-test
python evals/stockbench/backtest_engine.py --self-test
python evals/stockbench/runner.py --self-test
python evals/scorer.py --self-test

# 1. Build the price map (keyless Yahoo v8 chart endpoint, stdlib urllib)
python scripts/prices.py --tickers NVDA,GC=F,SPY \
    --start 2026-04-01 --end 2026-09-30 --out evals/prices/prices.json

# 2. Replay stored decisions against it
python evals/stockbench/runner.py --replay --prices evals/prices/prices.json \
    --snap-forward --holding-days 5 --run-name replay-2026

# Or from pre-collected signals (JSONL: {"ticker","date","rating"|"conviction"})
python evals/stockbench/runner.py --signals evals/signals.jsonl --yfinance
```

Output lands in `<--out-dir>/<run-name>/` (default `evals/results/<run-name>/`)
as two separate artifacts — `predictions.jsonl` (what the pipeline said) and
`metrics.json` (how it scored). Keeping prediction and score apart is
deliberate: you can re-score old predictions with new metrics without
re-running anything.

### Runner flags that change the answer

| Flag | Default | Why it matters |
|------|---------|----------------|
| `--direction long-only \| both` | `long-only` | `long-only` ignores Underweight/Sell shorts. The maintainer holds ETFs in a cash account and would never open those shorts, so scoring them measures a strategy nobody runs. Recorded in `metrics.json → config.direction`. |
| `--min-trades N` | `20` | Below `N` trades the inferential metrics are withheld — see below. |
| `--runs-dir PATH` | `data/runs` | Point the replay at a fixture tree instead of personal state. |
| `--out-dir PATH` | `evals/results` | Artifact root. |
| `--allow-ticker A,B` | — | Force a symbol past the session-label list (e.g. `GOLD` when you really mean Barrick). |
| `--snap-forward` | off | Enter on the next bar when the decision date is a weekend/holiday. Most `/gold` runs land on non-trading days without it. |

### Conviction mapping (5-tier → position)

| Rating | Conviction | Position (`--direction both`) | Position (`long-only`, default) |
|--------|-----------|------------------|------------------|
| Buy | +1.0 | long | long |
| Overweight | +0.5 | long (at default threshold 0.5) | long |
| Hold | 0.0 | flat | flat |
| Underweight | −0.5 | short | **not traded** |
| Sell | −1.0 | short | **not traded** |

### Metrics

Computed in `backtest_engine.compute_metrics` (formulas in its docstring):
annualized return `mean(r)·N`, volatility `std(r)·√N`, information ratio
`mean/std·√N`, max drawdown `min(cumsum − running_max)`, hit rate, trade count.
For a fixed-hold strategy `N = 252 / holding_days`.

Two independent guards, both returning `None` rather than a fake number:

- **Numeric** — `n < 2` leaves the sample std undefined; `std == 0` leaves the
  ratio undefined.
- **Small sample (`--min-trades`, default 20)** — below the floor the four
  *inferential* metrics (`annualized_return`, `information_ratio`,
  `volatility`, `hit_rate`) are withheld and `metrics.insufficient_sample`
  carries `{trade_count, min_trades}`; `format_metrics` prints a
  `SAMPLE TOO SMALL` banner. Annualising two 5-day holds produces a
  three-digit percentage that looks like a result and is not one. The three
  *descriptive* metrics (`avg_return`, `cumulative_return`, `max_drawdown`)
  are still published — they are facts about the trades that happened, not
  estimates of a population.

### Three correctness details that are easy to get wrong

- **Edge-triggered arming** — after a position opens, that ticker is disarmed
  until its conviction falls back below the threshold. Without this, a
  persistently bullish signal stream opens overlapping duplicate positions and
  inflates returns.
- **Tail-data guard** — a signal with fewer than `holding_days` bars remaining
  is recorded in `result.skipped`, never silently dropped. Request roughly
  `holding_days · 2 + 10` extra days of bars.
- **Ticker vs session label** — a run directory is `<TICKER>-<YYYY-MM-DD>`, and
  tickers may legally contain `-` (`BRK-B`), so a greedy parse turned
  `CPI-NIGHT-2026-06-10` and `ETF-PORTFOLIO-2026-06-02` into "tickers" and
  backtested them as if they were symbols. `runner.parse_run_dir_name` now
  requires an actual exchange-symbol shape (`NVDA`, `NDQ.AX`, `GC=F`,
  `0700.HK`, `XAUUSD=X`, `^AXJO`) plus a visible `SESSION_LABELS` list for the
  few single words a regex cannot separate from a real ticker. Every rejection
  is printed as a `note:` line — nothing is dropped silently.

### Reproducibility caveat

Backtest numbers are **not** guaranteed to reproduce across model versions,
prompt edits, or MCP data revisions. The engine is deterministic; the decisions
feeding it are not. Lower temperature reduces variance but does not remove it.
Treat a replay as a measurement of *this* configuration, not a universal claim.

Prices are the other moving part: adjusted closes change after a split or
dividend, which is why `evals/prices/prices.json` is committed rather than
refetched on every run.

## Running the other layers

```bash
# Knowledge eval (scaffold — 3 curated questions + 1 demo row)
python evals/financebench/runner.py --sample-size=4

# Live dispatch is the expensive path. It prints a cost estimate, refuses
# without an explicit ack, and then exits 3 — the adapter is not written.
python evals/stockbench/runner.py --window=2026-06-01:2026-09-30 \
    --tickers=NVDA,AAPL,MSFT --yes-i-accept-cost
```

## Key sources

- [FinanceBench (Patronus AI)](https://github.com/patronus-ai/financebench) — 10,231 Q&A on real 10-K/10-Q
- [StockBench](https://stockbench.github.io/) — rolling-window LLM trading benchmark
- [TradingAgents tests/](../reference/TradingAgents/tests/) — upstream test suite for reference

## Anti-patterns — Planned (not implemented)

No code checks these today. They are recorded here as the intended checks so
the gap stays visible:

- **Hallucinated tickers** — answers referencing symbols not in the test instrument
- **Look-ahead bias** — analysts referencing data with `date > trade_date`
- **Survivorship bias** — backtest universes built from today's constituents
- **Stale data** — input timestamps > 24h before `trade_date` for a daily horizon

Partial coverage that *does* exist: `scripts/validate_outputs.py` counts
`[UNSOURCED]` tags and warns above a soft cap, and `market-analyst` enforces the
7-day bar-staleness rule in its own prompt.

## Audit log — Planned (not implemented)

`audit-log-schema.md` specifies a per-decision JSON record (append-only,
hash-chained, retained ≥ 7 years) that would be required for any regulated use.
**Nothing writes it today.** The pipeline's only persistent record is the
append-only markdown log at `data/memory/trading_memory.md`, managed by
`scripts/memory.py`, which is neither JSON nor hash-chained.

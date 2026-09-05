# evals/prices/ — committed price maps

These JSON files are **public daily closing prices** fetched from the keyless
Yahoo v8 chart endpoint by [`scripts/prices.py`](../../scripts/prices.py).

They are **safe to commit**, unlike everything under `data/`:

| | `data/` | `evals/prices/` |
|---|---|---|
| Content | the maintainer's real positions, decisions and memory log | public closing prices |
| Personal | yes — gitignored | no |
| Purpose | trading state | make a backtest reproducible |

Committing the price map is what makes a replay backtest **reproducible**: the
engine is deterministic, so a fixed price map plus a fixed set of decisions
always produces byte-identical metrics. Without the file checked in, a rerun
silently reprices against whatever Yahoo says today (adjusted closes move on
splits and dividends) and the numbers drift for no visible reason.

## Format

Exactly what `evals/stockbench/backtest_engine.JsonPriceSource` consumes:

```json
{
  "NVDA": [{"date": "2026-04-01", "close": 123.45}, ...],
  "GC=F": [...],
  "SPY":  [...]
}
```

## Refresh

```bash
# replace
python scripts/prices.py --tickers NVDA,GC=F,SPY \
    --start 2026-04-01 --end 2026-09-30 --out evals/prices/prices.json

# add a ticker to an existing map (incoming wins on a same-date conflict)
python scripts/prices.py --tickers NDQ.AX --start 2026-04-01 --end 2026-09-30 --merge
```

Then rerun the replay:

```bash
python evals/stockbench/runner.py --replay --prices evals/prices/prices.json \
    --snap-forward --run-name replay-2026
```

Closes are **split/dividend adjusted** (`adjclose` when Yahoo supplies it), so
a refetch after a corporate action legitimately changes historical values.

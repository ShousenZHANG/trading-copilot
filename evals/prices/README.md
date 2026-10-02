# Fixed public prices for legacy signal replay

`prices.json` is a committed public daily-price map read by
`stockbench/backtest_engine.JsonPriceSource`. The original price-map builder
has been removed; this snapshot remains for reproducing existing replay inputs.

```json
{
  "SPY": [{"date": "2026-04-01", "close": 123.45}]
}
```

A fixed map and fixed signals produce deterministic metrics. The map does not
establish that signals were available at the time, that every needed session is
covered, or that the replay measures an actual portfolio. Adjusted prices may
also differ from historical as-traded prices.

For new evaluations, supply an explicit price map whose source, retrieval date,
price basis and coverage are recorded with the run, or explicitly select the
runner's optional `--yfinance` adapter. Do not silently refresh this committed
snapshot and compare the changed metrics as if only the model had changed.

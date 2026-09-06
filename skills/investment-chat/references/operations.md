# Recording actual operations

Call `record_investment_operation(operation, idempotency_key)` or pass the JSON
object in a UTF-8 file/stdin to `copilot_cli.py record --input ... --idempotency-key ...`.
Never interpolate the user's text into shell code.

The following is an input-shape illustration, not a real transaction:

```json
{
  "statement": "我已经买了2股QQQ，2026-09-04成交，每股700美元，手续费未知。",
  "execution_status": "executed",
  "instrument_id": "QQQ",
  "side": "buy",
  "quantity": "2",
  "unit": "share",
  "price": "700",
  "price_basis": "unit",
  "currency": "USD",
  "occurred_at": "2026-09-04",
  "fees": null,
  "source_message_id": "stable-message-identifier"
}
```

Use decimal strings. Omit unknown fields or set null; never default fees to zero.
Preserve original currency. GOLD.CNY uses grams or item count with weight_grams,
purity (a fraction from 0 to 1) and explicit unit/total price basis. For item
counts, weight_grams is gross grams PER ITEM. Supply merchant/product information
when given. A per-coin total is not a CNY/gram quote. Keep gross and fine weight
distinct. Foreign exchange belongs to valuation, not original transaction cost.

Generate one stable request ID per user statement and preserve it across retries.
Prefer a real external_trade_id plus account_id if the user provides one.
Two identical prices/quantities may be two real fills; only identical event
identity can be retried as the same operation. Duplicate candidates remain
pending until the user links or distinguishes them.

Resolve a pending duplicate with event_type="complete": duplicate_of=<existing
operation_id> for a confirmed repeated statement, or distinct_confirmation=true
for an explicitly confirmed separate fill. Preserve that new user statement.

To complete/correct: `event_type="complete"` or `"correct"`, `operation_id`,
`expected_version` from the latest receipt, the original statement plus the
user's new statement, and the supplied fields. To reverse: `event_type="reverse"`
with the same identity/version requirements. Each new change has its own stable
idempotency_key; old events remain in the journal.

Use context/receipts to report executed, pending, intent, reversed or duplicate
states accurately. Missing historical holdings and unpriced fees remain visible.

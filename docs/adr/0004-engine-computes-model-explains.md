# ADR-0004: The policy engine computes every order figure; the language model only explains

Status: accepted, 2026-09-19. Clauses 3 and 6 amended by [ADR-0005](0005-verified-data-constraints.md).

## Context

Until 0.4.0 the plugin ran a fourteen-agent research pipeline whose prose was
converted into a decision by a deterministic policy, and the concise advisor
refused to size positions because the journal never declares a complete
portfolio. Both paths left the user without a number, and the multi-agent path
produced text the policy could not verify.

The redesign replaces discretionary analysis with rules the user adopts:
strategies with public code and reproducible long-horizon backtests are
proposed together with their statistics, the user accepts one, and from then on
a daily scan evaluates the adopted rule against a validated snapshot.

## Decision

1. `scripts/copilot/policy.py` is the only component that produces `action`,
   `quantity`, `limit_price` and `rule_id`. Every figure traces to a stored
   evidence record, an adopted rule in `config/user.toml`, or arithmetic over
   them.
2. The language model receives the engine's decision and renders it in the
   user's language. It has no interface through which it can alter quantity,
   price, direction or rule identity. Prompts, skills and MCP tool schemas must
   not expose such a parameter.
3. One bounded exception: a daily brake enum `{none, reduce_50, skip}` that the
   model may emit from the day's macro-calendar surprise and news headlines. The
   brake can only reduce or cancel an engine-generated buy; it cannot increase a
   quantity, change a price, or turn a skip into a buy. Every brake decision is
   journaled with its reason.
4. A rule enters the library only through the admission gate: a backtest of at
   least fifteen years covering 2008, 2020 and 2022; annualised return, maximum
   drawdown, drawdown duration, turnover and Sharpe reported; transaction costs
   deducted; at most three parameters, with sensitivity reported; an
   out-of-sample segment; code and data sources public and reproducible locally.
5. The ETF sleeve holds registered equity ETFs and cash. The gold sleeve uses
   Shanghai Gold Exchange Au99.99 as its signal, with the user's actual bank
   accumulation purchases recorded by hand. The two sleeves are independent and
   are never summed.
6. Broker access is read-only: the IBKR Flex Web Service for positions and cash,
   and optionally an IB Gateway session with the Read-Only API flag for a third
   price source and news. No component sends an order.

## Consequences

- The multi-agent pipeline, its agents, commands, validators and report
  assembler are removed. `docs/methodology.md` becomes a historical record.
- Position sizing becomes computable once `config/user.toml` and the account
  sync supply the denominator. Until then the engine reports `research_only`.
- A user who swaps the language model does not change trading behaviour,
  because the model never held it.
- Every emitted instruction carries a `rule_id` and the backtest statistics
  under which that rule was adopted, so a user can audit why an order exists.

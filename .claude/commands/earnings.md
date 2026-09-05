---
description: "[inactive · 未激活] Quarterly earnings update for a single ticker — NOT IMPLEMENTED. The agent, the validator kind, and the report slot it needs do not exist yet; invoking it does nothing. Kept as the activation spec. Use /advise or /analyze after a company reports."
argument-hint: <TICKER> [--quarter=Q1-FY27]
---

# /earnings — Quarterly Earnings Update (not implemented)

> ⛔ **This command is a specification, not a feature.** It has no
> `earnings-reviewer` agent to dispatch to, `validate_outputs.py` has no
> `earnings` kind, and the assembler has no slot for an earnings note. Running it
> would fail on its first scripted step. Nothing below is executed.
>
> **What to run instead, today**: `/advise <TICKER>` for a fast post-print read,
> or `/analyze <TICKER>` when the quarter genuinely changes the thesis.

## What it would do (design intent)

A focused earnings update — narrower and cheaper than `/analyze` — run when a
covered name has just reported: pull the quarter's actual EPS/revenue against
consensus, the call commentary, and the post-print analyst revisions; compute
beat/miss magnitude, segment surprises, guidance delta and margin direction; and
write a short earnings note stating whether the prior thesis is intact,
weakening, or due for a re-rate.

## When it would be worth using

- Company reported within the last 7 days, and you hold or are considering the name
- The result was a meaningful beat or miss versus consensus
- Updated forward guidance shifts the thesis

## Difference vs `/analyze`

| `/analyze` | `/earnings` |
|------------|-------------|
| 12-agent full debate (Bull/Bear + 3-way risk + PM) | 1 specialist agent (earnings-reviewer), no debate |
| 30-60 min, $1-3 | 8-15 min, $0.30-0.80 |
| Generates a new investment thesis | Updates an existing thesis with new quarterly data |
| Produces a full decision report | Produces an earnings note with delta-from-prior |

## Args (design intent)

- **`$ARGUMENTS`**: first token is ticker (preserve the exchange suffix)
- `--quarter=Q1-FY27` (optional): override which quarter to analyze. If omitted,
  the agent infers the most-recent reported quarter.

## Memory log: deliberately out of scope

An earnings note is **evidence, not a decision**, so it must never be appended to
the memory log: the note's own prose contains the words Buy/Hold/Sell, and the
rating parser falls back to "first rating word anywhere in the file", so
appending one writes a rating no agent ever issued — which the weekly review then
resolves and learns from. See
[ADR-0002](../../docs/adr/0002-local-scheduling-and-evidence-only-stubs.md).

## TODO before activation

- [ ] Build `.claude/agents/specialists/earnings-reviewer.md` prompt (modeled on financial-services `earnings-reviewer.md`), granting the MCP servers it actually calls in its `tools:` allowlist
- [ ] Add `validate_earnings_update(text)` and a `kind=earnings` branch to `scripts/validate_outputs.py`
- [ ] Add `09-earnings-update.md` to the assembler if the note should surface in the main report
- [ ] Decide how a supersede is recorded: an earnings note must either carry an explicit `**Rating**` written to the 5-tier schema (and be appended with `memory.py append --rating`), or stay out of the log entirely. Never let the parser guess.
- [ ] Drop the `[inactive · 未激活]` prefix from `description:` and restore an Execution section only once all of the above exist

## Output language

User-facing output follows [.claude/config/output-language.md](../config/output-language.md)
(currently Chinese 中文). Ticker symbols, indicator names and numbers stay in English.

## Source

Direct port of [`anthropic/financial-services/plugins/vertical-plugins/equity-research/commands/earnings.md`](https://github.com/anthropics/financial-services/blob/main/plugins/vertical-plugins/equity-research/commands/earnings.md). Adapted: removed DOCX output (markdown only), replaced Bloomberg/FactSet with Finnhub free tier.

> ⚠️ Educational use only. Not investment advice. See [DISCLAIMER.md](../../DISCLAIMER.md).

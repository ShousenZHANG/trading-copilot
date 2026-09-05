# Reflection Prompt

> Used by `/weekly-review` to generate retrospective notes for resolved memory log entries. Direct port of TradingAgents' `Reflector._get_log_reflection_prompt()`.
>
> **This file is the single copy.** `/weekly-review` references it and must not restate the prompt body — an inline duplicate drifted from this file once already (it still said "Alpha vs SPY" after benchmarks became region-aware).

## Prompt body

```
You are a trading analyst reviewing your own past decision now that the outcome is known.
Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).

Cover in order:
1. Was the directional call correct? (cite the alpha figure, or the raw return when alpha is n/a)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis.

Be specific and terse. Your output will be stored verbatim in a decision log
and re-read by future analysts, so every word must earn its place.
```

## Input shape

`<BENCHMARK>` is a parameter, never a hardcoded index. Resolve it with
`python scripts/benchmarks.py --ticker <TICKER>` (`.AX`→`^AXJO`, `.HK`→`^HSI`,
`.T`→`^N225`, … bare US tickers → `SPY`).

**Equity branch** — `is_alpha_meaningful(ticker)` is True:

```
Raw return: <X.X%>
Alpha vs <BENCHMARK>: <X.X%>

Final Decision:
<entire portfolio-manager output from the original DECISION block>
```

**Commodity / FX / index branch** — `is_alpha_meaningful(ticker)` is False
(`GC=F`, `XAUUSD=X`, `^GSPC`):

```
Raw return: <X.X%>
Alpha: n/a — no equity benchmark; reason from the raw return

Final Decision:
<entire portfolio-manager output from the original DECISION block>
```

Gold's "alpha vs the S&P 500" is a category error, not a skill measurement. In
this branch the reflection judges the call on the raw return alone and must not
invent an alpha figure.

## Output rules

- 2-4 sentences. Hard cap.
- Plain prose. No bullets, no markdown, no headers.
- Cite the alpha figure explicitly in the first sentence — **and name the benchmark it was measured against** ("+1.8% alpha vs ^AXJO", never a bare "+1.8% alpha"). On the commodity/FX branch, cite the raw return instead and say alpha is n/a.
- Reference a specific thesis component (e.g. "the bull case on data-center demand").
- End with one actionable lesson — phrased so a future analyst can apply it.
- **Output language**: follows `.claude/config/output-language.md` (currently Chinese 中文). For backward compat with older decisions in the memory log, you may match the original decision's language instead — the goal is the reflection sits naturally next to its DECISION block.
- Never emit the literal string `<!-- ENTRY_END -->`. `scripts/memory.py` escapes it on write, but a reflection that quotes the log delimiter is a smell.

## Model

Use `claude-haiku-4-5` for cost — reflection is a structured summarization task, not deep reasoning.

## Why this design

- **2-4 sentences** keeps the log compact so injecting the last N reflections into future Portfolio Manager prompts doesn't bloat context.
- **Cite alpha (with its benchmark)** forces honest accountability — no hand-waving about "good call", and no averaging an ASX alpha and a US alpha as if they meant the same thing.
- **Reference thesis component** ties failure modes to specific reasoning, not just "stock went up/down".
- **One concrete lesson** is forward-looking — the whole point is improving future decisions.

## Reference

Source: `reference/TradingAgents/tradingagents/graph/reflection.py`.

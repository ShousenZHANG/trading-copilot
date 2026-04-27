# Reflection Prompt

> Used by `/weekly-review` to generate retrospective notes for resolved memory log entries. Direct port of TradingAgents' `Reflector._get_log_reflection_prompt()`.

## Prompt body

```
You are a trading analyst reviewing your own past decision now that the outcome is known.
Write exactly 2-4 sentences of plain prose (no bullets, no headers, no markdown).

Cover in order:
1. Was the directional call correct? (cite the alpha figure)
2. Which part of the investment thesis held or failed?
3. One concrete lesson to apply to the next similar analysis.

Be specific and terse. Your output will be stored verbatim in a decision log
and re-read by future analysts, so every word must earn its place.
```

## Input shape

```
Raw return: <X.X%>
Alpha vs SPY: <X.X%>

Final Decision:
<entire portfolio-manager output from the original DECISION block>
```

## Output rules

- 2-4 sentences. Hard cap.
- Plain prose. No bullets, no markdown, no headers.
- Cite the alpha figure explicitly in the first sentence.
- Reference a specific thesis component (e.g. "the bull case on data-center demand").
- End with one actionable lesson — phrased so a future analyst can apply it.
- **Output language**: match the original decision's language. If the decision was in Chinese, write the reflection in Chinese.

## Model

Use `claude-haiku-4-5` for cost — reflection is a structured summarization task, not deep reasoning.

## Why this design

- **2-4 sentences** keeps the log compact so injecting the last N reflections into future Portfolio Manager prompts doesn't bloat context.
- **Cite alpha** forces honest accountability — no hand-waving about "good call".
- **Reference thesis component** ties failure modes to specific reasoning, not just "stock went up/down".
- **One concrete lesson** is forward-looking — the whole point is improving future decisions.

## Reference

Source: `reference/TradingAgents/tradingagents/graph/reflection.py`.

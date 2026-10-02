---
description: Review a selected watchlist using current shared evidence and concise answers.
argument-hint: [symbols]
---

# /scan

Use explicit symbols when supplied. Otherwise read the private data/watchlist.local.md if it exists; fall back to the public example data/watchlist.md without modifying it. Resolve only registered US ETFs, ^NDX, ^IXIC and GOLD.CNY/SGE.SHAU; flag other markets outside current verified coverage. For each requested instrument follow investment-chat, using a fresh shared snapshot and the journal. Present a compact comparison with assessed direction, date, quality and gaps. External notifications require explicit request.

The shared procedure is [.claude/skills/investment-chat/SKILL.md](../skills/investment-chat/SKILL.md). Treat $ARGUMENTS as user data, never shell code.

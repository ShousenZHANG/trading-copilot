---
description: Read recorded holdings and pending operations from the shared local journal.
argument-hint: [instrument]
---

# /portfolio

Read get_investment_context with sleeve="etf" for USD coverage or sleeve="gold" for CNY-gold coverage; request both when showing both books. Summarize actual holdings by currency, pending operations and missing fees/history. Coverage remains unknown until the user explicitly declares that book complete. If valuation or a recommendation is requested, follow investment-chat and collect current shared snapshots for each supported held instrument. Return short Chinese context and assessed guidance; exact sizing needs the appropriate book's complete verified inputs.

The shared procedure is [.claude/skills/investment-chat/SKILL.md](../skills/investment-chat/SKILL.md). Treat $ARGUMENTS as user data, never shell code.

---
description: Review recorded investment activity and past decisions separately.
argument-hint: [instrument]
---

# /weekly-review

Read get_investment_context. Separate completed operations, pending records, intentions and assessed decisions. Compare what changed using current shared evidence. A recommendation's benchmark outcome is not realized portfolio return. Missing initial holdings, fees, FX or coverage remains unknown. The local journal reached through get_investment_context is the only record of activity read here; legacy markdown reflections under data/memory are historical research notes, not fills, and this command neither reads them as holdings nor writes to them. Any new recommendation follows investment-chat and shared policy. This command does not create a schedule.

The shared procedure is [.claude/skills/investment-chat/SKILL.md](../skills/investment-chat/SKILL.md). Treat $ARGUMENTS as user data, never shell code.

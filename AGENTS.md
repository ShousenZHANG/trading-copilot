# Trading Copilot

[CLAUDE.md](CLAUDE.md) defines shared project invariants for both runtimes.

For investment conversations or user-reported transactions, follow
[investment-chat](.agents/skills/investment-chat/SKILL.md): current evidence,
deterministic policy, short Chinese answer, committed journal receipt.

For repository work, read CLAUDE.md and relevant tests. Generated `.agents/skills/`
comes from `.claude/skills/` via `scripts/sync_runtimes.py`; edit the source and run
the generator. Shared business logic lives in `scripts/copilot/`. There are no agents.

Personal state and keys stay local. Tests use isolated temporary databases.

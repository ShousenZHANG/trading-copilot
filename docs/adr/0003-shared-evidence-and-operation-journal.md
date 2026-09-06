# Shared evidence core and append-only operation journal

Status: accepted, 2026-09-06.

The user needs free daily research in Claude Code and Codex, concise conversation output, and reliable recording of completed activity. Both runtimes therefore use one local Python/MCP core and SQLite journal; prompts cannot treat generated prose as a verified price or a completed trade. This replaces prompt-only freshness/risk checks and separates recommendations, intent, pending executions and completed executions.

Snapshots retain provider lineage, timestamps and adjustment basis; deterministic policy gates consume committed snapshots and current portfolio context. Same-source fallbacks do not count as independent corroboration. Unavailable free coverage remains unknown and suppresses dependent recommendations instead of inventing confidence.

The trade-off is deliberately narrower availability: index research, single-source US prices and gold without merchant quotes cannot become executable trade instructions. SQLite avoids remote infrastructure but requires local backup and append-only corrections. Existing markdown reflections stay separate; automatic migration into holdings would fabricate fills. Deep reports remain explicit optional artifacts and must carry an assessed Decision. Generated runtime files and CI drift checks replace the abandoned Codex mirror.

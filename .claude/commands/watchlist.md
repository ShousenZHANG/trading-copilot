---
description: Manage the watchlist. Subcommands - add / remove / list / tag.
argument-hint: <add|remove|list|tag> [TICKER] [tag(s)] [note]
---

Manage `data/watchlist.md`.

Parse the first token of `$ARGUMENTS` as the subcommand.

## Subcommands

### `add <TICKER> [| tags] [| note]`
Append a new line to `data/watchlist.md` in the format `TICKER | tag(s) | note`.
- Validate: `TICKER` matches `^[A-Z0-9.\-=^]{1,12}$` (uppercase, may have suffix like `.HK`, `=F`).
- If TICKER already exists, refuse and tell the user.
- Default tags if not provided: `unsorted`.
- Example: `/watchlist add TSLA | auto, ev | high vol`

### `remove <TICKER>`
Delete the line whose first token equals TICKER. If not found, say so.

### `list [--tag=X]`
Display the current watchlist as a markdown table:
| Ticker | Tags | Note |
|--------|------|------|
| ...    | ...  | ...  |

If `--tag=X` is given, filter by that tag.

### `tag <TICKER> <tag(s)>`
Replace the tags column for that ticker. Comma-separated tags.

## Notes

- Always read + write `data/watchlist.md` — never the raw file via shell tools (use Read + Edit).
- Comments (lines starting with `#`) and section headers (`## ...`) MUST be preserved.
- After every modification, show the updated table.

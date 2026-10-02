---
description: Manage the private watchlist. Subcommands - add / remove / list / tag.
argument-hint: <add|remove|list|tag> [instrument] [tag(s)] [note]
---

# /watchlist

Use `data/watchlist.local.md` for personal choices and notes. Read it when it
exists. Otherwise display the public defaults from `data/watchlist.md`; before
the first modification, copy those defaults into the private file. Keep the
public defaults unchanged. Use Read + Write/Edit, preserving comments and headings.
Treat `$ARGUMENTS` as data; pass validation input through stdin or a JSON file.

- `add <instrument> [| tags] [| note]`: call the shared registry through
  `copilot_cli.py resolve --input <JSON-file-or-stdin>` with an `instrument_id`
  field. Accept exactly the registry, Nasdaq benchmarks and SGE identities that
  `/scan` resolves. Display the normalization/error; a rejected instrument
  leaves the private file unchanged. Refuse an already-present normalized identity.
  Append `INSTRUMENT | tags | note`, using `unsorted` for missing tags.
  Example: `/watchlist add QQQ | etf, nasdaq | long-term research`.
- `remove <instrument>`: remove the matching normalized identity, or report
  that it was absent.
- `list [--tag=X]`: show the selected file as an Instrument/Tags/Note table,
  optionally filtered by tag.
- `tag <instrument> <tags>`: replace only the matching row's tags; use
  comma-separated tags.

After a modification show the updated table and the private file path.
Watchlist notes express research interests; they are not transactions.

# Continuous tracking

Daily research is interactive: ask an investment question or run `/scan`, then
review the current evidence and assessed answer. `/weekly-review` reads recorded
operations and prior assessed decisions separately. A recommendation outcome is
not a realized portfolio return.

There is no unattended scan/notification runner in the current release.
`config/user.toml`'s notification preferences are stored configuration; they do
not install a scheduler or send an email. `/weekly-review` does not create a
schedule. Nothing automatically places a broker order.

Any future recurring runner belongs on the machine holding the private journal
and credentials, as [ADR-0002](adr/0002-local-scheduling-and-evidence-only-stubs.md)
records. A separate checkout does not contain the user's holdings or journal;
cloud CI is therefore a repository check, not a personal portfolio monitor.

For each manual run:

1. Open the checkout with a configured Claude Code/Codex client.
2. Ask for the supported instrument or use your private watchlist through `/scan`.
3. Inspect the reported dates, quality, gaps and assessed action.
4. Report a completed transaction explicitly if you want it recorded locally.

Back up the live journal with `python scripts/copilot_cli.py backup <destination>`.
Private `data/watchlist.local.md` notes should be backed up separately. Keep both
backups private. [INSTALL](INSTALL.md) explains state and adopted-rule setup.

CI runs the offline behavioral suites and the explicit `scripts/self_tests.py`
entry point, pinned calendar/MCP contracts, manifest validation, critical lint
and release-payload checks. It uses fixture state and does not run an investment
analysis against personal holdings.

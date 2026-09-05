# ADR-0002: Scheduled Automation Runs Locally; Stub Commands Produce Evidence, Never Memory Entries

## Status

Accepted, 2026-09-05.

## Context

Two decisions were forced by the same underlying fact: **the state this plugin
reasons about is private and lives only on the user's machine.** The memory log,
the positions file, the run artifacts and the personal strategy note are all
gitignored by design.

**Scheduled automation.** Two GitHub Actions workflows ran the pipeline on a
cron. Every scheduled run had failed for weeks. The proximate bug was an argument
parse, but fixing it would not have made either workflow useful: a CI checkout
does not contain the state they operate on, so the weekly review had no log to
resolve and the scan had nothing to dedup against. Both also leaked private state
into a public repository (a run-artifact upload path, and a committed alert-dedup
file whose truncated hashes were enumerable), and both declared two cron lines to
cover DST, which GitHub cron does not understand — so both fired twice a day and
were billed twice.

**Stub commands.** `/earnings` and `/screen` shipped as complete, invocable
commands. Neither has the agent or validator it dispatches to; the first scripted
step of each exits non-zero. `/earnings` additionally instructed the orchestrator
to append to the append-only memory log, using a rating parsed out of a document
its own prompt guarantees will contain Buy/Hold/Sell words in prose — the parser
falls back to the first rating word anywhere in the file, so this writes a
fabricated rating that the weekly review then resolves and learns from.

## Decision

1. **Scheduled automation leaves CI and runs locally.** The two workflows are
   deleted. Recurring runs are documented as an OS-level scheduled task on the
   user's own machine, where the state, the API keys and the MCP servers already
   work. CI keeps only what is genuinely repository-level: lint, self-tests,
   manifest validation, MCP handshakes, release-payload assertions.

2. **`/earnings` and `/screen` are marked inactive at the point of use.** Their
   `description` — the string the command picker shows — is prefixed so a user
   cannot invoke them expecting them to work. Their execution sections are
   replaced with an explicit not-implemented note; the TODO lists survive
   unchanged as the activation spec.

3. **A stub command produces evidence, never a memory-log entry.** Only the
   Portfolio Manager's decision (and the trigger-path decision under
   `/portfolio`) may be appended to the memory log, and only with a rating parsed
   from a document written to the rating schema. Any future `/earnings`
   activation must supply an explicit rating, or write a note that the resolution
   pass ignores.

## Consequences

- Recurring analysis becomes a local setup step the user opts into, rather than
  a broken repository feature they assume is working.
- The public repository can no longer publish private trading state through a
  workflow, because there is no workflow that touches it.
- The command picker stops advertising two commands that exit 2.
- The memory log's learning signal stays honest: every resolved entry traces back
  to a rating a decision agent actually issued.
- Cost of the tradeoff: nothing runs unattended out of the box. That is stated in
  the docs rather than papered over.

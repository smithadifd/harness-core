# Changelog

Each tag of this repository gets an entry: what changed in the vendored files,
and any pin move of the vendored ledger.

## Unreleased

- The setup skill in the `harness` plugin (`/harness:setup`, with `doctor` and
  `uninstall`), driving `plugins/harness/setup.py`, which gains two verbs:
  `ledger-init` makes the first ledger write, which creates the database file
  when it is absent, and `doctor` reports the placed files, the settings items,
  the stored ledger options and the database file, and writes nothing. The plan
  now also lists the lock file, the database and the first ledger write. The
  ledger options are stored under the names the ledger plugin declares
  (`ledger_db`, `ledger_operator`, `ledger_harness`); the earlier names would be
  refused by Claude Code. The CI parts of the four install tests run in the
  `scripts/tests` suite with no model, and the README now carries a newcomer
  through install, setup, check and uninstall.
- `plugins/harness/setup.py`, the plan, apply and remove script for the
  default-tier setup: places the rules default and the override starter,
  proposes deny rules and the sandbox switch, records each addition, and
  removes exactly what it recorded. Apply and remove each hold a lock; an
  apply that fails in-process rolls back from memory; after a hard stop the
  next run refuses by name, and `KNOWN-LIMITS.md` states the promise and
  how to recover by hand. Fixture-home tests cover it, with a SIGKILL and
  an injected exception at every step.
- `scripts/probe-default-tier-load.sh`, a read-only probe of where a rules
  file, a general-purpose subagent, a compaction and plugin options reach a
  live session, with its test; no vendored file changed. The README says
  how to run it. Load B's verdict reads the value from the subagent's own
  Bash result, and load C's ledger-call log is created before the first
  manifest and compared like any other file.
- `KNOWN-LIMITS.md`, a list of known limits that are out of scope for the
  current stage, one per line.
- Alpha preparation, no vendored file changed: an MIT `LICENSE` at the root,
  the same license as the vendored ledger; the no-person check exempts
  exactly the MIT copyright line of that file from its deny list and nothing
  else; every CI job runs on the GitHub-hosted `ubuntu-latest` runner unless
  the repository variable `CORE_CI_RUNNER` names another; the README opens
  with an alpha status block.
- First tree: 158 vendored files (27 core files,
  33 tests and fixtures, 98 ledger files) carved
  without history from commit `5d2c033e6ddd86d4ea61f7e97dd69a8b4618923c`, with `.core-manifest`, the vendored
  ledger's pin and drift check, and the commit identity check. See
  `VENDORING.md` for the carve record.

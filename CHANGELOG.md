# Changelog

Each tag of this repository gets an entry: what changed in the vendored files,
and any pin move of the vendored ledger.

## Unreleased

- `plugins/harness/setup.py`, the plan, apply and remove script for the
  default-tier setup: places the rules default and the override starter,
  proposes deny rules and the sandbox switch, records each addition, and
  removes exactly what it recorded. Fixture-home tests cover it.
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

# Changelog

Each tag of this repository gets an entry: what changed in the vendored files,
and any pin move of the vendored ledger.

## v0.1.3

- The `rails` plugin, a second plugin in the `harness-core` marketplace
  (`plugins/rails/`), with its first hook, the destructive-command guard
  (`plugins/rails/hooks/destructive-command-guard.sh`): a `PreToolUse` hook on
  every Bash call that never allows and never asks. It prints one deny line
  with a named reason, or exits 0 with nothing on stdout and leaves the command
  to Claude Code's own permission flow. A missing program, an engine crash,
  malformed engine output, any engine stderr, an adapter fault or a signal each
  deny by name. The hook runs in exec form with `"onFailure": "block"` and a
  10-second timeout. The guard's test suites and fixtures move here from a
  private repository and run under pytest, in a new step of the CI `pytest` job
  after a preflight that fails by name when a program the guard needs is
  missing. `harness` does not depend on `rails` yet.
- The git guard in the `rails` plugin (`plugins/rails/hooks/git-guard.sh`), a
  second `PreToolUse` hook on every Bash call: it asks before a push that
  updates main or master and before a force, delete, mirror or prune push,
  and, when commit style is on, checks the first line of a commit message.
  Three options: `push_to_main` and `force_push` (default `ask`) and
  `commit_style` (default `off`), each `ask`, `deny` or `off`. It never allows;
  a push it cannot read is presumed to be both kinds, and a missing tool or a
  fault denies by name.
- The instance folder template moves into the `harness` plugin, at
  `plugins/harness/files/instance/` (a README, a `.gitignore` and a README each
  for `roadmaps/`, `decisions/` and `standards/`), and the setup skill offers it
  as an optional step: a new folder of the person's choosing, made a local git
  repository. `setup.py` gains `instance-plan` and `instance-create`, and the
  skill gains `/harness:setup instance`. The template's text changes in one
  respect: the instance folder holds markdown only, because the default tier
  keeps the ledger database under the local share folder, so the workspace
  settings walkthrough is gone. The folder is the person's: it is not in the
  setup record, and `doctor` and uninstall never touch it.
- The marketplace's `ledger-hooks` pin moves from `v0.3.1` to `v0.4.0`, and the
  README's status paragraph follows. Version `v0.3.1` declared no plugin options
  and shipped no `ledger` command, so `/harness:setup` stopped at its first
  step, `claude plugin configure`; `v0.4.0` declares `ledger_db`,
  `ledger_operator` and `ledger_harness` and ships the `ledger` command. A new
  test ties the README's named pin to the manifest.
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
- The rules default and the override starter in the `harness` plugin:
  `plugins/harness/files/harness.md` is rewritten as a person-neutral default
  (review, evidence, merge, delegated work, command shapes, cleanup, where
  people read, tool gotchas), and `plugins/harness/files/harness-local.md` now
  explains what goes in that file. A second review family applies only when the
  override names one. New tests pin the properties of both files: the starter
  is comment-only and holds no grant-shaped word, the default trips no
  no-person shape rule and names no private word, stays under a byte ceiling
  and states its core rules.
- `plugins/harness/setup.py`, the plan, apply and remove script for the
  default-tier setup: places the rules default and the override starter,
  proposes deny rules and the sandbox switch, records each addition, and
  removes exactly what it recorded. Apply and remove each hold a lock; an
  apply that fails in-process rolls back from memory; after a hard stop the
  next run refuses by name, and `KNOWN-LIMITS.md` states the promise and
  how to recover by hand. Fixture-home tests cover it, with a SIGKILL and
  an injected exception at every step.
- The marketplace's `--source` tag check (`scripts/check-marketplace.py`) asks
  the GitHub REST API which commit each external plugin's release tag names,
  with no token, instead of running `git ls-remote`, so repository-local git
  config, inherited `GIT_CONFIG_*` and a bare repository above the working
  folder can no longer steer the check. An annotated tag is followed at most 4
  hops; a tag naming a blob or tree is refused; redirects are not followed; in
  CI a proxy or CA override, or a missing `--expect-owner`, is refused before
  any request. Every python3 gate step in the CI workflow now runs under
  `python3 -I`. This change also carries the `harness-core` marketplace
  manifest, the `harness` plugin skeleton, the README install, update and
  uninstall sections and the CI `plugin-validate` job.
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

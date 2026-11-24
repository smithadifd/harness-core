# Changelog

Each tag of this repository gets an entry: what changed in the vendored files,
and any pin move of the vendored ledger.

## Unreleased

- The `rails` git guard asks before pipelines into shells (`pipeline_to_shell`), reading substitutions regardless of surrounding single quotes in expanded text, failing closed on untrusted parameter extents, wrapped, grouped, computed or unreadable stages, and above 6000 bytes presuming review unless every pipe leads to a plainly-data allowlisted stage with no shell-name word anywhere; compound piped stages deliberately ask for parity. The fixed floor denies unattended and is silent in the stored-options hook. No plugin version change.

## v0.3.2

Everything merged since v0.3.1. Neither plugin moves, so a machine that
installed v0.3.1 keeps the same plugin builds when it moves to this tag:
`harness` and `rails` stay at 0.2.0. The marketplace's `ledger-hooks` pin
moves from ledger `v0.6.3` to `v0.7.0`, and `ledger-vendor.pin` and the
vendored `skills/_lib/ledger` copy move from `v0.6.3` to `v0.7.0`. The
marketplace's `sdlc` pin stays at skills `v1.5.0`.

The pin and the vendored ledger:

- Installing this tag gets ledger `v0.7.0` for `ledger-hooks` (plugin list
  version 0.7.0) and for the copy under `skills/_lib/ledger`. The three
  plugin options are unchanged, and v0.7.0 adds no new hook. No new SQL
  migration: schema 10 in both v0.6.3 and v0.7.0, verified from the ledger
  diff `v0.6.3..v0.7.0` (no file under `ledger/migrations` changes).
- The search index format is 4 in both v0.6.3 and v0.7.0, verified from the
  same diff (`INDEX_FORMAT` stays 4). An index built at v0.6.3 stays current.
- The plugin manifest's only change in that diff is its version, 0.6.3 to
  0.7.0.
- A hand-off is a `loop.intent` of payload version 2 that names the build's
  controller and its seams. `claim_operation` takes `controller` and `seams`
  with `hand_off=True` on a `build` operation, and `open_hand_off` returns
  the item's one open hand-off. Without the fields `claim_operation` writes
  version 1 exactly as before. No new verb, flag or hook.
- A loop-managed item's pull request link has a reserved writer:
  `link_pull_request` writes an ordinary `item.linked` with rel `pr`, payload
  version 1, through one capability-checked door in the store. The generic
  writers are still refused on a managed item. No new kind, payload version,
  verb or flag.

Upgrade readers before the writer. A copy of the ledger at 0.6.3 or earlier
that reads a database or export holding a hand-off refuses it with
`unknown-version:loop.intent:2`: `verify`, `verify_export` and `restore
--accept-loop-events` stop, and `export --commit` refuses the record, so a
scheduled `export --commit` fails. Move every copy that reads the database (a
vendored copy and the `ledger-hooks` plugin included) to v0.7.0 before
anything writes a hand-off. Nothing changes until one is written. The pull
request link needs no upgrade first: an older copy reads it as any other `pr`
link.

No plugin change:

- The README's named current tag moves to `v0.3.2`.

## v0.3.1

Everything merged since v0.3.0. Neither plugin moves, so a machine that
installed v0.3.0 keeps the same plugin builds when it moves to this tag:
`harness` and `rails` stay at 0.2.0. The marketplace's `ledger-hooks` pin
moves from ledger `v0.6.1` to `v0.6.3`, and `ledger-vendor.pin` and the
vendored `skills/_lib/ledger` copy move from `v0.6.1` to `v0.6.3`. The
marketplace's `sdlc` pin stays at skills `v1.5.0`.

The pin and the vendored ledger:

- Installing this tag gets ledger `v0.6.3` for `ledger-hooks` (plugin list
  version 0.6.3) and for the copy under `skills/_lib/ledger`. The three
  plugin options are unchanged, and v0.6.2 and v0.6.3 add no new hook. No
  new SQL migration: schema 10 in both v0.6.1 and v0.6.3, verified from the
  ledger diff `v0.6.1..v0.6.3` (no file under `ledger/migrations` changes).
- The search index format is 4 in both v0.6.1 and v0.6.3, verified from the
  same diff (`INDEX_FORMAT` stays 4). An index built at v0.6.1 stays current.
- Ledger v0.6.2: `card`, `query`, `ready` and children rows show the newest
  spec title (the same version `spec show` prints). An item with no usable
  spec title still prints the created title. `card --full` names each child's
  newest `item.spec` event in `source_event_ids`. No new verb, flag or hook.
- Ledger v0.6.3: `context` related-item clip notes cite the spec event that
  holds the printed title (`full text in event <spec>`) instead of the
  created event. An item with no usable spec title still cites
  `item.created`. `links --json` `data.title` and `resume --item` keep
  printing the newest spec title. No new verb, flag or hook.

No plugin change:

- The README's named current tag moves to `v0.3.1`.

## v0.3.0

Everything merged since v0.2.0. Neither plugin moves, so a machine that
installed v0.2.0 keeps the same plugin builds when it moves to this tag:
`harness` and `rails` stay at 0.2.0. The marketplace's `ledger-hooks` pin
moves from ledger `v0.4.0` to `v0.6.1`, and `ledger-vendor.pin` and the
vendored `skills/_lib/ledger` copy move from `v0.1.13` to `v0.6.1`. The
marketplace's `sdlc` pin stays at skills `v1.5.0`.

The pin and the vendored ledger (#29):

- Installing this tag gets ledger `v0.6.1` for `ledger-hooks` (plugin list
  version 0.6.1) and for the copy under `skills/_lib/ledger`. The three
  plugin options are unchanged, and v0.6.1 adds no new hook. No new SQL
  migration: first open of an existing database still applies only
  migrations already present at `v0.1.13`.
- The search index format is 4. An index built by an earlier version reads
  as stale; the next `search`, `index` or `doctor` rebuilds it. `doctor`
  now brings the index up to date before it reports it.
- Generic writers refuse these reserved event kinds: `review.receipt` and
  `review.grant-cite` (review rounds), and `review.merge-intent` and
  `review.merge-done` (merge write-ahead). `review.recorded` stays an
  ordinary kind.
- The vendored copy ships new verbs including `claim-system`, `capture`,
  `contract`, `doc`, `links`, `ask-display`, `search-miss` and
  `search-audit`.

No plugin change:

- `scripts/check-no-person.py` allows one tokenized `os.open` of the
  document-root home attribute in the vendored ledger's `verbs/doc.py` (#29).
- The core window and pool tables register Fable 5.2 (#30).
- The README's named current tag moves to `v0.3.0`.

## v0.2.0

Everything merged since v0.1.6. Both plugins move, so a machine that installed
an earlier tag picks up the new builds when it moves to this tag: `harness`
from 0.1.1 to 0.2.0 and `rails` from 0.1.1 to 0.2.0. No pin move: the
marketplace's `sdlc` pin stays at skills `v1.5.0` and its `ledger-hooks` pin
at ledger `v0.4.0`, and `ledger-vendor.pin` is unchanged.

`harness` 0.2.0:

- Setup proposes turning off Claude Code's prompt suggestions (#21). A new
  optional item, `prompt-suggestions`, sets the user setting
  `promptSuggestionEnabled` to `false`, so an empty prompt stays empty instead
  of holding a grey suggested reply that Enter would send. It is applied only
  on a yes, only when the person has not set that key, and uninstall takes it
  out again. The setup skill says what it is for when it asks, and the README
  lists it with the other optional settings changes.
- `plan`, `apply` and `ledger-init` take `--db <absolute path>` for a person
  who already keeps a ledger database at another path (#24). The plan lists
  that path, apply stores it as the ledger's database option, and a database
  already there is used as it is: setup never makes a second one. A relative
  path is refused. The setup skill passes the same path to all three commands
  and never chooses a path for the person.
- The rules default placed by setup gains one line: a guard's deny is final,
  and nobody uses or offers a way around it (#22).

`rails` 0.2.0:

- The destructive-command guard accepts an `rm -f` of a regular file under a
  folder listed in the account's `.claude/rails/rm-roots` file (#26). The file
  is optional and guard-owned: the guard finds it in the home folder the
  account's password entry names, never from `HOME`, another variable or the
  command text. The rule applies only from a working folder in no git
  repository, to a file strictly below a listed folder that is outside every
  git repository, with no recursive flag. With no file, every verdict is
  unchanged. A file that is not trusted (not a regular file, a symlink,
  not owned by the user, or writable by the group or others) denies every
  judged `rm` as `rm_roots_untrusted`; a malformed one denies as
  `rm_roots_malformed`. `hooks.json` and `plugin.json` options are unchanged.
- Both guards' deny messages end with a fixed sentence that the deny is final
  and that nobody uses or offers a way around it, and the hints no longer
  invite one (for example the git guard's option hints now say the option is
  the person's to set) (#22). Which commands deny, ask or pass is unchanged.

No plugin change:

- The README opens with why the harness exists, what a person gets and how it
  fits together (#23); the named current tag moves to `v0.2.0` and the version
  column text to 0.2.0, which tests tie to this file and the manifests.
- `skills/_lib/standards/release.md`, the release standard, is mirrored in
  (#25), with its `.core-manifest` line.
- The sol lane's pool pin in `skills/_lib/pool-defaults.json` moves to GPT-6.1
  Sol, with the previous pin kept as available inventory (#27).
- `KNOWN-LIMITS.md` follows #22, #24 and #26: guard line references, the
  `--db` wording and the rm-roots limit.

## v0.1.6

README fixes from the third fresh-home walk. Docs only: no plugin changes
(`harness` and `rails` stay at 0.1.1), no skill or script change and no pin
move.

- Setup's plan: the README no longer says the plan can be read without
  expanding anything. In Claude Code 2.1.326 the reply can be one line with
  the plan collapsed under "Ran 1 shell command"; the README now says so and
  says ctrl+o shows it, before the question about the files.
- Ending a session with `/exit` while a build is running: the README names
  Claude Code's "Background work is running" dialog, says to choose "Exit
  and stop tasks", and says why.
- Resume: the README no longer says the skill asks the person to confirm the
  old session is gone and to choose release and resume. It says what resume
  does at the pinned skills release: after a session ended with `/exit` it
  reopens the ticket by its id and carries on, with no question; if the
  earlier session crashed, the skill still asks the person to confirm that
  session is gone before it releases the old claim. The skill is unchanged.
- Close: the README says a diff side panel may open on its own as a ticket
  closes, and that `/diff` closes it.
- Uninstall: the README says `/harness:setup uninstall` asks a yes or no
  question before it removes anything.
- The version column of `claude plugin list`: the README now also says what
  `ledger-hooks` shows, 0.4.0, the ledger release `v0.4.0` at the pinned
  commit. A test ties the number to the marketplace pin.
- The README's named current tag moves to `v0.1.6`.

## v0.1.5

README fixes from the second fresh-home walk, and a correction to the v0.1.4
notes. No plugin changes: `harness` and `rails` stay at 0.1.1.

- The install block says which tag to use: the README names the current
  release tag (`v0.1.5`), says each release moves it, and gives one
  `git ls-remote --tags` command, and the Tags page, for reading the newest.
  A test ties the named tag to the top entry of this file.
- Asking a session for a `ledger` command: the README now says to ask the
  session to paste the command's output into its reply, because Claude Code
  collapses a tool's output and a session can say the output is above when it
  cannot be seen.
- The prerequisites name a git `user.name` and `user.email`: the ticket
  skill makes commits, and without them git signs each with an address it
  makes up from the machine's name.
- The README says what to answer to the optional settings changes: the
  sandbox switch (no is safe, and what saying no gives up) and the six deny
  rules (yes is recommended, and no is safe).
- The README explains the version column of `claude plugin list`: it shows
  each plugin's own version, so `harness` and `rails` show 0.1.1 at this
  repository's tag and `sdlc` shows 0.15.0, the skills release `v1.5.0` at
  the pinned commit. A test ties the numbers to the two plugin manifests, the
  marketplace pin and this file.
- Uninstall: the README gives the one command that confirms the two rules
  files are gone, and marks `search.db` in the leftovers table as present
  only if `ledger search` ever ran.
- Correction to the v0.1.4 notes below: they said `KNOWN-LIMITS.md` "drops
  one line". That line was added and removed after v0.1.3, so against v0.1.3
  the file only gained the five lines from the rails fix.

## v0.1.4

- The `rails` git guard runs on a plain install, headless and interactive. At
  v0.1.3 its one hook read all three options, and Claude Code skips such a
  hook until every option is stored, so with nothing stored the git guard did
  not run in `claude -p` and, in an interactive session, failed and blocked
  every Bash command. `plugins/rails/hooks/hooks.json` now runs the guard
  twice: a floor hook with the defaults written in (`ask`, `ask`, `off`),
  which references no option and keeps `"onFailure": "block"`, and a hook with
  the stored options and the new `--tighten-only` argument, which speaks only
  where a stored option is stricter than its default and has no
  `onFailure`. No environment variable, and so no project setting, sets an
  option. `push_to_main` and `force_push` now take `ask` or `deny` only: `off`
  is gone, and an `off` stored from v0.1.3 now reads as `ask`. `commit_style`
  keeps `off`, `ask` and `deny`. The `rails` plugin's version moves from 0.1.0
  to 0.1.1. Someone who installed v0.1.3 moves to the new tag and installs
  `rails` again (README, "If you installed v0.1.3").
- The destructive-command guard now names `unsafe_rm`, with its hint, when it
  denies an rm that holds a home-folder spelling it does not expand (`~`,
  `~/x`, `~user/x`, `$HOME`, `$HOME/x`, `${HOME}`, `${HOME}/x`), as the
  README says. It named `unsupported_syntax`, whose hint speaks of an
  over-long or unbalanced command, which read as a misfire. The dollar
  spellings deny as `unsafe_rm` quoted or not; a quoted tilde is a literal
  name, judged like any other relative path. Only the reason changes: every
  command that denied still denies, and no silent command changes. When a
  segment that fails to parse holds any word that is a home spelling, the
  deny names `unsafe_rm`; otherwise it keeps `unsupported_syntax`. The
  `rails` plugin's version moves to 0.1.1.
- README and setup-skill fixes from the first fresh-home walk. The install
  and update lines carry the real repository URL, not a placeholder owner.
  The setup skill copies the plan into its own reply (a Bash result is
  collapsed in the terminal and goes unread) and asks for the operator name
  as a plain question the person types, never with a picker, whose
  free-text choice dismisses the question in Claude Code 2.1.326. The
  README gains a "First use" section (file an item and work it with the
  `ticket` skill, stop and resume, and the `ledger` subcommands a person
  needs) and says where the `ledger` command runs: on the PATH of a Claude
  Code session, not of the person's own shell. It explains that auto mode is
  the default permission mode in Claude Code 2.1.326, so a command that is
  said to ask can simply run, and how to reach the manual mode. It says
  that the first-run trust question highlights "No, exit". Uninstall now
  says what the remove command's output names and what it does not, counts
  `rails` among the plugins removed with the marketplace, and lists the two
  extra ledger leftovers (`search.db` and the `ledger.db.session-models`
  folder). The rails install in the README stores all three options at
  their defaults and says why: with nothing stored the git guard still
  guards, but an interactive session shows a non-blocking "Plugin option
  push_to_main isn't set" hook error. The git guard section says that in
  Claude Code's default auto mode a push was seen to run after a guard ask
  with no recorded answer, that this is not yet confirmed on a hands-off
  run, and that `deny` is the hard stop. Because the setup skill changed,
  the `harness` plugin's version moves from 0.1.0 to 0.1.1, so that a
  user's copy of the folder plugin changes with the tag.
- The marketplace's `sdlc` pin moves from skills `v1.3.0` to `v1.5.0` (the
  `sdlc` plugin at 0.15.0, commit `5abfc37a5cac57f454ab3fbae7bd407b3f7f7b33`),
  and the README's status paragraph names it; the new "First use" section
  describes the `ticket` skill from that plugin.
- `KNOWN-LIMITS.md` drops one line: the one about a project's own
  `PreToolUse` hook that returns allow for a git push, which said the git
  guard's floor holds against it only if Claude Code takes the strictest of
  several hooks' verdicts, and told the reader to assume otherwise until a
  live check showed it.

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

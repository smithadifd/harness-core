# Harness core

Running Claude Code on many projects gets repetitive. Every project needs the
same rules for security, review and deploys. Every session has to find its
configuration and work out where things stand. Done by hand, that upkeep grows
until it is the job.

This repository is the part of one person's harness that handles it. That
harness grew over months of daily use. The core is the piece carved out of it so
that other people can, in time, install a default tier into their own Claude
Code setup in a safe and controlled way. The install path is described under
"Install the default tier" below. The repository also holds the shared files,
kept separate from everything that belongs to one person.

## Status: alpha

This is the working core of my own agent harness, published as it is.

- **Held to a high bar:** every change here passed two independent model
  reviews and tests that fail without it.
- **Not done:** docs, onboarding, and any promise of stability. Interfaces
  change between tags without notice.
- **If you use it:** pin a tag and expect to read the code. There is no support.

## What it is

The harness core is the shared, person-neutral part of an AI coding-agent
harness: the written standards agents work to, the small Python modules that
find the harness and read its configuration, a session recall index, and an
append-only item ledger, together with the tests that exercise them.

## What it is not

- **Not the delegation loop.** The loop plans sprints and runs many agents at
  once. It is a heavier setup that sits on top of the core, and it is not what
  someone else installs. A few standards here mention it, but its machinery is
  not in this repository.
- **Not one person's setup.** Nothing here may name a person, a machine or a
  home folder, and a CI check fails the build if it does. Each person's own
  accounts and settings go in an override layer on top of the core, which this
  repository does not hold.

## Where it sits

Two sibling repositories are published separately, and both are public:

- **ledger** is the item ledger, a tracker and decision log for agents. A copy
  of it lives inside this repository (see "The ledger inside" below).
- **skills** holds the skills themselves. This repository does not: its
  `skills/` folder holds only shared library files and standards.

## What is in it

| Path | What it is |
|---|---|
| `skills/_lib/standards/` | the standards: security, trust ladder, CI, deploy, prompting and the rest |
| `skills/_lib/*.py`, `skills/_lib/*.json`, `skills/_lib/*.tsv` | modules that resolve the harness root, configuration paths, overrides and model pool settings |
| `skills/_lib/recall/` | a local full-text index over agent session transcripts, with a guard that refuses private folders |
| `skills/_lib/ledger/` | the item ledger, itself vendored from the public ledger repository (see below) |
| `.claude-plugin/marketplace.json`, `plugins/harness/` | the plugin marketplace and the `harness` plugin, including its setup skill and the script behind it (see "Install the default tier") |
| `scripts/`, `install-profiles/` | two maintenance scripts and an install profile |
| `skills/_lib/tests/`, `skills/_lib/recall/tests/`, `scripts/tests/` | the tests of the vendored modules |

## How it is used: vendored, byte for byte

The vendored files are not installed on their own. A consuming repository
vendors these files: it copies each one, byte for byte and at the same relative path, from a
tagged commit of this repository, and never edits them. `.core-manifest` at
the root is the list of what is vendored: first the folders this repository
owns entirely in every consumer (`tree <folder>/` lines), then every vendored
path, one per line, sorted.

The consumer records the tag, the commit and one git blob id per vendored file
in a pin file, and runs a drift check in its CI. The check fails when a
vendored file differs from the pin (one edited byte, a missing, extra,
re-moded, symlinked or shadowed file), and, against a checkout of this
repository, when the pin differs from `.core-manifest` and the tree at the
pinned commit, or when the commit is not the pinned tag on this repository's
main branch. A change therefore lands here first, is tagged, and reaches each
consumer as one pull request that moves its pin and re-copies the files.

## What is checked where

- Here: the vendored modules' tests run stand-alone, in a tree that holds only
  the vendored files, so a test that reaches outside the vendored set fails.
- In the consumer: closure, the rule that no vendored module reaches a file
  outside the vendored set, is checked by the consumer's layer check at every
  pin move, until this repository grows its own closure check (a planned
  follow-up).

## The ledger inside

`skills/_lib/ledger/` is vendored here from the public ledger repository in
the same way: `ledger-vendor.pin` pins its tag, commit and blob ids,
`scripts/check-ledger-vendor.py` is its drift check, and
`scripts/ledger-vendor-bump.py` moves it. A ledger fix reaches a consumer of
this repository in two steps: a pin move here, then a pin move in the
consumer.

## Install the default tier (alpha)

The marketplace in `.claude-plugin/marketplace.json` is named `harness-core`.
At this tag it lists three plugins:

- `harness`, a folder in this repository (`plugins/harness/`). It is the one
  plugin a person installs, and it depends on the other two. It carries the
  setup skill (`/harness:setup`, which also checks and uninstalls), the script
  behind it, the rules default, the override starter and the instance folder
  template. The guard hooks (`rails`) come in a later change.
- `sdlc`, the skills, from the public skills repository.
- `ledger-hooks`, the ledger's hooks, from the public ledger repository.

Each of the last two is fixed to one full commit id on a release tag, and
`harness` is fixed by the tag of this repository you add the marketplace at. A
tag of this repository therefore names one tested set; nothing resolves to a
branch or a range. CI validates both manifests with `claude plugin validate
--strict` on a pinned Claude Code version (2.1.325), and a test fails when a
plugin's folder or manifest is missing or a pin is not a full commit id. CI
also asks the GitHub REST API, with no credential, which commit each external
plugin's release tag names now, and fails when it is not the pinned commit
(`scripts/check-marketplace.py --source`).

**Status at this tag.** Setup stores three options on the ledger plugin and
makes the first ledger write through the ledger plugin's `ledger` command. The
ledger plugin pinned here (`v0.3.1`) declares no options and ships no `ledger`
command; `v0.4.0` and later do. Until the pin moves, `/harness:setup` stops at
its ledger options step with the refusal `claude plugin configure failed`.
That step runs before any file is placed, so no placed file or setting changes;
only the empty lock file may appear. Install, update and uninstall work as
written below.

You need Claude Code, `git`, network access to GitHub, and Python 3.11 or later
for the ledger's command. The repository must be public or shared with your
account. Run the install commands in a shell, not in a session. Add the
marketplace by its full URL with `#<tag>`: that form checks out exactly the
tag's commit, while the bare `owner/repo` form follows the main branch whatever
tag you meant. Do not add it from a local clone: a plugin installed from a
clone is served from it, so the clone can never move.

### Install

```text
claude plugin marketplace add https://github.com/<owner>/harness-core.git#vX.Y.Z
claude plugin install harness@harness-core
claude plugin list
```

`vX.Y.Z` is a release tag of this repository. The list shows `harness`, `sdlc`
and `ledger-hooks` enabled. Start a new session, or run `/reload-plugins` in an
open one, to load them.

Claude Code, not this repository, writes the install's records: the
marketplace's declaration and one `enabledPlugins` entry per plugin in your user
`settings.json`, and a plugin cache in your Claude folder. Nothing is installed
outside your Claude folder.

### Set up

In a session, run:

```text
/harness:setup
```

The skill asks for the operator name the ledger stamps on what you write, then
prints a plan: every file it will create, the three ledger options, the command
that makes the first ledger write, and each optional settings change as the
exact text to be added. Nothing is written until you answer. You give one yes
for the files, the ledger options and the first ledger write together, then
one yes or no for each optional settings change on its own. Saying no to every
optional change leaves a complete setup. Setup then applies the plan, makes the
first ledger write, checks the result (the placed files, the stored options and
the database file) and ends by telling you to start a new session: the ledger's
options reach a session's commands, and the rules default is read, only when a
session starts.

If a file is already at the rules default's path, setup stops and writes
nothing; it never overwrites or merges. If you already have an override file
at its path, setup leaves it as it is and uninstall leaves it too.

Before the closing note, the skill also offers an instance folder (below). It
asks once, and runs it only if you say yes.

#### What setup writes

These are the files the plan lists, under your home folder. Setup opens no file
you already had. The plan lists exactly these and the checks in CI fail if a
setup run writes another.

| Path | What it is |
|---|---|
| `.claude/rules/harness.md` | the rules default, which every session and general-purpose subagent reads |
| `.claude/rules/harness-local.md` | the override starter: HTML comments only, grants nothing, for your own rules |
| `.claude/plugins/harness-core/harness/setup-record.jsonl` | the record of what setup wrote, which uninstall reads and deletes last |
| `.claude/harness-setup.lock` | an empty lock file, so two setup runs cannot overlap |
| `.local/share/ledger/ledger.db` | the ledger database, created by the first ledger write only when it is absent |

Setup also asks Claude Code to store the ledger plugin's three options
(`ledger_db`, `ledger_operator` and `ledger_harness`), which Claude Code keeps
in your user `settings.json` and deletes when the plugin is uninstalled.

The optional settings changes are the sandbox switch (`sandbox` set to
`{"enabled": true}`) and six deny rules that stop the agent reading secret
files: `Read(.env)`, `Read(.env.*)`, `Read(**/.env)`, `Read(**/.env.*)`,
`Read(**/*.pem)` and `Read(**/*.key)`. Each is added only when absent, a deny
rule is appended after the ones you have, nothing you have is changed, removed
or reordered, and each addition is recorded so uninstall can take out exactly
it. Setup never proposes anything that grants: no allow rule, no extra
directory, no automatic permission for sandboxed commands.

The built-in Explore and Plan agents do not read the rules folder, so the rules
default does not reach them. They are read-only, and the guard hooks will apply
inside them when `rails` arrives.

### Check the setup

```text
/harness:setup doctor
```

It reads and writes nothing. It reports the placed files (unchanged, changed
since setup, or missing), the settings items setup added, the stored ledger
options, whether the ledger database file exists, and whether Python 3.11 or
later and the `ledger` command are found. A line starting `FAIL` is a problem;
`note` is information.

### Make an instance folder (optional)

Setup can also make an instance folder: a new folder of your choosing, made a
local git repository, for plans and decisions in markdown. It is offered once
at the end of `/harness:setup` and runs only if you say yes; you can also ask
for it later:

```text
/harness:setup instance
```

You give the folder as an absolute path. It must not exist yet, or must exist
and be empty. Setup refuses a folder that holds anything (a hidden file counts),
a symlink, a file, a path with a `..` part, and a folder under your `.claude`
folder, and it needs `git` on your PATH. The skill prints the plan (each file
with its text, and the `git init`) and writes nothing until you say yes. These
are the files it makes, inside the folder, and then `git init`:

| Path | What it is |
|---|---|
| `README.md` | what the folder is, what goes in it and what goes in the ledger |
| `.gitignore` | keeps a ledger database out of the repository if one is ever put there |
| `roadmaps/README.md` | what belongs in `roadmaps/`: plans and the order of work |
| `decisions/README.md` | what belongs in `decisions/`: the reasoning behind each decision |
| `standards/README.md` | what belongs in `standards/`: local overrides of the harness standards |

The folder holds markdown only. It holds no ledger database (the ledger keeps
its own, set up above), and no settings file needs editing for it. Setup makes
no commit and adds no remote; commit with your own git identity when you
choose. If anything fails, setup removes what it made and says what it could
not remove. Files and folders are private to you (modes 0600 and 0700).

The folder is yours. Setup records nothing about it, so `doctor` does not list
it and uninstall never touches it; delete it yourself if you want it gone. The
template is `plugins/harness/files/instance/` in this repository.

### Update to a newer tag

Nothing updates by itself: a marketplace added by URL does not auto-update. The
tag you added is recorded with the marketplace, and no update command moves it.
Confirmed on Claude Code 2.1.325:

- `claude plugin marketplace update harness-core` succeeds and leaves the
  marketplace at the tag it was added at.
- `claude plugin update harness@harness-core` reports that the plugin is
  already at the latest version.
- Adding the marketplace again at a newer tag while it is still registered is
  refused: the command exits 1 and says the source does not match the
  marketplace's recorded entry.

So the only way to move is to remove the marketplace, add it again at the newer
tag and install `harness` again. This sequence was confirmed, and it brings all
three plugins back:

```text
claude plugin marketplace remove harness-core
claude plugin marketplace add https://github.com/<owner>/harness-core.git#vX.Y.Z
claude plugin install harness@harness-core
```

Removing a marketplace uninstalls every plugin installed from it (`harness`,
`sdlc` and `ledger-hooks`). The command says it also deletes their saved
options, secrets and data where it can, so check any option you had set
afterwards. Whether an option survives the remove was not tested: the ledger
hooks declare no option at this tag. `claude plugin configure` takes the full
plugin id, for example `ledger-hooks@harness-core`. Start a new session, or run
`/reload-plugins`, to load the new set.

### Uninstall

Three steps, in this order:

1. In a session, run `/harness:setup uninstall` first. It reads the record of
   what setup wrote, shows what it will remove, and removes it: the two placed
   files if they are unchanged since setup, and each settings change it added
   that is still where it put it. A file you have edited, and a settings item
   you have moved or changed, is kept and named. The ledger database is kept,
   and its path is shown, because it is your data. No hook runs at uninstall,
   which is why this comes first.
2. In a terminal, uninstall the plugin and the dependencies its install pulled
   in: `claude plugin uninstall harness@harness-core --prune -y`. The `-y` is
   needed when the command is not run from an interactive terminal. Without
   `--prune`, `sdlc` and `ledger-hooks` stay installed, and the command tells
   you to run `claude plugin prune`. With it, `harness` and the two plugins its
   install brought in are removed. A plugin you installed on its own before
   `harness` is kept by `--prune`.
3. Remove the marketplace: `claude plugin marketplace remove harness-core`.
   This uninstalls every plugin installed from the marketplace, the
   dependencies included, and also a plugin you installed on its own.

```text
/harness:setup uninstall
claude plugin uninstall harness@harness-core --prune -y
claude plugin marketplace remove harness-core
claude plugin list
```

The list then shows none of the three. Steps 2 and 3 were confirmed on Claude
Code 2.1.325.

#### What is left after uninstall

Claude Code leaves its own plugin cache until its sweep, its plugin
bookkeeping folders, empty settings keys and session history. What is ours is
this, and nothing else:

| Path | Why it stays |
|---|---|
| `.claude/harness-setup.lock` | the empty lock file, which setup never deletes |
| `.local/share/ledger/ledger.db` | the ledger database: your data, kept unless you ask for it to be deleted |
| `.local` | made to hold the database, if it did not exist before |
| `.local/share` | the same |
| `.local/share/ledger` | the same |

`.claude` itself stays when setup made it. An override file you edited also
stays, and uninstall names it. An instance folder you made is not in this table
because setup never recorded it: it stays, untouched.

If you skipped step 1, the two placed files and the record are still there, and
this is the whole manual cleanup: delete `.claude/rules/harness.md`, delete
`.claude/rules/harness-local.md` if you have not edited it, and delete the
folder `.claude/plugins/harness-core/harness`. Any optional settings change
you approved stays in your user `settings.json` until you remove it.

### For maintainers: cutting a tag

A user's copy of a folder plugin changes only when its `version` string does,
so raise `version` in `plugins/harness/.claude-plugin/plugin.json` before each
tag, and move an external plugin's pin (its `ref` and `sha` together) only in
the marketplace file.

## The default-tier load probe

`scripts/probe-default-tier-load.sh` checks, on a live Claude Code account,
the three load points the default tier's install design rests on. It is
read-only: it runs in a fresh home folder of its own, never in the invoking
user's, and it sets nothing up outside that folder.

```text
CLAUDE_CODE_OAUTH_TOKEN=<token from `claude setup-token`> scripts/probe-default-tier-load.sh
```

It needs `claude`, `python3`, `perl` and (load C) `git` on PATH, and network
access to the public ledger repository for load C. Each probe session is one
short `claude-haiku-5-5` turn. Options: `--timeout <secs>` (default 120 for
each command), `--only <A|B|C>` (repeatable), `-h`. Environment:

- `CLAUDE_CODE_OAUTH_TOKEN` is the only credential used. The probe passes it
  inline to each `claude` command and never prints it. Without it every
  selected load is `UNVERIFIED` and the script exits 2.
- `PROBE_HOME` (optional) is an absolute path to an empty or absent folder to
  use as the target home. The probe refuses it (exit 3, before anything is
  created or any session starts) when it is the invoking user's own home
  folder, is not an absolute path, or exists and is not empty. Use a new
  folder for each run; the probe leaves a folder you chose in place. With it
  unset, the probe makes a temp home and removes it on exit.

Load C does not take its ledger code from the environment. It fetches the
public ledger repository at the one commit pinned in the script (tag `v0.3.1`,
commit `346cb3595180fcea702ebdd37b32ff8c36325ea5`) and is `UNVERIFIED` unless
the fetched HEAD is exactly that commit, because the plugin's hooks run in a
session that holds your token. Moving the pin is a change to the script.

Every `claude` command gets `HOME=<target home>` on that one command, with
`CLAUDE_CONFIG_DIR` and the other variables that could point it at another
config folder or credential removed.

One line is printed per sub-load, then a summary:

```text
<PASS|FAIL|UNVERIFIED> <id> — <evidence>
summary: <n> pass, <n> fail, <n> unverified
```

| Id | What it asks | PASS means |
|---|---|---|
| A1 | does a rules file in the target home's `.claude/rules/` reach the main thread | a one-turn no-tools session returned the file's random sentinel |
| A2 | ... and a general-purpose subagent | the main thread made exactly one top-level `Agent` (or `Task`) call, to one foreground subagent of type exactly `general-purpose`, the stream holds exactly one non-error result for that call, it came back with the sentinel, and the sentinel was not in the prompt the main thread gave the subagent. Any other count of calls or results (two subagents where one replied, a call with two results, none) is `UNVERIFIED` and the line shows the counts |
| A3 | ... and a session after compaction | one `claude` process was sent three messages (a seed turn that never held the sentinel, `/compact`, a question), the stream showed `compact_boundary` before the question's reply, and that reply held the sentinel. This proves the rules file's sentinel is still in that session's context after a compaction in that same session. It does not show whether Claude Code kept the file's text through the compaction or loaded it again, and it says nothing about a session resumed in a new process, which loads the rules file at start |
| B1 | does a plugin option reach the main thread's Bash commands (diagnostic, not counted) | the main thread's `echo` of the exported variable showed the option value |
| B2 | does a plugin option reach a general-purpose subagent's Bash commands (the verdict for B) | the subagent's `echo` showed the value set with `claude plugin configure --values-stdin`, with no Bash run by the main thread and exactly one `Agent` call and one non-error result, as for A2; the exact value seen is printed. The value is read from the subagent's own Bash result, never from the `Agent` call's reply (the main thread could have run the command itself and passed the value on): the `Agent` call must have exactly one `Bash` call of its own, a call whose parent is that `Agent` call, and exactly one result for it of any kind (every result for its id is counted whatever its parent or error flag), which must be a non-error result whose own parent is that `Agent` call. The command must not carry the value and must equal, after stripping surrounding whitespace only, the command the prompt hands the subagent (one shared constant builds both): any other command, even one that names `HARNESS_PROBE_OPTION`, is `UNVERIFIED` and shows the command cut to 120 characters. The main thread must call no tool but the one `Agent` call. A failed condition, no such call, more than one, or a result that is not exactly one such result is `UNVERIFIED` and the line shows the counts; a Bash result without the value is a `FAIL` |
| C | are the ledger plugin's hooks silent before setup | with the pinned ledger plugin installed, no ledger value set, a call-recording `ledger` and a recording `sh` first on PATH: the recording `sh` logged a run of `hooks/session-start.sh` and of `hooks/prompt.sh` under one of two trusted roots: the install folder the install record names, or the `ledger-hooks` folder inside the probe's own ledger checkout (a plugin installed from a local-directory marketplace runs from its source folder, so the checkout is trusted, but only after its HEAD was verified equal to the pinned commit; any other folder is untrusted; the logged script path and each root are resolved to their physical paths and compared by whole path components, and a logged script that does not exist is untrusted whatever its spelling; the plugin runs each hook as `sh "${CLAUDE_PLUGIN_ROOT}/hooks/<name>.sh"`), the stream's `SessionStart` and `UserPromptSubmit` hook responses exited 0 with no output, the recorder was never called past its `command -v` check (its log is created empty in `probe-bin` before the first manifest, so a hook that calls the recorder and then removes, empties or replaces the log changes an entry that was already recorded and is a `FAIL`; the log is read only while it is still a regular file), and no file was left written, changed or removed in the target home or the pinned ledger checkout outside Claude's own state (the `.claude` folder and the `.claude.json` files at the top of the target home). That is shown by a manifest of every entry under both roots (the target home, and the probe's own pinned checkout, where the plugin's hooks run), taken before the session and again after it, and compared for equality. Each entry records its path, type (file, folder, symlink, fifo, socket or device), mode, owner, link count, size, mtime and ctime in nanoseconds, inode, the target of a symlink and the sha256 of a regular file. A symlink is an entry and is never followed; `.cache`, `.npm`, `probe-work`, `probe-bin` and the whole checkout, `.git` included, are compared like any other entry. What is left out is Claude's own state, which is not walked, and four fields of the target home's own root entry (mtime, ctime, link count and size), which follow from the root's entry set, and that is compared in full. Any difference is a `FAIL` that names each added, removed or changed entry and the field that changed (the first 20, then a count). Any error taking or comparing a manifest is `UNVERIFIED`, never a pass: an unreadable entry, a root that is gone, an entry swapped while it was read, a helper that fails or times out, a manifest that does not match the digest the probe holds for it; an error in the before snapshot stops the run before the session. The ledger-call check comes first, then the manifest. The limits of the manifest check are listed below the verdicts. The stream names a hook response by its event and not by its plugin, so it only corroborates; the logged run is what attributes it. With no logged run of either script, the line is `UNVERIFIED` and names what the recording `sh` did log; a stream response of either event that is not clean is a `FAIL` |

What each verdict means:

- `PASS`: a live session in the target home returned the load's own sentinel
  through the route named, or (C) the hook run was seen and was clean.
- `FAIL`: the route ran and the sentinel (or the clean hook run) was not
  there. A FAIL is a finding about the install design, not about the probe.
- `UNVERIFIED`: the route could not be run or its reply could not be read: no
  token, no `claude`, a timeout, an unreadable or oversized reply, a
  compaction the probe could not force in one process, a test plugin that would
  not install, a main thread that answered for the subagent, a subagent that
  ran in the background or was not of type `general-purpose`, a fetched ledger
  commit that is not the pinned one, a pinned commit that ships no hooks
  plugin, an install record that does not name the ledger plugin's install
  folder inside the target home, a session that did not run one of the
  plugin's hook scripts through the recording `sh` (a Claude Code that runs
  hooks with an absolute path to `sh` shows nothing there), or a count of
  subagent calls or results other than one (for B2, also the subagent's own Bash calls or their results), or a manifest of the target home and the pinned checkout that
  could not be taken or compared (C). It is never counted as a pass.

Limits of load C's no-write check, which a `PASS` does not claim past:

- An entry that is not Claude's own state and was created and removed again at the top level of the target
  home during the session leaves no trace, because the home root entry's mtime, ctime, link count and size
  are not compared. The same in a subfolder is caught: it moves the subfolder's own mtime and ctime.
- The claim covers state left on disk by the session and its hooks once they have exited. A process a hook
  leaves running in the background is outside it: its writes during or after the after snapshot may not be
  seen, a change to an entry after the walk has passed it included. Short of that, a manifest rewritten
  after it was hashed is caught (the digest the probe holds), and so is a file replaced while it was being
  hashed.
- Writes under `.claude`, and to the `.claude.json` files at the top of the target home, still pass: they
  are Claude's own state and are not walked.

Exit codes: 0 every counted line PASS; 1 one or more FAIL and no UNVERIFIED; 2
one or more UNVERIFIED (it wins over FAIL); 3 usage error or refused
`PROBE_HOME`. The output and the exit codes are pinned by
`scripts/tests/test_probe_default_tier_load.py`, which runs the script against
a stubbed `claude` and `git` and touches no real account.

## Origin

This repository was carved without history from one commit of the repository
the files were first written in. [VENDORING.md](VENDORING.md) holds the carve
record and every pin move since.

Security reports: see [SECURITY.md](SECURITY.md).

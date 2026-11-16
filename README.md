# Harness core

Running Claude Code on many projects gets repetitive. Every project needs the
same rules for security, review and deploys. Every session has to find its
configuration and work out where things stand. Done by hand, that upkeep grows
until it is the job.

This repository is the part of one person's harness that handles it. That
harness grew over months of daily use. The core is the piece carved out of it so
that other people can, in time, install a default tier into their own Claude
Code setup in a safe and controlled way. That install path is not built yet.
This repository is the first step: the shared files, kept separate from
everything that belongs to one person.

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
| `scripts/`, `install-profiles/` | two maintenance scripts and an install profile |
| `skills/_lib/tests/`, `skills/_lib/recall/tests/`, `scripts/tests/` | the tests of the vendored modules |

## How it is used: vendored, byte for byte

Nothing here is installed on its own. A consuming repository vendors these
files: it copies each one, byte for byte and at the same relative path, from a
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
| B2 | does a plugin option reach a general-purpose subagent's Bash commands (the verdict for B) | the subagent's `echo` showed the value set with `claude plugin configure --values-stdin`, with no Bash run by the main thread and exactly one `Agent` call and one non-error result, as for A2; the exact value seen is printed. The value is read from the subagent's own Bash result, never from the `Agent` call's reply (the main thread could have run the command itself and passed the value on): the `Agent` call must have exactly one `Bash` call of its own, a call whose parent is that `Agent` call, and exactly one non-error result for it. No such call, more than one, or no non-error result for it is `UNVERIFIED` and the line shows the counts; a Bash result without the value is a `FAIL` |
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

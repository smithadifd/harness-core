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
| A2 | ... and a general-purpose subagent | the main thread's own `Agent` call to one foreground subagent of type exactly `general-purpose` came back with the sentinel, and the sentinel was not in the prompt it gave the subagent |
| A3 | ... and a session after compaction | one `claude` process was sent three messages (a seed turn that never held the sentinel, `/compact`, a question), the stream showed `compact_boundary` before the question's reply, and that reply held the sentinel. This proves the rules file's sentinel is still in that session's context after a compaction in that same session. It does not show whether Claude Code kept the file's text through the compaction or loaded it again, and it says nothing about a session resumed in a new process, which loads the rules file at start |
| B1 | does a plugin option reach the main thread's Bash commands (diagnostic, not counted) | the main thread's `echo` of the exported variable showed the option value |
| B2 | does a plugin option reach a general-purpose subagent's Bash commands (the verdict for B) | the subagent's `echo` showed the value set with `claude plugin configure --values-stdin`, with no Bash run by the main thread; the exact value seen is printed |
| C | are the ledger plugin's hooks silent before setup | with the pinned ledger plugin installed, no ledger value set and a call-recording `ledger` first on PATH, session start and one prompt each ran a hook of the `ledger-hooks` plugin that exited 0 with no output, the recorder was never called and no file was written outside Claude's own state. Only hook responses that name the `ledger-hooks` plugin count; responses from any other source are ignored, and with none that name it the line is `UNVERIFIED` |

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
  plugin, or hook responses that do not name the ledger plugin. It is never
  counted as a pass.

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

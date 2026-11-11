# Harness core

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

## Origin

This repository was carved without history from one commit of the repository
the files were first written in. [VENDORING.md](VENDORING.md) holds the carve
record and every pin move since.

Security reports: see [SECURITY.md](SECURITY.md).

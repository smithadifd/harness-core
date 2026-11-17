# An instance: plans and decisions in markdown

An instance is one person's or one context's source of truth for plans and decisions, kept as
markdown in a plain git repository. It needs no remote and no service. The harness setup made this
folder from a template and ran `git init` in it; from then on it is yours. Setup records nothing
about it, and `doctor` and `uninstall` never touch it.

## What goes where

| Markdown, in this folder (knowledge) | Ledger (work) |
|---|---|
| `roadmaps/`: plans and ordering | Items: topics, tickets, initiatives, decisions, gates |
| `decisions/`: the rationale behind a decision: context, options, consequences | Each item's state (open, claimed, blocked, closed), and the outcome of a decision |
| `standards/`: local overrides of the harness standards | Notes, links and elections recorded against an item |

Authority is by field, and no fact has two owners: the ledger owns live item state, decision
outcomes and other structured facts; markdown owns rationale, standards and curated knowledge.
A decision's outcome is recorded on its ledger item; the reasoning behind it goes in `decisions/`.

## Where the ledger is

Not here: this folder holds markdown only. The ledger keeps its database under your local share
folder, at `.local/share/ledger/ledger.db` below your home folder. That is the path setup stores in
the ledger plugin's `ledger_db` option, and `/harness:setup doctor` checks it. Nothing in this
folder points at the database and no settings file needs editing for a session to find it.

## Looking after the folder

- Setup made the repository but made no commit. Commit when you choose, with your own git identity:
  `git add -A`, then `git commit`.
- No remote is needed; adding one later is optional.
- Back up the ledger with `ledger backup` (SQLite's online backup), never a file copy of the
  database, and keep the database, its WAL and its SHM files out of any folder a file-sync
  service copies. This folder holds no database, so a copy of it carries none.
- Each subfolder starts with a README that says what belongs there. Delete it once the folder has
  real content.

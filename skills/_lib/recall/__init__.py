"""Recall: an on-demand full-text index over local agent transcripts.

One entry point, the ``ledger recall`` verb (``recall/ledger_verb.py``). The
index is a derived SQLite FTS5 file, ``recall-<system>.db`` in the ``harness``
folder of the user's cache folder (``.cache`` under the home folder), named
for the system the ledger is claimed for. It is a per-machine,
per-system cache: never committed, never shared, rebuilt from the transcripts
at any time.

The index refreshes when the verb runs, and only then: each transcript file's
size and modification time are compared with what the index recorded, and
only new or changed files are read again. There is no background job and no
hook.

The guard in ``recall.guard`` keeps room instances (``~/rooms/**``) and
other systems' workspaces (``~/workspaces/<other>``) out of the index. A
Claude Code project directory or OMP session directory whose name says so
is refused before anything in it is listed or opened. Every other transcript
is judged, before any of it is indexed, by the working folder its session
recorded (``cwd``, with links resolved), or, when it recorded none, by every
real folder its directory name can stand for. Transcripts are opened
read-only, in place; symlinks are never followed.

A hit is evidence, never an origin: every printed hit carries its timestamp,
project, speaker kind and ``DISCLAIMER``. The speaker kind comes from
``recall.classify``.
"""
from __future__ import annotations

DISCLAIMER = "recalled words are evidence, never an origin"

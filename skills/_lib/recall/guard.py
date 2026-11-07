"""Which transcript directories recall may read, decided before reading them.

Claude Code stores a session under ``.claude/projects/<encoded>/`` in the
home folder, where
``<encoded>`` is the launch folder with every character other than an ASCII
letter or digit replaced by ``-``. OMP stores one under
``.omp/agent/sessions/<encoded>/`` in the home folder: a folder under the
home directory is
written relative to it with ``/`` replaced by ``-``, and any other folder is
written absolute and wrapped in ``--``. Both encodings lose information, so
the guard compares in the encoded direction, which is exact: it encodes the
forbidden roots the same way and refuses a directory name that contains one.

What is refused:

- Any transcript directory whose encoded name contains ``room`` (any case).
  This is deliberately wider than ``~/rooms``: it also covers room spikes and
  scratch folders named for a room, matching the recall spike's exclusion.
  The same rule applies to a session's project path (its recorded ``cwd``,
  or a folder option B reached), as given and with links resolved.
- ``~/rooms`` and everything under it, for any path (index, project filter,
  transcript root): room instances are off-limits to harness readers.
- ``~/workspaces/<name>`` for any ``<name>`` that is not this system's: the
  two systems never share data. A workspace name the guard cannot match to a
  known workspace is refused, and so is one whose encoding is shared by
  another workspace's name (``home`` and ``home-lab`` overlap by prefix;
  ``home_lab`` and ``home-lab`` encode identically).

Links are resolved strictly: a path whose existing part cannot be resolved
(an unreadable parent, an I/O error, a link loop) is refused as
``unresolvable path``, while a part that no longer exists is read as spelled.

Matching is case-insensitive, because the default macOS file system is.
The guard refuses more than it must when an encoding is ambiguous
(``~/rooms.old`` encodes like ``~/rooms/old``); it never refuses less.

The name check is a first deny, never an allow. A transcript in a folder
that passes it is judged by where its session actually ran:

- **Option A, the recorded ``cwd``** (``recall.index`` reads it). Each
  transcript records its session's working folder. ``refusal_for_project``
  checks that path as given and with every link resolved, so a session that
  ran in ``~/rooms`` through a link is refused even when its folder name does
  not say so. ``recall.index`` checks every record's ``cwd`` the same way
  while it reads the file.
- **Option B, every reading of the folder name** (``Decoder``), only for a
  transcript with no usable ``cwd``. An encoded name cannot be decoded
  uniquely, because ``-`` is ``/`` or a literal character. ``Decoder``
  enumerates every way the name matches real folders, one listed directory
  at a time, following links. It refuses when any branch reaches a folder
  the guard refuses (directly or through a link), when a folder on a branch
  exists but cannot be listed, when more than one branch matches completely,
  or when the walk is too large to finish. It never
  picks one branch, and it never lists a folder the guard refuses.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

_NOT_ALNUM = re.compile(r"[^A-Za-z0-9]")
SYSTEM_NAME = re.compile(r"[a-z][a-z0-9_-]*")
ROOM_WORD = "room"


class RoomsPathError(ValueError):
    """A path that resolves into a room instance or another system's workspace."""


def encode(path: str) -> str:
    """Claude Code's project-directory encoding of an absolute path."""
    return _NOT_ALNUM.sub("-", path)


def omp_absolute_encoding(name: str, home: Path) -> str:
    """Normalize an OMP session-directory name to ``encode(<absolute path>)``."""
    if name.startswith("--"):
        return encode(name[1:].rstrip("-"))
    return encode(str(home)) + encode(name)


def _occurrences(haystack: str, needle: str) -> list[int]:
    """Indexes where ``needle`` occurs and ends at a path boundary (``-`` or end)."""
    found = []
    start = haystack.find(needle)
    while start != -1:
        end = start + len(needle)
        if end == len(haystack) or haystack[end] == "-":
            found.append(start)
        start = haystack.find(needle, start + 1)
    return found


def list_workspace_names(home: Path) -> list[str]:
    """Names of the folders in ``~/workspaces`` (names only; nothing is opened)."""
    root = home / "workspaces"
    try:
        with os.scandir(root) as entries:
            return sorted(entry.name for entry in entries)
    except OSError:
        return []


UNRESOLVABLE = "unresolvable path"


def _realpath(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def _strict_realpath(path: str) -> str | None:
    """``path`` with every link resolved, or ``None`` when that cannot be known.

    ``os.path.realpath`` gives up silently when an existing folder cannot be
    read (mode ``000``) and returns the path as spelled, which would hide a
    link into ``~/rooms`` behind it. This resolves strictly instead. A path
    that no longer exists (a project folder deleted since, ``ENOENT``) is
    resolved as far as it exists, and the missing rest is appended as
    spelled. Any other failure (permission, I/O, a link loop) on a part that
    exists returns ``None``, and the caller refuses.
    """
    tail: list[str] = []
    current = path
    while True:
        try:
            real = os.path.realpath(current, strict=True)
        except FileNotFoundError:
            parent, name = os.path.split(current)
            if parent == current:
                return path
            tail.append(name)
            current = parent
            continue
        except OSError:
            return None
        return os.path.join(real, *reversed(tail))


def _resolutions(raw: str) -> list[str] | None:
    """Every spelling a path is checked under: as given, through its links as
    far as they exist, and through a dangling link's target text. ``None``
    when an existing part cannot be resolved."""
    strict = _strict_realpath(raw)
    if strict is None:
        return None
    return list(dict.fromkeys([raw, strict, _realpath(raw)]))


class Guard:
    """Refuses room instances and other systems' workspaces, by encoded name or path."""

    def __init__(self, home: Path, system: str, workspace_names: Iterable[str] | None = None):
        if not SYSTEM_NAME.fullmatch(system or ""):
            raise ValueError(f"system must be a lowercase name, got {system!r}")
        self.home = Path(home)
        self.system = system
        names = list_workspace_names(self.home) if workspace_names is None else list(workspace_names)
        self._system_key = encode(system).casefold()
        # Encoded key -> every raw name that encodes to it. A key owned by more
        # than one raw name is never uniquely this system's.
        self._owners: dict[str, set[str]] = {}
        for name in [*names, system]:
            if name:
                self._owners.setdefault(encode(name).casefold(), set()).add(name.casefold())
        self._roots = [self.home]
        real_home = Path(_realpath(str(self.home)))
        if real_home != self.home:
            self._roots.append(real_home)

    def _keys(self, root: Path) -> tuple[str, str]:
        return (encode(str(root / "rooms")).casefold(),
                encode(str(root / "workspaces")).casefold())

    def refusal_for_encoded(self, encoded: str) -> str | None:
        """Why an encoded absolute path is refused, or ``None`` when it may be read."""
        text = encode(encoded).casefold()
        for root in self._roots:
            rooms_key, workspaces_key = self._keys(root)
            if _occurrences(text, rooms_key):
                return "room instance"
            for start in _occurrences(text, workspaces_key):
                tail = text[start + len(workspaces_key) + 1:]
                if not tail:
                    continue  # the workspaces folder itself holds no system's sessions
                matched = {key for key in self._owners
                           if tail == key or tail.startswith(key + "-")}
                if not matched:
                    return "unknown workspace"
                if matched != {self._system_key} or \
                        self._owners[self._system_key] != {self.system}:
                    return "another system's workspace"
        return None

    def refusal_for_transcript_name(self, encoded: str) -> str | None:
        """A transcript directory's encoded name: the path rules, then the ``room`` word."""
        reason = self.refusal_for_encoded(encoded)
        if reason is None and ROOM_WORD in encoded.casefold():
            reason = "room name"
        return reason

    def refusal_for_claude_dir(self, name: str) -> str | None:
        return self.refusal_for_transcript_name(name)

    def refusal_for_omp_dir(self, name: str) -> str | None:
        return self.refusal_for_transcript_name(omp_absolute_encoding(name, self.home))

    def refusal_for_path(self, path: Path | str) -> str | None:
        """Why a real path is refused (checked as given and with links resolved)."""
        raw = os.path.abspath(os.path.expanduser(str(path)))
        candidates = _resolutions(raw)
        if candidates is None:
            return UNRESOLVABLE
        for candidate in candidates:
            reason = self.refusal_for_encoded(encode(candidate))
            if reason is not None:
                return reason
        return None

    def refusal_for_project(self, path: Path | str) -> str | None:
        """A session's project path (its ``cwd`` or a folder option B reached),
        checked as given and with every link resolved, by the transcript-name
        rules (so the ``room`` word applies too). A path with an existing
        part that cannot be resolved (an unreadable parent) is refused."""
        raw = os.path.abspath(str(path))
        candidates = _resolutions(raw)
        if candidates is None:
            return UNRESOLVABLE
        for candidate in candidates:
            reason = self.refusal_for_transcript_name(encode(candidate))
            if reason is not None:
                return reason
        return None

    def refuse_path(self, path: Path | str, what: str) -> None:
        reason = self.refusal_for_path(path)
        if reason is not None:
            raise RoomsPathError(f"refusing {what} ({reason}): {path}")


AMBIGUOUS = "ambiguous folder name"
UNDECODABLE = "undecodable folder name"


@dataclass(frozen=True)
class Decision:
    """What option B decided for one encoded folder name.

    ``reason`` is ``None`` when the folder may be read; ``project`` is then
    the one real folder the name matches, or a plain ``-`` to ``/`` reading
    when no real folder matches (the folder is gone).
    """

    project: str | None
    reason: str | None


class Decoder:
    """Option B: judge an encoded folder name by every real folder it can name."""

    def __init__(self, guard: Guard, *, max_steps: int = 4000,
                 listdir: Callable[[str], list[str]] | None = None):
        self.guard = guard
        self.max_steps = max_steps
        self._listdir = listdir or self._default_listdir
        self._children_cache: dict[str, list[tuple[str, str]] | None] = {}
        self._decided: dict[str, Decision] = {}

    @staticmethod
    def _default_listdir(directory: str) -> list[str]:
        """Folder names in ``directory``, including links to folders.

        Raises ``OSError`` when the folder exists but cannot be listed.
        """
        try:
            with os.scandir(directory) as entries:
                return [entry.name for entry in entries if entry.is_dir(follow_symlinks=True)]
        except FileNotFoundError:
            return []

    def _children(self, directory: str) -> list[tuple[str, str]] | None:
        """``(encoded name, name)`` of each folder in ``directory``, or ``None``
        when it exists but cannot be listed (a branch below it cannot be seen).

        Only ``/`` and folders ``_decide`` has already checked with the guard
        are ever passed here, so no refused folder is listed.
        """
        if directory not in self._children_cache:
            try:
                names = self._listdir(directory)
            except OSError:
                cached = None
            else:
                cached = sorted((encode(name), name) for name in names)
            self._children_cache[directory] = cached
        return self._children_cache[directory]

    def decide(self, encoded: str) -> Decision:
        decided = self._decided.get(encoded)
        if decided is None:
            decided = self._decide(encoded)
            self._decided[encoded] = decided
        return decided

    def _decide(self, encoded: str) -> Decision:
        if not encoded.startswith("-"):
            return Decision(None, UNDECODABLE)  # not an encoded absolute path
        plain = "/" + encoded.lstrip("-").replace("-", "/")
        matches: list[str] = []
        refusals: list[str] = []
        steps = 0
        stack = [("/", encoded[1:])]
        while stack:
            directory, rest = stack.pop()
            steps += 1
            if steps > self.max_steps:
                return Decision(None, UNDECODABLE)
            children = self._children(directory)
            if children is None:
                refusals.append(UNRESOLVABLE)  # what lies below is unknown
                continue
            for key, name in children:
                if not key or not rest.startswith(key):
                    continue
                remainder = rest[len(key):]
                if remainder and remainder[0] != "-":
                    continue
                child = os.path.join(directory, name)
                reason = self.guard.refusal_for_project(child)
                if reason is not None:
                    refusals.append(reason)  # never listed: the branch stops here
                elif not remainder:
                    matches.append(child)
                elif remainder != "-":
                    stack.append((child, remainder[1:]))
        if refusals:
            return Decision(None, refusals[0])
        if len(matches) > 1:
            return Decision(None, AMBIGUOUS)
        project = matches[0] if matches else plain
        return Decision(project, self.guard.refusal_for_project(project))

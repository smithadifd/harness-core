#!/usr/bin/env python3
"""Fail when skills/_lib/ledger is not the pinned copy of the ledger repository.

skills/_lib/ledger is vendored: its bytes are the ``ledger/`` tree of the
ledger repository at the commit ledger-vendor.pin names, and nothing else. It
is never edited here. A change lands in the ledger repository first and comes
back as one pull request made by scripts/ledger-vendor-bump.py (the pin plus
the re-copied files). Two checks hold it there, and each catches a
different edit:

- offline (no option) compares the files under the folder with the manifest
  in ledger-vendor.pin. It runs in the lint job of skills-ci.yml and in the
  daily wave's pre-flight. It catches a vendored file edited, removed, added
  to the index, re-moded or symlinked WITHOUT its manifest line, and any
  untracked file Python could import beside it; it cannot catch a file
  edited together with its manifest line, since the manifest is then what
  the files agree with.
- ``--source <ledger checkout>`` also compares the manifest with the ledger
  repository itself at the pinned commit, and checks that the pinned commit
  is the pinned tag and is on the ledger's main. It runs in CI, against the
  pinned commit, tag and main fetched from the public ledger repository with
  no credentials, and is the check that catches a file and its manifest line
  edited together, or a pin moved to a commit the ledger never merged.

Usage: check-ledger-vendor.py [--source LEDGER_CHECKOUT]

The check always reads the repository that holds this script (the folder
above ``scripts/``); there is no option to point it at another tree.

Offline, it reports these kinds:

  changed  a manifest path whose git blob id, computed from the bytes on disk
           the way ``git hash-object`` does (sha1 over ``blob <len>\\0`` and
           the bytes), differs from the manifest's;
  missing  a manifest path with no file on disk;
  extra    a path in the index (a tracked file) under the folder that the
           manifest does not list, whether or not it is on disk;
  mode     a manifest path whose index entry is not ``100644`` at stage 0
           (including one that is not in the index at all), or whose file on
           disk is executable;
  symlink  a symlink anywhere under the folder, tracked or not, or the
           folder itself;
  untracked
           an untracked entry under the folder that the manifest does not
           list and the allow-list below does not cover: any file (a ``.py``,
           ``.pth``, ``.pyc`` or ``.so`` file, a FIFO), or an empty folder.
           Python would import such a file beside the vendored modules, and a
           package folder (``paths/__init__.py``) shadows a vendored module
           (``paths.py``) of the same name.

Untracked entries follow a fixed allow-list, and everything off it fails as
``untracked``:

  - a file named exactly ``.DS_Store``, in any folder, prints a warning on
    stderr and does not fail;
  - anything under a folder named exactly ``.pytest_cache`` (the folder
    itself when empty) prints a warning on stderr and does not fail;
  - anything under a folder named exactly ``__pycache__`` is ignored
    silently, because the interpreter writes it next to the code it imports
    (it never imports a module from there without the source beside it).

The allow-list matches names exactly (``.DS_Store.py`` or ``.pytest_cache.py``
fails), and covers untracked entries only: a tracked file the manifest does
not list is ``extra`` whatever its name, and a symlink is ``symlink``
wherever it is. Every hash is taken from the file on disk at run time;
nothing is cached.

``--source`` reads the checkout at use time: it must hold the pin's commit,
and ``git ls-tree -r <sha> ledger/`` of that commit must equal the manifest
exactly (kinds ``changed``, ``missing``, ``extra`` and ``mode`` again, printed
with ``pin manifest`` before the kind). Two refs of the checkout, resolved at
use time, must agree with the pin; each failure is drift (exit 1):

  pin tag  ``refs/tags/<tag>`` must exist and dereference to the pin's sha;
  pin sha  ``refs/remotes/origin/main`` (the ledger's main as last fetched;
           CI fetches it fresh for every run) must exist, and the pin's sha
           must be an ancestor of it (``git merge-base --is-ancestor``), so
           a commit GitHub serves only from a pull request ref is refused.

Exit 0 clean; 1 drift, one line per differing path or ref naming its kind; 2
usage, or an input the check cannot read: a missing or malformed pin (the
grammar is in the pin's own header and in ``parse_pin`` below; a pin with no
``tag`` line is malformed), an unreadable file, git unavailable, the folder
not inside this repository's work tree, or a source checkout without the
pin's commit. Exit 2 is never a pass: a caller that gates on this check
treats every non-zero exit as a refusal.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PIN_NAME = "ledger-vendor.pin"
VENDORED = "skills/_lib/ledger"
SOURCE_PREFIX = "ledger/"
PREFIX = "ledger vendor:"
# The ledger's main in a source checkout: a normal clone's remote-tracking
# ref, and the ref CI fetches refs/heads/main into.
LEDGER_MAIN_REF = "refs/remotes/origin/main"
# The untracked names that warn instead of failing (see the docstring).
UNTRACKED_WARN_FILE = ".DS_Store"
UNTRACKED_WARN_FOLDER = ".pytest_cache"
SILENT_FOLDER = "__pycache__"

_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
_SHA = re.compile(r"[0-9a-f]{40}")
_TAG = re.compile(r"[A-Za-z0-9._-]+")
_COMPONENT = re.compile(r"[A-Za-z0-9._-]+")
_MANIFEST_LINE = re.compile(r"([0-9a-f]{40}) (\S+)")


class PinError(ValueError):
    """The pin cannot be read or does not follow the grammar (exit 2)."""


class CheckError(RuntimeError):
    """An input the check needs cannot be read (exit 2)."""


@dataclass(frozen=True)
class Pin:
    repo: str
    sha: str
    manifest: dict[str, str]
    tag: str


def path_problem(path: str) -> str | None:
    """Why ``path`` is not a valid manifest path, or None when it is."""
    if path.startswith("/"):
        return "has a leading slash"
    parts = path.split("/")
    for part in parts:
        if part == "":
            return "has an empty component"
        if part in {".", ".."}:
            return f"has a {part!r} component"
        if not _COMPONENT.fullmatch(part):
            return "has a character outside [A-Za-z0-9._-]"
        if part == "__pycache__":
            return "has a __pycache__ component"
    if path.endswith(".pyc"):
        return "names a .pyc file"
    return None


def parse_pin(data: bytes) -> Pin:
    """Parse ledger-vendor.pin's bytes, refusing anything off the grammar.

    Grammar: UTF-8 text with LF line endings only, ending in a newline. A
    header of ``#`` comment lines and empty lines, in which exactly one
    ``repo <owner>/<name>`` line appears and then exactly one
    ``sha <40 lowercase hex>`` line, plus exactly one required
    ``tag <name>`` line (``[A-Za-z0-9._-]+``) anywhere among those key lines
    before the manifest. After the sha line, comment and empty
    lines may still appear until the first manifest line; from then on every
    line is a manifest line ``<40 lowercase hex> <path>`` with one space,
    where <path> is relative to ``ledger/`` and passes ``path_problem``. At
    least one manifest line; paths sorted ascending by byte value with no
    duplicates.
    """
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PinError(f"pin is not UTF-8 text ({exc.reason})") from exc
    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        raise PinError(f"line {line}: carriage return (CRLF line endings are refused)")
    if not text:
        raise PinError("pin is empty")
    if not text.endswith("\n"):
        raise PinError("pin does not end with a newline")
    repo: str | None = None
    sha: str | None = None
    tag: str | None = None
    manifest: dict[str, str] = {}
    previous: str | None = None
    for number, line in enumerate(text[:-1].split("\n"), 1):
        if manifest:
            if line == "" or line.startswith("#"):
                raise PinError(f"line {number}: comment or empty line inside the manifest")
        elif line == "" or line.startswith("#"):
            continue
        if line.startswith("tag ") or line == "tag":
            if tag is not None:
                raise PinError(f"line {number}: duplicate tag line")
            if manifest:
                raise PinError(f"line {number}: tag line inside the manifest")
            value = line[len("tag "):]
            if not _TAG.fullmatch(value):
                raise PinError(f"line {number}: tag is not a name of [A-Za-z0-9._-]")
            tag = value
            continue
        if line.startswith("repo ") or line == "repo":
            if repo is not None:
                raise PinError(f"line {number}: duplicate repo line")
            value = line[len("repo "):]
            if not _REPO.fullmatch(value):
                raise PinError(f"line {number}: repo is not <owner>/<name>")
            repo = value
            continue
        if line.startswith("sha ") or line == "sha":
            if sha is not None:
                raise PinError(f"line {number}: duplicate sha line")
            if repo is None:
                raise PinError(f"line {number}: sha line before the repo line")
            value = line[len("sha "):]
            if not _SHA.fullmatch(value):
                raise PinError(f"line {number}: sha is not 40 lowercase hex characters")
            sha = value
            continue
        match = _MANIFEST_LINE.fullmatch(line)
        if match is None:
            raise PinError(
                f"line {number}: not a comment, repo, sha or "
                "'<40 lowercase hex> <path>' manifest line"
            )
        if repo is None or sha is None:
            raise PinError(f"line {number}: manifest line before the repo and sha lines")
        blob, path = match.groups()
        problem = path_problem(path)
        if problem is not None:
            raise PinError(f"line {number}: manifest path {path!r} {problem}")
        if previous is not None:
            if path == previous:
                raise PinError(f"line {number}: duplicate manifest path {path}")
            if path.encode("utf-8") < previous.encode("utf-8"):
                raise PinError(f"line {number}: manifest is not sorted ({path} after {previous})")
        manifest[path] = blob
        previous = path
    if repo is None:
        raise PinError("no repo line")
    if sha is None:
        raise PinError("no sha line")
    if tag is None:
        raise PinError("no tag line (the pin requires exactly one 'tag <name>' line)")
    if not manifest:
        raise PinError("no manifest lines")
    return Pin(repo, sha, manifest, tag)


def read_pin(root: Path) -> Pin:
    path = root / PIN_NAME
    try:
        if path.is_symlink():
            raise PinError(f"{PIN_NAME} is a symlink")
        data = path.read_bytes()
    except OSError as exc:
        raise PinError(f"cannot read {PIN_NAME}: {exc.strerror or exc}") from exc
    return parse_pin(data)


def blob_id(data: bytes) -> str:
    """The git blob id of ``data``, as ``git hash-object`` computes it."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def _run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError as exc:
        raise CheckError(f"git is not available: {exc.strerror or exc}") from exc


def resolve_commit(checkout: Path, revision: str) -> str | None:
    """The full sha ``revision`` names in ``checkout``, or None when it has no such commit."""
    result = _run_git(
        ["rev-parse", "--verify", "--quiet", "--end-of-options", f"{revision}^{{commit}}"],
        checkout,
    )
    value = result.stdout.decode("ascii", "replace").strip()
    return value if result.returncode == 0 and _SHA.fullmatch(value) else None


def _git(args: list[str], cwd: Path) -> bytes:
    result = _run_git(args, cwd)
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CheckError(
            f"git {' '.join(args)} failed in {cwd}: {detail[-1] if detail else result.returncode}"
        )
    return result.stdout


def _index_entries(root: Path) -> dict[str, list[tuple[str, str]]]:
    """Index entries under the vendored folder: path -> [(mode, stage)]."""
    top = _git(["rev-parse", "--show-toplevel"], root).decode("utf-8", "replace").strip()
    if Path(top).resolve() != root.resolve():
        raise CheckError(f"{root} is not the top of a git work tree (git reports {top})")
    output = _git(["ls-files", "-s", "-z", "--", VENDORED], root)
    entries: dict[str, list[tuple[str, str]]] = {}
    prefix = VENDORED + "/"
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split()
        path = os.fsdecode(name)
        if len(fields) != 3 or not path.startswith(prefix):
            raise CheckError(f"cannot read git ls-files entry {record!r}")
        entries.setdefault(path[len(prefix):], []).append((fields[0], fields[2]))
    return entries


def _untracked_warns(relative: str, *, folder: bool) -> bool:
    """Whether an untracked path (relative to the vendored folder) is on the
    allow-list that warns instead of failing: a file named exactly
    ``.DS_Store``, or anything under a folder named exactly ``.pytest_cache``
    (an empty folder of that name included). ``folder`` says the path is an
    empty folder rather than a file."""
    parts = relative.split("/")
    if not folder and parts[-1] == UNTRACKED_WARN_FILE:
        return True
    return UNTRACKED_WARN_FOLDER in (parts if folder else parts[:-1])


def offline_drift(root: Path, pin: Pin) -> tuple[list[tuple[str, str]], list[str]]:
    """(kind, repository path) for every way the folder differs from the pin,
    and the repository paths of untracked entries on the warning allow-list
    (warnings, not drift)."""
    folder = root / VENDORED
    drift: set[tuple[str, str]] = set()
    on_disk: set[str] = set()
    other: set[str] = set()
    empty: set[str] = set()
    if folder.is_symlink():
        return [("symlink", VENDORED)], []
    if folder.exists() and not folder.is_dir():
        drift.add(("extra", VENDORED))
    elif folder.is_dir():
        def walk_error(exc: OSError) -> None:
            raise CheckError(f"cannot list {exc.filename}: {exc.strerror or exc}")

        for directory, dirnames, filenames in os.walk(folder, onerror=walk_error):
            base = Path(directory)
            relative_dir = base.relative_to(folder).as_posix()
            kept: list[str] = []
            for name in dirnames:
                relative = name if relative_dir == "." else f"{relative_dir}/{name}"
                if (base / name).is_symlink():
                    drift.add(("symlink", f"{VENDORED}/{relative}"))
                elif name != SILENT_FOLDER:
                    kept.append(name)
            if not dirnames and not filenames and relative_dir != ".":
                empty.add(relative_dir)
            dirnames[:] = kept
            for name in filenames:
                relative = name if relative_dir == "." else f"{relative_dir}/{name}"
                try:
                    info = (base / name).lstat()
                except OSError as exc:
                    raise CheckError(f"cannot stat {VENDORED}/{relative}: {exc.strerror}") from exc
                if stat.S_ISLNK(info.st_mode):
                    drift.add(("symlink", f"{VENDORED}/{relative}"))
                    continue
                if not stat.S_ISREG(info.st_mode):
                    other.add(relative)
                    continue
                on_disk.add(relative)
    index = _index_entries(root)
    symlinked = {path for kind, path in drift if kind == "symlink"}
    for path, blob in sorted(pin.manifest.items()):
        name = f"{VENDORED}/{path}"
        if name in symlinked:
            continue
        if path not in on_disk:
            drift.add(("missing", name))
            if path not in index:
                continue
        else:
            file = folder / path
            try:
                data = file.read_bytes()
                executable = bool(file.lstat().st_mode & 0o111)
            except OSError as exc:
                raise CheckError(f"cannot read {name}: {exc.strerror or exc}") from exc
            if blob_id(data) != blob:
                drift.add(("changed", name))
            if executable:
                drift.add(("mode", name))
        if index.get(path) != [("100644", "0")]:
            if any(mode == "120000" for mode, _ in index.get(path, [])):
                drift.add(("symlink", name))
            else:
                drift.add(("mode", name))
    warned: list[str] = []
    for path in sorted((on_disk | other | empty | set(index)) - set(pin.manifest)):
        name = f"{VENDORED}/{path}"
        if name in symlinked:
            continue
        if path in index:
            drift.add(("extra", name))
        elif _untracked_warns(path, folder=path in empty):
            warned.append(name)
        else:
            drift.add(("untracked", name))
    return sorted(drift, key=lambda item: (item[1], item[0])), warned


def source_drift(source: Path, pin: Pin) -> list[tuple[str, str]]:
    """(kind, ledger path) for every way the pinned commit differs from the manifest."""
    if not source.is_dir():
        raise CheckError(f"source checkout {source} is not a directory")
    if resolve_commit(source, pin.sha) != pin.sha:
        raise CheckError(f"source checkout {source} does not have the pinned commit {pin.sha}")
    output = _git(["ls-tree", "-r", "-z", "--full-tree", pin.sha, "--", SOURCE_PREFIX], source)
    tree: dict[str, tuple[str, str, str]] = {}
    for record in output.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split()
        path = os.fsdecode(name)
        if len(fields) != 3 or not path.startswith(SOURCE_PREFIX):
            raise CheckError(f"cannot read git ls-tree entry {record!r}")
        tree[path[len(SOURCE_PREFIX):]] = (fields[0], fields[1], fields[2])
    drift: set[tuple[str, str]] = set()
    for path, (mode, kind, blob) in tree.items():
        if mode != "100644" or kind != "blob":
            drift.add(("mode", SOURCE_PREFIX + path))
        if path not in pin.manifest:
            drift.add(("missing", SOURCE_PREFIX + path))
        elif pin.manifest[path] != blob:
            drift.add(("changed", SOURCE_PREFIX + path))
    for path in set(pin.manifest) - set(tree):
        drift.add(("extra", SOURCE_PREFIX + path))
    tagged = resolve_commit(source, f"refs/tags/{pin.tag}")
    if tagged is None:
        drift.add(("tag", f"{pin.tag} is not a tag (refs/tags/{pin.tag}) in the source checkout"))
    elif tagged != pin.sha:
        drift.add(("tag", f"{pin.tag} resolves to {tagged}, not the pin sha {pin.sha}"))
    main = resolve_commit(source, LEDGER_MAIN_REF)
    if main is None:
        drift.add(("main", f"{pin.sha}: the source checkout has no {LEDGER_MAIN_REF}"))
    else:
        result = _run_git(["merge-base", "--is-ancestor", pin.sha, main], source)
        if result.returncode == 1:
            drift.add(
                ("main", f"{pin.sha} is not on the ledger's main ({LEDGER_MAIN_REF} is {main})")
            )
        elif result.returncode != 0:
            detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise CheckError(
                f"git merge-base --is-ancestor failed in {source}: "
                f"{detail[-1] if detail else result.returncode}"
            )
    return sorted(drift, key=lambda item: (item[1], item[0]))


def run(source: Path | None) -> int:
    root = REPO_ROOT
    try:
        pin = read_pin(root)
    except PinError as exc:
        print(f"{PREFIX} ERROR: {PIN_NAME}: {exc}", file=sys.stderr)
        return 2
    try:
        drift, warned = offline_drift(root, pin)
        pinned = source_drift(source, pin) if source is not None else []
    except CheckError as exc:
        print(f"{PREFIX} ERROR: {exc}", file=sys.stderr)
        return 2
    for path in warned:
        print(
            f"{PREFIX} warning: untracked {path} is not a vendored file; "
            "it does not fail the check, remove it",
            file=sys.stderr,
        )
    for kind, path in drift:
        print(f"{PREFIX} drift: {kind} {path}")
    for kind, path in pinned:
        label = {"tag": "pin tag", "main": "pin sha"}.get(kind, f"pin manifest {kind}")
        print(f"{PREFIX} drift: {label} {path}")
    if drift or pinned:
        print(
            f"{PREFIX} FAIL: {len(drift) + len(pinned)} drifted path(s) against "
            f"{pin.repo}@{pin.sha}; never edit {VENDORED} by hand, bump the pin with "
            "scripts/ledger-vendor-bump.py"
        )
        return 1
    checked = (
        f" and the source checkout's tree, tag and {LEDGER_MAIN_REF}"
        if source is not None
        else ""
    )
    print(
        f"{PREFIX} ok: {VENDORED} matches {PIN_NAME}{checked} "
        f"({pin.repo}@{pin.sha} tag {pin.tag}, {len(pin.manifest)} files)"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check skills/_lib/ledger against ledger-vendor.pin."
    )
    parser.add_argument(
        "--source",
        type=Path,
        help="a ledger repository checkout; also verify the manifest against its pinned commit",
    )
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    return run(args.source)


if __name__ == "__main__":
    sys.exit(main())

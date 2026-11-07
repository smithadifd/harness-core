#!/usr/bin/env python3
"""Move the vendored ledger to another commit of the ledger repository.

Usage: ledger-vendor-bump.py LEDGER_CHECKOUT COMMIT

COMMIT must be a tag of the ledger repository (``v0.1.0``), read as
``refs/tags/COMMIT``: the pin records it on its required ``tag`` line beside
the sha it resolves to. A sha, a branch or any other revision is refused
before anything is written.

skills/_lib/ledger is never edited in this repository. A ledger change lands
in the ledger repository first; this script then brings one commit of it here,
as one reviewable change:

  1. verifies LEDGER_CHECKOUT has the tag COMMIT, that every path under
     ``ledger/`` at that commit is a regular file (mode 100644) whose path the
     pin grammar accepts, and that no path component from ``skills`` down to
     and under skills/_lib/ledger is a symlink; otherwise it refuses before
     writing anything;
  2. writes exactly those files into skills/_lib/ledger, byte for byte from
     the commit's blobs (never from the checkout's working tree), mode 0644,
     and deletes every file the old pin listed or the index tracks under the
     folder that the commit lacks (then any folder left empty), never through
     a symlink. Each file is written to a uniquely named temporary file in
     its folder, created exclusively and set to mode 0644, then renamed over
     the target, so the bump never writes into an existing file (a vendored
     file hard-linked elsewhere keeps the other name's bytes); the temporary
     file is removed on every failure. Nothing outside skills/_lib/ledger and
     ledger-vendor.pin is written;
  3. rewrites ledger-vendor.pin: the repo line is kept, the tag, the sha and
     the manifest (``git ls-tree -r COMMIT ledger/``) are replaced. The new
     pin is written the same way, through a temporary file renamed into
     place;
  4. stages the written, deleted and modified paths of the folder and the pin
     (``git add``; untracked files it did not write are left unstaged) so the
     index modes are the ones the drift check reads. It never commits or
     pushes;
  5. runs ``scripts/check-ledger-vendor.py --source LEDGER_CHECKOUT``, which
     also requires the commit to be on the checkout's
     ``refs/remotes/origin/main`` (fetch the ledger first).

The script always works on the repository that holds it. Bumping to the
current pin rewrites identical bytes and leaves no diff.

Exit 0 when the drift check passes; 1 when it reports drift after the files
were written (the working tree is left as it is for review: an index entry
that git keeps at a non-100644 mode, as it does when core.fileMode is off, is
one cause); 2 on a usage error or a refusal before anything was written (no
such tag, a non-regular path at the commit, a malformed existing pin, a
symlink under skills/_lib/ledger or among its parents, git unavailable). If
writing fails part way, the script exits 2 and the working tree is left for
review; re-running it to the same commit restores a consistent copy. Nobody
else is expected to write the folder while it runs: it is frozen to this
script and the pin.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CHECK = REPO_ROOT / "scripts" / "check-ledger-vendor.py"
DEFAULT_REPO = "smithadifd/ledger"
PIN_HEADER = """\
# The pinned source of the vendored ledger. skills/_lib/ledger is a byte-for-byte
# copy of the ledger/ tree of `repo` at commit `sha`, and nothing else; the
# ledger repository's VENDORING.md defines that mapping. Never edit the copy
# here: a change lands in the ledger repository first and reaches this
# repository as one reviewed pull request (this file plus the copied files),
# made by
#   python3 scripts/ledger-vendor-bump.py <ledger checkout> <tag>
# which re-copies the tree, rewrites this file and runs the drift check.
#
# Read by scripts/check-ledger-vendor.py and written by
# scripts/ledger-vendor-bump.py. Two checks read it. Offline, the files under
# skills/_lib/ledger are hashed against the manifest's git blob ids (the lint
# job of .github/workflows/skills-ci.yml and the daily wave's pre-flight,
# launchd-kit/triage-wave-run.sh --wave). With --source, the manifest itself
# is compared with the ledger repository at `sha`, `tag` must resolve to `sha`
# there, and `sha` must be on the ledger's main (CI fetches the commit, the
# tag and main from the public repository without credentials).
#
# Grammar (anything else is refused with exit 2): UTF-8, LF line endings only,
# ending in a newline. `#` comment lines and empty lines may appear only
# before the first manifest line. Exactly one `repo <owner>/<name>` line, then
# exactly one `sha <40 lowercase hex>` line; exactly one `tag <name>` line
# ([A-Za-z0-9._-]+), required, anywhere among those key lines. Then one
# manifest line per vendored file:
# `<git blob id, 40 lowercase hex> <path>`, one space, where
# <path> is relative to ledger/ (no leading slash; no empty, `.` or `..`
# component; components of [A-Za-z0-9._-] only; no __pycache__ component; no
# .pyc file). At least one manifest line, sorted ascending by byte value,
# no duplicates. The manifest is `git ls-tree -r <sha> ledger/` of the commit.
"""


def _load_check():
    spec = importlib.util.spec_from_file_location("check_ledger_vendor", CHECK)
    if spec is None or spec.loader is None:
        raise SystemExit(f"ledger vendor bump: ERROR: cannot load {CHECK}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_ledger_vendor"] = module
    spec.loader.exec_module(module)
    return module


class BumpError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path) -> bytes:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, check=False)
    except OSError as exc:
        raise BumpError(f"git is not available: {exc.strerror or exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise BumpError(
            f"git {' '.join(args)} failed in {cwd}: {detail[-1] if detail else result.returncode}"
        )
    return result.stdout


def _symlink_between(folder: Path, target: Path) -> Path | None:
    """The first symlink among ``folder`` and the components from it down to
    ``target`` (``target`` included), read with lstat, or None."""
    current = folder
    if current.is_symlink():
        return current
    for part in target.relative_to(folder).parts:
        current = current / part
        if current.is_symlink():
            return current
    return None


def _replace_file(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` through an exclusively created, uniquely
    named temporary file in the same folder, set to mode 0644 and then
    renamed over ``target``; removed on any failure. The rename replaces the
    name, so an existing file's inode (one hard-linked elsewhere) is never
    written."""
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise


def _write_pin(pin_path: Path, rendered: bytes) -> None:
    """Write the pin through ``_replace_file``."""
    _replace_file(pin_path, rendered)


def render_pin(repo: str, sha: str, manifest: dict[str, str], tag: str) -> bytes:
    lines = [PIN_HEADER, f"repo {repo}\n", f"tag {tag}\n", f"sha {sha}\n"]
    for path in sorted(manifest, key=lambda name: name.encode("utf-8")):
        lines.append(f"{manifest[path]} {path}\n")
    return "".join(lines).encode("utf-8")


def bump(source: Path, commit: str) -> int:
    check = _load_check()
    folder = REPO_ROOT / check.VENDORED
    pin_path = REPO_ROOT / check.PIN_NAME
    # Refusals before anything is written.
    old_manifest: dict[str, str] = {}
    repo = DEFAULT_REPO
    if pin_path.exists() or pin_path.is_symlink():
        try:
            old = check.read_pin(REPO_ROOT)
        except check.PinError as exc:
            raise BumpError(f"{check.PIN_NAME}: {exc}; fix or remove it first") from exc
        repo = old.repo
        old_manifest = old.manifest
    if not source.is_dir():
        raise BumpError(f"ledger checkout {source} is not a directory")
    if commit.startswith("-") or not check._TAG.fullmatch(commit):
        raise BumpError(
            f"{commit!r} is not a tag name; the pin requires a tag, so bump to a tag "
            "of the ledger repository"
        )
    try:
        sha = check.resolve_commit(source, f"refs/tags/{commit}")
    except check.CheckError as exc:
        raise BumpError(str(exc)) from exc
    if sha is None:
        raise BumpError(
            f"ledger checkout {source} has no tag {commit} (refs/tags/{commit}); "
            "the pin requires a tag, so bump to a tag of the ledger repository"
        )
    tag = commit
    listing = _git(["ls-tree", "-r", "-z", "--full-tree", sha, "--", check.SOURCE_PREFIX], source)
    manifest: dict[str, str] = {}
    for record in listing.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        fields = meta.decode("ascii", "replace").split()
        full = os.fsdecode(name)
        if len(fields) != 3 or not full.startswith(check.SOURCE_PREFIX):
            raise BumpError(f"cannot read git ls-tree entry {record!r}")
        mode, kind, blob = fields
        path = full[len(check.SOURCE_PREFIX):]
        if mode != "100644" or kind != "blob":
            raise BumpError(f"ledger/{path} at {sha} is {kind} mode {mode}, not a 100644 file")
        problem = check.path_problem(path)
        if problem is not None:
            raise BumpError(f"ledger/{path} at {sha} {problem}")
        manifest[path] = blob
    if not manifest:
        raise BumpError(f"commit {sha} has no files under ledger/")
    for parent in (REPO_ROOT / "skills", REPO_ROOT / "skills" / "_lib", folder):
        if parent.is_symlink():
            raise BumpError(
                f"{parent.relative_to(REPO_ROOT).as_posix()} is a symlink; "
                "refusing before writing anything"
            )
    if folder.exists() and not folder.is_dir():
        raise BumpError(f"{check.VENDORED} is not a real directory")
    if folder.is_dir():
        for directory, dirnames, filenames in os.walk(folder):
            for name in [*dirnames, *filenames]:
                entry = Path(directory) / name
                if entry.is_symlink():
                    raise BumpError(
                        f"{entry.relative_to(REPO_ROOT).as_posix()} is a symlink; "
                        "refusing before writing anything"
                    )
    contents: dict[str, bytes] = {}
    for path, blob in manifest.items():
        data = _git(["cat-file", "blob", blob], source)
        if check.blob_id(data) != blob:
            raise BumpError(f"blob {blob} for ledger/{path} does not hash to its id")
        contents[path] = data
    try:
        tracked = set(check._index_entries(REPO_ROOT))
    except check.CheckError as exc:
        raise BumpError(str(exc)) from exc

    # Write the files, then delete what the commit lacks, then the pin.
    for path, data in sorted(contents.items()):
        target = folder / path
        if (link := _symlink_between(folder, target)) is not None:
            raise BumpError(f"{link.relative_to(REPO_ROOT).as_posix()} is a symlink")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.is_symlink() or target.is_dir():
            raise BumpError(f"{check.VENDORED}/{path} is a symlink or folder; resolve it by hand")
        _replace_file(target, data)
    gone = sorted((set(old_manifest) | tracked) - set(manifest))
    for path in gone:
        target = folder / path
        if (link := _symlink_between(folder, target.parent)) is not None:
            raise BumpError(f"{link.relative_to(REPO_ROOT).as_posix()} is a symlink")
        if target.is_symlink() or target.is_file():
            target.unlink()
        parent = target.parent
        while parent != folder and parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
            parent = parent.parent
    rendered = render_pin(repo, sha, manifest, tag)
    if not pin_path.exists() or pin_path.read_bytes() != rendered:
        _write_pin(pin_path, rendered)

    written = [f"{check.VENDORED}/{path}" for path in sorted(manifest)]
    _git(["add", "--", check.PIN_NAME, *written], REPO_ROOT)
    _git(["add", "-u", "--", check.VENDORED], REPO_ROOT)
    print(
        f"ledger vendor bump: {check.VENDORED} written from {repo}@{sha} (tag {tag}) "
        f"({len(manifest)} files, {len(gone)} removed); staged, not committed",
        flush=True,
    )
    result = subprocess.run(
        [sys.executable, str(CHECK), "--source", str(source)], check=False
    )
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] in {"-h", "--help"}:
        print("usage: ledger-vendor-bump.py LEDGER_CHECKOUT COMMIT", file=sys.stderr)
        return 0 if args[:1] in (["-h"], ["--help"]) else 2
    try:
        return bump(Path(args[0]), args[1])
    except BumpError as exc:
        print(f"ledger vendor bump: ERROR: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(
            f"ledger vendor bump: ERROR: writing failed ({exc}); the working tree is left "
            "for review, re-run to the same commit to restore a consistent copy",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())

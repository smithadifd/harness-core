#!/usr/bin/env python3
"""Fail when .core-manifest does not list exactly the vendored files of this repository.

Usage: check-core-manifest.py

The repository checked is the one that holds this script (the folder above
``scripts/``), whatever the working directory; it must be the top of a git
work tree. There are no options.

Every input is read from the git index, which is the checked-out commit in
CI: what is tracked and at which git mode (``git ls-files --stage``), and the
bytes of ``.core-manifest`` and ``VENDORING.md`` (their staged blobs). A file
edited on disk but not staged is not what this check reads.

``.core-manifest`` grammar (the grammar the consumer's drift check parses):
UTF-8 text, LF line endings only, ending in a newline, no empty or comment
lines. Zero or more ``tree <folder>/`` lines, then one or more paths, one per
line, sorted ascending by byte value, with no duplicates and no two equal
after casefold. A path or folder has components of ``[A-Za-z0-9._-]`` only,
no empty, ``.`` or ``..`` component, no leading slash, no ``__pycache__``
component, no component casefolding to ``.git``, no last component
casefolding to ``.gitattributes``, ``.gitmodules`` or ``.gitignore``, no first
component casefolding to ``.github``, and does not end in ``.pyc``. Tree
lines end in ``/``; no two are the same or one inside another.

``VENDORING.md``'s repository-only table: the line ``## Repository-only
files`` exactly once; after it, optional prose (no line starting with ``#``
or ``|``), then a table that starts with the line ``| Path | What it is |``
followed by the line ``|---|---|``; then one row per file, each exactly
``| `<path>` | <text> |`` (one space inside each bar, the path in backticks,
non-empty text with no bar or backtick); the table ends at the first empty
line or at the end of the file. Any other line inside the table is refused
by name. A table path has no leading slash and no empty, ``.`` or ``..``
component, and appears once.

Rules, each printed as ``<path>: <rule>``:

- ``listed but not tracked``: a ``.core-manifest`` path is not in the index;
- ``listed at mode <mode>``: a ``.core-manifest`` path is tracked at a git
  mode other than 100644 (100755, a symlink, a submodule);
- ``not at stage 0``: a tracked path has an unmerged index entry;
- ``tracked but unlisted``: a tracked path is neither in ``.core-manifest``
  nor in the repository-only table;
- ``under tree <folder>/ but not listed``: a tracked path under a ``tree``
  folder is not a ``.core-manifest`` path (the repository-only table does not
  excuse it);
- ``tree has no listed path``: a ``tree`` folder has no ``.core-manifest``
  path under it (printed as ``<folder>/: tree has no listed path``);
- ``listed as vendored and repository-only``: a path is in both;
- ``repository-only but not tracked``: a table path is not in the index;
- ``.core-manifest: <reason>`` / ``VENDORING.md: <reason>``: the file is not
  tracked as a regular file, cannot be read, or is off its grammar.

Exit 0 with one ``core manifest: ok`` line when no rule fires; exit 1 on any
rule, an unreadable input, or a git failure. Nothing is ever skipped.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "core manifest:"
CORE_MANIFEST = ".core-manifest"
VENDORING = "VENDORING.md"
TABLE_HEADING = "## Repository-only files"
TABLE_HEAD = "| Path | What it is |"
TABLE_RULE = "|---|---|"
TABLE_ROW = re.compile(r"\| `([^`|\s]+)` \| ([^`|]*[^`|\s][^`|]*) \|")
VENDORED_MODE = "100644"
_COMPONENT = re.compile(r"[A-Za-z0-9._-]+")
GIT_DIR = ".git"
GIT_CONFIG_FILES = frozenset({".gitattributes", ".gitmodules", ".gitignore"})
CI_FOLDER = ".github"
# Variables that would make git read another repository, index or object
# store than the one that holds this script; they are dropped before git runs.
_GIT_LOCATION_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})


class CheckError(RuntimeError):
    """An input cannot be read (exit 1, as every failure)."""


def _git_env() -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if key not in _GIT_LOCATION_ENV
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def git(args: list[str], root: Path) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=root, env=_git_env(),
                              capture_output=True, check=False)
    except OSError as exc:
        raise CheckError(f"git is not available ({exc.strerror or exc})") from exc
    if done.returncode != 0:
        message = done.stderr.decode("utf-8", "replace").strip().splitlines()
        raise CheckError(f"git {args[0]} failed: {message[-1] if message else done.returncode}")
    return done.stdout


def require_top(root: Path) -> None:
    top = git(["rev-parse", "--show-toplevel"], root).decode("utf-8", "replace").strip()
    if Path(top).resolve() != root.resolve():
        raise CheckError(f"{root} is not the top of a git work tree")


def tracked(root: Path) -> dict[str, tuple[str, str, str]]:
    """path -> (mode, blob id, stage) for every index entry; an unmerged path
    keeps a nonzero stage."""
    out = git(["ls-files", "--stage", "-z"], root)
    entries: dict[str, tuple[str, str, str]] = {}
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, blob, stage = meta.decode("ascii").split(" ")
        path = name.decode("utf-8", "surrogateescape")
        if path in entries and entries[path][2] != "0":
            stage = stage if stage != "0" else entries[path][2]
        entries[path] = (mode, blob, stage)
    return entries


def path_problem(path: str) -> str | None:
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
        if part.casefold() == GIT_DIR:
            return f"has a component {part!r} that casefolds to {GIT_DIR}"
    if parts[-1].casefold() in GIT_CONFIG_FILES:
        return f"has a last component {parts[-1]!r} that casefolds to a git configuration file"
    if parts[0].casefold() == CI_FOLDER:
        return f"has a first component {parts[0]!r} that casefolds to {CI_FOLDER}"
    if path.endswith(".pyc"):
        return "names a .pyc file"
    return None


def _read_staged(root: Path, index: dict[str, tuple[str, str, str]], name: str) -> str:
    entry = index.get(name)
    if entry is None:
        raise ValueError("is not tracked")
    if entry[0] != "100644" or entry[2] != "0":
        raise ValueError(f"is tracked at mode {entry[0]} stage {entry[2]}, not as a regular file")
    try:
        data = git(["cat-file", "blob", entry[1]], root)
    except CheckError as exc:
        raise ValueError(f"cannot be read ({exc})") from exc
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"is not UTF-8 text ({exc.reason})") from exc


def parse_core_manifest(text: str) -> tuple[list[str], list[str], list[str]]:
    """(tree folders, paths, problems). A tree with no path under it is a
    problem the caller prints by folder; every other problem is a grammar
    refusal of the file."""
    problems: list[str] = []
    trees: list[str] = []
    paths: list[str] = []
    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        return [], [], [f"line {line}: carriage return (CRLF line endings are refused)"]
    if not text:
        return [], [], ["is empty"]
    if not text.endswith("\n"):
        return [], [], ["does not end with a newline"]
    folded: dict[str, str] = {}
    for number, line in enumerate(text[:-1].split("\n"), 1):
        if line == "":
            problems.append(f"line {number}: empty line")
            continue
        if line == "tree" or line.startswith("tree "):
            value = line[len("tree "):]
            if paths:
                problems.append(f"line {number}: tree line after the first path")
                continue
            if not value.endswith("/"):
                problems.append(f"line {number}: tree path must end in /")
                continue
            folder = value[:-1]
            problem = path_problem(folder)
            if problem is not None:
                problems.append(f"line {number}: tree path {value!r} {problem}")
                continue
            clash = [other for other in trees if other == folder
                     or folder.startswith(other + "/") or other.startswith(folder + "/")]
            if clash:
                problems.append(f"line {number}: tree {value} repeats or overlaps tree {clash[0]}/")
                continue
            trees.append(folder)
            continue
        problem = path_problem(line)
        if problem is not None:
            problems.append(f"line {number}: path {line!r} {problem}")
            continue
        if paths and line == paths[-1]:
            problems.append(f"line {number}: duplicate path {line}")
            continue
        if paths and line.encode("utf-8") < paths[-1].encode("utf-8"):
            problems.append(f"line {number}: paths are not sorted ({line} after {paths[-1]})")
        other = folded.get(line.casefold())
        if other is not None:
            problems.append(f"line {number}: paths {other} and {line} are equal after casefold")
        folded[line.casefold()] = line
        paths.append(line)
    if not paths and not problems:
        problems.append("no paths")
    return trees, paths, problems


def table_problem(path: str) -> str | None:
    if path.startswith("/"):
        return "has a leading slash"
    for part in path.split("/"):
        if part == "":
            return "has an empty component"
        if part in {".", ".."}:
            return f"has a {part!r} component"
    return None


def parse_repo_only(text: str) -> tuple[list[str], list[str]]:
    """(paths, problems) from VENDORING.md's repository-only table."""
    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        return [], [f"line {line}: carriage return (CRLF line endings are refused)"]
    lines = text.split("\n")
    starts = [index for index, line in enumerate(lines) if line == TABLE_HEADING]
    if len(starts) != 1:
        return [], [f"needs the line {TABLE_HEADING!r} exactly once, found {len(starts)}"]
    index = starts[0] + 1
    while index < len(lines) and lines[index] != TABLE_HEAD:
        if lines[index].startswith("#") or lines[index].startswith("|"):
            return [], [f"line {index + 1}: {lines[index]!r} before the table head {TABLE_HEAD!r}"]
        index += 1
    if index >= len(lines):
        return [], [f"no table head {TABLE_HEAD!r} after {TABLE_HEADING!r}"]
    if index + 1 >= len(lines) or lines[index + 1] != TABLE_RULE:
        return [], [f"line {index + 2}: the table head must be followed by {TABLE_RULE!r}"]
    paths: list[str] = []
    problems: list[str] = []
    index += 2
    while index < len(lines) and lines[index] != "":
        line = lines[index]
        match = TABLE_ROW.fullmatch(line)
        if match is None:
            problems.append(f"line {index + 1}: not a table row of the form | `<path>` | <text> |")
        else:
            path = match.group(1)
            problem = table_problem(path)
            if problem is not None:
                problems.append(f"line {index + 1}: path {path!r} {problem}")
            elif path in paths:
                problems.append(f"line {index + 1}: duplicate path {path}")
            else:
                paths.append(path)
        index += 1
    if not paths and not problems:
        problems.append("the repository-only table has no rows")
    return paths, problems


def check(root: Path) -> list[str]:
    require_top(root)
    index = tracked(root)
    findings: list[str] = []
    try:
        manifest_text = _read_staged(root, index, CORE_MANIFEST)
    except ValueError as exc:
        return [f"{CORE_MANIFEST}: {exc}"]
    try:
        vendoring_text = _read_staged(root, index, VENDORING)
    except ValueError as exc:
        return [f"{VENDORING}: {exc}"]
    trees, listed, problems = parse_core_manifest(manifest_text)
    findings.extend(f"{CORE_MANIFEST}: {problem}" for problem in problems)
    repo_only, table_problems = parse_repo_only(vendoring_text)
    findings.extend(f"{VENDORING}: {problem}" for problem in table_problems)
    listed_set = set(listed)
    repo_only_set = set(repo_only)
    for folder in trees:
        if not any(path.startswith(folder + "/") for path in listed):
            findings.append(f"{folder}/: tree has no listed path")
    for path in listed:
        entry = index.get(path)
        if entry is None:
            findings.append(f"{path}: listed but not tracked")
            continue
        if entry[0] != VENDORED_MODE:
            findings.append(f"{path}: listed at mode {entry[0]}")
    for path in repo_only:
        if path in listed_set:
            findings.append(f"{path}: listed as vendored and repository-only")
        if path not in index:
            findings.append(f"{path}: repository-only but not tracked")
    for path in sorted(index):
        _mode, _blob, stage = index[path]
        if stage != "0":
            findings.append(f"{path}: not at stage 0")
        if path in listed_set:
            continue
        folder = next((tree for tree in trees if path.startswith(tree + "/")), None)
        if folder is not None:
            findings.append(f"{path}: under tree {folder}/ but not listed")
            continue
        if path not in repo_only_set:
            findings.append(f"{path}: tracked but unlisted")
    return findings


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(f"{PREFIX} ERROR: takes no arguments", file=sys.stderr)
        return 1
    try:
        findings = check(ROOT)
    except CheckError as exc:
        print(f"{PREFIX} ERROR: {exc}")
        return 1
    for finding in findings:
        print(finding)
    if findings:
        print(f"{PREFIX} FAIL: {len(findings)} finding(s)")
        return 1
    print(f"{PREFIX} ok: every tracked file is vendored (listed in {CORE_MANIFEST}) "
          f"or repository-only (listed in {VENDORING})")
    return 0


if __name__ == "__main__":
    sys.exit(main())

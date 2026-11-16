#!/usr/bin/env python3
"""Fail when the marketplace or the harness plugin's manifest breaks the invariant of a tag.

The invariant every tag of this repository keeps: the marketplace lists each
plugin once; the in-repo `harness` plugin is a folder here at the same tag; each
external plugin is the one expected source for its name, fixed to a full 40-hex
commit on a release tag; nothing resolves to a branch or a range.

Usage: check-marketplace.py [--source]

The check always reads the repository that holds this script (the folder above
``scripts/``); there is no option to point it at another tree.

Offline (no option) reads the two manifests as JSON and fetches nothing. It
runs in the pytest job through scripts/tests/test_marketplace_manifest.py and
on the command line. Its grammar (anything else is refused, each by the name
shown in ``problems``):

- ``.claude-plugin/marketplace.json``: one JSON object with no duplicate key,
  with ``name`` equal to ``harness-core``, ``owner.name``, and a non-empty
  ``plugins`` list.
- Each plugin entry: an object whose ``name`` is one of ``harness``, ``sdlc``,
  ``ledger-hooks``, ``rails``, listed once, and whose ``source`` is either
  - a ``./<folder>`` path inside this repository (no ``..``, no backslash, no
    symlink that leaves the repository), whose folder exists and holds
    ``.claude-plugin/plugin.json``; or
  - an object of exactly one of two shapes, each with a ``ref`` that is a
    release tag ``vX.Y.Z`` and a ``sha`` that is a full lowercase 40-hex commit
    id: ``{"source": "url", "url": "https://..."}`` or
    ``{"source": "git-subdir", "url": "https://...", "path": "<folder>"}``.
    Every other source type (github, npm, archive, command) and every other key
    is refused.
- An external plugin is bound to its one expected source by name
  (``EXTERNAL_PLUGINS`` below, the only place that table is written): ``sdlc``
  is source type ``url`` at the skills repository with no subdirectory, and
  ``ledger-hooks`` is source type ``git-subdir`` at the ledger repository with
  path ``plugins/ledger-hooks``. The repository URL is
  ``https://github.com/<owner>/<repository>.git``, where the owner is the owner
  in the ``repo`` line of ledger-vendor.pin (the skills and ledger repositories
  share one owner), so this file never names the owner. Any other type, URL or
  path, an external entry for any other name, and a folder source for one of
  these two names, is refused.
- ``harness`` is listed and is a folder in this repository.
- A folder plugin's manifest: one JSON object with no duplicate key, whose
  ``name`` equals its entry's ``name``, with a ``version`` that is ``X.Y.Z`` set
  in the manifest only (not also in the entry), and ``dependencies`` (when
  present) a list of plain names, each another entry of this marketplace (no
  version range, no ``name@marketplace``, no object form, not itself).

``--source`` also resolves the tag of each external entry that passed every
offline check, against the live repository the table above names for that
plugin (never the url written in the manifest), at use time, with
``git ls-remote`` on
``refs/tags/<ref>`` and its peeled form ``refs/tags/<ref>^{}``. An annotated tag
is peeled (the peeled line is used when it is present); a lightweight tag is its
own commit. The commit must equal ``sha`` exactly. A tag that is not there, a
``git ls-remote`` that fails or times out, an output line off the grammar
``<40 lowercase hex><TAB>refs/tags/<ref>[^{}]``, and a tag that resolves to any
other commit are each a refusal, never a pass. An entry that failed an offline check is not resolved at all
and is reported as such. A url that starts with ``-``, holds whitespace or does
not start with ``https://`` is refused before any subprocess. git runs with no
shell and a fixed argv that ends its options with ``--`` before the url. It
runs from a fresh temporary folder that is not inside any repository (so no
repository-local git configuration applies), with a child environment built
from scratch: PATH from the parent, HOME a fresh empty temporary folder,
``GIT_CONFIG_NOSYSTEM=1``, ``GIT_CONFIG_GLOBAL=/dev/null``,
``GIT_TERMINAL_PROMPT=0``, and no other variable, so no inherited ``GIT_*``
variable (``GIT_CONFIG_COUNT``, ``GIT_CONFIG_PARAMETERS``, ``GIT_DIR`` and the
rest) can rewrite a repository url. Both folders are removed afterwards. CI runs
it in the ``plugin-validate`` job of ci.yml, with no credential.

Exit 0 clean; 1 on any refusal, one line per refusal; 2 on usage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

REPO_ROOT = Path(__file__).resolve().parents[1]
PREFIX = "marketplace:"
MARKETPLACE = ".claude-plugin/marketplace.json"
PLUGIN_MANIFEST = ".claude-plugin/plugin.json"
PIN = "ledger-vendor.pin"
MARKETPLACE_NAME = "harness-core"
PLUGIN_NAMES = ("harness", "sdlc", "ledger-hooks", "rails")
FULL_COMMIT = re.compile(r"[0-9a-f]{40}")
RELEASE_TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+")
VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
LOCAL_SOURCE = re.compile(r"\./[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*")
OWNER_REPO = re.compile(r"[A-Za-z0-9._-]+/[A-Za-z0-9._-]+")
LS_REMOTE_LINE = re.compile(r"([0-9a-f]{40})\t(refs/tags/\S+)")
# The fields each external source type needs besides source, ref and sha.
EXTERNAL_SOURCES = {
    "url": ("url",),
    "git-subdir": ("url", "path"),
}
# The one expected source of each external plugin name: its source type, the
# repository's name (the owner comes from ledger-vendor.pin) and the folder in
# that repository (None for a plugin at the repository root).
EXTERNAL_PLUGINS = {
    "sdlc": {"source": "url", "repository": "skills", "path": None},
    "ledger-hooks": {"source": "git-subdir", "repository": "ledger", "path": "plugins/ledger-hooks"},
}
LS_REMOTE_SECONDS = 120


def expected_url(name: str, owner: str) -> str:
    """The one repository url of an external plugin name: the table's, never the manifest's."""
    return f"https://github.com/{owner}/{EXTERNAL_PLUGINS[name]['repository']}.git"

# (url, tag) -> (exit code, stdout). The one boundary to the network.
LsRemote = Callable[[str, str], "tuple[int, str]"]


def load_json(path: Path):
    """The file's JSON, refusing a duplicate key (json.loads would keep the last)."""
    def pairs(items):
        found = {}
        for key, value in items:
            if key in found:
                raise ValueError(f"duplicate key {key!r}")
            found[key] = value
        return found
    return json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=pairs)


def pin_owner(root: Path) -> tuple[str | None, str | None]:
    """(owner, None) from the pin's repo line, or (None, the problem)."""
    try:
        text = (root / PIN).read_text(encoding="utf-8")
    except (OSError, ValueError) as exc:
        return None, f"{PIN}: cannot be read to give the owner ({exc.__class__.__name__})"
    repos = [line[len("repo "):] for line in text.split("\n") if line.startswith("repo ")]
    if len(repos) != 1 or not OWNER_REPO.fullmatch(repos[0]):
        return None, f"{PIN}: has no single repo line of owner/name to give the owner"
    return repos[0].split("/")[0], None


def external_problems(label: str, name: str, source: dict, owner: str | None) -> list[str]:
    kind = source.get("source")
    if kind not in EXTERNAL_SOURCES:
        return [f"{label}: source type {kind!r} is not allowed (url or git-subdir only)"]
    found: list[str] = []
    expected = EXTERNAL_PLUGINS.get(name)
    if expected is None:
        found.append(f"{label}: is not an expected external plugin ({', '.join(EXTERNAL_PLUGINS)})")
    elif kind != expected["source"]:
        found.append(f"{label}: source type {kind!r} is not the expected {expected['source']!r}")
    needed = EXTERNAL_SOURCES[kind]
    unknown = sorted(set(source) - {"source", "ref", "sha", *needed})
    if unknown:
        found.append(f"{label}: unknown source key {unknown[0]!r}")
    for key in needed:
        if not isinstance(source.get(key), str) or not source[key]:
            found.append(f"{label}: source field {key!r} is missing")
    url = source.get("url")
    if isinstance(url, str) and not url.startswith("https://"):
        found.append(f"{label}: source url is not https")
    if expected is not None and owner is not None and isinstance(url, str) and url.startswith("https://"):
        wanted = expected_url(name, owner)
        if url != wanted:
            found.append(f"{label}: source url is not the expected {expected['repository']} repository")
    path = source.get("path")
    if expected is not None and expected["path"] is not None and isinstance(path, str):
        if path != expected["path"]:
            found.append(f"{label}: source path is not {expected['path']!r}")
    ref = source.get("ref")
    if ref is None:
        found.append(f"{label}: no ref")
    elif not isinstance(ref, str) or not RELEASE_TAG.fullmatch(ref):
        found.append(f"{label}: ref is not a release tag vX.Y.Z")
    sha = source.get("sha")
    if sha is None:
        found.append(f"{label}: no pin (sha missing)")
    elif not isinstance(sha, str) or not FULL_COMMIT.fullmatch(sha):
        found.append(f"{label}: pin is not a full 40-hex commit id")
    return found


def folder_problems(root: Path, label: str, entry: dict, source: str) -> tuple[list[str], list]:
    """(problems, dependencies) of a plugin that is a folder in this repository."""
    if not LOCAL_SOURCE.fullmatch(source) or ".." in source.split("/"):
        return [f"{label}: source is not a ./ folder path inside the repository"], []
    folder = root / source[2:]
    if not folder.resolve().is_relative_to(root.resolve()):
        return [f"{label}: source resolves outside the repository"], []
    if not folder.is_dir():
        return [f"{label}: folder {source} is missing"], []
    manifest = folder / PLUGIN_MANIFEST
    if not manifest.is_file():
        return [f"{label}: manifest {source}/{PLUGIN_MANIFEST} is missing"], []
    try:
        data = load_json(manifest)
    except ValueError as exc:
        return [f"{label}: manifest is not valid JSON ({exc})"], []
    if not isinstance(data, dict):
        return [f"{label}: manifest is not a JSON object"], []
    found: list[str] = []
    if data.get("name") != entry.get("name"):
        found.append(f"{label}: manifest name {data.get('name')!r} differs from the entry name")
    version = data.get("version")
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        found.append(f"{label}: manifest version is missing or not X.Y.Z")
    if "version" in entry:
        found.append(f"{label}: version is set in the entry and in the manifest")
    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(d, str) for d in dependencies):
        found.append(f"{label}: dependencies must be a list of plain plugin names, no range")
        dependencies = []
    return found, dependencies


def problems(root: Path, external: dict | None = None) -> list[str]:
    """The offline refusals. When ``external`` is given it is filled with each
    plugin name whose source is an object: its source when that entry passed
    every offline check, else None (the entry --source must not resolve)."""
    path = root / MARKETPLACE
    if not path.is_file():
        return [f"{MARKETPLACE}: is missing"]
    try:
        data = load_json(path)
    except ValueError as exc:
        return [f"{MARKETPLACE}: is not valid JSON ({exc})"]
    if not isinstance(data, dict):
        return [f"{MARKETPLACE}: top level is not an object"]
    found: list[str] = []
    if data.get("name") != MARKETPLACE_NAME:
        found.append(f"{MARKETPLACE}: name is not {MARKETPLACE_NAME!r}")
    owner_entry = data.get("owner")
    if not (isinstance(owner_entry, dict) and isinstance(owner_entry.get("name"), str) and owner_entry["name"]):
        found.append(f"{MARKETPLACE}: owner.name is missing")
    plugins = data.get("plugins")
    if not isinstance(plugins, list) or not plugins:
        return [*found, f"{MARKETPLACE}: plugins is not a non-empty list"]
    owner, owner_problem = pin_owner(root)
    if owner_problem is not None:
        found.append(owner_problem)
    entries: dict[str, dict] = {}
    dependencies: dict[str, list] = {}
    for index, entry in enumerate(plugins):
        label = f"plugins[{index}]"
        if not isinstance(entry, dict):
            found.append(f"{label}: is not an object")
            continue
        name = entry.get("name")
        if not isinstance(name, str) or name not in PLUGIN_NAMES:
            found.append(f"{label}: plugin name {name!r} is not one of {', '.join(PLUGIN_NAMES)}")
            continue
        label = f"plugin {name}"
        if name in entries:
            found.append(f"{label}: is listed twice")
            if external is not None and name in external:
                external[name] = None
            continue
        entries[name] = entry
        source = entry.get("source")
        if isinstance(source, str):
            if name in EXTERNAL_PLUGINS:
                found.append(f"{label}: source is not the expected external source")
                if external is not None:
                    external[name] = None
                continue
            more, dependencies[name] = folder_problems(root, label, entry, source)
            found.extend(more)
        elif isinstance(source, dict):
            more = external_problems(label, name, source, owner)
            found.extend(more)
            if external is not None:
                clean = not more and owner is not None and name in EXTERNAL_PLUGINS
                external[name] = source if clean else None
        else:
            found.append(f"{label}: source is neither a ./ path nor an object")
    if "harness" not in entries:
        found.append("plugin harness: is not listed")
    elif not isinstance(entries["harness"].get("source"), str):
        found.append("plugin harness: source is not a folder in this repository")
    for name, wanted in dependencies.items():
        for dependency in wanted:
            if dependency == name:
                found.append(f"plugin {name}: depends on itself")
            elif dependency not in entries:
                found.append(f"plugin {name}: dependency {dependency!r} is not an entry of this marketplace")
    return found


# --source: each external entry's tag against the live repository.

def url_refusal(url) -> str | None:
    """The name of what is wrong with a url that is about to reach git, or None."""
    if not isinstance(url, str) or url == "":
        return "url is empty"
    if url.startswith("-"):
        return "url starts with '-'"
    if any(char.isspace() for char in url):
        return "url holds whitespace"
    if not url.startswith("https://"):
        return "url is not https"
    return None


def child_env(home: str) -> dict[str, str]:
    """The environment of the git child, built from scratch: PATH from the parent,
    the fresh empty HOME, the three git settings, and nothing else (no other
    GIT_* variable, no credential, nothing the runner set)."""
    env = {
        "HOME": home,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
    }
    if "PATH" in os.environ:
        env["PATH"] = os.environ["PATH"]
    return env


def inside_a_repository(folder: Path) -> bool:
    """True when the folder or any folder above it holds a .git entry."""
    return any((parent / ".git").exists() for parent in (folder, *folder.parents))


def run_ls_remote(url: str, tag: str) -> tuple[int, str]:
    """``git ls-remote`` on the tag ref and its peeled form: (exit code, stdout).
    No shell, a fixed argv whose options end at ``--`` before the repository, no
    terminal prompt. git runs from a fresh temporary folder that is not inside
    any repository, with a child environment built from scratch (``child_env``),
    so neither a repository-local git configuration nor an inherited GIT_*
    variable can rewrite the url; both temporary folders are removed afterwards.
    A git that cannot run, times out, or has no folder outside a repository to run
    from is a non-zero code, never a pass. Callers give it a url that
    ``url_refusal`` has passed."""
    try:
        with tempfile.TemporaryDirectory(prefix="marketplace-cwd.", ignore_cleanup_errors=True) as cwd, \
                tempfile.TemporaryDirectory(prefix="marketplace-home.", ignore_cleanup_errors=True) as home:
            if inside_a_repository(Path(cwd).resolve()):
                return 255, ""
            done = subprocess.run(
                ["git", "ls-remote", "--tags", "--", url, f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"],
                shell=False, stdin=subprocess.DEVNULL, capture_output=True, cwd=cwd, env=child_env(home),
                check=False, timeout=LS_REMOTE_SECONDS,
            )
    except (OSError, subprocess.SubprocessError):
        return 255, ""
    return done.returncode, done.stdout.decode("utf-8", "replace")


def git_ls_remote(url: str, tag: str) -> tuple[int, str]:
    """``run_ls_remote`` for a url that passes ``url_refusal``; any other url is a
    non-zero code before any subprocess starts."""
    if url_refusal(url) is not None:
        return 255, ""
    return run_ls_remote(url, tag)


def resolve_tag(output: str, tag: str) -> tuple[str | None, str | None]:
    """(commit, None) the tag names in ``git ls-remote`` output, or (None, the problem).

    The peeled line (``refs/tags/<tag>^{}``) is the commit of an annotated tag
    and is used when present; the plain line is the commit of a lightweight tag."""
    direct = f"refs/tags/{tag}"
    seen: dict[str, str] = {}
    for line in output.split("\n"):
        if line == "":
            continue
        match = LS_REMOTE_LINE.fullmatch(line)
        if match is None or match.group(2) not in (direct, f"{direct}^{{}}"):
            return None, "ls-remote output is not <40 hex><TAB>refs/tags/<tag> lines of the tag and its peeled form"
        if match.group(2) in seen:
            return None, "ls-remote output is not <40 hex><TAB>refs/tags/<tag> lines of the tag and its peeled form"
        seen[match.group(2)] = match.group(1)
    if not seen:
        return None, f"tag {tag} is not in the source repository"
    return seen.get(f"{direct}^{{}}", seen.get(direct)), None


def source_problems(root: Path, ls_remote: LsRemote = git_ls_remote) -> list[str]:
    """One refusal for every external entry whose tag does not resolve to its sha.

    Only an entry that passed every offline check is resolved, and its url is the
    table's (``expected_url``), never the manifest's."""
    external: dict[str, dict | None] = {}
    problems(root, external)
    owner, _ = pin_owner(root)
    found: list[str] = []
    for name, source in external.items():
        label = f"plugin {name}"
        if source is None or owner is None:
            found.append(f"{label}: not resolved, it failed the offline check")
            continue
        url, ref, sha = expected_url(name, owner), source["ref"], source["sha"]
        refusal = url_refusal(url)
        if refusal is not None:
            found.append(f"{label}: not resolved, {refusal}")
            continue
        code, output = ls_remote(url, ref)
        if code != 0:
            found.append(f"{label}: git ls-remote failed (exit {code})")
            continue
        commit, problem = resolve_tag(output, ref)
        if problem is not None:
            found.append(f"{label}: {problem}")
        elif commit != sha:
            found.append(f"{label}: tag {ref} resolves to {commit}, not the pin {sha}")
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check the marketplace and the harness plugin's manifest.")
    parser.add_argument("--source", action="store_true",
                        help="also resolve each external entry's tag with git ls-remote and compare it with the pin")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        return 2 if exc.code else 0
    found = problems(REPO_ROOT)
    if args.source:
        found.extend(source_problems(REPO_ROOT))
    for line in found:
        print(f"{PREFIX} refused: {line}")
    if found:
        print(f"{PREFIX} FAIL: {len(found)} refusal(s)")
        return 1
    checked = " and each external tag against its repository" if args.source else ""
    print(f"{PREFIX} ok: the marketplace and the harness plugin hold the invariant{checked}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

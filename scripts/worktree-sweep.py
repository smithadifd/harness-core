#!/usr/bin/env python3
"""Register seat-created worktrees and print policy-approved removal plans.

``register`` and ``transfer`` are the only operations that write a registry. With
``--adopt-legacy``, ``register`` first proves an existing linked worktree is
clean and merged or pushed, then asks the removal policy to approve a
prospective registry row. ``transfer`` runs at a seat handoff: it appends to the
successor seat's registry a re-seated copy of each ``worktree`` row the
predecessor seat owns, so the successor may remove those worktrees after the
predecessor retires. A row whose path lies outside the predecessor's own
roots (see ``seat_roots``) is not carried; transfer names it on a
``transfer_foreign_row: <path>`` line instead. Each carried path is also
recorded in the successor's ``inherited-roots.tsv``, so the successor's own
later transfer still counts it as one of its roots. ``plan`` prints commands
for the calling seat to execute separately; it never removes a worktree.
Each worktree ``plan`` keeps is also appended, with its reason, as one
timestamped line to ``cleanup.log`` beside the registry (or at
``--cleanup-log``); a line that cannot be written
is dropped and changes nothing else. A row whose path no longer exists on
disk is reported (and logged) as ``gone <path>``; plan never writes the
registry, so the row stays until it is pruned.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.parse

HEADER = "repo\tpath\tkind\tseat\trow\tcreated_at"
STAMP = re.compile(r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
SEAT_LABEL = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
ROW_KIND = re.compile(r"\A[a-z][a-z0-9_-]*\Z")
REGISTRY_BASENAME = "cleanup-items.tsv"
LANE_ROOTS_BASENAME = "lane-roots.tsv"
LANE_ROOTS_HEADER = "path\trepo\tbranch\tcreated_at"
LIFECYCLE_BASENAME = "seat-lifecycle.tsv"
LIFECYCLE_FIELDS = 13
INHERITED_ROOTS_BASENAME = "inherited-roots.tsv"
INHERITED_ROOTS_HEADER = "path\tfrom\trow\tcreated_at"
GH_TIMEOUT_SECONDS = 2.0
GUARD_TIMEOUT_SECONDS = 10.0
GIT_REMOTE_TIMEOUT_SECONDS = 15.0
LOCK_TIMEOUT_SECONDS = 5.0


class Refused(ValueError):
    pass


def field(value: str, name: str) -> str:
    if not value or any(char in value for char in "\t\r\n"):
        raise Refused(f"{name} must be non-empty and free of tabs and newlines")
    return value


def absolute_real(value: str, name: str) -> str:
    field(value, name)
    if not os.path.isabs(value):
        raise Refused(f"{name} must be absolute")
    return field(os.path.realpath(value), name)


def registry_path(value: str) -> str:
    field(value, "registry")
    if not os.path.isabs(value):
        raise Refused("registry must be absolute")
    parent = os.path.realpath(os.path.dirname(value))
    return os.path.join(parent, os.path.basename(value))


def git(path: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", path, *args], capture_output=True, text=True, check=False
    )


def listed_worktrees(repo: str) -> set[str]:
    result = git(repo, "worktree", "list", "--porcelain")
    if result.returncode != 0:
        return set()
    return {
        os.path.realpath(line.removeprefix("worktree "))
        for line in result.stdout.splitlines()
        if line.startswith("worktree ")
    }


def is_linked_worktree(repo: str, path: str) -> bool:
    if path not in listed_worktrees(repo):
        return False
    top = git(path, "rev-parse", "--show-toplevel")
    git_dir = git(path, "rev-parse", "--path-format=absolute", "--git-dir")
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    repo_common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if any(item.returncode != 0 for item in (top, git_dir, common, repo_common)):
        return False
    return (
        os.path.realpath(top.stdout.strip()) == path
        and os.path.realpath(git_dir.stdout.strip())
        != os.path.realpath(common.stdout.strip())
        and os.path.realpath(common.stdout.strip())
        == os.path.realpath(repo_common.stdout.strip())
    )


def validate_row(parts: list[str]) -> tuple[str, str, str, str, str, str]:
    if len(parts) != 6:
        raise Refused("registry row must have six TSV fields")
    repo, path, kind, seat, row, created = parts
    for value, name in zip(
        parts, ("repo", "path", "kind", "seat", "row", "created_at")
    ):
        field(value, name)
    if kind != "worktree":
        raise Refused("kind must be worktree")
    if not os.path.isabs(repo) or os.path.realpath(repo) != repo:
        raise Refused("repo must be an absolute realpath")
    if not os.path.isabs(path) or os.path.realpath(path) != path:
        raise Refused("path must be an absolute realpath")
    if STAMP.fullmatch(created) is None:
        raise Refused("created_at must be UTC YYYY-MM-DDTHH:MM:SSZ")
    return repo, path, kind, seat, row, created


def read_registry(registry: str) -> list[tuple[str, str, str, str, str, str]]:
    try:
        descriptor = os.open(registry, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise Refused(f"cannot read registry: {exc.strerror or exc}") from exc
    try:
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        with os.fdopen(os.dup(descriptor), encoding="utf-8") as stream:
            lines = stream.read().splitlines()
    except (OSError, UnicodeError) as exc:
        raise Refused(f"cannot read registry: {exc}") from exc
    finally:
        os.close(descriptor)
    if not lines or lines[0] != HEADER:
        raise Refused("malformed registry header")
    rows = [validate_row(line.split("\t")) for line in lines[1:]]
    paths = [item[1] for item in rows]
    if len(paths) != len(set(paths)):
        raise Refused("duplicate path in registry")
    return rows


def register(args: argparse.Namespace) -> int:
    registry = registry_path(args.registry)
    root = os.path.dirname(registry)
    os.makedirs(root, mode=0o700, exist_ok=True)
    if os.path.islink(root) or os.path.islink(registry):
        raise Refused("registry and its directory must not be symlinks")
    os.chmod(root, 0o700)
    repo = absolute_real(args.repo, "repo")
    path = absolute_real(args.path, "path")
    seat = field(args.seat, "seat")
    row = field(args.row, "row")
    if args.kind != "worktree":
        raise Refused("kind must be worktree")
    if not is_linked_worktree(repo, path):
        raise Refused("worktree path is not a linked worktree of repo")
    created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    descriptor = os.open(
        registry, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
    )
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        os.fchmod(descriptor, 0o600)
        with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
            existing = stream.read()
        lines = existing.splitlines()
        if existing and (not lines or lines[0] != HEADER):
            raise Refused("malformed registry header")
        prior = [validate_row(line.split("\t")) for line in lines[1:]]
        if any(item[1] == path for item in prior):
            raise Refused("duplicate path")
        prefix = "" if existing.endswith("\n") or not existing else "\n"
        if not existing:
            prefix = HEADER + "\n"
        record = "\t".join((repo, path, "worktree", seat, row, created)) + "\n"
        os.lseek(descriptor, 0, os.SEEK_END)
        os.write(descriptor, (prefix + record).encode("utf-8"))
    finally:
        os.close(descriptor)
    return 0


def gh_rows(repo: str, branch: str, state: str, fields: str) -> list[dict] | None:
    if shutil.which("gh") is None:
        return None
    try:
        result = subprocess.run(
            [
                "gh", "pr", "list", "--head", branch, "--state", state,
                "--json", fields,
            ],
            cwd=repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=GH_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    try:
        rows = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    return rows if isinstance(rows, list) and all(isinstance(row, dict) for row in rows) else None


def merged_pr_matches_head(repo: str, branch: str, head: str) -> bool | None:
    merged_rows = gh_rows(repo, branch, "merged", "number,headRefOid")
    if merged_rows is None:
        return None
    for row in merged_rows:
        number = row.get("number")
        oid = row.get("headRefOid")
        if type(number) is not int or number <= 0 or not isinstance(oid, str):
            return None
        if re.fullmatch(r"[0-9a-fA-F]{40,64}", oid) is None:
            return None
        if oid == head:
            return True
    return False


class ProbeFailed(Exception):
    """A git or gh read failed; the caller refuses with this reason (fail closed)."""


OID = re.compile(r"\A[0-9a-fA-F]{40,64}\Z")


def git_timed(path: str, *args: str) -> subprocess.CompletedProcess[str] | None:
    """Run a git read that may reach the remote, with a timeout and no prompts."""
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        return subprocess.run(
            ["git", "-C", path, *args],
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=GIT_REMOTE_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def is_ancestor(path: str, ancestor: str, descendant: str) -> bool:
    """``git merge-base --is-ancestor``: exit 0 yes, 1 no, anything else fails."""
    result = git(path, "merge-base", "--is-ancestor", ancestor, descendant)
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise ProbeFailed("git state unavailable")


NETWORK_SCHEMES = frozenset(
    {"ssh", "git", "http", "https", "ftp", "ftps", "git+ssh", "ssh+git"}
)
HELPER_URL = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9+.-]*::")
SCHEME_URL = re.compile(r"\A([A-Za-z][A-Za-z0-9+.-]*)://")


def remote_urls(path: str, remote: str) -> list[str]:
    """The fetch URLs of ``remote``, as ls-remote and fetch read them.

    ``git remote get-url --all`` applies ``url.<base>.insteadOf``. Push URLs
    are not read: a proof reads the remote through its fetch URL only. A name
    git does not know as a remote is itself the URL (an upstream may name a
    URL directly).
    """
    result = git(path, "remote", "get-url", "--all", remote)
    if result.returncode == 0:
        urls = [line for line in result.stdout.splitlines() if line]
        if urls:
            return urls
    return [remote]


def classify_url(path: str, url: str) -> tuple[str, list[str]]:
    """Classify ``url`` as git resolves it: network, local paths, or unknown.

    Returns ``("network", [])`` for an ssh, git, http(s) or ftp(s) URL or an
    scp-style ``host:path``; ``("local", candidates)`` for a path; and
    ``("unknown", [])`` for a remote-helper URL (``<helper>::...`` or an
    unknown scheme), whose reach cannot be told. Any ``file:`` URL is local
    whatever its authority (git reads ``file://<any host>/p`` as the path
    ``/p``); its path is taken both raw and percent-decoded. A relative path
    is relative to the directory git runs in (the worktree).
    """
    if url[:5].lower() == "file:":
        rest = url[5:]
        if rest.startswith("//"):
            slash = rest.find("/", 2)
            if slash == -1:
                return "local", []
            rest = rest[slash:]
        candidates = [rest, urllib.parse.unquote(rest)]
        return "local", [os.path.join(path, item) for item in candidates if item]
    if HELPER_URL.match(url):
        return "unknown", []
    scheme = SCHEME_URL.match(url)
    if scheme:
        if scheme.group(1).lower() in NETWORK_SCHEMES:
            return "network", []
        return "unknown", []
    colon, slash = url.find(":"), url.find("/")
    if colon != -1 and (slash == -1 or colon < slash):
        return "network", []
    return "local", [os.path.join(path, url)] if url else []


def is_self_remote(path: str, remote: str, unresolved: str) -> bool:
    """True when ``remote`` cannot prove a push: it may be this repository.

    Each fetch URL is classified as git resolves it (``classify_url``). A
    local URL (``.``, a path, any ``file:`` URL) is this repository when its
    realpath is this worktree, the main checkout, this worktree's git dir or
    the common git dir, or when ``git rev-parse --git-common-dir`` run there
    names our common git dir; its branches are then local refs. A URL that
    cannot be classified (a remote helper) is not a proof either. A local URL
    that resolves to no repository (a missing path, a bundle file) cannot be
    read as a repository, so it raises ProbeFailed(unresolved) and the caller
    refuses by name: this fails closed. Only a network URL, or a local URL
    naming a different repository, may prove a push.
    """
    common_result = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    git_dir_result = git(path, "rev-parse", "--path-format=absolute", "--git-dir")
    if common_result.returncode != 0 or git_dir_result.returncode != 0:
        raise ProbeFailed("git state unavailable")
    common = os.path.realpath(common_result.stdout.strip())
    targets = {
        os.path.realpath(path),
        common,
        os.path.realpath(git_dir_result.stdout.strip()),
    }
    if os.path.basename(common) == ".git":
        targets.add(os.path.dirname(common))
    for url in remote_urls(path, remote):
        kind, candidates = classify_url(path, url)
        if kind == "network":
            continue
        if kind == "unknown":
            return True
        resolved = False
        for candidate in candidates:
            real = os.path.realpath(candidate)
            if real in targets:
                return True
            found = git(real, "rev-parse", "--path-format=absolute", "--git-common-dir")
            if found.returncode == 0:
                if os.path.realpath(found.stdout.strip()) == common:
                    return True
                resolved = True
        if not resolved:
            raise ProbeFailed(unresolved)
    return False


def on_default_branch(path: str, head: str) -> bool:
    """True when HEAD is reachable from origin's default branch.

    The default branch and its live tip are both read from the remote in one
    ``git ls-remote --symref origin HEAD``, never assumed and never taken from
    the local tracking ref, which may be stale (the remote default may have
    been rewritten since this clone last fetched). The tip is fetched by oid
    when it is not in the local object store, and HEAD is tested against it.
    Any read failure, or a reply without exactly one branch symref and one
    tip, raises ProbeFailed. An origin that is this repository itself is no
    proof (False).
    """
    if is_self_remote(path, "origin", "git state unavailable"):
        return False
    result = git_timed(path, "ls-remote", "--symref", "origin", "HEAD")
    if result is None or result.returncode != 0:
        raise ProbeFailed("git state unavailable")
    defaults, tips = [], []
    for line in result.stdout.splitlines():
        if re.fullmatch(r"ref: refs/heads/\S+\tHEAD", line) is not None:
            defaults.append(line)
            continue
        oid, _, name = line.partition("\t")
        if name == "HEAD" and OID.fullmatch(oid) is not None:
            tips.append(oid)
    if len(defaults) != 1 or len(tips) != 1:
        raise ProbeFailed("git state unavailable")
    tip = tips[0]
    ensure_commit(path, tip, "git state unavailable")
    return is_ancestor(path, head, tip)


def have_commit(path: str, oid: str) -> bool:
    return git(path, "cat-file", "-e", f"{oid}^{{commit}}").returncode == 0


def fetch_by_oid(path: str, remote: str, oids: list[str]) -> None:
    """Fetch commits by oid from ``remote``, adding objects only.

    The fetch writes no ref and no FETCH_HEAD. Its result is not trusted:
    callers test afterwards which commits are present.
    """
    for start in range(0, len(oids), 256):
        git_timed(
            path, "-c", "gc.auto=0", "-c", "maintenance.auto=false", "fetch",
            "--quiet", "--no-tags", "--no-write-fetch-head",
            "--no-recurse-submodules", remote, *oids[start:start + 256],
        )


def ensure_commit(path: str, oid: str, reason: str) -> None:
    """Make sure commit ``oid`` is local, fetching it by oid from origin if not.

    The fetch adds objects only: it writes no ref and no FETCH_HEAD. If the
    commit still cannot be read, raise ProbeFailed(reason) (fail closed).
    """
    if have_commit(path, oid):
        return
    fetch_by_oid(path, "origin", [oid])
    if not have_commit(path, oid):
        raise ProbeFailed(reason)


def local_commits(path: str, oids: list[str]) -> set[str]:
    """The subset of ``oids`` that are commits in the local object store."""
    if not oids:
        return set()
    try:
        result = subprocess.run(
            [
                "git", "-C", path, "cat-file",
                "--batch-check=%(objectname) %(objecttype)",
            ],
            input="".join(f"{oid}\n" for oid in oids),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise ProbeFailed("git state unavailable") from exc
    if result.returncode != 0:
        raise ProbeFailed("git state unavailable")
    found = set()
    for line in result.stdout.splitlines():
        oid, _, kind = line.partition(" ")
        if kind == "commit":
            found.add(oid)
    return found


def live_heads(path: str, remote: str, reason: str, *patterns: str) -> list[tuple[str, str]]:
    """The branches ``remote`` holds now, as ``(oid, refname)``, read by ls-remote.

    Never read from a local tracking ref, which may be stale (the remote may
    have been force-pushed since this clone last fetched). A remote that
    cannot be read (error, timeout, an option-like name) or a malformed reply
    raises ProbeFailed(reason).
    """
    if not remote or remote.startswith("-"):
        raise ProbeFailed(reason)
    result = git_timed(path, "ls-remote", "--heads", remote, *patterns)
    if result is None or result.returncode != 0:
        raise ProbeFailed(reason)
    heads = []
    for line in result.stdout.splitlines():
        oid, sep, name = line.partition("\t")
        if not sep or OID.fullmatch(oid) is None or not name.startswith("refs/heads/"):
            raise ProbeFailed(reason)
        heads.append((oid.lower(), name))
    return heads


def contained_in(path: str, head: str, tips: list[str]) -> bool:
    """True when ``head`` is an ancestor of (or equal to) at least one tip.

    ``git rev-list --count HEAD --not <tips>`` is 0 exactly when HEAD is
    reachable from some tip. Every tip must be a local commit.
    """
    if not tips:
        return False
    result = git(path, "rev-list", "--count", head, "--not", *tips)
    count = result.stdout.strip()
    if result.returncode != 0 or not count.isdigit():
        raise ProbeFailed("git state unavailable")
    return count == "0"


def upstream_holds_head(path: str, branch: str, head: str) -> bool:
    """True when the branch's upstream, read live from its remote, is HEAD.

    The upstream is ``branch.<branch>.remote`` and ``branch.<branch>.merge``;
    its tip is read with ``git ls-remote``, never from the local tracking ref.
    No upstream, a local-branch upstream (remote ``.``), or an upstream the
    remote no longer holds is not a proof (False). An upstream remote that
    cannot be read raises ProbeFailed (fail closed).
    """
    remote = git(path, "config", "--get", f"branch.{branch}.remote")
    merge = git(path, "config", "--get", f"branch.{branch}.merge")
    if remote.returncode == 1 or merge.returncode == 1:
        return False
    if remote.returncode != 0 or merge.returncode != 0:
        raise ProbeFailed("git state unavailable")
    name, ref = remote.stdout.strip(), merge.stdout.strip()
    if name == "." or not ref.startswith("refs/heads/"):
        return False
    if not name.startswith("-") and is_self_remote(
        path, name, "upstream remote unavailable"
    ):
        return False
    heads = live_heads(path, name, "upstream remote unavailable", ref)
    return any(oid == head.lower() and refname == ref for oid, refname in heads)


def remote_contains_head(path: str, head: str) -> bool:
    """True when a branch some remote holds now contains HEAD.

    Each configured remote's branch tips are read live (``git ls-remote
    --heads``), never from ``refs/remotes/``, which may be stale. Tips already
    in the local object store are tested first; the rest are fetched by oid
    (objects only, no ref written) and tested. A remote that is this
    repository itself is skipped as a proof. With no proof, an unreadable
    remote or a tip that could not be fetched raises ProbeFailed("remote
    unavailable"), and a repository with no remote raises ProbeFailed("no
    remote"): unreadable means not pushed.
    """
    listed = git(path, "remote")
    if listed.returncode != 0:
        raise ProbeFailed("git state unavailable")
    remotes = listed.stdout.split()
    if not remotes:
        raise ProbeFailed("no remote")
    head = head.lower()
    unavailable = False
    for remote in remotes:
        # A remote that is this repository holds only local refs: skip it.
        try:
            if is_self_remote(path, remote, "remote unavailable"):
                continue
        except ProbeFailed:
            unavailable = True
            continue
        try:
            tips = sorted({oid for oid, _ in live_heads(path, remote, "remote unavailable")})
        except ProbeFailed:
            unavailable = True
            continue
        if head in tips:
            return True
        present = local_commits(path, tips)
        if contained_in(path, head, sorted(present)):
            return True
        missing = [oid for oid in tips if oid not in present]
        if not missing:
            continue
        fetch_by_oid(path, remote, missing)
        fetched = local_commits(path, missing)
        if len(fetched) != len(missing):
            unavailable = True
        if contained_in(path, head, sorted(fetched)):
            return True
    if unavailable:
        raise ProbeFailed("remote unavailable")
    return False


def merged_pr_contains_head(repo: str, path: str, branch: str, head: str) -> bool:
    """True when HEAD is an ancestor of (or equal to) a merged PR's final head.

    The PR is looked up by the tree's branch (``gh pr list --head <branch>
    --state merged``), and a row counts only if its ``state`` is ``MERGED`` and
    its ``headRefName`` is that branch. When the PR's ``headRefOid`` is not in
    the local object store (the remote branch was deleted after the merge), it
    is fetched by oid from origin with no ref written; if it still is not
    present, the proof fails closed. Any gh or git read failure raises
    ProbeFailed.
    """
    rows = gh_rows(repo, branch, "merged", "number,headRefOid,headRefName,state")
    if rows is None:
        raise ProbeFailed("gh unavailable")
    candidates = []
    for row in rows:
        number = row.get("number")
        oid = row.get("headRefOid")
        if type(number) is not int or number <= 0 or not isinstance(oid, str):
            raise ProbeFailed("gh unavailable")
        if OID.fullmatch(oid) is None:
            raise ProbeFailed("gh unavailable")
        if row.get("state") == "MERGED" and row.get("headRefName") == branch:
            candidates.append(oid)
    for oid in candidates:
        ensure_commit(path, oid, "merged PR head unavailable")
        if is_ancestor(path, head, oid):
            return True
    return False


def policy_reason(repo: str, path: str, *, adopting: bool = False) -> str | None:
    branch_result = git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = branch_result.stdout.strip()
    if branch_result.returncode != 0 or not branch:
        return "detached HEAD"
    open_rows = gh_rows(repo, branch, "open", "number")
    if open_rows is None:
        return "gh unavailable"
    numbers = [row.get("number") for row in open_rows]
    if not all(type(number) is int and number > 0 for number in numbers):
        return "gh unavailable"
    if numbers:
        return f"open PR #{numbers[0]}"
    head_result = git(path, "rev-parse", "--verify", "HEAD")
    head = head_result.stdout.strip()
    if head_result.returncode != 0 or OID.fullmatch(head) is None:
        return "git state unavailable"
    # Pushed: a branch some remote holds now contains HEAD, read live; a stale
    # refs/remotes/ entry is never a proof, and an unreadable remote refuses.
    try:
        if remote_contains_head(path, head):
            return None
    except ProbeFailed as exc:
        return str(exc)
    if adopting:
        # register --adopt-legacy: a HEAD that is an ancestor of a merged PR's
        # final head (update-branch, then squash merge) has its commits on the
        # remote. plan keeps the exact-head test below.
        try:
            merged = merged_pr_contains_head(repo, path, branch, head)
        except ProbeFailed as exc:
            return str(exc)
        return None if merged else "unpushed commits"
    merged = merged_pr_matches_head(repo, branch, head)
    if merged is None:
        return "gh unavailable"
    return None if merged else "unpushed commits"


def guard_reason(
    guard: str, registry: str, seat: str, repo: str, path: str, line: str
) -> str | None:
    if not os.path.isfile(guard):
        return f"cleanup guard absent: {guard}"
    env = os.environ.copy()
    env.update(CLEANUP_ITEMS_REGISTRY=registry, CLEANUP_SEAT=seat)
    payload = json.dumps({"command": line, "cwd": repo}, separators=(",", ":")) + "\n"
    try:
        result = subprocess.run(
            [guard, "--policy"],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
            env=env,
            timeout=GUARD_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired):
        return f"cleanup guard unavailable: {guard}"
    if result.returncode != 0:
        return f"cleanup guard unavailable: {guard}"
    try:
        verdict = json.loads(result.stdout)
    except json.JSONDecodeError:
        return f"cleanup guard unavailable: {guard}"
    if verdict == {"decision": "allow", "reason": "accepted"}:
        return None
    if (
        isinstance(verdict, dict)
        and verdict.get("decision") in {"deny", "no_opinion"}
        and isinstance(verdict.get("reason"), str)
    ):
        return f"cleanup guard {verdict['decision']}: {verdict['reason']}"
    return f"cleanup guard unavailable: {guard}"


def legacy_adoption_reason(repo: str, path: str) -> str | None:
    listed = listed_worktrees(repo)
    if path not in listed:
        return "not a listed worktree of repo"
    top = git(path, "rev-parse", "--show-toplevel")
    git_dir = git(path, "rev-parse", "--path-format=absolute", "--git-dir")
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    repo_common = git(repo, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if any(item.returncode != 0 for item in (top, git_dir, common, repo_common)):
        return "git state unavailable"
    if os.path.realpath(top.stdout.strip()) != path:
        return "not a worktree root"
    if os.path.realpath(common.stdout.strip()) != os.path.realpath(
        repo_common.stdout.strip()
    ):
        return "not a linked worktree of repo"
    if os.path.realpath(git_dir.stdout.strip()) == os.path.realpath(common.stdout.strip()):
        return "main worktree"

    status = git(path, "status", "--porcelain=v1", "--untracked-files=all")
    if status.returncode != 0:
        return "git state unavailable"
    if status.stdout:
        return "dirty worktree"

    branch_result = git(path, "symbolic-ref", "--quiet", "--short", "HEAD")
    branch = branch_result.stdout.strip()
    head_result = git(path, "rev-parse", "--verify", "HEAD")
    head = head_result.stdout.strip()
    if (
        branch_result.returncode != 0
        or not branch
        or head_result.returncode != 0
        or not head
    ):
        return "detached or missing branch"

    try:
        # Pushed: the upstream's live tip on its remote is HEAD.
        if upstream_holds_head(path, branch, head):
            return None
        # Pushed: HEAD is reachable from origin's default branch; no upstream needed.
        if on_default_branch(path, head):
            return None
        # Merged by PR: HEAD is an ancestor of a merged PR's final head.
        if merged_pr_contains_head(repo, path, branch, head):
            return None
    except ProbeFailed as exc:
        return str(exc)
    return "branch is neither merged by PR nor pushed to its upstream"


def adopt_legacy(args: argparse.Namespace) -> int:
    registry = registry_path(args.registry)
    repo = absolute_real(args.repo, "repo")
    path = absolute_real(args.path, "path")
    seat = field(args.seat, "seat")
    row = field(args.row, "row")
    if args.kind != "worktree":
        raise Refused("kind must be worktree")

    reason = legacy_adoption_reason(repo, path)
    if reason is None:
        reason = policy_reason(repo, path, adopting=True)
    if reason is not None:
        print(f"refused {path}: {reason}", flush=True)
        return 0

    root = os.path.dirname(registry)
    os.makedirs(root, mode=0o700, exist_ok=True)
    if os.path.islink(root) or os.path.islink(registry):
        raise Refused("registry and its directory must not be symlinks")
    os.chmod(root, 0o700)
    created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    record = "\t".join((repo, path, "worktree", seat, row, created)) + "\n"

    try:
        descriptor = os.open(registry, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        existing = ""
    else:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_SH)
            with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
                existing = stream.read()
        finally:
            os.close(descriptor)
    lines = existing.splitlines()
    if existing and (not lines or lines[0] != HEADER):
        raise Refused("malformed registry header")
    prior = [validate_row(line.split("\t")) for line in lines[1:]]
    matches = [item for item in prior if item[1] == path]
    if matches:
        if matches[0][:5] == (repo, path, "worktree", seat, row):
            print(f"adopted {path}: already registered", flush=True)
        else:
            print(f"refused {path}: already registered with different ownership", flush=True)
        return 0

    prospective = existing or HEADER + "\n"
    prefix = "" if prospective.endswith("\n") else "\n"
    with tempfile.TemporaryDirectory(prefix=".cleanup-items-adopt-", dir=root) as candidate_dir:
        os.chmod(candidate_dir, 0o700)
        candidate_registry = os.path.join(candidate_dir, "cleanup-items.tsv")
        candidate_descriptor = os.open(
            candidate_registry,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
            0o600,
        )
        try:
            os.write(
                candidate_descriptor,
                (prospective + prefix + record).encode("utf-8"),
            )
        finally:
            os.close(candidate_descriptor)

        line = shlex.join(("git", "-C", repo, "worktree", "remove", path))
        guard = os.path.expanduser(
            os.environ.get(
                "CLEANUP_GUARD",
                os.path.join("~", ".claude", "hooks", "destructive-command-guard.sh"),
            )
        )
        reason = guard_reason(guard, candidate_registry, seat, repo, path, line)
        if reason is None:
            reason = legacy_adoption_reason(repo, path)
        if reason is not None:
            print(f"refused {path}: {reason}", flush=True)
            return 0

        descriptor = os.open(
            registry, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            os.fchmod(descriptor, 0o600)
            with os.fdopen(os.dup(descriptor), "r", encoding="utf-8") as stream:
                current = stream.read()
            current_lines = current.splitlines()
            if current and (not current_lines or current_lines[0] != HEADER):
                raise Refused("malformed registry header")
            current_prior = [
                validate_row(item.split("\t")) for item in current_lines[1:]
            ]
            current_matches = [item for item in current_prior if item[1] == path]
            if current_matches:
                if current_matches[0][:5] == (repo, path, "worktree", seat, row):
                    print(f"adopted {path}: already registered", flush=True)
                else:
                    print(
                        f"refused {path}: already registered with different ownership",
                        flush=True,
                    )
                return 0
            current_prefix = "" if current.endswith("\n") or not current else "\n"
            if not current:
                current_prefix = HEADER + "\n"
            os.lseek(descriptor, 0, os.SEEK_END)
            os.write(descriptor, (current_prefix + record).encode("utf-8"))
        finally:
            os.close(descriptor)
    print(f"adopted {path}", flush=True)
    return 0


def seat_label(value: str, role: str) -> str:
    if SEAT_LABEL.fullmatch(value) is None:
        raise Refused(f"{role} label invalid: {value!r}")
    return value


def seat_registry(scratch: str, label: str, role: str) -> str:
    """Derive a seat's registry from its lifecycle scratch, as launch-seat does."""
    field(scratch, f"{role} scratch")
    if not os.path.isabs(scratch):
        raise Refused(f"{role} scratch must be absolute")
    return os.path.join(
        os.path.realpath(scratch), f"watch-{label}", REGISTRY_BASENAME
    )


def open_trusted_registry(registry: str, role: str, flags: int) -> int:
    """Open a registry only if it passes the cleanup guard's trust checks."""
    name = f"{role} registry {registry}"
    if os.path.basename(registry) != REGISTRY_BASENAME:
        raise Refused(f"{name}: basename must be {REGISTRY_BASENAME}")
    if not os.path.isabs(registry) or os.path.realpath(registry) != registry:
        raise Refused(f"{name}: not an absolute canonical path (symlink component)")
    uid = os.getuid()
    try:
        parent = os.lstat(os.path.dirname(registry))
    except OSError as exc:
        raise Refused(f"{name}: directory unreadable: {exc.strerror or exc}") from exc
    if not stat.S_ISDIR(parent.st_mode):
        raise Refused(f"{name}: parent is not a directory")
    if parent.st_uid != uid:
        raise Refused(f"{name}: directory not owned by the current user")
    if stat.S_IMODE(parent.st_mode) != 0o700:
        raise Refused(
            f"{name}: directory mode {stat.S_IMODE(parent.st_mode):04o}, expected 0700"
        )
    try:
        descriptor = os.open(registry, flags | os.O_NOFOLLOW)
    except FileNotFoundError as exc:
        raise Refused(f"{name}: missing") from exc
    except OSError as exc:
        raise Refused(f"{name}: cannot open: {exc.strerror or exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Refused(f"{name}: not a regular file")
        if info.st_uid != uid:
            raise Refused(f"{name}: not owned by the current user")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise Refused(
                f"{name}: mode {stat.S_IMODE(info.st_mode):04o}, expected 0600"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def lock_registry(descriptor: int, operation: int, name: str) -> None:
    deadline = time.monotonic() + LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fcntl.flock(descriptor, operation | fcntl.LOCK_NB)
            return
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise Refused(f"{name}: lock busy") from None
            time.sleep(0.05)


def read_descriptor(descriptor: int, name: str) -> str:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(descriptor, 65536)
        if not chunk:
            break
        chunks.append(chunk)
    try:
        return b"".join(chunks).decode("utf-8")
    except UnicodeDecodeError as exc:
        raise Refused(f"{name}: not UTF-8") from exc


def validate_other_row(parts: list[str]) -> tuple[str, str, str, str, str, str]:
    """Hold a row of any other kind to the grammar the guard reads every row by."""
    repo, path, kind, seat, row, created = parts
    for value, label in zip(
        parts, ("repo", "path", "kind", "seat", "row", "created_at")
    ):
        field(value, label)
    if ROW_KIND.fullmatch(kind) is None:
        raise Refused("kind is malformed")
    if not os.path.isabs(repo) or os.path.realpath(repo) != repo:
        raise Refused("repo must be an absolute realpath")
    if not os.path.isabs(path) or os.path.realpath(path) != path:
        raise Refused("path must be an absolute realpath")
    if STAMP.fullmatch(created) is None:
        raise Refused("created_at must be UTC YYYY-MM-DDTHH:MM:SSZ")
    return repo, path, kind, seat, row, created


def parse_transfer_registry(
    text: str, name: str
) -> list[tuple[str, str, str, str, str, str]]:
    """Parse every row of every kind before anything is written.

    A worktree row must pass the full ``validate_row``; a row of any other kind
    must carry the same absolute canonical repo and path. No path may appear on
    two rows, whatever their kinds.
    """
    body = text[:-1] if text.endswith("\n") else text
    lines = body.split("\n")
    if lines[0] != HEADER:
        raise Refused(f"{name}: malformed registry header")
    rows = []
    for number, line in enumerate(lines[1:], start=2):
        parts = line.split("\t")
        try:
            if len(parts) != 6:
                raise Refused("registry row must have six TSV fields")
            if parts[2] == "worktree":
                rows.append(validate_row(parts))
            else:
                rows.append(validate_other_row(parts))
        except Refused as exc:
            raise Refused(f"{name}: line {number}: {exc}") from exc
    seen: set[str] = set()
    for item in rows:
        if item[1] in seen:
            raise Refused(f"{name}: duplicate path {item[1]}")
        seen.add(item[1])
    return rows


def under(path: str, root: str) -> bool:
    """True when ``path`` is ``root`` or lies below it.

    Both are resolved (symlink-free, absolute) paths; the prefix must end at
    a path-component boundary, so ``/a/b`` is not under ``/a/bc``.
    """
    if path == root:
        return True
    base = root if root.endswith(os.sep) else root + os.sep
    return path.startswith(base)


def read_private_text(path: str) -> str | None:
    """The text of a regular file read without following a final symlink.

    A missing, symlinked, non-regular or unreadable file gives ``None``.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        with os.fdopen(os.dup(descriptor), encoding="utf-8") as stream:
            return stream.read()
    except (OSError, UnicodeError):
        return None
    finally:
        os.close(descriptor)


def parse_inherited_roots(text: str) -> list[tuple[str, str, str, str]] | None:
    """The entries of an inherited-roots record, or ``None`` when malformed.

    ``inherited-roots.tsv`` sits beside a seat's registry
    (``<scratch>/watch-<label>/``); only ``transfer`` writes it, under the
    exclusive lock of that registry. Its grammar is exactly:

    - an empty file (created, never written): no entries; otherwise
    - the line ``path<TAB>from<TAB>row<TAB>created_at``, then one line per
      carried row: ``<path>\t<from>\t<row>\t<created_at>``, where ``path``
      is the absolute, normalised path of a row an earlier transfer carried
      to this seat, ``from`` is the seat label it came from, ``row`` is the
      row id (non-empty, no tab or newline) and ``created_at`` is a UTC
      ``YYYY-MM-DDTHH:MM:SSZ`` stamp;
    - every line, the last included, ends in a newline.

    Any other text (a torn last line, a wrong header, a line with another
    field count or a bad field) is malformed as a whole: ``None``.
    """
    if text == "":
        return []
    if not text.endswith("\n"):
        return None
    lines = text[:-1].split("\n")
    if lines[0] != INHERITED_ROOTS_HEADER:
        return None
    entries = []
    for line in lines[1:]:
        parts = line.split("\t")
        if len(parts) != 4:
            return None
        path, origin, row, created = parts
        try:
            for value, name in zip(parts, ("path", "from", "row", "created_at")):
                field(value, name)
        except Refused:
            return None
        if (
            not os.path.isabs(path)
            or os.path.normpath(path) != path
            or SEAT_LABEL.fullmatch(origin) is None
            or STAMP.fullmatch(created) is None
        ):
            return None
        entries.append((path, origin, row, created))
    return entries


def read_inherited_roots(path: str) -> frozenset[str]:
    """The exact roots a seat inherited, from its inherited-roots record.

    The record must be a regular file reached without a final symlink, owned
    by the current user with mode 0600, UTF-8 and in the grammar of
    ``parse_inherited_roots``. Anything else (missing, symlinked, another
    owner or mode, unreadable, malformed) gives no inherited roots, so the
    rows only it would vouch for are named, never carried.
    """
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError:
        return frozenset()
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            return frozenset()
        with os.fdopen(os.dup(descriptor), encoding="utf-8") as stream:
            entries = parse_inherited_roots(stream.read())
    except (OSError, UnicodeError):
        return frozenset()
    finally:
        os.close(descriptor)
    if entries is None:
        return frozenset()
    return frozenset(entry[0] for entry in entries)


def open_inherited_record(path: str, name: str) -> int:
    """Open the successor's inherited-roots record read-write.

    A missing record is created empty with mode 0600; its directory is the
    successor registry's, which the registry checks hold to the current user
    and mode 0700. An existing record must be a regular file, not a symlink,
    owned by the current user with mode 0600, or the transfer is refused.
    """
    try:
        descriptor = os.open(
            path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
    except FileExistsError:
        descriptor = None
    except OSError as exc:
        raise Refused(f"{name}: cannot create: {exc.strerror or exc}") from exc
    if descriptor is not None:
        try:
            os.fchmod(descriptor, 0o600)
        except BaseException:
            os.close(descriptor)
            raise
        return descriptor
    try:
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    except OSError as exc:
        raise Refused(f"{name}: cannot open: {exc.strerror or exc}") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise Refused(f"{name}: not a regular file")
        if info.st_uid != os.getuid():
            raise Refused(f"{name}: not owned by the current user")
        if stat.S_IMODE(info.st_mode) != 0o600:
            raise Refused(
                f"{name}: mode {stat.S_IMODE(info.st_mode):04o}, expected 0600"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def append_block(descriptor: int, payload: bytes, name: str) -> int:
    """Append ``payload`` in one write and return the size before it.

    A short or failed write is cut back to that size, so nothing is appended.
    """
    size = os.lseek(descriptor, 0, os.SEEK_END)
    try:
        written = os.write(descriptor, payload)
    except OSError:
        os.ftruncate(descriptor, size)
        raise
    if written != len(payload):
        os.ftruncate(descriptor, size)
        raise Refused(f"{name}: short write; nothing appended")
    return size


def seat_roots(
    scratch: str, label: str
) -> tuple[str, frozenset[str], frozenset[str]]:
    """The predecessor seat's own roots, derived from its scratch.

    Never read from a cleanup row, so a row cannot vouch for itself:

    - the scratch root: the realpath of ``scratch``, the lifecycle scratch
      that holds ``watch-<label>/cleanup-items.tsv``;
    - the worktree roots, each resolved with realpath:
      - every ``path`` in ``<scratch>/lane-roots.tsv`` (lane_roots.py's record
        of the lane worktrees dispatched under that scratch), and
      - the cwd field of every ``launch`` row for ``label`` in
        ``<scratch>/seat-lifecycle.tsv`` (the directory launch-seat started
        the seat in, which is the worktree it registers for the seat);
    - the inherited roots: every path in
      ``<scratch>/watch-<label>/inherited-roots.tsv``, the record an earlier
      transfer into this seat wrote beside its registry (see
      ``read_inherited_roots``). Each is an exact root: it vouches for a row
      at that path only, never for a path below it.

    A file that is missing, a symlink, not a regular file, unreadable, or not
    in its writer's format gives no roots, and a lane-roots or lifecycle line
    that is not well formed is skipped: fewer roots only means more rows are
    named, never fewer.
    """
    scratch_root = os.path.realpath(scratch)
    roots: set[str] = set()
    lanes = read_private_text(os.path.join(scratch_root, LANE_ROOTS_BASENAME))
    if lanes is not None:
        lines = lanes.splitlines()
        if lines and lines[0] == LANE_ROOTS_HEADER:
            for line in lines[1:]:
                path = line.split("\t")[0]
                if path and os.path.isabs(path):
                    roots.add(os.path.realpath(path))
    lifecycle = read_private_text(os.path.join(scratch_root, LIFECYCLE_BASENAME))
    if lifecycle is not None:
        for line in lifecycle.splitlines():
            parts = line.split("\t")
            if (
                len(parts) == LIFECYCLE_FIELDS
                and parts[1] == "launch"
                and parts[2] == label
                and os.path.isabs(parts[4])
            ):
                roots.add(os.path.realpath(parts[4]))
    inherited = read_inherited_roots(
        os.path.join(scratch_root, f"watch-{label}", INHERITED_ROOTS_BASENAME)
    )
    return scratch_root, frozenset(roots), inherited


def own_row(
    path: str,
    scratch_root: str,
    worktree_roots: frozenset[str],
    inherited_roots: frozenset[str] = frozenset(),
) -> bool:
    """True when a row's resolved ``path`` belongs to the seat's own roots.

    A path that is exactly one of the seat's worktree roots or inherited
    roots is its own, even when it lies inside another of its worktrees (a
    linked worktree nested in another). Otherwise a path is the seat's own
    when it lies under its scratch root and is not strictly inside one of
    its worktree roots: a tree nested inside a seat worktree is a test
    fixture or another tool's output, never a worktree the seat created. A
    worktree root that holds the scratch itself (a launch directory above
    it) is left out of that nesting test, or it would disown every row under
    the scratch.
    """
    if path in worktree_roots or path in inherited_roots:
        return True
    enclosing = [root for root in worktree_roots if not under(scratch_root, root)]
    if any(under(path, root) for root in enclosing):
        return False
    return under(path, scratch_root)


def transfer_rows(args: argparse.Namespace) -> int:
    predecessor = seat_label(args.from_label, "predecessor")
    successor = seat_label(args.to_label, "successor")
    if predecessor == successor:
        raise Refused("predecessor and successor are the same seat")
    source = seat_registry(args.scratch, predecessor, "predecessor")
    target = seat_registry(
        args.successor_scratch or args.scratch, successor, "successor"
    )
    caller_registry = getattr(args, "caller_registry", None)
    if caller_registry is not None:
        field(caller_registry, "caller registry")
        if target != caller_registry or os.path.realpath(caller_registry) != caller_registry:
            raise Refused("caller registry does not rebuild from its scratch and seat (canonical path required)")
    if source == target:
        raise Refused("predecessor and successor share one registry")
    source_name = f"predecessor registry {source}"
    target_name = f"successor registry {target}"
    source_fd = open_trusted_registry(source, "predecessor", os.O_RDONLY)
    try:
        target_fd = open_trusted_registry(target, "successor", os.O_RDWR)
        try:
            # One global order (by path) so two concurrent transfers never
            # wait on each other's locks in opposite orders.
            for _, descriptor, operation, name in sorted(
                (
                    (source, source_fd, fcntl.LOCK_SH, source_name),
                    (target, target_fd, fcntl.LOCK_EX, target_name),
                )
            ):
                lock_registry(descriptor, operation, name)
            owned = [
                item
                for item in parse_transfer_registry(
                    read_descriptor(source_fd, source_name), source_name
                )
                if item[2] == "worktree" and item[3] == predecessor
            ]
            # A row the predecessor did not create under its own roots (a
            # test fixture written through an inherited registry, say) is not
            # carried; it is named once the transfer has succeeded.
            scratch_root, worktree_roots, inherited_roots = seat_roots(
                args.scratch, predecessor
            )
            foreign = [
                item[1] for item in owned
                if not own_row(
                    item[1], scratch_root, worktree_roots, inherited_roots
                )
            ]
            owned = [item for item in owned if item[1] not in foreign]
            current = read_descriptor(target_fd, target_name)
            # Every successor row, whatever its kind, claims its path: only
            # the exact row an earlier transfer wrote may already hold it.
            present = {
                item[1]: item
                for item in parse_transfer_registry(current, target_name)
            }
            created = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            records = []
            for repo, path, kind, _, row, _ in owned:
                existing = present.get(path)
                if existing is not None and existing[:5] == (
                    repo, path, kind, successor, row
                ):
                    continue
                if existing is not None:
                    raise Refused(
                        f"{target_name}: {path} already registered with different "
                        f"ownership ({existing[2]} row {existing[4]} of {existing[3]})"
                    )
                records.append("\t".join((repo, path, kind, successor, row, created)))
            if owned:
                # Every carried path, already present or appended now, is
                # recorded as an inherited root of the successor, so its own
                # later transfer still carries the row. The record is written
                # first, still under the successor registry's exclusive lock,
                # and cut back if the registry append then fails: a carried
                # row never lands without its record. A registry row with no
                # record (written before this record existed) is named at the
                # successor's next transfer; rerunning this transfer records
                # it.
                carry_inherited(
                    os.path.join(os.path.dirname(target), INHERITED_ROOTS_BASENAME),
                    owned, predecessor, created, target_fd, current, records,
                    target_name,
                )
        finally:
            os.close(target_fd)
    finally:
        os.close(source_fd)
    for path in foreign:
        print(f"transfer_foreign_row: {path}", flush=True)
    print(
        f"cleanup_transfer: {len(records)} rows from {predecessor} to {successor}",
        flush=True,
    )
    if args.report_paths:
        # Internal line grammar for retire's plan selector. Report all carried
        # paths, not only new appends, so a repeated transfer plans the same set.
        for _, path, _, _, _, _ in owned:
            print(f"cleanup_transfer_path: {path}", flush=True)
    return 0


def carry_inherited(
    inherited: str,
    owned: list[tuple[str, str, str, str, str, str]],
    predecessor: str,
    created: str,
    target_fd: int,
    current: str,
    records: list[str],
    target_name: str,
) -> None:
    """Record ``owned``'s paths as inherited roots, then append ``records``.

    Runs under the successor registry's exclusive lock. A malformed record
    refuses the transfer before anything is written. Each block is one write
    (``append_block``); when the registry append fails, the record is cut
    back to its size before this transfer. The registry block is appended in
    place rather than renamed over the registry, because other writers lock
    the registry's own inode.
    """
    name = f"successor inherited-roots record {inherited}"
    descriptor = open_inherited_record(inherited, name)
    try:
        text = read_descriptor(descriptor, name)
        entries = parse_inherited_roots(text)
        if entries is None:
            raise Refused(f"{name}: malformed record")
        known = {entry[0] for entry in entries}
        lines = [
            "\t".join((path, predecessor, row, created))
            for _, path, _, _, row, _ in owned
            if path not in known
        ]
        before = None
        if lines:
            header = INHERITED_ROOTS_HEADER + "\n" if text == "" else ""
            payload = (header + "".join(line + "\n" for line in lines)).encode("utf-8")
            before = append_block(descriptor, payload, name)
        if records:
            prefix = "" if current.endswith("\n") else "\n"
            payload = (prefix + "\n".join(records) + "\n").encode("utf-8")
            try:
                append_block(target_fd, payload, target_name)
            except BaseException:
                if before is not None:
                    os.ftruncate(descriptor, before)
                raise
    finally:
        os.close(descriptor)


def transfer(args: argparse.Namespace) -> int:
    try:
        return transfer_rows(args)
    except Refused as exc:
        raise Refused(f"cleanup_transfer refused: {exc}") from exc


def cleanup_log(path: str, text: str) -> None:
    """Append one timestamped line to the cleanup log, dropping it on any failure.

    The log only records: a line that cannot be written (an unwritable
    directory, a path that is a directory, a symlink or a fifo with no reader,
    a full disk) is dropped, and the plan's output and exit code do not change.

    As in merge-pr.sh's REFUSAL_LOG_PY, the log is never shortened: the
    whole line is written (looping on partial writes), and on a failure that
    cannot be finished past (a short write, EFBIG past a file-size limit) the
    write stops and the fragment stays. Before writing, the log's last byte is
    read (pread; the log is opened read-write), and if the log is not empty
    and does not end in a newline a newline is written first, so a torn tail
    is closed off and this line starts on its own. A torn fragment can remain
    as one unparseable line; no other writer's line is ever lost. SIGXFSZ is
    ignored (CPython already ignores it at startup; this keeps it so), so a
    file-size limit fails the write instead of killing the sweep. Appenders
    take a non-blocking flock and drop the line when it is busy. Only a
    regular file is written.
    """
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    text = text.replace("\\", "\\\\").replace("\r", "\\r").replace("\n", "\\n")
    data = f"{stamp} worktree-sweep {text}\n".encode("utf-8")
    flags = (
        os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
        | os.O_CLOEXEC
    )
    try:
        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        handle = os.open(path, flags, 0o600)
        try:
            if not stat.S_ISREG(os.fstat(handle).st_mode):
                raise OSError("not a regular file")
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            # Nothing here ever shortens the log. A torn tail left by an earlier
            # writer is closed off with a newline, so this line starts on its own.
            size = os.fstat(handle).st_size
            if size and os.pread(handle, 1, size - 1) != b"\n":
                data = b"\n" + data
            done = 0
            while done < len(data):
                written = os.write(handle, data[done:])
                if written <= 0:
                    raise OSError("short write")
                done += written
        finally:
            os.close(handle)
    except Exception:
        pass


def path_missing(path: str) -> bool:
    """True only when ``path`` does not exist on disk.

    A path that exists but cannot be inspected (a permission error, say) is
    not missing: it keeps the policy's normal refusal.
    """
    try:
        os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return True
    except OSError:
        return False
    return False


def plan(args: argparse.Namespace) -> int:
    registry = registry_path(args.registry)
    # The seat's scratch holds its registry (`<scratch>/watch-<label>/`), so
    # the log defaults to that directory; roll-land names its own scratch.
    log_path = args.cleanup_log or os.path.join(
        os.path.dirname(registry), "cleanup.log"
    )
    rows = read_registry(registry)
    paths = set(args.path) if args.path is not None else None
    selected = [
        item
        for item in rows
        if ((args.row is not None and item[4] == args.row)
            or (args.seat is not None and item[3] == args.seat))
        and (paths is None or item[1] in paths)
    ]
    guard = os.path.expanduser(
        os.environ.get(
            "CLEANUP_GUARD",
            os.path.join("~", ".claude", "hooks", "destructive-command-guard.sh"),
        )
    )
    for repo, path, _, seat, _, _ in selected:
        if path_missing(path):
            # Already removed: report it, never as a kept tree. A path that
            # exists but is not a worktree still goes through the policy.
            print(f"gone {path}", flush=True)
            cleanup_log(log_path, f"plan gone {path}")
            continue
        reason = policy_reason(repo, path)
        line = shlex.join(("git", "-C", repo, "worktree", "remove", path))
        if reason is None:
            reason = guard_reason(guard, registry, seat, repo, path, line)
        if reason is None:
            print(line, flush=True)
        else:
            print(f"kept {path}: {reason}", flush=True)
            cleanup_log(log_path, f"plan kept {path}: {reason}")
    return 0


def parse(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    add = commands.add_parser("register")
    add.add_argument("--registry", required=True)
    add.add_argument("--repo", required=True)
    add.add_argument("--path", required=True)
    add.add_argument("--kind", required=True, choices=("worktree",))
    add.add_argument("--seat", required=True)
    add.add_argument("--row", required=True)
    add.add_argument("--adopt-legacy", action="store_true")
    show = commands.add_parser("plan")
    show.add_argument("--registry", required=True)
    select = show.add_mutually_exclusive_group(required=True)
    select.add_argument("--row")
    select.add_argument("--seat")
    show.add_argument("--cleanup-log")
    show.add_argument("--path", action="append", help="limit selected rows to these exact paths (repeatable)")
    move = commands.add_parser("transfer")
    move.add_argument("--scratch", required=True)
    move.add_argument("--successor-scratch")
    move.add_argument("--from", dest="from_label", required=True)
    move.add_argument("--to", dest="to_label", required=True)
    move.add_argument("--caller-registry", help="require the rebuilt target to equal this canonical caller registry")
    move.add_argument("--report-paths", action="store_true", help="report every carried path, including already-present rows")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        args = parse(argv)
        if args.command == "register":
            return adopt_legacy(args) if args.adopt_legacy else register(args)
        if args.command == "transfer":
            return transfer(args)
        return plan(args)
    except (Refused, OSError) as exc:
        print(f"worktree-sweep: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

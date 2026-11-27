from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[2]
SWEEP = Path(os.environ.get("WORKTREE_SWEEP_TEST_SCRIPT", ROOT / "scripts" / "worktree-sweep.py"))
HEADER = "repo\tpath\tkind\tseat\trow\tcreated_at"


def run_git(path: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True, capture_output=True
    )
    return result.stdout.strip()


@pytest.fixture
def cleanup_case(tmp_path: Path):
    remote = tmp_path / "origin.git"
    repo = tmp_path / "repo"
    worktree = tmp_path / "feature"
    registry = tmp_path / "watch-seat" / "cleanup-items.tsv"
    bin_dir = tmp_path / "bin"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
    subprocess.run(["git", "clone", "-q", str(remote), str(repo)], check=True)
    run_git(repo, "config", "user.name", "Fixture")
    run_git(repo, "config", "user.email", "fixture@example.invalid")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    run_git(repo, "add", "base.txt")
    run_git(repo, "commit", "-q", "-m", "base")
    run_git(repo, "push", "-q", "origin", "main")
    run_git(repo, "worktree", "add", "-q", "-b", "feature", str(worktree), "origin/main")
    (worktree / "feature.txt").write_text("feature\n", encoding="utf-8")
    run_git(worktree, "add", "feature.txt")
    run_git(worktree, "commit", "-q", "-m", "feature")
    bin_dir.mkdir()
    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "state = sys.argv[sys.argv.index('--state') + 1]\n"
        "if os.environ.get('GH_FAIL') == '1': raise SystemExit(1)\n"
        "if os.environ.get('GH_MALFORMED') == state: print('{'); raise SystemExit(0)\n"
        "if state == 'open':\n"
        "    n = os.environ.get('GH_OPEN')\n"
        "    print(json.dumps([{'number': int(n)}] if n else []))\n"
        "else:\n"
        "    # One fixture PR (GH_MERGED*), listed only under its own state, as gh does.\n"
        "    n = os.environ.get('GH_MERGED')\n"
        "    oid = os.environ.get('GH_MERGED_HEAD')\n"
        "    pr_state = os.environ.get('GH_MERGED_STATE', 'MERGED')\n"
        "    branch = os.environ.get('GH_MERGED_BRANCH', 'feature')\n"
        "    fields = sys.argv[sys.argv.index('--json') + 1].split(',')\n"
        "    row = {'number': int(n) if n else 0, 'headRefOid': oid, 'headRefName': branch, 'state': pr_state}\n"
        "    # GH_MERGED_ANY_STATE=1 lists the PR under every query, whatever its state.\n"
        "    any_state = os.environ.get('GH_MERGED_ANY_STATE') == '1'\n"
        "    listed = bool(n) and (any_state or state in ('all', pr_state.lower()))\n"
        "    print(json.dumps([{k: row[k] for k in fields}] if listed else []))\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    guard = bin_dir / "cleanup-guard"
    # Mirror destructive-command-guard.sh::trusted_cleanup_registry: its
    # CLEANUP_REGISTRY_BASENAME contract requires exactly cleanup-items.tsv.
    guard.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, shlex, stat, subprocess, sys, time\n"
        "payload=json.load(sys.stdin); command=payload['command']; words=shlex.split(command)\n"
        "log=os.environ.get('GUARD_LOG')\n"
        "if log: pathlib.Path(log).write_text(command + '\\n', encoding='utf-8')\n"
        "counter=os.environ.get('GUARD_CALL_COUNT'); count=0\n"
        "if counter:\n"
        "  counter_path=pathlib.Path(counter)\n"
        "  count=int(counter_path.read_text() or '0') + 1 if counter_path.exists() else 1\n"
        "  counter_path.write_text(str(count), encoding='utf-8')\n"
        "registry=pathlib.Path(os.environ['CLEANUP_ITEMS_REGISTRY'])\n"
        "seat=os.environ['CLEANUP_SEAT']; decision='allow'; reason='accepted'\n"
        "try:\n"
        "  trusted=(registry.name=='cleanup-items.tsv' and stat.S_IMODE(registry.stat().st_mode)==0o600 and stat.S_IMODE(registry.parent.stat().st_mode)==0o700)\n"
        "  rows=registry.read_text(encoding='utf-8').splitlines()[1:]\n"
        "  target=words[5]; repo=words[2]\n"
        "  owned=any(row.split('\\t')[:4]==[repo,target,'worktree',seat] for row in rows)\n"
        "except (OSError, IndexError): trusted=False; owned=False; target=''\n"
        "if words[:2] != ['git','-C'] or words[3:5] != ['worktree','remove'] or len(words)!=6 or '--force' in words: trusted=False\n"
        "allowed=os.environ.get('GUARD_ALLOWED_ROOT')\n"
        "outside=bool(allowed) and os.path.commonpath([os.path.realpath(target), os.path.realpath(allowed)]) != os.path.realpath(allowed)\n"
        "if os.environ.get('GUARD_DENY') == '1' or outside or not trusted or not owned:\n"
        "  decision='deny'; reason='unsafe_worktree_remove'\n"
        "elif subprocess.run(['git','-C',target,'status','--porcelain'],capture_output=True,text=True).stdout:\n"
        "  decision='deny'; reason='unsafe_worktree_remove'\n"
        "else:\n"
        "  cache=pathlib.Path(target)/'.pytest_cache'\n"
        "  if cache.exists() and any((p.name.lower() in {'head','refs','objects'}) for p in cache.rglob('*')):\n"
        "    decision='deny'; reason='unsafe_worktree_remove'\n"
        "if counter and count == int(os.environ.get('GUARD_SLEEP_CALL', '2')):\n"
        "  pathlib.Path(os.environ['GUARD_SLEEP_MARKER']).write_text('sleeping', encoding='utf-8')\n"
        "  release=os.environ.get('GUARD_SLEEP_RELEASE')\n"
        "  if release:\n"
        "    while not pathlib.Path(release).exists(): time.sleep(0.01)\n"
        "  else: time.sleep(float(os.environ['GUARD_SLEEP_SECONDS']))\n"
        "print(json.dumps({'decision':decision,'reason':reason}))\n",
        encoding="utf-8",
    )
    guard.chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=f"{bin_dir}{os.pathsep}{env['PATH']}",
        CLEANUP_GUARD=str(guard),
        GUARD_LOG=str(tmp_path / "guard.log"),
    )
    return {
        "remote": remote.resolve(),
        "repo": repo.resolve(),
        "worktree": worktree.resolve(),
        "registry": registry.resolve(),
        "guard": guard.resolve(),
        "guard_log": tmp_path / "guard.log",
        "env": env,
        "tmp": tmp_path,
    }


def invoke(case, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, str(SWEEP), *args], text=True, capture_output=True, check=False,
        env=env or case["env"],
    )


def register(case, *, repo: Path | None = None, path: Path | None = None):
    return invoke(
        case, "register", "--registry", str(case["registry"]),
        "--repo", str(repo or case["repo"]), "--path", str(path or case["worktree"]),
        "--kind", "worktree", "--seat", "seat", "--row", "ROW",
    )


def adopt(
    case, *, repo: Path | None = None, path: Path | None = None,
    env: dict[str, str] | None = None,
):
    return invoke(
        case, "register", "--adopt-legacy", "--registry", str(case["registry"]),
        "--repo", str(repo or case["repo"]), "--path", str(path or case["worktree"]),
        "--kind", "worktree", "--seat", "seat", "--row", "ROW", env=env,
    )


def registry_rows(case) -> list[list[str]]:
    if not case["registry"].exists():
        return []
    lines = case["registry"].read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    return [line.split("\t") for line in lines[1:]]


def plan(case, *, env: dict[str, str] | None = None):
    return invoke(
        case, "plan", "--registry", str(case["registry"]), "--row", "ROW", env=env,
    )


def pushed(case) -> None:
    run_git(case["worktree"], "push", "-q", "-u", "origin", "feature")


def test_adopt_legacy_accepts_a_clean_branch_merged_into_origin_main(cleanup_case):
    case = cleanup_case
    run_git(case["repo"], "merge", "-q", "--ff-only", "feature")
    run_git(case["repo"], "push", "-q", "origin", "main")

    result = adopt(case)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert [row[:5] for row in registry_rows(case)] == [[
        str(case["repo"]), str(case["worktree"]), "worktree", "seat", "ROW"
    ]]
    assert "--force" not in case["guard_log"].read_text(encoding="utf-8")


def test_adopt_legacy_accepts_a_squash_merged_branch_without_upstream(cleanup_case):
    case = cleanup_case
    env = case["env"].copy()
    env.update(
        GH_MERGED="91",
        GH_MERGED_HEAD=run_git(case["worktree"], "rev-parse", "HEAD"),
    )

    result = adopt(case, env=env)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert len(registry_rows(case)) == 1


def test_adopt_legacy_accepts_a_pushed_branch_without_origin_main(cleanup_case):
    case = cleanup_case
    pushed(case)
    run_git(case["repo"], "update-ref", "-d", "refs/remotes/origin/main")

    result = adopt(case)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert len(registry_rows(case)) == 1


def test_adopt_legacy_accepts_a_clean_pushed_branch_and_is_idempotent(cleanup_case):
    case = cleanup_case
    pushed(case)

    first = adopt(case)
    second = adopt(case)

    assert first.stdout == f"adopted {case['worktree']}\n"
    assert second.stdout == f"adopted {case['worktree']}: already registered\n"
    assert len(registry_rows(case)) == 1


def test_concurrent_adopters_register_the_same_worktree_once(cleanup_case):
    case = cleanup_case
    pushed(case)
    marker = case["tmp"] / "first-adopter-in-guard"
    release = case["tmp"] / "release-first-adopter"
    env = case["env"].copy()
    env.update(
        GUARD_CALL_COUNT=str(case["tmp"] / "guard-count"),
        GUARD_SLEEP_CALL="1",
        GUARD_SLEEP_MARKER=str(marker),
        GUARD_SLEEP_RELEASE=str(release),
    )
    command = [
        sys.executable, str(SWEEP), "register", "--adopt-legacy",
        "--registry", str(case["registry"]),
        "--repo", str(case["repo"]),
        "--path", str(case["worktree"]),
        "--kind", "worktree", "--seat", "seat", "--row", "ROW",
    ]
    first = subprocess.Popen(
        command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    deadline = time.monotonic() + 3
    while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "the first adopter must be held inside the guard"

    try:
        second = subprocess.run(
            command, text=True, capture_output=True, check=False, env=env, timeout=3,
        )
    finally:
        release.write_text("released\n", encoding="utf-8")
    first_stdout, first_stderr = first.communicate(timeout=3)

    assert first.returncode == 0, first_stderr
    assert second.returncode == 0, second.stderr
    outputs = [first_stdout, second.stdout]
    assert outputs.count(f"adopted {case['worktree']}\n") == 1, outputs
    assert outputs.count(
        f"adopted {case['worktree']}: already registered\n"
    ) == 1, outputs
    assert [row[1] for row in registry_rows(case)] == [str(case["worktree"])]


def test_adopt_legacy_refuses_an_open_pr(cleanup_case):
    case = cleanup_case
    pushed(case)
    env = case["env"].copy()
    env["GH_OPEN"] = "42"

    result = adopt(case, env=env)

    assert result.stdout == f"refused {case['worktree']}: open PR #42\n"
    assert not case["registry"].exists()


def test_adopt_legacy_refuses_a_local_upstream_that_was_never_pushed(cleanup_case):
    case = cleanup_case
    run_git(case["worktree"], "branch", "local-upstream", "HEAD")
    run_git(
        case["worktree"],
        "branch", "--set-upstream-to", "local-upstream", "feature",
    )

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert not case["registry"].exists()


def test_adopt_legacy_refuses_a_dirty_worktree_by_name(cleanup_case):
    case = cleanup_case
    pushed(case)
    (case["worktree"] / "feature.txt").write_text("changed\n", encoding="utf-8")

    result = adopt(case)

    assert result.stdout == f"refused {case['worktree']}: dirty worktree\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_an_untracked_file_by_name(cleanup_case):
    case = cleanup_case
    pushed(case)
    (case["worktree"] / "untracked.txt").write_text("new\n", encoding="utf-8")

    result = adopt(case)

    assert result.stdout == f"refused {case['worktree']}: dirty worktree\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_an_unmerged_unpushed_branch_by_name(cleanup_case):
    case = cleanup_case

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


def update_branch_and_squash_merge(case, *, fetch_final_head: bool) -> str:
    """Push feature, update-branch it on the remote, squash-merge it, delete it.

    Mirrors a GitHub PR that was update-branched (a merge commit on top of the
    seat's head) and then squash-merged with its branch deleted. The final head
    stays reachable on the remote as refs/pull/91/head, as on GitHub. Returns
    that final head; the worktree's HEAD is its strict ancestor.
    """
    pushed(case)
    remote, other = case["remote"], case["tmp"] / "other"
    run_git(remote, "config", "uploadpack.allowReachableSHA1InWant", "true")
    subprocess.run(["git", "clone", "-q", str(remote), str(other)], check=True)
    run_git(other, "config", "user.name", "Fixture")
    run_git(other, "config", "user.email", "fixture@example.invalid")
    (other / "main.txt").write_text("main moves\n", encoding="utf-8")
    run_git(other, "add", "main.txt")
    run_git(other, "commit", "-q", "-m", "main moves")
    run_git(other, "push", "-q", "origin", "main")
    run_git(other, "checkout", "-q", "-b", "feature", "origin/feature")
    run_git(other, "merge", "-q", "--no-ff", "-m", "update branch", "origin/main")
    run_git(other, "push", "-q", "origin", "feature")
    final_head = run_git(other, "rev-parse", "HEAD")
    if fetch_final_head:
        run_git(case["repo"], "fetch", "-q", "origin")
    run_git(other, "checkout", "-q", "main")
    run_git(other, "merge", "-q", "--squash", "feature")
    run_git(other, "commit", "-q", "-m", "feature (#91)")
    run_git(other, "push", "-q", "origin", "main")
    run_git(remote, "update-ref", "refs/pull/91/head", final_head)
    run_git(other, "push", "-q", "origin", "--delete", "feature")
    run_git(case["repo"], "fetch", "-q", "--prune", "origin")
    head = run_git(case["worktree"], "rev-parse", "HEAD")
    assert head != final_head
    subprocess.run(
        ["git", "-C", str(case["repo"]), "merge-base", "--is-ancestor", head, final_head],
        capture_output=True, check=fetch_final_head,
    )
    upstream = subprocess.run(
        ["git", "-C", str(case["worktree"]), "rev-parse", "--verify", "@{upstream}"],
        capture_output=True, check=False,
    )
    assert upstream.returncode != 0, "the upstream branch must be gone"
    return final_head


def merged_env(case, final_head: str, **extra: str) -> dict[str, str]:
    env = case["env"].copy()
    env.update(GH_MERGED="91", GH_MERGED_HEAD=final_head, **extra)
    return env


def has_commit(path: Path, oid: str) -> bool:
    return subprocess.run(
        ["git", "-C", str(path), "cat-file", "-e", f"{oid}^{{commit}}"],
        capture_output=True, check=False,
    ).returncode == 0


def test_adopt_legacy_accepts_an_ancestor_of_a_merged_prs_final_head(cleanup_case):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=True)

    result = adopt(case, env=merged_env(case, final_head))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert [row[:5] for row in registry_rows(case)] == [[
        str(case["repo"]), str(case["worktree"]), "worktree", "seat", "ROW"
    ]]


def test_adopt_legacy_fetches_a_merged_prs_final_head_it_does_not_have(cleanup_case):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=False)
    assert not has_commit(case["repo"], final_head)

    result = adopt(case, env=merged_env(case, final_head))

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert has_commit(case["repo"], final_head)
    assert run_git(case["repo"], "for-each-ref", "--contains", final_head) == ""


def test_adopt_legacy_refuses_when_the_merged_prs_head_cannot_be_read(cleanup_case):
    case = cleanup_case
    update_branch_and_squash_merge(case, fetch_final_head=False)

    result = adopt(case, env=merged_env(case, "f" * 40))

    assert result.stdout == f"refused {case['worktree']}: merged PR head unavailable\n"
    assert registry_rows(case) == []


@pytest.mark.parametrize("pr_state", ["CLOSED", "OPEN"])
def test_adopt_legacy_does_not_count_a_pr_that_was_not_merged(cleanup_case, pr_state):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=True)
    # The merged query itself returns the row, so the state check is what refuses it.
    env = merged_env(
        case, final_head, GH_MERGED_STATE=pr_state, GH_MERGED_ANY_STATE="1"
    )

    result = adopt(case, env=env)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


def test_adopt_legacy_does_not_count_a_merged_pr_of_another_branch(cleanup_case):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=True)

    result = adopt(case, env=merged_env(case, final_head, GH_MERGED_BRANCH="other"))

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_a_gh_failure_on_a_merged_ancestor(cleanup_case):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=True)

    result = adopt(case, env=merged_env(case, final_head, GH_FAIL="1"))

    assert result.stdout == f"refused {case['worktree']}: gh unavailable\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_a_dirty_merged_ancestor(cleanup_case):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=True)
    (case["worktree"] / "feature.txt").write_text("changed\n", encoding="utf-8")

    result = adopt(case, env=merged_env(case, final_head))

    assert result.stdout == f"refused {case['worktree']}: dirty worktree\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_a_local_only_commit_as_owner_policy(cleanup_case):
    case = cleanup_case
    final_head = update_branch_and_squash_merge(case, fetch_final_head=True)
    (case["worktree"] / "local.txt").write_text("local only\n", encoding="utf-8")
    run_git(case["worktree"], "add", "local.txt")
    run_git(case["worktree"], "commit", "-q", "-m", "local only")

    result = adopt(case, env=merged_env(case, final_head))

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


def default_branch_case(case, default: str) -> None:
    """Land feature on the remote's default branch, move it on, drop the upstream."""
    run_git(case["worktree"], "push", "-q", "origin", f"feature:{default}")
    if default != "main":
        run_git(case["remote"], "symbolic-ref", "HEAD", f"refs/heads/{default}")
    run_git(case["repo"], "fetch", "-q", "origin")
    run_git(case["repo"], "checkout", "-q", "-B", "later", f"origin/{default}")
    (case["repo"] / "later.txt").write_text("later\n", encoding="utf-8")
    run_git(case["repo"], "add", "later.txt")
    run_git(case["repo"], "commit", "-q", "-m", "later")
    run_git(case["repo"], "push", "-q", "origin", f"later:{default}")
    run_git(case["repo"], "fetch", "-q", "origin")
    run_git(case["worktree"], "branch", "--unset-upstream")


@pytest.mark.parametrize("default", ["main", "trunk"])
def test_adopt_legacy_accepts_a_head_on_the_default_branch_without_upstream(
    cleanup_case, default
):
    case = cleanup_case
    default_branch_case(case, default)
    head = run_git(case["worktree"], "rev-parse", "HEAD")
    assert head != run_git(case["repo"], "rev-parse", f"origin/{default}")

    result = adopt(case)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert len(registry_rows(case)) == 1


def test_adopt_legacy_refuses_a_head_only_on_a_non_default_branch(cleanup_case):
    case = cleanup_case
    default_branch_case(case, "trunk")
    # HEAD is on origin/trunk only; the remote's default is main again.
    run_git(case["remote"], "symbolic-ref", "HEAD", "refs/heads/main")

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


def push_from_another_clone(case, *, orphan: bool) -> str:
    """Move the remote's main without this clone fetching; return the new tip."""
    other = case["tmp"] / "other-pusher"
    subprocess.run(["git", "clone", "-q", str(case["remote"]), str(other)], check=True)
    run_git(other, "config", "user.name", "Fixture")
    run_git(other, "config", "user.email", "fixture@example.invalid")
    if orphan:
        run_git(other, "checkout", "-q", "--orphan", "rewritten")
        run_git(other, "rm", "-rq", "--cached", ".")
    (other / "moved.txt").write_text("moved\n", encoding="utf-8")
    run_git(other, "add", "moved.txt")
    run_git(other, "commit", "-q", "-m", "rewritten" if orphan else "moved on")
    run_git(other, "push", "-q", "--force", "origin", "HEAD:main")
    return run_git(other, "rev-parse", "HEAD")


def test_adopt_legacy_refuses_a_head_dropped_from_a_rewritten_default_branch(
    cleanup_case,
):
    case = cleanup_case
    default_branch_case(case, "main")
    stale = run_git(case["repo"], "rev-parse", "refs/remotes/origin/main")
    tip = push_from_another_clone(case, orphan=True)
    # This clone has not fetched: its tracking ref still holds HEAD.
    assert run_git(case["repo"], "rev-parse", "refs/remotes/origin/main") == stale
    head = run_git(case["worktree"], "rev-parse", "HEAD")
    run_git(case["repo"], "merge-base", "--is-ancestor", head, stale)

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []
    assert has_commit(case["repo"], tip)


def test_adopt_legacy_fetches_the_live_default_tip_it_does_not_have(cleanup_case):
    case = cleanup_case
    default_branch_case(case, "main")
    tip = push_from_another_clone(case, orphan=False)
    assert not has_commit(case["repo"], tip)

    result = adopt(case)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert has_commit(case["repo"], tip)
    assert run_git(case["repo"], "for-each-ref", "--contains", tip) == ""


def test_adopt_legacy_refuses_when_the_remote_cannot_be_read(cleanup_case):
    case = cleanup_case
    default_branch_case(case, "main")
    run_git(case["repo"], "remote", "set-url", "origin", str(case["tmp"] / "missing.git"))

    result = adopt(case)

    assert result.stdout == f"refused {case['worktree']}: git state unavailable\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_the_main_worktree_by_name(cleanup_case):
    case = cleanup_case

    result = adopt(case, path=case["repo"])

    assert result.stdout == f"refused {case['repo']}: main worktree\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_a_detached_worktree_by_name(cleanup_case):
    case = cleanup_case
    pushed(case)
    run_git(case["worktree"], "checkout", "-q", "--detach")

    result = adopt(case)

    assert result.stdout == f"refused {case['worktree']}: detached or missing branch\n"
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_a_tree_outside_guard_allowed_roots(cleanup_case):
    case = cleanup_case
    pushed(case)
    env = case["env"].copy()
    env["GUARD_ALLOWED_ROOT"] = str(case["tmp"] / "allowed")

    result = adopt(case, env=env)

    assert result.stdout == (
        f"refused {case['worktree']}: cleanup guard deny: unsafe_worktree_remove\n"
    )
    assert registry_rows(case) == []
    assert not case["registry"].exists()
    assert list(case["registry"].parent.iterdir()) == []


def test_adopt_legacy_refuses_if_the_worktree_becomes_dirty_during_guard(cleanup_case):
    case = cleanup_case
    pushed(case)
    marker = case["tmp"] / "guard-snapshotted-clean-tree"
    env = case["env"].copy()
    env.update(
        GUARD_CALL_COUNT=str(case["tmp"] / "guard-count"),
        GUARD_SLEEP_CALL="1",
        GUARD_SLEEP_MARKER=str(marker),
        GUARD_SLEEP_SECONDS="1",
    )
    process = subprocess.Popen(
        [
            sys.executable, str(SWEEP), "register", "--adopt-legacy",
            "--registry", str(case["registry"]),
            "--repo", str(case["repo"]),
            "--path", str(case["worktree"]),
            "--kind", "worktree", "--seat", "seat", "--row", "ROW",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    deadline = time.monotonic() + 3
    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "the guard must snapshot the clean tree before the mutation"
    (case["worktree"] / "dirty-during-guard.txt").write_text(
        "dirty\n", encoding="utf-8"
    )

    stdout, stderr = process.communicate(timeout=3)

    assert process.returncode == 0, stderr
    assert stdout == f"refused {case['worktree']}: dirty worktree\n"
    assert not case["registry"].exists()


def test_register_creates_the_guard_trusted_private_registry(cleanup_case):
    case = cleanup_case
    result = register(case)
    assert result.returncode == 0, result.stderr
    assert stat.S_IMODE(case["registry"].parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(case["registry"].stat().st_mode) == 0o600
    lines = case["registry"].read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    assert lines[1].split("\t")[:5] == [
        str(case["repo"]), str(case["worktree"]), "worktree", "seat", "ROW"
    ]
    refused = invoke(
        case, "register", "--registry", str(case["registry"]),
        "--repo", str(case["repo"]), "--path", str(case["worktree"]),
        "--kind", "copy", "--seat", "seat", "--row", "ROW",
    )
    assert refused.returncode == 2


def test_clean_pushed_worktree_prints_exact_nonforce_guarded_line(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    result = plan(case)
    expected = f"git -C {case['repo']} worktree remove {case['worktree']}"
    assert result.returncode == 0
    assert result.stdout.splitlines() == [expected]
    assert case["guard_log"].read_text(encoding="utf-8").strip() == expected
    assert "--force" not in result.stdout


def test_each_decided_plan_line_is_flushed_before_a_later_guard_stalls(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0

    second = case["tmp"] / "feature-two"
    run_git(case["repo"], "worktree", "add", "-q", "-b", "feature-two", str(second), "origin/main")
    (second / "second.txt").write_text("second\n", encoding="utf-8")
    run_git(second, "add", "second.txt")
    run_git(second, "commit", "-q", "-m", "second")
    run_git(second, "push", "-q", "-u", "origin", "feature-two")
    assert register(case, path=second).returncode == 0

    marker = case["tmp"] / "second-guard-started"
    env = case["env"].copy()
    env.pop("PYTHONUNBUFFERED", None)
    env.update(
        GUARD_CALL_COUNT=str(case["tmp"] / "guard-count"),
        GUARD_SLEEP_MARKER=str(marker),
        GUARD_SLEEP_SECONDS="30",
    )
    process = subprocess.Popen(
        [
            sys.executable, str(SWEEP), "plan", "--registry", str(case["registry"]),
            "--row", "ROW",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    deadline = time.monotonic() + 3
    while not marker.exists() and process.poll() is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists(), "the second guard invocation must reach its sleep"
    plan_limit = 0.25
    try:
        process.communicate(timeout=plan_limit)
    except subprocess.TimeoutExpired:
        process.kill()
    stdout, stderr = process.communicate(timeout=2)

    expected = f"git -C {case['repo']} worktree remove {case['worktree']}"
    assert stdout.splitlines() == [expected], stderr


def test_dirty_worktree_is_kept_when_the_guard_denies(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    (case["worktree"] / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    result = plan(case)
    assert result.stdout == f"kept {case['worktree']}: cleanup guard deny: unsafe_worktree_remove\n"
    assert "worktree remove" not in result.stdout


def test_unpushed_worktree_is_kept_before_the_guard_is_consulted(cleanup_case):
    case = cleanup_case
    assert register(case).returncode == 0
    result = plan(case)
    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    assert not case["guard_log"].exists()


def test_detached_head_is_kept_before_the_guard_is_consulted(cleanup_case):
    case = cleanup_case
    pushed(case)
    run_git(case["worktree"], "checkout", "-q", "--detach")
    assert register(case).returncode == 0

    result = plan(case)

    assert result.stdout == f"kept {case['worktree']}: detached HEAD\n"
    assert not case["guard_log"].exists()


def test_open_pr_keeps_even_a_pushed_worktree(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    env = case["env"].copy()
    env["GH_OPEN"] = "42"
    result = plan(case, env=env)
    assert result.stdout == f"kept {case['worktree']}: open PR #42\n"
    assert not case["guard_log"].exists()


def test_merged_pr_must_match_the_current_unpushed_head(cleanup_case):
    case = cleanup_case
    assert register(case).returncode == 0
    env = case["env"].copy()
    env.update(GH_MERGED="91", GH_MERGED_HEAD="0" * 40)
    kept = plan(case, env=env)
    assert kept.stdout == f"kept {case['worktree']}: unpushed commits\n"
    env["GH_MERGED_HEAD"] = run_git(case["worktree"], "rev-parse", "HEAD")
    allowed = plan(case, env=env)
    assert allowed.stdout.startswith("git -C ")


def test_stub_guard_denial_never_prints_a_removal_line(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    env = case["env"].copy()
    env["GUARD_DENY"] = "1"
    result = plan(case, env=env)
    assert result.stdout == f"kept {case['worktree']}: cleanup guard deny: unsafe_worktree_remove\n"
    assert not any(line.startswith("git ") for line in result.stdout.splitlines())


def test_symlinked_repo_input_prints_canonical_paths(cleanup_case):
    case = cleanup_case
    pushed(case)
    repo_link = case["tmp"] / "repo-link"
    worktree_link = case["tmp"] / "worktree-link"
    repo_link.symlink_to(case["repo"], target_is_directory=True)
    worktree_link.symlink_to(case["worktree"], target_is_directory=True)
    assert register(case, repo=repo_link, path=worktree_link).returncode == 0
    result = plan(case)
    assert result.stdout == f"git -C {case['repo']} worktree remove {case['worktree']}\n"
    assert str(repo_link) not in result.stdout and str(worktree_link) not in result.stdout


def test_world_readable_registry_is_kept_by_the_guard(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    case["registry"].chmod(0o644)
    result = plan(case)
    assert result.stdout == f"kept {case['worktree']}: cleanup guard deny: unsafe_worktree_remove\n"
    assert "\ngit " not in "\n" + result.stdout


def test_bare_repository_inside_ignored_cache_is_kept_by_the_guard(cleanup_case):
    case = cleanup_case
    (case["worktree"] / ".gitignore").write_text(".pytest_cache/\n", encoding="utf-8")
    run_git(case["worktree"], "add", ".gitignore")
    run_git(case["worktree"], "commit", "-q", "-m", "ignore cache")
    pushed(case)
    subprocess.run(
        ["git", "init", "-q", "--bare", str(case["worktree"] / ".pytest_cache" / "nested.git")],
        check=True,
    )
    assert register(case).returncode == 0
    result = plan(case)
    assert result.stdout == f"kept {case['worktree']}: cleanup guard deny: unsafe_worktree_remove\n"


def test_absent_guard_is_named_and_no_line_is_printed(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    env = case["env"].copy()
    missing = case["tmp"] / "missing-cleanup-guard"
    env["CLEANUP_GUARD"] = str(missing)
    result = plan(case, env=env)
    assert result.stdout == f"kept {case['worktree']}: cleanup guard absent: {missing}\n"


# --- "pushed" is read from the live remote, never a stale tracking ref -----


def force_push_feature_away(case) -> str:
    """Force-push the remote's feature off HEAD from another clone; no local fetch.

    Returns the new remote tip. This clone's refs/remotes/origin/feature still
    names the worktree's HEAD, so only a live read sees the rewrite.
    """
    other = case["tmp"] / "force-pusher"
    subprocess.run(["git", "clone", "-q", str(case["remote"]), str(other)], check=True)
    run_git(other, "config", "user.name", "Fixture")
    run_git(other, "config", "user.email", "fixture@example.invalid")
    run_git(other, "checkout", "-q", "-B", "feature", "origin/main")
    (other / "replacement.txt").write_text("replacement\n", encoding="utf-8")
    run_git(other, "add", "replacement.txt")
    run_git(other, "commit", "-q", "-m", "replacement")
    run_git(other, "push", "-q", "--force", "origin", "feature")
    return run_git(other, "rev-parse", "HEAD")


def stale_feature_case(case) -> tuple[str, str]:
    pushed(case)
    head = run_git(case["worktree"], "rev-parse", "HEAD")
    tip = force_push_feature_away(case)
    assert tip != head
    # The clone has not fetched: its tracking ref and upstream still read HEAD.
    assert run_git(case["repo"], "rev-parse", "refs/remotes/origin/feature") == head
    assert run_git(case["worktree"], "rev-parse", "@{upstream}") == head
    return head, tip


def test_adopt_legacy_refuses_an_upstream_force_pushed_away_from_head(cleanup_case):
    case = cleanup_case
    head, _ = stale_feature_case(case)

    result = adopt(case)

    # The legacy upstream test refuses by its own reason; a stale tracking ref
    # would have passed it.
    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []
    # The live read wrote no ref: the tracking ref is still the stale one.
    assert run_git(case["repo"], "rev-parse", "refs/remotes/origin/feature") == head


def test_plan_keeps_a_tree_whose_upstream_was_force_pushed_away(cleanup_case):
    case = cleanup_case
    head, tip = stale_feature_case(case)
    assert register(case).returncode == 0

    result = plan(case)

    # refs/remotes/origin/feature still contains HEAD; the live remote does not.
    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    assert not case["guard_log"].exists()
    # The live tip was fetched by oid to test containment, writing no ref.
    assert has_commit(case["repo"], tip)
    assert run_git(case["repo"], "for-each-ref", "--contains", tip) == ""
    assert run_git(case["repo"], "rev-parse", "refs/remotes/origin/feature") == head


def drop_tracking_refs(case) -> None:
    """Delete this clone's refs/remotes/ so only a live read can prove a push."""
    refs = run_git(
        case["repo"], "for-each-ref", "--format=%(refname)", "refs/remotes/"
    ).splitlines()
    assert refs
    for ref in refs:
        run_git(case["repo"], "update-ref", "-d", ref)


def test_adopt_legacy_accepts_an_upstream_the_remote_still_holds(cleanup_case):
    case = cleanup_case
    pushed(case)
    drop_tracking_refs(case)

    result = adopt(case)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"adopted {case['worktree']}\n"
    assert len(registry_rows(case)) == 1


def test_plan_removes_a_tree_whose_head_the_remote_still_holds(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    drop_tracking_refs(case)

    result = plan(case)

    assert result.stdout == f"git -C {case['repo']} worktree remove {case['worktree']}\n"


def test_adopt_legacy_refuses_an_unreachable_upstream_remote(cleanup_case):
    case = cleanup_case
    pushed(case)
    run_git(case["repo"], "remote", "set-url", "origin", str(case["tmp"] / "missing.git"))

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: upstream remote unavailable\n"
    )
    assert registry_rows(case) == []


def test_plan_keeps_a_tree_whose_remote_cannot_be_read(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "set-url", "origin", str(case["tmp"] / "missing.git"))

    result = plan(case)

    assert result.stdout == f"kept {case['worktree']}: remote unavailable\n"
    assert not case["guard_log"].exists()


# --- a remote that is this repository is never a push proof --------------


def self_remote_url(case, kind: str) -> str:
    if kind == "dot":
        return "."
    if kind == "common-dir":
        return run_git(
            case["worktree"], "rev-parse", "--path-format=absolute", "--git-common-dir"
        )
    if kind == "other-linked-worktree":
        # Neither this worktree, the main checkout nor a git dir by path: only
        # its common git dir shows it is this repository.
        other = case["tmp"] / "other-linked"
        run_git(case["repo"], "worktree", "add", "-q", "--detach", str(other), "origin/main")
        return str(other)
    return f"file://{case['repo']}"


@pytest.mark.parametrize(
    "kind", ["dot", "common-dir", "file-url-main-checkout", "other-linked-worktree"]
)
def test_plan_keeps_a_tree_whose_only_remote_is_this_repository(cleanup_case, kind):
    case = cleanup_case
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "set-url", "origin", self_remote_url(case, kind))

    result = plan(case)

    # The local feature ref holds HEAD, but a self remote is no proof.
    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    assert not case["guard_log"].exists()


@pytest.mark.parametrize(
    "kind", ["dot", "common-dir", "file-url-main-checkout", "other-linked-worktree"]
)
def test_adopt_legacy_refuses_an_upstream_on_this_repository(cleanup_case, kind):
    case = cleanup_case
    run_git(case["repo"], "remote", "set-url", "origin", self_remote_url(case, kind))
    run_git(case["repo"], "config", "branch.feature.remote", "origin")
    run_git(case["repo"], "config", "branch.feature.merge", "refs/heads/feature")

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


def test_adopt_legacy_refuses_a_head_only_on_this_repositorys_default_branch(
    cleanup_case,
):
    case = cleanup_case
    run_git(case["repo"], "merge", "-q", "--ff-only", "feature")
    run_git(case["repo"], "remote", "set-url", "origin", ".")

    result = adopt(case)

    assert result.stdout == (
        f"refused {case['worktree']}: branch is neither merged by PR nor pushed "
        "to its upstream\n"
    )
    assert registry_rows(case) == []


@pytest.mark.parametrize(
    "authority", ["LOCALHOST", "localhost.", "127.0.0.1", "[::1]", "example.invalid"]
)
def test_plan_keeps_a_tree_whose_remote_is_a_file_url_of_this_repository_on_any_host(
    cleanup_case, authority
):
    case = cleanup_case
    assert register(case).returncode == 0
    # git reads file://<any host>/<path> as the local path <path>.
    run_git(case["repo"], "remote", "set-url", "origin", f"file://{authority}{case['repo']}")

    result = plan(case)

    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    assert not case["guard_log"].exists()


def test_plan_keeps_a_tree_whose_remote_is_a_bundle_of_this_repository(cleanup_case):
    case = cleanup_case
    assert register(case).returncode == 0
    bundle = case["tmp"] / "repo.bundle"
    run_git(case["repo"], "bundle", "create", "-q", str(bundle), "--all")
    # A local path that is no repository (git still reads the bundle's refs)
    # cannot be resolved, so it is no proof and refuses by name.
    run_git(case["repo"], "remote", "set-url", "origin", str(bundle))

    result = plan(case)

    assert result.stdout == f"kept {case['worktree']}: remote unavailable\n"
    assert not case["guard_log"].exists()


@pytest.mark.parametrize("prefix", ["nohelper::", "nohelper://"])
def test_a_remote_helper_url_is_skipped_as_unclassifiable(cleanup_case, prefix):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "set-url", "origin", f"{prefix}{case['remote']}")

    result = plan(case)

    # Skipped as no proof, rather than read and found unavailable.
    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    assert not case["guard_log"].exists()


def test_a_pushurl_on_this_repository_does_not_discard_the_fetch_proof(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "set-url", "--push", "origin", ".")

    planned = plan(case)
    adopted = adopt(case)

    # ls-remote reads the fetch URL (the separate bare remote), which holds HEAD.
    assert planned.stdout == f"git -C {case['repo']} worktree remove {case['worktree']}\n"
    assert adopted.stdout == f"adopted {case['worktree']}: already registered\n"


def test_a_self_remote_is_skipped_and_another_remote_still_proves(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "set-url", "origin", ".")
    run_git(case["repo"], "remote", "add", "mirror", f"file://{case['remote']}")

    result = plan(case)

    assert result.stdout == f"git -C {case['repo']} worktree remove {case['worktree']}\n"


def test_the_separate_bare_remote_by_file_url_still_proves(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "set-url", "origin", f"file://{case['remote']}")

    planned = plan(case)
    adopted = adopt(case)

    assert planned.stdout == f"git -C {case['repo']} worktree remove {case['worktree']}\n"
    assert adopted.stdout == f"adopted {case['worktree']}: already registered\n"


def test_plan_keeps_a_tree_with_no_remote(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "remote", "remove", "origin")
    env = case["env"].copy()
    env.update(GH_MERGED="91", GH_MERGED_HEAD=run_git(case["worktree"], "rev-parse", "HEAD"))

    result = plan(case, env=env)

    assert result.stdout == f"kept {case['worktree']}: no remote\n"
    assert not case["guard_log"].exists()


# --- cleanup.log: each worktree the plan keeps, with its reason ----------

CLEANUP_LINE = re.compile(
    r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z worktree-sweep plan kept (.+)\Z"
)


def kept_lines(log: Path) -> list[str]:
    """The `<path>: <reason>` of each cleanup.log line, every line checked."""
    if not log.exists():
        return []
    found = []
    for line in log.read_text(encoding="utf-8").splitlines():
        match = CLEANUP_LINE.fullmatch(line)
        assert match, line
        found.append(match.group(1))
    return found


def test_a_removed_worktree_is_reported_gone_not_kept(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "worktree", "remove", str(case["worktree"]))
    assert not case["worktree"].exists()

    result = plan(case)

    assert result.returncode == 0, result.stderr
    assert result.stdout == f"gone {case['worktree']}\n"
    assert not any(line.startswith("kept ") for line in result.stdout.splitlines())
    assert not case["guard_log"].exists()
    log = (case["registry"].parent / "cleanup.log").read_text(encoding="utf-8")
    assert re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z worktree-sweep plan gone "
        + re.escape(str(case["worktree"])) + "\n",
        log,
    ), log
    # plan never writes the registry: the row stays.
    assert [row[1] for row in registry_rows(case)] == [str(case["worktree"])]


def test_a_path_that_exists_but_is_not_a_worktree_is_still_kept(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    run_git(case["repo"], "worktree", "remove", str(case["worktree"]))
    case["worktree"].mkdir()

    result = plan(case)

    assert result.stdout == f"kept {case['worktree']}: detached HEAD\n"
    assert kept_lines(case["registry"].parent / "cleanup.log") == [
        f"{case['worktree']}: detached HEAD"
    ]


def test_kept_worktree_is_logged_in_the_registrys_directory(cleanup_case):
    case = cleanup_case
    assert register(case).returncode == 0
    result = plan(case)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    log = case["registry"].parent / "cleanup.log"
    assert kept_lines(log) == [f"{case['worktree']}: unpushed commits"]


def test_guard_refusal_is_logged_with_its_reason(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    env = case["env"].copy()
    env["GUARD_DENY"] = "1"
    result = plan(case, env=env)
    assert result.stdout == f"kept {case['worktree']}: cleanup guard deny: unsafe_worktree_remove\n"
    assert kept_lines(case["registry"].parent / "cleanup.log") == [
        f"{case['worktree']}: cleanup guard deny: unsafe_worktree_remove"
    ]


def test_cleanup_log_option_names_the_log(cleanup_case):
    case = cleanup_case
    assert register(case).returncode == 0
    log = case["tmp"] / "roll-scratch" / "cleanup.log"
    log.parent.mkdir()
    result = invoke(
        case, "plan", "--registry", str(case["registry"]), "--row", "ROW",
        "--cleanup-log", str(log),
    )
    assert result.stdout == f"kept {case['worktree']}: unpushed commits\n"
    assert kept_lines(log) == [f"{case['worktree']}: unpushed commits"]
    assert not (case["registry"].parent / "cleanup.log").exists()


def test_removable_worktree_writes_no_cleanup_line(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    result = plan(case)
    assert result.stdout == f"git -C {case['repo']} worktree remove {case['worktree']}\n"
    assert not (case["registry"].parent / "cleanup.log").exists()


@pytest.mark.parametrize("spoil", ["directory", "parent-is-a-file", "read-only-file", "symlink"])
def test_unwritable_cleanup_log_changes_nothing(cleanup_case, spoil):
    case = cleanup_case
    assert register(case).returncode == 0
    working = case["tmp"] / "working.log"
    bad = case["tmp"] / "bad.log"
    target = case["tmp"] / "symlink-target.log"
    if spoil == "directory":
        bad.mkdir()
    elif spoil == "parent-is-a-file":
        (case["tmp"] / "not-a-directory").write_text("", encoding="utf-8")
        bad = case["tmp"] / "not-a-directory" / "cleanup.log"
    elif spoil == "read-only-file":
        bad.write_text("", encoding="utf-8")
        bad.chmod(0o444)
    else:
        target.write_text("", encoding="utf-8")
        bad.symlink_to(target)

    def run(log: Path):
        return invoke(
            case, "plan", "--registry", str(case["registry"]), "--row", "ROW",
            "--cleanup-log", str(log),
        )

    logged = run(working)
    dropped = run(bad)
    assert kept_lines(working) == [f"{case['worktree']}: unpushed commits"]
    assert (dropped.returncode, dropped.stdout, dropped.stderr) == (
        logged.returncode, logged.stdout, logged.stderr
    )
    if spoil == "directory":
        assert list(bad.iterdir()) == []
    elif spoil == "read-only-file":
        assert bad.read_text(encoding="utf-8") == ""
    elif spoil == "symlink":
        assert target.read_text(encoding="utf-8") == ""


def test_a_torn_tail_is_closed_by_the_next_line(cleanup_case):
    """A file-size limit lets only part of the line land: Linux stops the write
    at the limit's offset, macOS caps a fresh append descriptor's write at the
    limit's byte count, and either way the next write fails with EFBIG. The
    log is never shortened, so the fragment stays; the next line closes it
    with a newline first. The sweep is the writer, so the limit covers its
    whole run."""
    import resource

    case = cleanup_case
    assert register(case).returncode == 0
    log = case["tmp"] / "cleanup.log"
    prefix = b"2026-01-01T00:00:00Z worktree-sweep plan kept /pad: pad\n"
    limit = len(prefix) + 8
    hard = resource.getrlimit(resource.RLIMIT_FSIZE)[1]

    def run(*, limited: bool):
        return subprocess.run(
            [sys.executable, str(SWEEP), "plan", "--registry", str(case["registry"]), "--row", "ROW",
             "--cleanup-log", str(log)],
            text=True, capture_output=True, check=False, env=case["env"],
            preexec_fn=(
                (lambda: resource.setrlimit(resource.RLIMIT_FSIZE, (limit, hard)))
                if limited else None
            ),
        )

    log.write_bytes(prefix)
    logged = run(limited=False)
    assert logged.stdout == f"kept {case['worktree']}: unpushed commits\n"
    full = log.read_bytes()[len(prefix):]
    # The line cannot fit under the limit, from either end.
    assert len(full) > limit

    log.write_bytes(prefix)
    short = run(limited=True)
    assert (short.returncode, short.stdout, short.stderr) == (
        logged.returncode, logged.stdout, logged.stderr
    )
    # The fragment stays after the prior content, with no newline. Where the
    # limit bites differs by platform (Linux stops at the limit's offset, macOS
    # at its byte count), so only its shape is pinned: non-empty, and the
    # leading bytes of the line the unlimited run wrote, timestamp masked.
    torn = log.read_bytes()
    assert torn.startswith(prefix)
    fragment = torn[len(prefix):]
    assert fragment and b"\n" not in fragment, torn

    def masked(raw: bytes) -> bytes:
        return re.sub(rb"\d", b"0", raw[:20]) + raw[20:]

    assert masked(full).startswith(masked(fragment)), (fragment, full)

    # The next append closes the torn tail: prior content, the fragment, a
    # newline, then exactly one well-formed line.
    again = run(limited=False)
    assert (again.returncode, again.stdout, again.stderr) == (
        logged.returncode, logged.stdout, logged.stderr
    )
    body = log.read_bytes()
    assert body.startswith(torn + b"\n"), body
    record = body[len(torn) + 1:]
    assert record.endswith(b"\n") and record.count(b"\n") == 1, body
    match = CLEANUP_LINE.fullmatch(record[:-1].decode("utf-8"))
    assert match, record
    assert match.group(1) == f"{case['worktree']}: unpushed commits"


# --- handoff transfer of worktree cleanup rows ---------------------------

PRED = "seat-a"
SUCC = "seat-b"
STAMP_RE = r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z"


def seat_registry_file(scratch: Path, label: str, rows=()) -> Path:
    watch = scratch / f"watch-{label}"
    watch.mkdir(parents=True)
    watch.chmod(0o700)
    registry = watch / "cleanup-items.tsv"
    registry.write_text(
        HEADER + "\n" + "".join("\t".join(item) + "\n" for item in rows),
        encoding="utf-8",
    )
    registry.chmod(0o600)
    return registry


def run_transfer(scratch: Path, *, pred: str = PRED, succ: str = SUCC,
                 successor_scratch: Path | None = None, caller_registry: str | None = None,
                 report_paths: bool = False, env=None):
    argv = [sys.executable, str(SWEEP), "transfer", "--scratch", str(scratch), "--from", pred, "--to", succ]
    if successor_scratch is not None:
        argv.extend(["--successor-scratch", str(successor_scratch)])
    if caller_registry is not None:
        argv.append(f"--caller-registry={caller_registry}")
    if report_paths:
        argv.append("--report-paths")
    return subprocess.run(
        argv, text=True, capture_output=True, check=False, env=env or os.environ.copy()
    )


@pytest.fixture
def transfer_case(tmp_path: Path):
    scratch = (tmp_path / "scratch").resolve()
    scratch.mkdir()
    base = tmp_path.resolve()
    repo = str(base / "repo")
    stamp = "2026-10-01T00:00:00Z"
    # The predecessor's worktrees lie under its scratch, one of its own roots.
    owned = [
        (repo, str(scratch / "wt-one"), "worktree", PRED, "ROW1", stamp),
        (repo, str(scratch / "wt-two"), "worktree", PRED, "ROW2", stamp),
    ]
    other = (repo, str(base / "copy-dir"), "copy", PRED, "ROW3", stamp)
    pred = seat_registry_file(scratch, PRED, [owned[0], other, owned[1]])
    succ = seat_registry_file(scratch, SUCC)
    return {"scratch": scratch, "pred": pred, "succ": succ, "owned": owned, "base": base}


def transferred_rows(registry: Path) -> list[list[str]]:
    lines = registry.read_text(encoding="utf-8").splitlines()
    assert lines[0] == HEADER
    return [line.split("\t") for line in lines[1:]]


def test_transfer_reseats_only_worktree_rows_and_is_idempotent(transfer_case):
    case = transfer_case
    before_pred = case["pred"].read_bytes()
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    rows = transferred_rows(case["succ"])
    assert [item[:5] for item in rows] == [
        [repo, path, "worktree", SUCC, row] for repo, path, _, _, row, _ in case["owned"]
    ], rows
    assert all(re.fullmatch(STAMP_RE, item[5]) for item in rows), rows
    # Append-only: the predecessor keeps its own rows untouched.
    assert case["pred"].read_bytes() == before_pred
    assert stat.S_IMODE(case["succ"].stat().st_mode) == 0o600

    after_first = case["succ"].read_bytes()
    again = run_transfer(case["scratch"])
    assert again.returncode == 0, again.stderr
    assert again.stdout == f"cleanup_transfer: 0 rows from {PRED} to {SUCC}\n"
    assert case["succ"].read_bytes() == after_first


def test_transfer_follows_a_chain_of_handoffs(transfer_case):
    case = transfer_case
    assert run_transfer(case["scratch"]).returncode == 0
    third = seat_registry_file(case["scratch"], "seat-c")
    result = run_transfer(case["scratch"], pred=SUCC, succ="seat-c")
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"cleanup_transfer: 2 rows from {SUCC} to seat-c\n"
    assert [item[:5] for item in transferred_rows(third)] == [
        [repo, path, "worktree", "seat-c", row] for repo, path, _, _, row, _ in case["owned"]
    ]


def test_transfer_skips_rows_the_predecessor_does_not_own(tmp_path: Path):
    scratch = (tmp_path / "scratch").resolve()
    scratch.mkdir()
    base = tmp_path.resolve()
    stamp = "2026-10-01T00:00:00Z"
    seat_registry_file(scratch, PRED, [
        (str(base / "repo"), str(base / "foreign"), "worktree", "someone-else", "ROWX", stamp),
    ])
    succ = seat_registry_file(scratch, SUCC)
    before = succ.read_bytes()
    result = run_transfer(scratch)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"cleanup_transfer: 0 rows from {PRED} to {SUCC}\n"
    assert succ.read_bytes() == before


def test_transfer_refuses_a_conflicting_successor_row(transfer_case):
    case = transfer_case
    repo, path, _, _, _, stamp = case["owned"][1]
    case["succ"].write_text(
        HEADER + "\n" + "\t".join((repo, path, "worktree", SUCC, "OTHER", stamp)) + "\n",
        encoding="utf-8",
    )
    before = case["succ"].read_bytes()
    result = run_transfer(case["scratch"])
    assert result.returncode == 2
    assert "cleanup_transfer refused: successor registry" in result.stderr
    assert "already registered with different ownership" in result.stderr
    assert case["succ"].read_bytes() == before


def _mode_0644(case):
    case["pred"].chmod(0o644)
    return f"predecessor registry {case['pred']}: mode 0644, expected 0600"


def _symlinked_file(case):
    real = case["base"] / "elsewhere" / "cleanup-items.tsv"
    real.parent.mkdir(mode=0o700)
    real.write_bytes(case["pred"].read_bytes())
    real.chmod(0o600)
    case["pred"].unlink()
    case["pred"].symlink_to(real)
    return f"predecessor registry {case['pred']}: not an absolute canonical path"


def _bad_header(case):
    text = case["pred"].read_text(encoding="utf-8")
    case["pred"].write_text(text.replace("created_at", "created", 1), encoding="utf-8")
    return f"predecessor registry {case['pred']}: malformed registry header"


def _dir_mode_0755(case):
    case["pred"].parent.chmod(0o755)
    return f"predecessor registry {case['pred']}: directory mode 0755, expected 0700"


def _malformed_row(case):
    with case["pred"].open("a", encoding="utf-8") as stream:
        stream.write("only\tfour\tfields\there\n")
    return f"predecessor registry {case['pred']}: line 5: registry row must have six TSV fields"


def _successor_mode_0644(case):
    case["succ"].chmod(0o644)
    return f"successor registry {case['succ']}: mode 0644, expected 0600"


def _successor_missing(case):
    case["succ"].unlink()
    return f"successor registry {case['succ']}: missing"


@pytest.mark.parametrize(
    "spoil",
    [_mode_0644, _symlinked_file, _bad_header, _dir_mode_0755, _malformed_row,
     _successor_mode_0644, _successor_missing],
    ids=lambda spoil: spoil.__name__.lstrip("_"),
)
def test_transfer_refuses_an_untrusted_registry_by_name(transfer_case, spoil):
    case = transfer_case
    expected = spoil(case)
    before = case["succ"].read_bytes() if case["succ"].exists() else None
    result = run_transfer(case["scratch"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert f"worktree-sweep: cleanup_transfer refused: {expected}" in result.stderr, result.stderr
    after = case["succ"].read_bytes() if case["succ"].exists() else None
    assert after == before


def test_transferred_row_matches_the_guard_grammar_and_plans_for_the_successor(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    scratch = case["registry"].parent.parent
    # cleanup_case's registry is <tmp>/watch-seat/cleanup-items.tsv, owned by seat "seat".
    case["registry"].parent.chmod(0o700)
    succ = seat_registry_file(scratch, "seat-next")
    before = invoke(case, "plan", "--registry", str(succ), "--seat", "seat-next")
    assert before.returncode == 0 and before.stdout == "", before.stdout + before.stderr

    result = run_transfer(scratch, pred="seat", succ="seat-next", env=case["env"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == "cleanup_transfer: 1 rows from seat to seat-next\n"
    lines = succ.read_text(encoding="utf-8").split("\n")
    assert lines[0] == HEADER and lines[-1] == "" and len(lines) == 3, lines
    fields = lines[1].split("\t")
    assert len(fields) == 6, fields
    repo, path, kind, seat, row, created = fields
    assert os.path.isabs(repo) and os.path.realpath(repo) == repo == str(case["repo"])
    assert os.path.isabs(path) and os.path.realpath(path) == path == str(case["worktree"])
    assert kind == "worktree"
    assert seat == "seat-next"
    assert row == "ROW"
    assert re.fullmatch(STAMP_RE, created), created

    # The successor's own plan now lists the inherited worktree, and the
    # fixture guard (which mirrors the installed guard's registry match)
    # admits the removal under the successor's registry and seat.
    planned = invoke(case, "plan", "--registry", str(succ), "--seat", "seat-next")
    expected = f"git -C {case['repo']} worktree remove {case['worktree']}"
    assert planned.returncode == 0, planned.stderr
    assert planned.stdout.splitlines() == [expected]


def test_transfer_matching_caller_registry_reports_all_carried_paths(transfer_case):
    case = transfer_case
    source = case["pred"].read_bytes()
    for count in (2, 0):
        result = run_transfer(
            case["scratch"], caller_registry=str(case["succ"]), report_paths=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines() == [
            f"cleanup_transfer: {count} rows from {PRED} to {SUCC}",
            *(f"cleanup_transfer_path: {row[1]}" for row in case["owned"]),
        ]
    assert [row[:5] for row in transferred_rows(case["succ"])] == [
        [repo, path, "worktree", SUCC, row]
        for repo, path, _, _, row, _ in case["owned"]
    ]
    assert case["pred"].read_bytes() == source


@pytest.mark.parametrize("form", [
    "wrong-label", "wrong-basename", "relative", "dotdot", "symlink",
    "symlink-watch-dir",
])
def test_transfer_refuses_non_rebuilding_caller_registry(transfer_case, form):
    case = transfer_case
    scratch = case["scratch"]
    caller = str(case["succ"])
    if form == "wrong-label":
        caller = caller.replace(f"watch-{SUCC}", "watch-other")
    elif form == "wrong-basename":
        caller = caller.replace("cleanup-items.tsv", "other.tsv")
    elif form == "relative":
        caller = os.path.relpath(caller)
    elif form == "dotdot":
        caller = str(scratch / ".." / "scratch" / f"watch-{SUCC}" / "cleanup-items.tsv")
    elif form == "symlink":
        link = case["base"] / "scratch-link"
        link.symlink_to(scratch, target_is_directory=True)
        caller = str(link / f"watch-{SUCC}" / "cleanup-items.tsv")
    else:
        watch = case["succ"].parent
        target = scratch / "real-watch"
        watch.rename(target)
        watch.symlink_to(target, target_is_directory=True)
    before = case["pred"].read_bytes(), case["succ"].read_bytes()

    result = run_transfer(scratch, caller_registry=caller)

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == (
        "worktree-sweep: cleanup_transfer refused: caller registry does not rebuild "
        "from its scratch and seat (canonical path required)\n"
    ), result.stderr
    assert (case["pred"].read_bytes(), case["succ"].read_bytes()) == before
    assert not list(scratch.rglob("inherited-roots.tsv"))


def test_plan_path_selector_intersects_seat_and_accepts_repeated_paths(cleanup_case):
    case = cleanup_case
    pushed(case)
    assert register(case).returncode == 0
    gone = case["tmp"].resolve() / "gone-selected"
    unrelated = case["tmp"].resolve() / "gone-unrelated"
    foreign = case["tmp"].resolve() / "gone-foreign"
    with case["registry"].open("a") as stream:
        for path, seat, row in [
            (gone, "seat", "GONE"), (unrelated, "seat", "OTHER"),
            (foreign, "other-seat", "FOREIGN"),
        ]:
            stream.write("\t".join((
                str(case["repo"]), str(path), "worktree", seat, row,
                "2026-01-01T00:00:00Z",
            )) + "\n")

    result = invoke(
        case, "plan", "--registry", str(case["registry"]), "--seat", "seat",
        "--path", str(case["worktree"]), "--path", str(gone), "--path", str(foreign),
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        f"git -C {case['repo']} worktree remove {case['worktree']}", f"gone {gone}",
    ], "path selection must intersect seat ownership and exclude unselected rows"


def test_transfer_refuses_a_copy_row_already_holding_an_inherited_path(transfer_case):
    case = transfer_case
    repo, path, _, _, _, stamp = case["owned"][1]
    case["succ"].write_text(
        HEADER + "\n" + "\t".join((repo, path, "copy", SUCC, "ROW9", stamp)) + "\n",
        encoding="utf-8",
    )
    before = case["succ"].read_bytes()
    result = run_transfer(case["scratch"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert (
        f"worktree-sweep: cleanup_transfer refused: successor registry {case['succ']}: "
        f"{path} already registered with different ownership (copy row ROW9 of {SUCC})"
    ) in result.stderr, result.stderr
    assert case["succ"].read_bytes() == before


def _append(registry: Path, *fields: str) -> None:
    with registry.open("a", encoding="utf-8") as stream:
        stream.write("\t".join(fields) + "\n")


def _worktree_relative_repo(case):
    _append(case["pred"], "repo", str(case["base"] / "wt-three"), "worktree", PRED,
            "ROW4", "2026-10-01T00:00:00Z")
    return f"predecessor registry {case['pred']}: line 5: repo must be an absolute realpath"


def _copy_relative_path(case):
    _append(case["pred"], str(case["base"] / "repo"), "copy-rel", "copy", PRED,
            "ROW4", "2026-10-01T00:00:00Z")
    return f"predecessor registry {case['pred']}: line 5: path must be an absolute realpath"


def _copy_dotdot_repo(case):
    base = case["base"]
    _append(case["pred"], f"{base}/x/../repo", str(base / "copy-dotdot"), "copy", PRED,
            "ROW4", "2026-10-01T00:00:00Z")
    return f"predecessor registry {case['pred']}: line 5: repo must be an absolute realpath"


def _copy_symlinked_parent_path(case):
    base = case["base"]
    (base / "real-parent").mkdir()
    (base / "link-parent").symlink_to(base / "real-parent")
    _append(case["pred"], str(base / "repo"), str(base / "link-parent" / "copy-target"),
            "copy", PRED, "ROW4", "2026-10-01T00:00:00Z")
    return f"predecessor registry {case['pred']}: line 5: path must be an absolute realpath"


def _successor_copy_relative_repo(case):
    _append(case["succ"], "repo", str(case["base"] / "other-copy"), "copy", SUCC,
            "ROW8", "2026-10-01T00:00:00Z")
    return f"successor registry {case['succ']}: line 2: repo must be an absolute realpath"


def _duplicate_worktree_path(case):
    repo, path, _, _, _, stamp = case["owned"][0]
    _append(case["pred"], repo, path, "worktree", "someone-else", "ROW4", stamp)
    return f"predecessor registry {case['pred']}: duplicate path {path}"


def _duplicate_path_across_kinds(case):
    repo, path, _, _, _, stamp = case["owned"][0]
    _append(case["pred"], repo, path, "copy", PRED, "ROW4", stamp)
    return f"predecessor registry {case['pred']}: duplicate path {path}"


def _successor_duplicate_path(case):
    stamp = "2026-10-01T00:00:00Z"
    path = str(case["base"] / "unrelated")
    _append(case["succ"], str(case["base"] / "repo"), path, "copy", SUCC, "ROW7", stamp)
    _append(case["succ"], str(case["base"] / "repo"), path, "worktree", SUCC, "ROW8", stamp)
    return f"successor registry {case['succ']}: duplicate path {path}"


@pytest.mark.parametrize(
    "spoil",
    [_worktree_relative_repo, _copy_relative_path, _copy_dotdot_repo,
     _copy_symlinked_parent_path, _successor_copy_relative_repo,
     _duplicate_worktree_path, _duplicate_path_across_kinds, _successor_duplicate_path],
    ids=lambda spoil: spoil.__name__.lstrip("_"),
)
def test_transfer_refuses_a_malformed_row_of_any_kind_by_name(transfer_case, spoil):
    case = transfer_case
    expected = spoil(case)
    before = case["succ"].read_bytes()
    result = run_transfer(case["scratch"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert f"worktree-sweep: cleanup_transfer refused: {expected}\n" == result.stderr, result.stderr
    assert case["succ"].read_bytes() == before


# --- transfer carries only rows under the predecessor's own roots --------

LANE_ROOTS_HEADER = "path\trepo\tbranch\tcreated_at\n"


def add_owned_row(case, path: Path, row: str = "ROWF") -> str:
    """Append a worktree row the predecessor owns, as an inherited registry would."""
    _append(case["pred"], str(case["base"] / "repo"), str(path), "worktree", PRED,
            row, "2026-10-01T00:00:00Z")
    return str(path)


def write_lane_roots(scratch: Path, *paths: Path) -> None:
    (scratch / "lane-roots.tsv").write_text(
        LANE_ROOTS_HEADER + "".join(
            f"{path}\t/repo\troll/x\t2026-10-01T00:00:00Z\n" for path in paths
        ),
        encoding="utf-8",
    )


def write_launch_row(scratch: Path, label: str, cwd: Path | str) -> None:
    fields = ("2026-10-01T00:00:00Z", "launch", label, "claude", str(cwd),
              "workspace:1", "-", "123", "workspace", "-", "-", "-", "-")
    with (scratch / "seat-lifecycle.tsv").open("a", encoding="utf-8") as stream:
        stream.write("\t".join(fields) + "\n")


def successor_paths(case) -> list[str]:
    return [item[1] for item in transferred_rows(case["succ"])]


def owned_paths(case) -> list[str]:
    return [path for _, path, _, _, _, _ in case["owned"]]


def test_transfer_names_and_drops_a_fixture_row_outside_the_seat_roots(transfer_case):
    """A row a test run wrote through an inherited registry (a path under the
    pytest temp root, outside the seat's scratch) is never carried: it is
    named, and the seat's own rows still move. A rerun names it again and
    carries nothing."""
    case = transfer_case
    fixture = add_owned_row(
        case, case["base"] / "pytest-of-someone" / "pytest-1" / "wt" / "L5", row="L5"
    )
    before_pred = case["pred"].read_bytes()
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"transfer_foreign_row: {fixture}\n"
        f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    )
    assert successor_paths(case) == owned_paths(case)
    assert fixture not in case["succ"].read_text(encoding="utf-8")
    # Transfer never writes the predecessor's registry; the row stays there.
    assert case["pred"].read_bytes() == before_pred

    again = run_transfer(case["scratch"])
    assert again.returncode == 0, again.stderr
    assert again.stdout == (
        f"transfer_foreign_row: {fixture}\n"
        f"cleanup_transfer: 0 rows from {PRED} to {SUCC}\n"
    )


def test_transfer_root_match_ends_at_a_path_component(transfer_case):
    """``<scratch>-sibling`` shares the scratch's string prefix but is not
    under it."""
    case = transfer_case
    sibling = add_owned_row(case, Path(f"{case['scratch']}-sibling") / "wt")
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"transfer_foreign_row: {sibling}"
    assert successor_paths(case) == owned_paths(case)


def test_transfer_resolves_a_symlinked_scratch_before_matching(transfer_case):
    """The scratch named through a symlink is the same root: rows under its
    real path are the seat's own."""
    case = transfer_case
    alias = case["base"] / "scratch-alias"
    alias.symlink_to(case["scratch"], target_is_directory=True)
    result = run_transfer(alias)
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    assert successor_paths(case) == owned_paths(case)


def test_transfer_carries_lane_roots_and_the_launch_worktree(transfer_case):
    """Worktrees outside the scratch are the seat's own when its lane-roots.tsv
    lists them or its launch row (followed through a symlink) started the
    seat there. Another seat's launch row vouches for nothing."""
    case = transfer_case
    base = case["base"]
    lane = add_owned_row(case, base / "work" / "wt" / "ROWL", row="ROWL")
    real_seat_wt = base / "seat-wt"
    real_seat_wt.mkdir()
    seat_link = base / "seat-wt-link"
    seat_link.symlink_to(real_seat_wt, target_is_directory=True)
    seat_wt = add_owned_row(case, real_seat_wt, row=PRED)
    other_wt = add_owned_row(case, base / "other-seat-wt", row="ROWO")
    write_lane_roots(case["scratch"], Path(lane))
    write_launch_row(case["scratch"], PRED, seat_link)
    write_launch_row(case["scratch"], "seat-other", other_wt)
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"transfer_foreign_row: {other_wt}\n"
        f"cleanup_transfer: 4 rows from {PRED} to {SUCC}\n"
    )
    assert successor_paths(case) == owned_paths(case) + [lane, seat_wt]


def test_transfer_drops_a_fixture_nested_inside_a_seat_worktree(transfer_case):
    """A test run inside a builder worktree makes fixture trees below it
    (``.test-row-phase-queue-*``). They lie under the scratch but inside one
    of the seat's worktrees, so they are named, not carried."""
    case = transfer_case
    builder = case["scratch"] / "wt" / "ROWB"
    write_lane_roots(case["scratch"], builder)
    own = add_owned_row(case, builder, row="ROWB")
    nested = add_owned_row(
        case, builder / ".test-row-phase-queue-abc" / "scr" / "L5" / "worktree", row="L5"
    )
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"transfer_foreign_row: {nested}\n"
        f"cleanup_transfer: 3 rows from {PRED} to {SUCC}\n"
    )
    assert successor_paths(case) == owned_paths(case) + [own]


def test_transfer_keeps_scratch_rows_when_the_launch_dir_holds_the_scratch(transfer_case):
    """A seat launched in a directory above its scratch still owns the
    worktrees under that scratch."""
    case = transfer_case
    write_launch_row(case["scratch"], PRED, case["base"])
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    assert successor_paths(case) == owned_paths(case)


def test_transfer_reads_no_roots_from_a_symlinked_lane_roots_file(transfer_case):
    """lane-roots.tsv reached through a symlink gives no roots, so a row only
    it would vouch for is named."""
    case = transfer_case
    lane = add_owned_row(case, case["base"] / "work" / "wt" / "ROWL", row="ROWL")
    elsewhere = case["base"] / "planted"
    elsewhere.mkdir()
    write_lane_roots(elsewhere, Path(lane))
    (case["scratch"] / "lane-roots.tsv").symlink_to(elsewhere / "lane-roots.tsv")
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[0] == f"transfer_foreign_row: {lane}"
    assert successor_paths(case) == owned_paths(case)


def write_lane_roots_text(scratch: Path, text: str) -> None:
    (scratch / "lane-roots.tsv").write_text(text, encoding="utf-8")


@pytest.mark.parametrize(
    "first_line",
    ["path\trepo\tbranch\n", "path\trepo\tbranch\tcreated_at\textra\n",
     "/elsewhere/wt\t/repo\troll/x\t2026-10-01T00:00:00Z\n"],
    ids=["short-header", "long-header", "data-line-first"],
)
def test_transfer_reads_no_roots_from_a_lane_roots_file_without_its_header(
    transfer_case, first_line
):
    """A lane-roots.tsv whose first line is not lane_roots.py's exact header
    gives no roots, so a row only it would vouch for is named."""
    case = transfer_case
    lane = add_owned_row(case, case["base"] / "work" / "wt" / "ROWL", row="ROWL")
    write_lane_roots_text(
        case["scratch"], first_line + f"{lane}\t/repo\troll/x\t2026-10-01T00:00:00Z\n"
    )
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"transfer_foreign_row: {lane}\n"
        f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    )
    assert successor_paths(case) == owned_paths(case)


@pytest.mark.parametrize("count", [12, 14])
def test_transfer_reads_no_root_from_a_launch_row_of_another_width(transfer_case, count):
    """Only a seat-lifecycle.tsv row of exactly 13 fields is a launch row; a
    row one field short or long vouches for nothing."""
    case = transfer_case
    seat_wt = add_owned_row(case, case["base"] / "seat-wt", row=PRED)
    fields = ["2026-10-01T00:00:00Z", "launch", PRED, "claude", seat_wt,
              "workspace:1", "-", "123", "workspace", "-", "-", "-", "-"]
    fields = fields[:count] + ["-"] * (count - len(fields))
    with (case["scratch"] / "seat-lifecycle.tsv").open("a", encoding="utf-8") as stream:
        stream.write("\t".join(fields) + "\n")
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        f"transfer_foreign_row: {seat_wt}\n"
        f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    )
    assert successor_paths(case) == owned_paths(case)


@pytest.mark.parametrize("outer_via", ["lane-roots", "launch"])
def test_transfer_carries_a_registered_worktree_nested_in_another(transfer_case, outer_via):
    """A linked worktree that is itself a registered root is the seat's own
    even when it lies inside another of the seat's worktrees: exact
    membership is decided before the nesting test."""
    case = transfer_case
    outer = case["base"] / "work" / "wt" / "OUTER"
    inner = outer / "nested" / "INNER"
    outer_row = add_owned_row(case, outer, row="OUTER")
    inner_row = add_owned_row(case, inner, row="INNER")
    if outer_via == "launch":
        write_lane_roots(case["scratch"], inner)
        write_launch_row(case["scratch"], PRED, outer)
    else:
        write_lane_roots(case["scratch"], outer, inner)
    result = run_transfer(case["scratch"])
    assert result.returncode == 0, result.stderr
    assert result.stdout == f"cleanup_transfer: 4 rows from {PRED} to {SUCC}\n"
    assert successor_paths(case) == owned_paths(case) + [outer_row, inner_row]


# --- carried rows keep their provenance across handoffs ------------------

INHERITED_HEADER = "path\tfrom\trow\tcreated_at\n"


def followed_scratch(scratch: Path, label: str) -> Path:
    """The scratch of a seat launched with ``--scratch <scratch>/watch-<label>``;
    its own registry is the canonical watch child of that scratch."""
    followed = scratch / f"watch-{label}"
    seat_registry_file(followed, label)
    return followed


def inherited_record(scratch: Path, label: str) -> Path:
    return scratch / f"watch-{label}" / "inherited-roots.tsv"


def owned_rows(case, seat: str) -> list[list[str]]:
    return [[repo, path, "worktree", seat, row] for repo, path, _, _, row, _ in case["owned"]]


def hop(scratch: Path, pred: str, succ: str) -> tuple[Path, subprocess.CompletedProcess]:
    """Hand ``pred`` (whose scratch is ``scratch``) off to ``succ``, launched
    in the followed layout under it."""
    successor = followed_scratch(scratch, succ)
    return successor, run_transfer(scratch, pred=pred, succ=succ, successor_scratch=successor)


def test_transfer_carries_inherited_rows_across_followed_handoffs(transfer_case):
    """A→B→C→D, each successor launched with ``--scratch <prev>/watch-<it>``.
    The rows lie under A's scratch only; each hop still carries them because
    the previous transfer recorded them as inherited roots of its successor."""
    case = transfer_case
    b, first = hop(case["scratch"], PRED, SUCC)
    assert first.returncode == 0, first.stderr
    assert first.stdout == f"cleanup_transfer: 2 rows from {PRED} to {SUCC}\n"
    record = inherited_record(b, SUCC)
    assert stat.S_IMODE(record.stat().st_mode) == 0o600
    lines = record.read_text(encoding="utf-8").split("\n")
    assert lines[0] + "\n" == INHERITED_HEADER and lines[-1] == "", lines
    entries = [line.split("\t") for line in lines[1:-1]]
    assert [item[:3] for item in entries] == [
        [path, PRED, row] for _, path, _, _, row, _ in case["owned"]
    ]
    assert all(len(item) == 4 and re.fullmatch(STAMP_RE, item[3]) for item in entries)

    c, second = hop(b, SUCC, "seat-c")
    assert second.returncode == 0, second.stderr
    assert second.stdout == f"cleanup_transfer: 2 rows from {SUCC} to seat-c\n"
    c_registry = c / "watch-seat-c" / "cleanup-items.tsv"
    assert [item[:5] for item in transferred_rows(c_registry)] == owned_rows(case, "seat-c")

    d, third = hop(c, "seat-c", "seat-d")
    assert third.returncode == 0, third.stderr
    assert third.stdout == "cleanup_transfer: 2 rows from seat-c to seat-d\n"
    d_registry = d / "watch-seat-d" / "cleanup-items.tsv"
    assert [item[:5] for item in transferred_rows(d_registry)] == owned_rows(case, "seat-d")


def test_an_inherited_root_vouches_only_for_its_exact_path(transfer_case):
    """A row below an inherited root is not carried on that root's word."""
    case = transfer_case
    b, first = hop(case["scratch"], PRED, SUCC)
    assert first.returncode == 0, first.stderr
    nested = str(Path(case["owned"][0][1]) / "fixture" / "wt")
    _append(b / f"watch-{SUCC}" / "cleanup-items.tsv", str(case["base"] / "repo"),
            nested, "worktree", SUCC, "L5", "2026-10-01T00:00:00Z")
    c, second = hop(b, SUCC, "seat-c")
    assert second.returncode == 0, second.stderr
    assert second.stdout == (
        f"transfer_foreign_row: {nested}\n"
        f"cleanup_transfer: 2 rows from {SUCC} to seat-c\n"
    )


def test_a_carried_row_without_its_record_is_named_until_the_transfer_reruns(transfer_case):
    """The torn state: the successor registry holds the carried rows but the
    record of them is missing (rows carried before the record existed, or a
    record lost since). The next hop names those rows and carries none of
    them; rerunning the original transfer appends no row but writes the
    record, and the next hop then carries them."""
    case = transfer_case
    b, first = hop(case["scratch"], PRED, SUCC)
    assert first.returncode == 0, first.stderr
    inherited_record(b, SUCC).unlink()
    c = followed_scratch(b, "seat-c")
    c_registry = c / "watch-seat-c" / "cleanup-items.tsv"
    before = c_registry.read_bytes()
    named = run_transfer(b, pred=SUCC, succ="seat-c", successor_scratch=c)
    assert named.returncode == 0, named.stderr
    assert named.stdout == "".join(
        f"transfer_foreign_row: {path}\n" for path in owned_paths(case)
    ) + f"cleanup_transfer: 0 rows from {SUCC} to seat-c\n"
    assert c_registry.read_bytes() == before

    rerun = run_transfer(case["scratch"], successor_scratch=b)
    assert rerun.returncode == 0, rerun.stderr
    assert rerun.stdout == f"cleanup_transfer: 0 rows from {PRED} to {SUCC}\n"
    assert inherited_record(b, SUCC).is_file()
    healed = run_transfer(b, pred=SUCC, succ="seat-c", successor_scratch=c)
    assert healed.returncode == 0, healed.stderr
    assert healed.stdout == f"cleanup_transfer: 2 rows from {SUCC} to seat-c\n"
    assert [item[:5] for item in transferred_rows(c_registry)] == owned_rows(case, "seat-c")


def _record_symlinked(record: Path, base: Path) -> None:
    real = base / "planted-record.tsv"
    real.write_bytes(record.read_bytes())
    real.chmod(0o600)
    record.unlink()
    record.symlink_to(real)


def _record_mode_0644(record: Path, base: Path) -> None:
    record.chmod(0o644)


def _record_bad_header(record: Path, base: Path) -> None:
    text = record.read_text(encoding="utf-8")
    record.write_text(text.replace("created_at", "created", 1), encoding="utf-8")


def _record_torn_tail(record: Path, base: Path) -> None:
    record.write_text(record.read_text(encoding="utf-8")[:-1], encoding="utf-8")


def _record_extra_field(record: Path, base: Path) -> None:
    with record.open("a", encoding="utf-8") as stream:
        stream.write(f"{base}/x\tseat-a\tROW9\t2026-10-01T00:00:00Z\textra\n")


def _record_relative_path(record: Path, base: Path) -> None:
    with record.open("a", encoding="utf-8") as stream:
        stream.write("wt-one\tseat-a\tROW9\t2026-10-01T00:00:00Z\n")


def _record_bad_stamp(record: Path, base: Path) -> None:
    with record.open("a", encoding="utf-8") as stream:
        stream.write(f"{base}/x\tseat-a\tROW9\t2026-10-01\n")


def _record_bad_label(record: Path, base: Path) -> None:
    with record.open("a", encoding="utf-8") as stream:
        stream.write(f"{base}/x\t-seat\tROW9\t2026-10-01T00:00:00Z\n")


def _record_not_utf8(record: Path, base: Path) -> None:
    with record.open("ab") as stream:
        stream.write(b"\xff\n")


def _record_missing(record: Path, base: Path) -> None:
    record.unlink()


RECORD_SPOILS = [
    _record_symlinked, _record_mode_0644, _record_bad_header, _record_torn_tail,
    _record_extra_field, _record_relative_path, _record_bad_stamp, _record_bad_label,
    _record_not_utf8, _record_missing,
]


@pytest.mark.parametrize("spoil", RECORD_SPOILS, ids=lambda spoil: spoil.__name__.lstrip("_"))
def test_an_untrusted_inherited_record_gives_no_inherited_roots(transfer_case, spoil):
    """A record that is symlinked, loosely permissioned, outside its grammar
    in any line, or missing vouches for nothing: the rows only it would vouch
    for are named, and none is carried."""
    case = transfer_case
    b, first = hop(case["scratch"], PRED, SUCC)
    assert first.returncode == 0, first.stderr
    spoil(inherited_record(b, SUCC), case["base"])
    c, second = hop(b, SUCC, "seat-c")
    assert second.returncode == 0, second.stderr
    assert second.stdout == "".join(
        f"transfer_foreign_row: {path}\n" for path in owned_paths(case)
    ) + f"cleanup_transfer: 0 rows from {SUCC} to seat-c\n"


def _target_symlinked(record: Path, base: Path) -> str:
    real = base / "planted-record.tsv"
    real.write_text("", encoding="utf-8")
    real.chmod(0o600)
    record.symlink_to(real)
    return "cannot open"


def _target_mode_0644(record: Path, base: Path) -> str:
    record.write_text(INHERITED_HEADER, encoding="utf-8")
    record.chmod(0o644)
    return "mode 0644, expected 0600"


def _target_malformed(record: Path, base: Path) -> str:
    record.write_text("path\tfrom\trow\n", encoding="utf-8")
    record.chmod(0o600)
    return "malformed record"


def _target_directory(record: Path, base: Path) -> str:
    record.mkdir()
    return "cannot open"


@pytest.mark.parametrize(
    "spoil",
    [_target_symlinked, _target_mode_0644, _target_malformed, _target_directory],
    ids=lambda spoil: spoil.__name__.lstrip("_"),
)
def test_transfer_refuses_an_untrusted_successor_record_and_appends_nothing(
    transfer_case, spoil
):
    case = transfer_case
    record = case["succ"].parent / "inherited-roots.tsv"
    reason = spoil(record, case["base"])
    before = case["succ"].read_bytes()
    result = run_transfer(case["scratch"])
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr.startswith(
        f"worktree-sweep: cleanup_transfer refused: successor inherited-roots record "
        f"{record}: {reason}"
    ), result.stderr
    assert case["succ"].read_bytes() == before


def test_transfer_refuses_the_same_seat_as_predecessor_and_successor(transfer_case):
    case = transfer_case
    before = case["pred"].read_bytes()
    result = run_transfer(case["scratch"], pred=PRED, succ=PRED)
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert result.stderr == (
        "worktree-sweep: cleanup_transfer refused: predecessor and successor are the same seat\n"
    )
    assert case["pred"].read_bytes() == before


def load_sweep():
    spec = importlib.util.spec_from_file_location("worktree_sweep_under_test", SWEEP)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def transfer_in_process(case, capsys):
    sweep = load_sweep()
    code = sweep.main(
        ["transfer", "--scratch", str(case["scratch"]), "--from", PRED, "--to", SUCC]
    )
    return code, capsys.readouterr()


def test_transfer_refuses_a_registry_directory_owned_by_another_user(
    transfer_case, monkeypatch, capsys
):
    case = transfer_case
    before = case["succ"].read_bytes()
    real_uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: real_uid + 1)
    code, captured = transfer_in_process(case, capsys)
    assert code == 2
    assert captured.out == ""
    assert captured.err == (
        f"worktree-sweep: cleanup_transfer refused: predecessor registry {case['pred']}: "
        "directory not owned by the current user\n"
    )
    assert case["succ"].read_bytes() == before


def test_transfer_refuses_a_registry_file_owned_by_another_user(
    transfer_case, monkeypatch, capsys
):
    case = transfer_case
    before = case["succ"].read_bytes()
    real_fstat = os.fstat

    def foreign_fstat(descriptor):
        info = real_fstat(descriptor)
        values = list(info[:10])
        values[4] = info.st_uid + 1
        return os.stat_result(values)

    monkeypatch.setattr(os, "fstat", foreign_fstat)
    code, captured = transfer_in_process(case, capsys)
    assert code == 2
    assert captured.out == ""
    assert captured.err == (
        f"worktree-sweep: cleanup_transfer refused: predecessor registry {case['pred']}: "
        "not owned by the current user\n"
    )
    assert case["succ"].read_bytes() == before


@pytest.mark.parametrize("failure", ["short", "error"])
def test_transfer_cuts_a_torn_append_back_to_the_original_registry(
    transfer_case, monkeypatch, capsys, failure
):
    case = transfer_case
    before = case["succ"].read_bytes()
    real_write = os.write

    def torn_write(descriptor, data):
        if f"\tworktree\t{SUCC}\t".encode() not in bytes(data):
            return real_write(descriptor, data)
        written = real_write(descriptor, bytes(data)[:7])
        if failure == "error":
            raise OSError(28, "No space left on device")
        return written

    # A foreign row is named only after a transfer succeeds.
    add_owned_row(case, case["base"] / "pytest-fixture" / "wt")
    monkeypatch.setattr(os, "write", torn_write)
    code, captured = transfer_in_process(case, capsys)
    assert code == 2
    assert captured.out == ""
    if failure == "short":
        assert captured.err == (
            f"worktree-sweep: cleanup_transfer refused: successor registry {case['succ']}: "
            "short write; nothing appended\n"
        )
    else:
        assert captured.err == "worktree-sweep: [Errno 28] No space left on device\n"
    assert case["succ"].read_bytes() == before
    # The inherited-roots record written before the registry append is cut
    # back with it: created empty here, and empty again.
    assert (case["succ"].parent / "inherited-roots.tsv").read_bytes() == b""


@pytest.mark.parametrize("failure", ["short", "error"])
def test_transfer_appends_no_row_when_the_inherited_record_write_fails(
    transfer_case, monkeypatch, capsys, failure
):
    case = transfer_case
    record = case["succ"].parent / "inherited-roots.tsv"
    record.write_text("path\tfrom\trow\tcreated_at\n", encoding="utf-8")
    record.chmod(0o600)
    before = case["succ"].read_bytes()
    before_record = record.read_bytes()
    real_write = os.write

    def torn_write(descriptor, data):
        if f"\t{PRED}\tROW1\t".encode() not in bytes(data):
            return real_write(descriptor, data)
        written = real_write(descriptor, bytes(data)[:7])
        if failure == "error":
            raise OSError(28, "No space left on device")
        return written

    monkeypatch.setattr(os, "write", torn_write)
    code, captured = transfer_in_process(case, capsys)
    assert code == 2
    assert captured.out == ""
    if failure == "short":
        assert captured.err == (
            "worktree-sweep: cleanup_transfer refused: successor inherited-roots record "
            f"{record}: short write; nothing appended\n"
        )
    else:
        assert captured.err == "worktree-sweep: [Errno 28] No space left on device\n"
    assert case["succ"].read_bytes() == before
    assert record.read_bytes() == before_record


# A seam for the sweep, run in front of it. It does nothing unless SEAM_LOG
# names one of its arguments; then it runs the sweep in-process with one patch,
# chosen by SEAM_MODE:
#  - foreign-append: the first write to the log lands half the line, a second
#    appender that takes no lock then adds SEAM_RECORD, and the next write
#    fails, leaving a complete record after the fragment.
#  - flock-as-linux: flock on a non-regular file succeeds, as it does on
#    Linux. macOS refuses flock on a FIFO, which would hide the regular-file
#    check behind the lock.
SEAM_SHIM = """#!{python}
import errno, fcntl, os, runpy, stat, sys
real = {real!r}
argv = sys.argv[1:]
log, mode = os.environ.get("SEAM_LOG"), os.environ.get("SEAM_MODE")
if not (log and mode and log in argv):
    os.execv(real, [real] + argv)
if mode == "foreign-append":
    target = os.stat(log)
    real_write = os.write
    calls = []

    def write(fd, data):
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (target.st_dev, target.st_ino):
            return real_write(fd, data)
        calls.append(fd)
        if len(calls) > 1:
            raise OSError(errno.EFBIG, "File too large")
        written = real_write(fd, bytes(data[: len(data) // 2]))
        other = os.open(log, os.O_WRONLY | os.O_APPEND)
        try:
            real_write(other, os.environ["SEAM_RECORD"].encode())
        finally:
            os.close(other)
        return written

    os.write = write
else:
    real_flock = fcntl.flock

    def flock(fd, operation):
        try:
            return real_flock(fd, operation)
        except OSError as exc:
            if exc.errno not in (errno.ENOTSUP, errno.EOPNOTSUPP):
                raise
            if stat.S_ISREG(os.fstat(fd).st_mode):
                raise

    fcntl.flock = flock
if "-c" in argv:
    at = argv.index("-c")
    sys.argv = ["-c"] + argv[at + 2:]
    exec(compile(argv[at + 1], "<string>", "exec"), {{"__name__": "__main__"}})
else:
    sys.argv = argv
    runpy.run_path(argv[0], run_name="__main__")
"""


def _plan_with_log(case, log: Path, seam: dict[str, str] | None = None):
    args = ["plan", "--registry", str(case["registry"]), "--row", "ROW",
            "--cleanup-log", str(log)]
    if seam is None:
        return invoke(case, *args)
    shim = case["tmp"] / "seam.py"
    shim.write_text(
        SEAM_SHIM.format(python=sys.executable, real=shutil.which("python3")),
        encoding="utf-8",
    )
    return subprocess.run(
        [sys.executable, str(shim), str(SWEEP), *args], text=True,
        capture_output=True, check=False,
        env={**case["env"], "SEAM_LOG": str(log), **seam},
    )


def test_a_foreign_append_during_a_failed_write_survives(cleanup_case):
    """A writer that skips the lock appends a whole line after this writer's
    half line, then this writer's write fails. Nothing is cut: the prior
    content, this writer's fragment and the other line all stay."""
    case = cleanup_case
    assert register(case).returncode == 0
    log = case["tmp"] / "cleanup.log"
    prefix = b"2026-01-01T00:00:00Z worktree-sweep plan kept /pad: pad\n"
    foreign = "2026-01-01T00:00:00Z worktree-sweep plan kept /foreign: foreign\n"
    logged = _plan_with_log(case, case["tmp"] / "working.log")
    assert logged.stdout == f"kept {case['worktree']}: unpushed commits\n"
    log.write_bytes(prefix)
    raced = _plan_with_log(
        case, log, {"SEAM_MODE": "foreign-append", "SEAM_RECORD": foreign}
    )
    assert (raced.returncode, raced.stdout, raced.stderr) == (
        logged.returncode, logged.stdout, logged.stderr
    )
    body = log.read_bytes()
    assert body.startswith(prefix)
    assert body.endswith(foreign.encode())
    # This writer's half line is still there, between the two whole lines.
    fragment = body[len(prefix):-len(foreign)]
    assert b" worktree-sweep " in fragment and b"\n" not in fragment


def test_a_busy_log_lock_drops_the_line(cleanup_case):
    """Another appender holds the lock: the line is dropped, nothing waits,
    and exit code, stdout and stderr match the unlocked run."""
    import fcntl

    case = cleanup_case
    assert register(case).returncode == 0
    log = case["tmp"] / "cleanup.log"
    logged = _plan_with_log(case, log)
    assert kept_lines(log) == [f"{case['worktree']}: unpushed commits"]
    before = log.read_bytes()
    holder = os.open(log, os.O_RDONLY)
    try:
        fcntl.flock(holder, fcntl.LOCK_EX)
        busy = _plan_with_log(case, log)
    finally:
        os.close(holder)
    assert log.read_bytes() == before
    assert (busy.returncode, busy.stdout, busy.stderr) == (
        logged.returncode, logged.stdout, logged.stderr
    )


def test_a_fifo_log_with_a_reader_gets_nothing(cleanup_case):
    """A FIFO with a reader opens without error, so this reaches the
    regular-file check. The writer opens with O_NONBLOCK, so the open never
    waits on a FIFO; the reader is open first all the same."""
    case = cleanup_case
    assert register(case).returncode == 0
    logged = _plan_with_log(case, case["tmp"] / "working.log")
    assert logged.stdout == f"kept {case['worktree']}: unpushed commits\n"
    fifo = case["tmp"] / "cleanup.fifo"
    os.mkfifo(fifo)
    reader = os.open(fifo, os.O_RDONLY | os.O_NONBLOCK)
    try:
        dropped = _plan_with_log(case, fifo, {"SEAM_MODE": "flock-as-linux"})
        try:
            got = os.read(reader, 65536)
        except BlockingIOError:
            got = b""
    finally:
        os.close(reader)
    assert got == b""
    assert (dropped.returncode, dropped.stdout, dropped.stderr) == (
        logged.returncode, logged.stdout, logged.stderr
    )

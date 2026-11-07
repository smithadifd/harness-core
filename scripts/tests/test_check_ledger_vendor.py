"""Tests for scripts/check-ledger-vendor.py and scripts/ledger-vendor-bump.py.

Every case runs a copy of both scripts inside a scratch toolkit repository
built under tmp_path, with a scratch ledger repository as the source, so no
case reads or changes this repository's own vendored copy. The scripts always
check the repository that holds them, so the copy is the only way in.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import types
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
CHECK = SCRIPTS / "check-ledger-vendor.py"
BUMP = SCRIPTS / "ledger-vendor-bump.py"

GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}

FIRST = {
    "ledger/__init__.py": "",
    "ledger/old.py": "OLD = 1\n",
    "ledger/store.py": "VALUE = 1\n",
    "ledger/migrations/0001_initial.sql": "CREATE TABLE t (id INTEGER);\n",
    "ledger/tests/test_store.py": "def test_store():\n    assert True\n",
}
SECOND = {
    "ledger/__init__.py": "",
    "ledger/__main__.py": "print('ledger')\n",
    "ledger/store.py": "VALUE = 2\n",
    "ledger/migrations/0001_initial.sql": "CREATE TABLE t (id INTEGER);\n",
    "ledger/tests/test_store.py": "def test_store():\n    assert True\n",
}


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, env=GIT_ENV, capture_output=True, text=True, check=True
    ).stdout


def commit_tree(repo: Path, files: dict[str, str], message: str) -> str:
    for existing in git(repo, "ls-files").splitlines():
        if existing not in files:
            git(repo, "rm", "-q", existing)
    for relative, content in files.items():
        path = repo / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        git(repo, "add", relative)
    git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return git(repo, "rev-parse", "HEAD").strip()


def release(repo: Path, sha: str, tag: str) -> None:
    """Tag ``sha`` and move the checkout's fetched main (the ref --source reads)
    to the local main, as a clone that has fetched would have it."""
    git(repo, "tag", "-a", tag, "-m", tag, sha)
    git(repo, "update-ref", "refs/remotes/origin/main", "refs/heads/main")


@pytest.fixture
def source(tmp_path: Path) -> tuple[Path, str, str]:
    """A scratch ledger: ``first`` tagged v1.0.0 and ``second`` tagged v2.0.0,
    both on main."""
    repo = tmp_path / "ledger-src"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    commit_tree(repo, {"README.md": "outside the vendored tree\n"}, "init")
    first = commit_tree(repo, {"README.md": "x\n", **FIRST}, "first")
    second = commit_tree(repo, {"README.md": "x\n", **SECOND}, "second")
    release(repo, first, "v1.0.0")
    release(repo, second, "v2.0.0")
    return repo, first, second


def run(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(script), *args],
        env=env or GIT_ENV,
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.fixture
def toolkit(tmp_path: Path, source: tuple[Path, str, str]) -> Path:
    """A scratch toolkit vendoring the source's second commit, committed clean."""
    repo = tmp_path / "toolkit"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy2(CHECK, repo / "scripts" / CHECK.name)
    shutil.copy2(BUMP, repo / "scripts" / BUMP.name)
    (repo / "README.md").write_text("toolkit\n", encoding="utf-8")
    (repo / ".gitignore").write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    result = run(repo / "scripts" / BUMP.name, str(source[0]), "v2.0.0")
    assert result.returncode == 0, result.stdout + result.stderr
    git(repo, "commit", "-q", "-m", "vendor")
    return repo


def check(repo: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return run(repo / "scripts" / CHECK.name, *args, env=env)


VENDORED = "skills/_lib/ledger"


# --- offline drift --------------------------------------------------------------


def test_clean_copy_passes(toolkit: Path) -> None:
    result = check(toolkit)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "ledger vendor: ok: skills/_lib/ledger matches ledger-vendor.pin" in result.stdout


def test_interpreter_caches_are_ignored(toolkit: Path) -> None:
    for folder in (toolkit / VENDORED / "__pycache__", toolkit / VENDORED / "tests" / "__pycache__"):
        folder.mkdir()
        (folder / "store.cpython-314.pyc").write_bytes(b"\0")
        (folder / "anything-else.py").write_bytes(b"\0")

    result = check(toolkit)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""


def test_one_edited_byte_is_changed(toolkit: Path) -> None:
    target = toolkit / VENDORED / "store.py"
    target.write_bytes(target.read_bytes().replace(b"2", b"3"))

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: changed skills/_lib/ledger/store.py" in result.stdout
    assert result.stdout.count("drift:") == 1


def test_missing_file_is_missing(toolkit: Path) -> None:
    (toolkit / VENDORED / "migrations" / "0001_initial.sql").unlink()

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "ledger vendor: drift: missing skills/_lib/ledger/migrations/0001_initial.sql"
        in result.stdout
    )


def test_missing_folder_reports_every_file(toolkit: Path) -> None:
    git(toolkit, "rm", "-q", "-r", VENDORED)

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count("drift: missing") == len(SECOND)
    assert "drift: mode" not in result.stdout


@pytest.mark.parametrize(
    "relative",
    [
        ".DS_Store",
        "migrations/.DS_Store",
        ".pytest_cache/x",
        ".pytest_cache/v/cache/nodeids",
        "tests/.pytest_cache/README.md",
    ],
)
def test_untracked_allow_listed_file_is_a_warning_not_a_failure(
    toolkit: Path, relative: str
) -> None:
    extra = toolkit / VENDORED / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"\0\1")

    result = check(toolkit)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        f"ledger vendor: warning: untracked skills/_lib/ledger/{relative} is not a vendored "
        "file; it does not fail the check, remove it"
    ) in result.stderr
    assert "drift:" not in result.stdout
    assert "ledger vendor: ok:" in result.stdout


def test_untracked_empty_pytest_cache_folder_is_a_warning(toolkit: Path) -> None:
    (toolkit / VENDORED / ".pytest_cache").mkdir()

    result = check(toolkit)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning: untracked skills/_lib/ledger/.pytest_cache is not a vendored" in result.stderr


@pytest.mark.parametrize(
    "relative",
    [
        # A package folder shadows the vendored module of the same name.
        "paths/__init__.py",
        "store/__init__.py",
        "verbs/append/__init__.py",
        "foo.py",
        "verbs/local.py",
        "local.pth",
        "store.cpython-314-darwin.so",
        "stray.pyc",
        "tests/stray.pyc",
        # Near misses of the allow-list.
        ".DS_Store.py",
        "DS_Store",
        ".pytest_cache.py",
        ".pytest_cache_x/conftest.py",
        "pytest_cache/conftest.py",
    ],
)
def test_untracked_file_off_the_allow_list_fails_by_name(toolkit: Path, relative: str) -> None:
    extra = toolkit / VENDORED / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_bytes(b"X = 1\n")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"ledger vendor: drift: untracked skills/_lib/ledger/{relative}\n" in result.stdout
    assert "warning:" not in result.stderr


def test_untracked_empty_folder_fails_by_name(toolkit: Path) -> None:
    (toolkit / VENDORED / "verbs" / "append").mkdir(parents=True)

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: untracked skills/_lib/ledger/verbs/append\n" in result.stdout


def test_untracked_fifo_fails_by_name(toolkit: Path) -> None:
    os.mkfifo(toolkit / VENDORED / "pipe")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: untracked skills/_lib/ledger/pipe\n" in result.stdout


def test_tracked_extra_file_is_extra(toolkit: Path) -> None:
    (toolkit / VENDORED / ".DS_Store").write_bytes(b"\0\1")
    git(toolkit, "add", "-f", f"{VENDORED}/.DS_Store")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: extra skills/_lib/ledger/.DS_Store" in result.stdout
    assert "warning:" not in result.stderr


def test_tracked_extra_file_is_extra_even_when_deleted_from_disk(toolkit: Path) -> None:
    extra = toolkit / VENDORED / "local.py"
    extra.write_text("X = 1\n", encoding="utf-8")
    git(toolkit, "add", f"{VENDORED}/local.py")
    extra.unlink()

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: extra skills/_lib/ledger/local.py" in result.stdout


def test_tracked_pycache_file_is_extra(toolkit: Path) -> None:
    cache = toolkit / VENDORED / "__pycache__"
    cache.mkdir()
    (cache / "store.cpython-314.pyc").write_bytes(b"\0")
    git(toolkit, "add", "-f", f"{VENDORED}/__pycache__/store.cpython-314.pyc")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "ledger vendor: drift: extra skills/_lib/ledger/__pycache__/store.cpython-314.pyc"
        in result.stdout
    )


def test_vendored_file_replaced_by_symlink_is_symlink(toolkit: Path) -> None:
    target = toolkit / VENDORED / "store.py"
    elsewhere = toolkit / "store-elsewhere.py"
    elsewhere.write_bytes(target.read_bytes())
    target.unlink()
    target.symlink_to(elsewhere)

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: symlink skills/_lib/ledger/store.py" in result.stdout
    assert "drift: changed" not in result.stdout


def test_extra_symlink_is_symlink(toolkit: Path) -> None:
    # Untracked, and still a failure: a symlink never becomes a warning.
    (toolkit / VENDORED / "linked.py").symlink_to(toolkit / "README.md")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: symlink skills/_lib/ledger/linked.py" in result.stdout
    assert "warning:" not in result.stderr


def test_symlinked_directory_is_symlink(toolkit: Path) -> None:
    (toolkit / VENDORED / "more").symlink_to(toolkit / "scripts")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: symlink skills/_lib/ledger/more" in result.stdout


def test_symlinked_folder_is_symlink(toolkit: Path, tmp_path: Path) -> None:
    folder = toolkit / VENDORED
    moved = tmp_path / "moved-ledger"
    shutil.copytree(folder, moved)
    shutil.rmtree(folder)
    folder.symlink_to(moved)

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: symlink skills/_lib/ledger\n" in result.stdout


def test_executable_bit_on_disk_is_mode(toolkit: Path) -> None:
    (toolkit / VENDORED / "store.py").chmod(0o755)

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: mode skills/_lib/ledger/store.py" in result.stdout


def test_executable_mode_in_the_index_is_mode(toolkit: Path) -> None:
    git(toolkit, "update-index", "--chmod=+x", f"{VENDORED}/store.py")
    (toolkit / VENDORED / "store.py").chmod(0o644)

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: mode skills/_lib/ledger/store.py" in result.stdout


def test_untracked_copy_with_the_right_bytes_is_mode(toolkit: Path) -> None:
    git(toolkit, "rm", "-q", "--cached", f"{VENDORED}/store.py")

    result = check(toolkit)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: mode skills/_lib/ledger/store.py" in result.stdout


def test_missing_pin_is_exit_2(toolkit: Path) -> None:
    (toolkit / "ledger-vendor.pin").unlink()

    result = check(toolkit)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "ledger vendor: ERROR: ledger-vendor.pin: cannot read ledger-vendor.pin" in result.stderr


def test_git_unavailable_is_exit_2(toolkit: Path, tmp_path: Path) -> None:
    empty = tmp_path / "empty-path"
    empty.mkdir()
    env = {**GIT_ENV, "PATH": str(empty)}

    result = check(toolkit, env=env)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "ledger vendor: ERROR: git is not available" in result.stderr


def test_not_a_git_work_tree_is_exit_2(toolkit: Path) -> None:
    shutil.rmtree(toolkit / ".git")

    result = check(toolkit)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "ledger vendor: ERROR: git rev-parse --show-toplevel failed" in result.stderr


# --- pin grammar ------------------------------------------------------------------

SHA = "a" * 40
BLOB = "b" * 40
GOOD_PIN = (
    f"# header\ntag v0.1.0\nrepo smithadifd/ledger\nsha {SHA}\n"
    f"{BLOB} __init__.py\n{BLOB} tests/test_x.py\n"
)
UNTAGGED_PIN = GOOD_PIN.replace("tag v0.1.0\n", "\n")


def test_good_pin_parses() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_ledger_vendor_under_test", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        pin = module.parse_pin(GOOD_PIN.encode())
    finally:
        sys.modules.pop(spec.name, None)

    assert pin.repo == "smithadifd/ledger"
    assert pin.sha == SHA
    assert pin.tag == "v0.1.0"
    assert pin.manifest == {"__init__.py": BLOB, "tests/test_x.py": BLOB}


@pytest.mark.parametrize(
    "text",
    [
        UNTAGGED_PIN.replace("repo smithadifd/ledger\n", "repo smithadifd/ledger\ntag v0.1.0\n"),
        UNTAGGED_PIN.replace("repo smithadifd/ledger\n", "tag v0.1.0\nrepo smithadifd/ledger\n"),
        UNTAGGED_PIN.replace(f"sha {SHA}\n", f"sha {SHA}\n# note\ntag v0.1.0\n"),
    ],
    ids=["after-repo", "before-repo", "after-sha"],
)
def test_pin_with_a_tag_line_parses(text: str) -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_ledger_vendor_tag_test", CHECK)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
        pin = module.parse_pin(text.encode())
    finally:
        sys.modules.pop(spec.name, None)

    assert pin.tag == "v0.1.0"
    assert pin.sha == SHA


NEAR_MISSES = [
    ("crlf", GOOD_PIN.replace("\n", "\r\n"), "line 1: carriage return"),
    ("no-final-newline", GOOD_PIN[:-1], "pin does not end with a newline"),
    ("empty", "", "pin is empty"),
    ("not-utf8", None, "pin is not UTF-8 text"),
    ("duplicate-repo", GOOD_PIN.replace("sha ", "repo smithadifd/ledger\nsha "),
     "line 4: duplicate repo line"),
    ("duplicate-sha", GOOD_PIN.replace(f"{BLOB} __init__.py", f"sha {SHA}\n{BLOB} __init__.py"),
     "line 5: duplicate sha line"),
    ("short-sha", GOOD_PIN.replace(f"sha {SHA}", "sha 2d75aeb"),
     "line 4: sha is not 40 lowercase hex characters"),
    ("uppercase-sha", GOOD_PIN.replace(f"sha {SHA}", f"sha {SHA.upper()}"),
     "line 4: sha is not 40 lowercase hex characters"),
    ("sha-before-repo", f"sha {SHA}\nrepo smithadifd/ledger\n{BLOB} a.py\n",
     "line 1: sha line before the repo line"),
    ("no-repo", f"{BLOB} a.py\n", "line 1: manifest line before the repo and sha lines"),
    ("no-sha-line", f"repo smithadifd/ledger\n{BLOB} a.py\n",
     "line 2: manifest line before the repo and sha lines"),
    ("no-manifest", f"repo smithadifd/ledger\ntag v1\nsha {SHA}\n", "no manifest lines"),
    ("no-tag", UNTAGGED_PIN,
     "no tag line (the pin requires exactly one 'tag <name>' line)"),
    ("no-tag-no-manifest", f"repo smithadifd/ledger\nsha {SHA}\n", "no tag line"),
    ("bare-tag-keyword", GOOD_PIN.replace("tag v0.1.0\n", "tag\n"),
     "line 2: tag is not a name of [A-Za-z0-9._-]"),
    ("bad-repo", GOOD_PIN.replace("repo smithadifd/ledger", "repo ledger"),
     "line 3: repo is not <owner>/<name>"),
    ("unknown-line", GOOD_PIN.replace("# header", "ref v1"),
     "line 1: not a comment, repo, sha or '<40 lowercase hex> <path>' manifest line"),
    ("dotdot", GOOD_PIN.replace("tests/test_x.py", "../escape.py"),
     "line 6: manifest path '../escape.py' has a '..' component"),
    ("inner-dotdot", GOOD_PIN.replace("tests/test_x.py", "tests/../../escape.py"),
     "line 6: manifest path 'tests/../../escape.py' has a '..' component"),
    ("dot", GOOD_PIN.replace("tests/test_x.py", "./test_x.py"),
     "line 6: manifest path './test_x.py' has a '.' component"),
    ("leading-slash", GOOD_PIN.replace("tests/test_x.py", "/etc/passwd"),
     "line 6: manifest path '/etc/passwd' has a leading slash"),
    ("empty-component", GOOD_PIN.replace("tests/test_x.py", "tests//test_x.py"),
     "line 6: manifest path 'tests//test_x.py' has an empty component"),
    ("odd-character", GOOD_PIN.replace("tests/test_x.py", "tests/test_x$.py"),
     "line 6: manifest path 'tests/test_x$.py' has a character outside [A-Za-z0-9._-]"),
    ("pycache", GOOD_PIN.replace("tests/test_x.py", "tests/__pycache__/x.py"),
     "line 6: manifest path 'tests/__pycache__/x.py' has a __pycache__ component"),
    ("pyc", GOOD_PIN.replace("tests/test_x.py", "tests/test_x.pyc"),
     "line 6: manifest path 'tests/test_x.pyc' names a .pyc file"),
    ("unsorted", GOOD_PIN.replace(f"{BLOB} __init__.py\n{BLOB} tests/test_x.py",
                                  f"{BLOB} tests/test_x.py\n{BLOB} __init__.py"),
     "line 6: manifest is not sorted (__init__.py after tests/test_x.py)"),
    ("duplicate-path", GOOD_PIN.replace("tests/test_x.py", "__init__.py"),
     "line 6: duplicate manifest path __init__.py"),
    ("tab-separator", GOOD_PIN.replace(f"{BLOB} __init__.py", f"{BLOB}\t__init__.py"),
     "line 5: not a comment, repo, sha or"),
    ("two-spaces", GOOD_PIN.replace(f"{BLOB} __init__.py", f"{BLOB}  __init__.py"),
     "line 5: not a comment, repo, sha or"),
    ("whitespace-in-path", GOOD_PIN.replace("tests/test_x.py", "tests/test x.py"),
     "line 6: not a comment, repo, sha or"),
    ("trailing-space", GOOD_PIN.replace("__init__.py\n", "__init__.py \n"),
     "line 5: not a comment, repo, sha or"),
    ("short-blob", GOOD_PIN.replace(f"{BLOB} __init__.py", "bbbbbbb __init__.py"),
     "line 5: not a comment, repo, sha or"),
    ("uppercase-blob", GOOD_PIN.replace(f"{BLOB} __init__.py", f"{BLOB.upper()} __init__.py"),
     "line 5: not a comment, repo, sha or"),
    ("comment-in-manifest", GOOD_PIN.replace(f"{BLOB} tests", f"# note\n{BLOB} tests"),
     "line 6: comment or empty line inside the manifest"),
    ("blank-in-manifest", GOOD_PIN.replace(f"{BLOB} tests", f"\n{BLOB} tests"),
     "line 6: comment or empty line inside the manifest"),
    ("duplicate-tag", GOOD_PIN.replace("repo smithadifd/ledger\n",
                                       "repo smithadifd/ledger\ntag v0.1.0\n"),
     "line 4: duplicate tag line"),
    ("malformed-tag", GOOD_PIN.replace("tag v0.1.0\n", "tag v0.1.0/../x\n"),
     "line 2: tag is not a name of [A-Za-z0-9._-]"),
    ("empty-tag", GOOD_PIN.replace("tag v0.1.0\n", "tag \n"),
     "line 2: tag is not a name of [A-Za-z0-9._-]"),
    ("tag-in-manifest", UNTAGGED_PIN.replace(f"{BLOB} tests", f"tag v0.1.0\n{BLOB} tests"),
     "line 6: tag line inside the manifest"),
    ("repo-after-sha", GOOD_PIN.replace(f"{BLOB} __init__.py",
                                        f"repo smithadifd/ledger\n{BLOB} __init__.py"),
     "line 5: duplicate repo line"),
]


@pytest.mark.parametrize(
    ("text", "message"),
    [(text, message) for _, text, message in NEAR_MISSES],
    ids=[name for name, _, _ in NEAR_MISSES],
)
def test_pin_near_miss_is_refused_by_name(toolkit: Path, text: str | None, message: str) -> None:
    data = b"repo smithadifd/ledger\nsha \xff\n" if text is None else text.encode("utf-8")
    (toolkit / "ledger-vendor.pin").write_bytes(data)

    result = check(toolkit)

    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    assert f"ledger vendor: ERROR: ledger-vendor.pin: {message}" in result.stderr


# --- --source ---------------------------------------------------------------------


def test_source_mode_clean(toolkit: Path, source: tuple[Path, str, str]) -> None:
    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "and the source checkout's tree" in result.stdout


def test_source_without_the_pinned_commit_is_exit_2(toolkit: Path, tmp_path: Path) -> None:
    other = tmp_path / "other-ledger"
    other.mkdir()
    git(other, "init", "-q", "-b", "main")
    commit_tree(other, {"ledger/__init__.py": ""}, "unrelated")

    result = check(toolkit, "--source", str(other))

    assert result.returncode == 2, result.stdout + result.stderr
    assert "does not have the pinned commit" in result.stderr


def _tag(repo: Path, name: str, sha: str) -> None:
    git(repo, "tag", "-a", name, "-m", name, sha)


def _add_tag_line(toolkit: Path, name: str) -> None:
    """Point the pin's (required) tag line at ``name``."""
    _rewrite_pin(toolkit, "tag v2.0.0\n", f"tag {name}\n")


def test_source_mode_accepts_a_tag_that_names_the_pin(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    _tag(source[0], "v9.0.0", source[2])
    _add_tag_line(toolkit, "v9.0.0")

    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"@{source[2]} tag v9.0.0," in result.stdout


def test_source_mode_catches_a_tag_that_names_another_commit(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    _tag(source[0], "v9.0.0", source[1])
    _add_tag_line(toolkit, "v9.0.0")

    offline = check(toolkit)
    result = check(toolkit, "--source", str(source[0]))

    assert offline.returncode == 0, offline.stdout + offline.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        f"ledger vendor: drift: pin tag v9.0.0 resolves to {source[1]}, "
        f"not the pin sha {source[2]}"
    ) in result.stdout


def test_source_mode_without_the_pinned_tag_is_drift(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    _add_tag_line(toolkit, "v9.0.0")

    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "ledger vendor: drift: pin tag v9.0.0 is not a tag (refs/tags/v9.0.0) "
        "in the source checkout"
    ) in result.stdout


def test_source_mode_reads_the_tag_ref_not_a_branch_of_the_same_name(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    # A branch named like the tag does not stand in for refs/tags/<tag>.
    git(source[0], "branch", "v9.0.0", source[2])
    _add_tag_line(toolkit, "v9.0.0")

    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: pin tag v9.0.0 is not a tag" in result.stdout


def _side_commit(source: tuple[Path, str, str]) -> str:
    """A commit off main (as a pull request head is) whose ledger/ tree is
    the second commit's, so only the ancestry check can tell it apart."""
    repo = source[0]
    git(repo, "checkout", "-q", "-b", "side", source[2])
    side = commit_tree(repo, {"README.md": "side\n", **SECOND}, "side")
    git(repo, "checkout", "-q", "main")
    return side


def _repin(toolkit: Path, sha: str, tag: str) -> None:
    pin = toolkit / "ledger-vendor.pin"
    lines = pin.read_text(encoding="utf-8").split("\n")
    lines = [
        f"sha {sha}" if line.startswith("sha ") else f"tag {tag}" if line.startswith("tag ") else line
        for line in lines
    ]
    pin.write_text("\n".join(lines), encoding="utf-8")


def test_source_mode_refuses_a_pinned_sha_not_on_main(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    side = _side_commit(source)
    _tag(source[0], "v9.0.0", side)
    _repin(toolkit, side, "v9.0.0")

    offline = check(toolkit)
    result = check(toolkit, "--source", str(source[0]))

    assert offline.returncode == 0, offline.stdout + offline.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        f"ledger vendor: drift: pin sha {side} is not on the ledger's main "
        f"(refs/remotes/origin/main is {source[2]})"
    ) in result.stdout
    assert "pin manifest" not in result.stdout
    assert "pin tag" not in result.stdout


def test_source_mode_refuses_a_checkout_without_main(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    git(source[0], "update-ref", "-d", "refs/remotes/origin/main")

    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        f"ledger vendor: drift: pin sha {source[2]}: the source checkout has no "
        "refs/remotes/origin/main"
    ) in result.stdout


def test_source_mode_reads_main_at_use_time(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    # The side commit is refused until main has merged it, then accepted.
    side = _side_commit(source)
    _tag(source[0], "v9.0.0", side)
    _repin(toolkit, side, "v9.0.0")
    assert check(toolkit, "--source", str(source[0])).returncode == 1

    git(source[0], "merge", "-q", "--ff-only", "side")
    git(source[0], "update-ref", "refs/remotes/origin/main", "refs/heads/main")
    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 0, result.stdout + result.stderr


def _rewrite_pin(toolkit: Path, old: str, new: str) -> None:
    pin = toolkit / "ledger-vendor.pin"
    text = pin.read_text(encoding="utf-8")
    assert text.count(old) == 1
    pin.write_text(text.replace(old, new), encoding="utf-8")


def _blob(data: bytes) -> str:
    import hashlib

    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def test_source_mode_catches_a_manifest_that_moved_with_an_edit(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    # An edit carried into the pin by hand: the offline check agrees with the
    # pin, so only the source comparison can catch it.
    store = toolkit / VENDORED / "store.py"
    old_blob = _blob(store.read_bytes())
    store.write_bytes(b"VALUE = 99\n")
    _rewrite_pin(toolkit, f"{old_blob} store.py", f"{_blob(b'VALUE = 99' + bytes([10]))} store.py")
    git(toolkit, "add", "-A")

    offline = check(toolkit)
    result = check(toolkit, "--source", str(source[0]))

    assert offline.returncode == 0, offline.stdout + offline.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: pin manifest changed ledger/store.py" in result.stdout


def test_source_mode_catches_a_file_dropped_from_the_manifest(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    main_blob = _blob((toolkit / VENDORED / "__main__.py").read_bytes())
    _rewrite_pin(toolkit, f"{main_blob} __main__.py\n", "")
    git(toolkit, "rm", "-q", f"{VENDORED}/__main__.py")

    result = check(toolkit, "--source", str(source[0]))

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: pin manifest missing ledger/__main__.py" in result.stdout


def test_source_mode_catches_a_file_added_to_the_manifest(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    extra = toolkit / VENDORED / "zz_local.py"
    extra.write_bytes(b"X = 1\n")
    pin = toolkit / "ledger-vendor.pin"
    pin.write_text(pin.read_text(encoding="utf-8") + f"{_blob(b'X = 1' + bytes([10]))} zz_local.py\n",
                   encoding="utf-8")
    git(toolkit, "add", "-A")

    offline = check(toolkit)
    result = check(toolkit, "--source", str(source[0]))

    assert offline.returncode == 0, offline.stdout + offline.stderr
    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: pin manifest extra ledger/zz_local.py" in result.stdout


# --- the bump script --------------------------------------------------------------


def bump(toolkit: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return run(toolkit / "scripts" / BUMP.name, *args)


def test_bump_to_the_same_pin_is_a_no_op(toolkit: Path, source: tuple[Path, str, str]) -> None:
    before = (toolkit / "ledger-vendor.pin").read_bytes()

    result = bump(toolkit, str(source[0]), "v2.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert git(toolkit, "status", "--porcelain") == ""
    assert (toolkit / "ledger-vendor.pin").read_bytes() == before


def test_bump_to_another_commit_moves_pin_and_files(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    pin = (toolkit / "ledger-vendor.pin").read_text(encoding="utf-8")
    assert f"sha {source[1]}\n" in pin
    assert f"sha {source[2]}\n" not in pin
    assert (toolkit / VENDORED / "store.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert (toolkit / VENDORED / "old.py").is_file()
    assert not (toolkit / VENDORED / "__main__.py").exists()
    status = sorted(git(toolkit, "status", "--porcelain").splitlines())
    assert status == [
        "A  skills/_lib/ledger/old.py",
        "D  skills/_lib/ledger/__main__.py",
        "M  ledger-vendor.pin",
        "M  skills/_lib/ledger/store.py",
    ]
    assert check(toolkit).returncode == 0
    assert check(toolkit, "--source", str(source[0])).returncode == 0


def test_bump_to_a_tag_records_it_beside_the_sha(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    _tag(source[0], "v9.0.0", source[1])

    result = bump(toolkit, str(source[0]), "v9.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    pin = (toolkit / "ledger-vendor.pin").read_text(encoding="utf-8")
    assert f"repo smithadifd/ledger\ntag v9.0.0\nsha {source[1]}\n" in pin
    assert check(toolkit, "--source", str(source[0])).returncode == 0


@pytest.mark.parametrize("revision", ["sha", "main", "HEAD~1"])
def test_bump_to_anything_but_a_tag_writes_nothing(
    toolkit: Path, source: tuple[Path, str, str], revision: str
) -> None:
    pin_before = (toolkit / "ledger-vendor.pin").read_bytes()
    commit = source[1] if revision == "sha" else revision

    result = bump(toolkit, str(source[0]), commit)

    assert result.returncode == 2, result.stdout + result.stderr
    assert "the pin requires a tag, so bump to a tag of the ledger repository" in result.stderr
    assert git(toolkit, "status", "--porcelain") == ""
    assert (toolkit / "ledger-vendor.pin").read_bytes() == pin_before


def test_bump_removes_folders_left_empty(toolkit: Path, source: tuple[Path, str, str]) -> None:
    repo = source[0]
    third = commit_tree(
        repo, {"README.md": "x\n", "ledger/__init__.py": "", "ledger/store.py": "V = 3\n"}, "third"
    )
    release(repo, third, "v3.0.0")

    result = bump(toolkit, str(repo), "v3.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (toolkit / VENDORED / "migrations").exists()
    assert not (toolkit / VENDORED / "tests").exists()


def test_bump_to_an_unknown_commit_writes_nothing(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    result = bump(toolkit, str(source[0]), "v9.9.9")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "has no tag v9.9.9 (refs/tags/v9.9.9)" in result.stderr
    assert git(toolkit, "status", "--porcelain") == ""


def test_bump_refuses_a_symlink_in_the_source_tree(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    repo = source[0]
    (repo / "ledger" / "link.py").symlink_to("store.py")
    git(repo, "add", "ledger/link.py")
    git(repo, "commit", "-q", "-m", "link")
    head = git(repo, "rev-parse", "HEAD").strip()
    release(repo, head, "v3.0.0")

    result = bump(toolkit, str(repo), "v3.0.0")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "ledger/link.py at" in result.stderr and "not a 100644 file" in result.stderr
    assert git(toolkit, "status", "--porcelain") == ""


def test_bump_refuses_a_malformed_existing_pin(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    (toolkit / "ledger-vendor.pin").write_text("sha 123\n", encoding="utf-8")

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 2, result.stdout + result.stderr
    assert "ledger-vendor.pin: line 1: sha line before the repo line" in result.stderr
    assert (toolkit / VENDORED / "store.py").read_text(encoding="utf-8") == "VALUE = 2\n"


def test_bump_leaves_the_tree_for_review_when_the_check_fails_after(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    # With core.fileMode off, `git add` keeps an existing index mode, so the
    # bump writes the files and the pin and the check after it finds `mode`.
    git(toolkit, "update-index", "--chmod=+x", f"{VENDORED}/store.py")
    git(toolkit, "config", "core.fileMode", "false")

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: mode skills/_lib/ledger/store.py" in result.stdout
    assert (toolkit / VENDORED / "store.py").read_text(encoding="utf-8") == "VALUE = 1\n"
    assert f"sha {source[1]}\n" in (toolkit / "ledger-vendor.pin").read_text(encoding="utf-8")
    assert git(toolkit, "log", "--oneline").count("\n") == 2


def test_bump_with_an_untracked_ds_store_warns_and_passes(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    stray = toolkit / VENDORED / ".DS_Store"
    stray.write_bytes(b"\0")

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "warning: untracked skills/_lib/ledger/.DS_Store" in result.stderr
    assert "?? skills/_lib/ledger/.DS_Store" in git(toolkit, "status", "--porcelain")


def test_bump_with_an_untracked_module_fails_after_writing(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    stray = toolkit / VENDORED / "stray.py"
    stray.write_text("X = 1\n", encoding="utf-8")

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "ledger vendor: drift: untracked skills/_lib/ledger/stray.py" in result.stdout
    assert "?? skills/_lib/ledger/stray.py" in git(toolkit, "status", "--porcelain")


@pytest.mark.parametrize("linked", ["skills/_lib/ledger/migrations", "skills/_lib"])
def test_bump_refuses_a_symlink_under_or_above_the_folder(
    toolkit: Path, source: tuple[Path, str, str], tmp_path: Path, linked: str
) -> None:
    # The linked folder's real contents live outside the repository, with a
    # sentinel that a bump writing or deleting through the link would change.
    outside = tmp_path / "outside" / linked.replace("/", "_")
    shutil.copytree(toolkit / linked, outside)
    shutil.rmtree(toolkit / linked)
    (toolkit / linked).symlink_to(outside)
    sentinel = outside / ("0001_initial.sql" if linked.endswith("migrations") else "ledger/store.py")
    sentinel.write_text("SENTINEL\n", encoding="utf-8")
    pin_before = (toolkit / "ledger-vendor.pin").read_bytes()
    listing_before = sorted(p.relative_to(outside).as_posix() for p in outside.rglob("*"))

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 2, result.stdout + result.stderr
    assert f"{linked} is a symlink; refusing before writing anything" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "SENTINEL\n"
    assert sorted(p.relative_to(outside).as_posix() for p in outside.rglob("*")) == listing_before
    assert (toolkit / "ledger-vendor.pin").read_bytes() == pin_before


def test_bump_never_clobbers_or_follows_a_preexisting_temporary_name(
    toolkit: Path, source: tuple[Path, str, str], tmp_path: Path
) -> None:
    sentinel = tmp_path / "sentinel.txt"
    sentinel.write_text("SENTINEL\n", encoding="utf-8")
    (toolkit / "ledger-vendor.pin.tmp").symlink_to(sentinel)
    (toolkit / ".ledger-vendor.pin.tmp").write_text("KEEP\n", encoding="utf-8")

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "SENTINEL\n"
    assert (toolkit / "ledger-vendor.pin.tmp").is_symlink()
    assert (toolkit / ".ledger-vendor.pin.tmp").read_text(encoding="utf-8") == "KEEP\n"
    assert f"sha {source[1]}\n" in (toolkit / "ledger-vendor.pin").read_text(encoding="utf-8")
    assert list(toolkit.glob(".ledger-vendor.pin.*.tmp")) == []


def test_bump_never_writes_outside_the_folder_and_the_pin(
    toolkit: Path, source: tuple[Path, str, str]
) -> None:
    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    changed = git(toolkit, "status", "--porcelain", "--untracked-files=all").splitlines()
    assert changed
    for line in changed:
        path = line[3:]
        assert path == "ledger-vendor.pin" or path.startswith(VENDORED + "/"), line
    assert (toolkit / "README.md").read_text(encoding="utf-8") == "toolkit\n"


def test_bump_never_writes_through_a_hard_link(
    toolkit: Path, source: tuple[Path, str, str], tmp_path: Path
) -> None:
    # A vendored file that shares its inode with a file outside the repository:
    # the bump replaces the name and never writes the shared inode.
    target = toolkit / VENDORED / "store.py"
    sentinel = tmp_path / "sentinel-store.py"
    sentinel.write_bytes(target.read_bytes())
    sentinel.chmod(0o600)
    target.unlink()
    os.link(sentinel, target)

    result = bump(toolkit, str(source[0]), "v1.0.0")

    assert result.returncode == 0, result.stdout + result.stderr
    assert sentinel.read_text(encoding="utf-8") == "VALUE = 2\n"
    assert sentinel.stat().st_mode & 0o777 == 0o600
    assert target.read_text(encoding="utf-8") == "VALUE = 1\n"
    assert target.stat().st_mode & 0o777 == 0o644
    assert target.stat().st_ino != sentinel.stat().st_ino
    assert list((toolkit / VENDORED).rglob("*.tmp")) == []


def _bump_in_process(
    toolkit: Path, monkeypatch: pytest.MonkeyPatch, after_prewalk: callable
) -> types.ModuleType:
    """Load the scratch toolkit's bump script in this process and run
    ``after_prewalk`` once, at the first blob read: after the pre-walk that
    refuses symlinks and before the first file is written."""
    for key, value in GIT_ENV.items():
        if key.startswith("GIT_"):
            monkeypatch.setenv(key, value)
    monkeypatch.setitem(sys.modules, "check_ledger_vendor", types.ModuleType("placeholder"))
    spec = importlib.util.spec_from_file_location(
        "ledger_vendor_bump_under_test", toolkit / "scripts" / BUMP.name
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    real_git = module._git
    fired: list[bool] = []

    def git_then_hook(args: list[str], cwd: Path) -> bytes:
        if args[:2] == ["cat-file", "blob"] and not fired:
            fired.append(True)
            after_prewalk()
        return real_git(args, cwd)

    monkeypatch.setattr(module, "_git", git_then_hook)
    module.fired = fired
    return module


def _link_out(folder: Path, outside: Path) -> None:
    shutil.copytree(folder, outside)
    shutil.rmtree(folder)
    folder.symlink_to(outside)


def test_bump_write_rechecks_a_symlink_made_after_the_prewalk(
    toolkit: Path,
    source: tuple[Path, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    outside = tmp_path / "outside-migrations"
    sentinel = outside / "0001_initial.sql"

    def link() -> None:
        _link_out(toolkit / VENDORED / "migrations", outside)
        sentinel.write_text("SENTINEL\n", encoding="utf-8")

    module = _bump_in_process(toolkit, monkeypatch, link)

    code = module.main([str(source[0]), "v1.0.0"])

    assert module.fired == [True]
    assert sentinel.read_text(encoding="utf-8") == "SENTINEL\n"
    assert sorted(p.name for p in outside.iterdir()) == ["0001_initial.sql"]
    assert code == 2
    assert "skills/_lib/ledger/migrations is a symlink" in capsys.readouterr().err


def test_bump_delete_rechecks_a_symlink_made_after_the_prewalk(
    toolkit: Path,
    source: tuple[Path, str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # v3.0.0 lacks tests/test_store.py, so the delete loop reaches tests/.
    repo = source[0]
    third = commit_tree(
        repo, {"README.md": "x\n", "ledger/__init__.py": "", "ledger/store.py": "V = 3\n"}, "third"
    )
    release(repo, third, "v3.0.0")
    outside = tmp_path / "outside-tests"
    sentinel = outside / "test_store.py"

    def link() -> None:
        _link_out(toolkit / VENDORED / "tests", outside)

    module = _bump_in_process(toolkit, monkeypatch, link)

    code = module.main([str(repo), "v3.0.0"])

    assert module.fired == [True]
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "def test_store():\n    assert True\n"
    assert code == 2
    assert "skills/_lib/ledger/tests is a symlink" in capsys.readouterr().err

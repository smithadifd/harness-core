"""check-core-manifest.py: every tracked file is vendored or repository-only.

Each case builds a scratch git repository holding a copy of the script, so
the script checks that repository (it reads the one that holds it). Nothing
here reads this checkout's own tree except to copy the script.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-core-manifest.py"

MANIFEST = (
    "tree lib/owned/\n"
    "lib/owned/a.py\n"
    "lib/owned/b.py\n"
    "lib/shared.py\n"
)
TABLE = (
    "# Vendoring\n"
    "\n"
    "## Repository-only files\n"
    "\n"
    "Every tracked file that is not vendored.\n"
    "\n"
    "| Path | What it is |\n"
    "|---|---|\n"
    "| `.core-manifest` | the manifest |\n"
    "| `VENDORING.md` | this file |\n"
    "| `scripts/check-core-manifest.py` | the check |\n"
    "| `lib/local.py` | a repository-only module |\n"
    "\n"
    "## After the table\n"
)
FILES = {
    ".core-manifest": MANIFEST,
    "VENDORING.md": TABLE,
    "lib/owned/a.py": "a = 1\n",
    "lib/owned/b.py": "b = 1\n",
    "lib/shared.py": "s = 1\n",
    "lib/local.py": "local = 1\n",
}


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def build(tmp_path: Path, files: dict[str, str] | None = None, stage: bool = True) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    (repo / "scripts").mkdir()
    shutil.copyfile(SCRIPT, repo / "scripts" / "check-core-manifest.py")
    for name, text in (FILES if files is None else files).items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    if stage:
        git(repo, "add", "-A")
    return repo


def run(repo: Path, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    return subprocess.run(
        [sys.executable, str(repo / "scripts" / "check-core-manifest.py")],
        cwd=cwd or repo, env=env, capture_output=True, text=True,
    )


def lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stdout.splitlines()


def test_a_covered_tree_passes(tmp_path):
    result = run(build(tmp_path))
    assert result.returncode == 0, result.stdout + result.stderr
    assert lines(result) == [
        "core manifest: ok: every tracked file is vendored (listed in .core-manifest) "
        "or repository-only (listed in VENDORING.md)"
    ]


def test_the_result_does_not_depend_on_the_working_directory(tmp_path):
    repo = build(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    assert run(repo, cwd=elsewhere).returncode == 0
    assert run(repo, cwd=repo / "lib").returncode == 0


# J2: each of the four integrity rules exits 1 naming the path.

def test_j2_a_listed_path_that_is_not_tracked_fails(tmp_path):
    files = dict(FILES)
    files[".core-manifest"] = MANIFEST.replace("lib/shared.py\n", "lib/gone.py\nlib/shared.py\n")
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/gone.py: listed but not tracked" in lines(result)


def test_j2_a_listed_path_deleted_from_the_index_fails(tmp_path):
    repo = build(tmp_path)
    git(repo, "rm", "-q", "--cached", "lib/shared.py")
    result = run(repo)
    assert result.returncode == 1
    assert "lib/shared.py: listed but not tracked" in lines(result)


def test_j2_a_tracked_path_neither_listed_nor_repository_only_fails(tmp_path):
    files = dict(FILES)
    files["lib/stray.py"] = "stray = 1\n"
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/stray.py: tracked but unlisted" in lines(result)


def test_j2_a_listed_path_at_mode_100755_fails(tmp_path):
    repo = build(tmp_path)
    git(repo, "update-index", "--chmod=+x", "lib/shared.py")
    result = run(repo)
    assert result.returncode == 1
    assert "lib/shared.py: listed at mode 100755" in lines(result)


def test_j2_a_listed_symlink_fails_by_mode(tmp_path):
    repo = build(tmp_path)
    (repo / "lib" / "shared.py").unlink()
    os.symlink("local.py", repo / "lib" / "shared.py")
    git(repo, "add", "-A")
    result = run(repo)
    assert result.returncode == 1
    assert "lib/shared.py: listed at mode 120000" in lines(result)


def test_j2_a_tree_folder_holding_an_unlisted_tracked_file_fails(tmp_path):
    files = dict(FILES)
    files["lib/owned/c.py"] = "c = 1\n"
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/owned/c.py: under tree lib/owned/ but not listed" in lines(result)


def test_j2_the_repository_only_table_does_not_excuse_a_file_under_a_tree(tmp_path):
    files = dict(FILES)
    files["lib/owned/c.py"] = "c = 1\n"
    files["VENDORING.md"] = TABLE.replace(
        "| `lib/local.py` |", "| `lib/owned/c.py` | excused? |\n| `lib/local.py` |"
    )
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/owned/c.py: under tree lib/owned/ but not listed" in lines(result)


def test_j2_a_tree_with_no_listed_path_fails(tmp_path):
    files = dict(FILES)
    files[".core-manifest"] = "tree lib/empty/\n" + MANIFEST
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/empty/: tree has no listed path" in lines(result)


# The table's own integrity.

def test_a_repository_only_path_that_is_not_tracked_fails(tmp_path):
    files = dict(FILES)
    files["VENDORING.md"] = TABLE.replace(
        "| `lib/local.py` |", "| `lib/ghost.py` | gone |\n| `lib/local.py` |"
    )
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/ghost.py: repository-only but not tracked" in lines(result)


def test_a_path_both_vendored_and_repository_only_fails(tmp_path):
    files = dict(FILES)
    files["VENDORING.md"] = TABLE.replace(
        "| `lib/local.py` |", "| `lib/shared.py` | both |\n| `lib/local.py` |"
    )
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert "lib/shared.py: listed as vendored and repository-only" in lines(result)


# Inputs are read from the index (the checked-out commit), never from disk
# edits that are not staged.

def test_an_unstaged_disk_edit_does_not_hide_a_staged_gap(tmp_path):
    files = dict(FILES)
    files["lib/stray.py"] = "stray = 1\n"
    repo = build(tmp_path, files)
    (repo / "VENDORING.md").write_text(TABLE.replace(
        "| `lib/local.py` |", "| `lib/stray.py` | hidden on disk only |\n| `lib/local.py` |"
    ))
    result = run(repo)
    assert result.returncode == 1
    assert "lib/stray.py: tracked but unlisted" in lines(result)


def test_an_untracked_manifest_fails(tmp_path):
    repo = build(tmp_path)
    git(repo, "rm", "-q", "--cached", ".core-manifest")
    result = run(repo)
    assert result.returncode == 1
    assert ".core-manifest: is not tracked" in lines(result)


def test_outside_a_git_work_tree_fails(tmp_path):
    folder = tmp_path / "plain"
    (folder / "scripts").mkdir(parents=True)
    shutil.copyfile(SCRIPT, folder / "scripts" / "check-core-manifest.py")
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    result = subprocess.run(
        [sys.executable, str(folder / "scripts" / "check-core-manifest.py")],
        cwd=folder, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout.startswith("core manifest: ERROR: git rev-parse failed")


def test_a_script_below_the_top_of_the_work_tree_fails(tmp_path):
    repo = build(tmp_path)
    nested = repo / "nested" / "scripts"
    nested.mkdir(parents=True)
    shutil.copyfile(SCRIPT, nested / "check-core-manifest.py")
    result = run(repo / "nested")
    assert result.returncode == 1
    assert result.stdout == (
        f"core manifest: ERROR: {(repo / 'nested').resolve()} is not the top of a git work tree\n")


def test_git_missing_from_path_fails(tmp_path):
    repo = build(tmp_path)
    empty = tmp_path / "no-git"
    empty.mkdir()
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env["PATH"] = str(empty)
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "check-core-manifest.py")],
        cwd=repo, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert result.stdout.startswith("core manifest: ERROR: git is not available")


def test_a_manifest_tracked_as_executable_fails(tmp_path):
    repo = build(tmp_path)
    git(repo, "update-index", "--chmod=+x", ".core-manifest")
    result = run(repo)
    assert result.returncode == 1
    assert lines(result) == [
        ".core-manifest: is tracked at mode 100755 stage 0, not as a regular file",
        "core manifest: FAIL: 1 finding(s)",
    ]


def test_a_manifest_that_is_not_utf8_fails(tmp_path):
    repo = build(tmp_path)
    (repo / ".core-manifest").write_bytes(b"lib/shared\xff.py\n")
    git(repo, "add", ".core-manifest")
    result = run(repo)
    assert result.returncode == 1
    assert lines(result)[0] == ".core-manifest: is not UTF-8 text (invalid start byte)"


def test_a_manifest_whose_blob_cannot_be_read_fails(tmp_path):
    repo = build(tmp_path)
    git(repo, "update-index", "--cacheinfo", "100644," + "1" * 40 + ",.core-manifest")
    result = run(repo)
    assert result.returncode == 1
    assert lines(result)[0].startswith(".core-manifest: cannot be read (git cat-file failed")


def test_arguments_are_refused(tmp_path):
    repo = build(tmp_path)
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "check-core-manifest.py"), "--anything"],
        cwd=repo, capture_output=True, text=True,
    )
    assert result.returncode == 1
    assert "takes no arguments" in result.stderr


# The .core-manifest grammar: each near miss refused by name.

@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (MANIFEST.replace("\n", "\r\n"), "line 1: carriage return (CRLF line endings are refused)"),
        (MANIFEST.rstrip("\n"), "does not end with a newline"),
        ("", "is empty"),
        (MANIFEST.replace("lib/owned/b.py\n", "lib/owned/b.py\n\n"), "line 4: empty line"),
        ("tree lib/owned\n" + MANIFEST.split("\n", 1)[1], "line 1: tree path must end in /"),
        (MANIFEST + "tree lib/late/\n", "line 5: tree line after the first path"),
        (MANIFEST.replace("lib/owned/b.py\n", "lib/owned/b.py\nlib/owned/b.py\n"),
         "line 4: duplicate path lib/owned/b.py"),
        ("tree lib/owned/\nlib/owned/b.py\nlib/owned/a.py\nlib/shared.py\n",
         "line 3: paths are not sorted (lib/owned/a.py after lib/owned/b.py)"),
        (MANIFEST.replace("lib/shared.py", "/lib/shared.py"),
         "line 4: path '/lib/shared.py' has a leading slash"),
        (MANIFEST.replace("lib/shared.py", "lib/../shared.py"),
         "line 4: path 'lib/../shared.py' has a '..' component"),
        (MANIFEST.replace("lib/shared.py", "lib/shared.pyc"),
         "line 4: path 'lib/shared.pyc' names a .pyc file"),
        ("# a comment\n" + MANIFEST,
         "line 1: path '# a comment' has a character outside [A-Za-z0-9._-]"),
        ("tree lib/owned/\ntree lib/owned/\n" + MANIFEST.split("\n", 1)[1],
         "line 2: tree lib/owned/ repeats or overlaps tree lib/owned/"),
    ],
    ids=["crlf", "no-final-newline", "empty", "empty-line", "tree-no-slash", "tree-late",
         "duplicate", "unsorted", "leading-slash", "dotdot", "pyc", "comment", "tree-repeat"],
)
def test_manifest_grammar_near_misses_are_refused_by_name(tmp_path, text, reason):
    files = dict(FILES)
    files[".core-manifest"] = text
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert f".core-manifest: {reason}" in lines(result), result.stdout


# The repository-only table grammar: each near miss refused by name.

@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (TABLE.replace("## Repository-only files", "## Repository-Only Files"),
         "needs the line '## Repository-only files' exactly once, found 0"),
        (TABLE + "\n## Repository-only files\n",
         "needs the line '## Repository-only files' exactly once, found 2"),
        (TABLE.replace("| Path | What it is |\n|---|---|\n", ""),
         "line 7: '| `.core-manifest` | the manifest |' before the table head "
         "'| Path | What it is |'"),
        (TABLE.replace("|---|---|", "|---|---|---|"),
         "line 8: the table head must be followed by '|---|---|'"),
        (TABLE.replace("| `lib/local.py` | a repository-only module |",
                       "|  `lib/local.py` | a repository-only module |"),
         "line 12: not a table row of the form | `<path>` | <text> |"),
        (TABLE.replace("| `lib/local.py` | a repository-only module |",
                       "- `lib/local.py`: a repository-only module"),
         "line 12: not a table row of the form | `<path>` | <text> |"),
        (TABLE.replace("| `lib/local.py` | a repository-only module |",
                       "| lib/local.py | a repository-only module |"),
         "line 12: not a table row of the form | `<path>` | <text> |"),
        (TABLE.replace("| `lib/local.py` | a repository-only module |",
                       "| `lib/local.py` |  |"),
         "line 12: not a table row of the form | `<path>` | <text> |"),
        (TABLE.replace("| `lib/local.py` | a repository-only module |",
                       "```\n| `lib/local.py` | a repository-only module |\n```"),
         "line 12: not a table row of the form | `<path>` | <text> |"),
        (TABLE.replace("| `lib/local.py` |", "| `lib/local.py` | twice |\n| `lib/local.py` |"),
         "line 13: duplicate path lib/local.py"),
        (TABLE.replace("| `lib/local.py` |", "| `/lib/x.py` | slash |\n| `lib/local.py` |"),
         "line 12: path '/lib/x.py' has a leading slash"),
        (TABLE.replace("\n", "\r\n"), "line 1: carriage return (CRLF line endings are refused)"),
    ],
    ids=["heading-case", "heading-twice", "no-head", "bad-rule", "extra-space", "bullet",
         "no-backticks", "empty-text", "fence", "duplicate", "leading-slash", "crlf"],
)
def test_table_grammar_near_misses_are_refused_by_name(tmp_path, text, reason):
    files = dict(FILES)
    files["VENDORING.md"] = text
    result = run(build(tmp_path, files))
    assert result.returncode == 1
    assert f"VENDORING.md: {reason}" in lines(result), result.stdout

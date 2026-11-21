"""check-no-person.py: no tracked file names a person, a machine, a home path
or a private host.

Each case builds a scratch git repository holding a copy of the script, so
the script checks that repository (it reads the one that holds it). Every
plant is built from fragments and every deny-list term is invented here, so
this file passes the check it tests: it holds no real name, home path or
private host, and the real deny list never appears in it.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "check-no-person.py"
ALLOWANCE = "scripts/no-person-allowance.tsv"
HEADER = "# path\tsha256 of the lowercased matched text\treason\n"
# Invented deny-list terms, spelled in fragments.
TERM = "zq" + "plantword"
OTHER = "zq" + "otherword"
DENY = TERM + "|" + OTHER
CONFIG_VARS = ("CI", "GITHUB_ACTIONS", "NO_PERSON_DENY")

# One plant per shape rule; none of them is a deny-list term.
SHAPE_PLANTS = {
    "absolute home": "see /Us" + "ers/x/notes.md",
    "linux home": "see /ho" + "me/x/notes.md",
    "encoded home": "folder -Us" + "ers-alice-project",
    "private-network hostname": "ssh buildbox.ho" + "me now",
    "private home folder": "cd ~/co" + "de/project",
    "private config path": "load ~/x/sec" + "rets/job.env",
    "decision number": "see AD" + "R 0020",
    "private schema": "the x.act" + ".v1 wire",
}
SECRET_PLANTS = {
    "token": "token gh" + "p_" + "A" * 24,
    "private key": "-----BEGIN RSA PRI" + "VATE KEY-----",
}


def sha(text: str) -> str:
    return hashlib.sha256(text.lower().encode("utf-8")).hexdigest()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


def build(tmp_path: Path, files: dict[str, str | bytes], allowance: str = HEADER) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "scripts").mkdir()
    shutil.copyfile(SCRIPT, repo / "scripts" / "check-no-person.py")
    (repo / ALLOWANCE).write_bytes(allowance.encode("utf-8"))
    for name, content in files.items():
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
    git(repo, "add", "-A")
    return repo


def run(repo: Path, **env_vars: str) -> subprocess.CompletedProcess[str]:
    env = {
        key: value for key, value in os.environ.items()
        if key not in CONFIG_VARS and not key.startswith("GIT_")
    }
    env.update(env_vars)
    result = subprocess.run(
        [sys.executable, str(repo / "scripts" / "check-no-person.py")],
        cwd=repo, env=env, capture_output=True, text=True,
    )
    # Whatever the case, the output never carries a deny-list term or the
    # variable's value.
    shown = (result.stdout + result.stderr).lower()
    assert TERM not in shown and OTHER not in shown, "the output printed a deny-list term"
    if env_vars.get("NO_PERSON_DENY"):
        assert env_vars["NO_PERSON_DENY"].lower() not in shown, "the output printed the variable"
    return result


def lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return result.stdout.splitlines()


def load_checker():
    spec = importlib.util.spec_from_file_location("check_no_person", SCRIPT)
    assert spec is not None and spec.loader is not None
    checker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(checker)
    return checker


def test_a_clean_tree_passes_with_the_deny_list_set(tmp_path):
    repo = build(tmp_path, {"notes.md": "A generic note.\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 0, result.stdout
    assert lines(result) == ["no-person: ok: 3 tracked files scanned; deny list set"]


# J3: every shape fails in any tracked file, not only the standards.

@pytest.mark.parametrize("path", ["scripts/tool.py", "skills/_lib/module.py",
                                  "skills/_lib/standards/rule.md", "README.md"])
@pytest.mark.parametrize("name", sorted(SHAPE_PLANTS))
def test_j3_a_shape_in_any_tracked_file_fails_naming_path_line_and_rule(tmp_path, path, name):
    repo = build(tmp_path, {path: "clean line\n" + SHAPE_PLANTS[name] + "\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{path}:2: no-person shape: {name}" in lines(result), result.stdout


@pytest.mark.parametrize("name", sorted(SECRET_PLANTS))
def test_j3_a_secret_shape_fails(tmp_path, name):
    repo = build(tmp_path, {"scripts/tool.py": SECRET_PLANTS[name] + "\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"scripts/tool.py:1: secret shape: {name}" in lines(result), result.stdout


@pytest.mark.parametrize("plant", ["Path.ho" + "me() / 'x'", "self.ho" + "me = root",
                                   "see the .ho" + "mepage"])
def test_j3_the_narrowed_host_shape_lets_code_through(tmp_path, plant):
    repo = build(tmp_path, {"scripts/tool.py": plant + "\n"})
    assert run(repo, NO_PERSON_DENY=DENY).returncode == 0


def test_j3_word_boundaries_are_ascii_and_case_sensitive(tmp_path):
    # A non-ASCII letter that ignore-case folds into the ASCII class (the
    # Kelvin sign) beside the decision-number shape does not hide it.
    repo = build(tmp_path, {"notes.md": "K" + "AD" + "R 0020\n" + "xAD" + "R 0020\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert lines(result)[0] == "notes.md:1: no-person shape: decision number"
    assert "notes.md:2: no-person shape: decision number" not in lines(result)


def test_j3_a_shape_in_a_path_name_fails(tmp_path):
    name = "notes/-Us" + "ers-alice.md"
    repo = build(tmp_path, {name: "clean\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{name}: path name: no-person shape: encoded home" in lines(result)


def test_j3_a_binary_file_is_scanned(tmp_path):
    repo = build(tmp_path, {"data.bin": b"\x00\xff\x10/Us" + b"ers/x/y\x00\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert "data.bin:1: no-person shape: absolute home" in lines(result)


def test_j3_the_ledger_tree_allows_exactly_its_two_shapes(tmp_path):
    allowed = SHAPE_PLANTS["private home folder"] + "\n" + SHAPE_PLANTS["decision number"] + "\n"
    repo = build(tmp_path, {"skills/_lib/ledger/store.py": allowed})
    assert run(repo, NO_PERSON_DENY=DENY).returncode == 0
    repo2 = build(tmp_path / "second", {
        "skills/_lib/ledger/store.py": allowed + SHAPE_PLANTS["absolute home"] + "\n",
    })
    result = run(repo2, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert lines(result)[:-1] == ["skills/_lib/ledger/store.py:3: no-person shape: absolute home"]


def test_j3_the_two_ledger_shapes_still_fail_outside_the_ledger_tree(tmp_path):
    repo = build(tmp_path, {"skills/_lib/ledger.py": SHAPE_PLANTS["decision number"] + "\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert "skills/_lib/ledger.py:1: no-person shape: decision number" in lines(result)


LEDGER_DOC = "skills/_lib/ledger/verbs/doc.py"
DOC_HOME_OPEN = "home_fd = os.open(root" + ".home, os.O_RDONLY | directory)\n"


def test_j3_the_ledger_doc_home_open_line_is_allowed_once(tmp_path):
    repo = build(tmp_path, {LEDGER_DOC: DOC_HOME_OPEN})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 0, result.stdout


def test_j3_a_second_ledger_doc_home_open_line_fails(tmp_path):
    repo = build(tmp_path, {LEDGER_DOC: DOC_HOME_OPEN * 2})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:2: no-person shape: private-network hostname" in lines(result)
    assert f"{LEDGER_DOC}:1: no-person shape: private-network hostname" not in lines(result)


def test_j3_another_shape_on_the_allowed_line_still_fails(tmp_path):
    source = DOC_HOME_OPEN.rstrip("\n") + "  # " + SHAPE_PLANTS["absolute home"] + "\n"
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:1: no-person shape: absolute home" in lines(result)
    assert f"{LEDGER_DOC}:1: no-person shape: private-network hostname" not in lines(result)


def test_j3_the_home_open_line_fails_in_another_ledger_file(tmp_path):
    path = "skills/_lib/ledger/verbs/other.py"
    repo = build(tmp_path, {path: DOC_HOME_OPEN})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{path}:1: no-person shape: private-network hostname" in lines(result)


def test_j3_the_home_open_line_fails_outside_the_ledger_tree(tmp_path):
    path = "lib/doc.py"
    repo = build(tmp_path, {path: DOC_HOME_OPEN})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{path}:1: no-person shape: private-network hostname" in lines(result)


@pytest.mark.parametrize("source", [
    "home_fd = os.open(other" + ".home, flags)\n",
    "home_fd = open(root" + ".home, flags)\n",
    "text = 'os.open(root" + ".home, flags)'\n",
    "# os.open(root" + ".home, flags)\n",
])
def test_j3_ledger_doc_home_open_near_misses_fail(tmp_path, source):
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:1: no-person shape: private-network hostname" in lines(result)


@pytest.mark.parametrize("tail", [
    "`\n",
    "\"\"\"unterminated\n",
    "if True:\n    pass\n  pass\n",
])
def test_j3_a_tokenize_error_gives_the_ledger_doc_no_allowance(tmp_path, tail):
    repo = build(tmp_path, {LEDGER_DOC: DOC_HOME_OPEN + tail})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:1: no-person shape: private-network hostname" in lines(result)


def test_j3_an_unknown_source_encoding_gives_the_ledger_doc_no_allowance(tmp_path):
    source = b"# coding: no-such-codec\n" + DOC_HOME_OPEN.encode("utf-8")
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:2: no-person shape: private-network hostname" in lines(result)


def test_j3_an_undecodable_byte_gives_the_ledger_doc_no_allowance(tmp_path):
    source = DOC_HOME_OPEN.encode("utf-8") + b"# \xff\n"
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:1: no-person shape: private-network hostname" in lines(result)


def test_j3_a_latin_1_column_shift_cannot_move_the_ledger_doc_allowance(tmp_path):
    multibyte = "é".encode("utf-8") * 15
    source = (b"# coding: latin-1\n\"" + multibyte +
              b"\"; os.open(root." + b"home(0)); host." + b"home\n")
    checker = load_checker()
    _text, spans = checker.ledger_doc_home_spans(LEDGER_DOC, source)
    assert spans == frozenset()
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:2: no-person shape: private-network hostname" in lines(result)


def test_j3_utf_8_sig_ledger_doc_home_open_is_allowed_once(tmp_path):
    source = b"\xef\xbb\xbf" + DOC_HOME_OPEN.encode("utf-8")
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 0, result.stdout


def test_j3_a_non_utf_8_source_gives_the_ledger_doc_no_allowance(tmp_path):
    source = b"# coding: latin-1\n" + DOC_HOME_OPEN.encode("ascii")
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{LEDGER_DOC}:2: no-person shape: private-network hostname" in lines(result)


def test_j3_an_eligible_span_cannot_move_to_another_hostname(monkeypatch):
    checker = load_checker()
    monkeypatch.setattr(checker, "LEDGER_DOC_HOME_TOKENS", (
        (checker.tokenize.NAME, "os"), (checker.tokenize.OP, "."),
        (checker.tokenize.NAME, "open"), (checker.tokenize.OP, "("),
        (checker.tokenize.NAME, "host"), (checker.tokenize.OP, "."),
        (checker.tokenize.NAME, "home"),
    ))
    hits = checker.scan_text(
        LEDGER_DOC, b"os.open(host." + b"home, flags)\n", None, {}, set())
    assert hits == [f"{LEDGER_DOC}:1: no-person shape: private-network hostname"]


def test_j3_the_real_vendored_ledger_doc_passes_the_whole_check(tmp_path):
    source = (SCRIPT.parents[1] / LEDGER_DOC).read_bytes()
    repo = build(tmp_path, {LEDGER_DOC: source})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 0, result.stdout


def test_j3_the_index_is_scanned_not_the_disk(tmp_path):
    repo = build(tmp_path, {"notes.md": SHAPE_PLANTS["absolute home"] + "\n"})
    (repo / "notes.md").write_text("cleaned on disk only\n")
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert "notes.md:1: no-person shape: absolute home" in lines(result)


# J4: the deny list unset.

WARN = ("no-person: WARN: NO_PERSON_DENY IS UNSET: the deny list did not run; "
        "only the shape rules were checked")


def test_j4_unset_outside_ci_warns_loudly_and_passes_a_clean_tree(tmp_path):
    repo = build(tmp_path, {"notes.md": "A generic note.\n"})
    result = run(repo)
    assert result.returncode == 0
    assert lines(result) == [
        WARN, "no-person: ok: 3 tracked files scanned; deny list unset (WARN above)",
    ]


def test_j4_unset_outside_ci_still_runs_the_shape_rules(tmp_path):
    repo = build(tmp_path, {"notes.md": SHAPE_PLANTS["encoded home"] + "\n"})
    result = run(repo)
    assert result.returncode == 1
    assert lines(result)[:2] == [WARN, "notes.md:1: no-person shape: encoded home"]


def test_j4_empty_outside_ci_warns_like_unset(tmp_path):
    repo = build(tmp_path, {"notes.md": "A generic note.\n"})
    result = run(repo, NO_PERSON_DENY="")
    assert result.returncode == 0
    assert lines(result)[0] == WARN


@pytest.mark.parametrize("ci_var", ["CI", "GITHUB_ACTIONS"])
@pytest.mark.parametrize("value", [None, ""], ids=["unset", "empty"])
def test_j4_unset_or_empty_in_ci_fails(tmp_path, ci_var, value):
    repo = build(tmp_path, {"notes.md": "A generic note.\n"})
    extra = {ci_var: "true"}
    if value is not None:
        extra["NO_PERSON_DENY"] = value
    result = run(repo, **extra)
    assert result.returncode == 1
    assert ("no-person: NO_PERSON_DENY is unset or empty in CI; the deny list cannot be "
            "skipped there") in lines(result)


def test_j4_an_invalid_expression_fails_without_printing_it(tmp_path):
    repo = build(tmp_path, {"notes.md": "A generic note.\n"})
    result = run(repo, NO_PERSON_DENY=TERM + "|(unclosed")
    assert result.returncode == 1
    assert "no-person: NO_PERSON_DENY is not a valid regular expression" in lines(result)
    assert "unclosed" not in result.stdout + result.stderr


def test_j4_an_expression_matching_the_empty_string_fails(tmp_path):
    repo = build(tmp_path, {"notes.md": "A generic note.\n"})
    result = run(repo, NO_PERSON_DENY=TERM + "|")
    assert result.returncode == 1
    assert "no-person: NO_PERSON_DENY matches the empty string" in lines(result)


# J5: a deny-list word fails, named deny-list, never printed.

def test_j5_a_deny_word_fails_naming_path_line_and_deny_list(tmp_path):
    repo = build(tmp_path, {"docs/notes.md": "one\ntwo\nwritten by " + TERM + " today\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert lines(result) == ["docs/notes.md:3: deny-list", "no-person: FAIL: 1 finding(s)"]


def test_j5_the_deny_list_is_case_insensitive(tmp_path):
    repo = build(tmp_path, {"notes.md": "by " + TERM.upper() + "\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert "notes.md:1: deny-list" in lines(result)


def test_j5_a_deny_word_in_a_path_name_fails_without_printing_the_path(tmp_path):
    repo = build(tmp_path, {"notes/" + TERM + ".md": "clean\n", "a.md": "clean\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    # git ls-files order: a.md, notes/<term>.md, scripts/...
    assert "tracked path #2: deny-list (path name)" in lines(result)


def test_j5_the_deny_list_does_not_run_under_the_ledger_tree(tmp_path):
    repo = build(tmp_path, {"skills/_lib/ledger/store.py": "# by " + TERM + "\n"})
    assert run(repo, NO_PERSON_DENY=DENY).returncode == 0


def test_j5_a_deny_word_in_a_binary_file_fails(tmp_path):
    repo = build(tmp_path, {"blob.bin": b"\x00\x01" + TERM.encode() + b"\xff\n"})
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert "blob.bin:1: deny-list" in lines(result)


# The allowance: (path, sha256 of the lowercased match) pairs.

def allowance(*rows: tuple[str, str]) -> str:
    return HEADER + "".join(f"{path}\t{digest}\ta planted reason\n" for path, digest in rows)


def test_an_allowed_pair_passes(tmp_path):
    repo = build(tmp_path, {"lib/a.py": "x = '" + TERM + "'\n"},
                 allowance(("lib/a.py", sha(TERM))))
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 0, result.stdout


def test_the_allowance_hashes_the_lowercased_match(tmp_path):
    repo = build(tmp_path, {"lib/a.py": "x = '" + TERM.upper() + "'\n"},
                 allowance(("lib/a.py", sha(TERM))))
    assert run(repo, NO_PERSON_DENY=DENY).returncode == 0


def test_the_same_term_in_an_unlisted_file_fails(tmp_path):
    repo = build(tmp_path, {"lib/a.py": "x = '" + TERM + "'\n", "lib/b.py": "y = '" + TERM + "'\n"},
                 allowance(("lib/a.py", sha(TERM))))
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert lines(result) == ["lib/b.py:1: deny-list", "no-person: FAIL: 1 finding(s)"]


def test_a_new_term_in_an_allowed_file_fails(tmp_path):
    repo = build(tmp_path, {"lib/a.py": "x = '" + TERM + "'\ny = '" + OTHER + "'\n"},
                 allowance(("lib/a.py", sha(TERM))))
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert lines(result) == ["lib/a.py:2: deny-list", "no-person: FAIL: 1 finding(s)"]


def test_removing_one_row_fails_naming_that_path(tmp_path):
    files = {"lib/a.py": "x = '" + TERM + "'\n", "lib/b.py": "y = '" + TERM + "'\n"}
    full = build(tmp_path, files, allowance(("lib/a.py", sha(TERM)), ("lib/b.py", sha(TERM))))
    assert run(full, NO_PERSON_DENY=DENY).returncode == 0
    short = build(tmp_path / "short", files, allowance(("lib/b.py", sha(TERM))))
    result = run(short, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert lines(result) == ["lib/a.py:1: deny-list", "no-person: FAIL: 1 finding(s)"]


def test_a_stale_row_fails(tmp_path):
    repo = build(tmp_path, {"lib/a.py": "clean\n"},
                 allowance(("lib/a.py", sha(TERM))))
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert (f"{ALLOWANCE}:2: stale allowance (the pair matched nothing in this scan)"
            in lines(result))


def test_a_row_under_the_ledger_tree_is_stale(tmp_path):
    repo = build(tmp_path, {"skills/_lib/ledger/a.py": "x = '" + TERM + "'\n"},
                 allowance(("skills/_lib/ledger/a.py", sha(TERM))))
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{ALLOWANCE}:2: stale allowance (the pair matched nothing in this scan)" in lines(result)


def test_a_missing_allowance_file_fails(tmp_path):
    repo = build(tmp_path, {"notes.md": "clean\n"})
    git(repo, "rm", "-q", "--cached", ALLOWANCE)
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{ALLOWANCE}: unreadable (not tracked as a regular file)" in lines(result)


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("lib/a.py\t" + "0" * 64 + "\n", "not a row of <path> TAB <sha256> TAB <reason>"),
        ("lib/a.py " + "0" * 64 + " a reason\n", "not a row of <path> TAB <sha256> TAB <reason>"),
        ("lib/a.py\t" + "A" * 64 + "\treason\n", "the hash is not 64 lowercase hex"),
        ("lib/a.py\t" + "0" * 63 + "\treason\n", "the hash is not 64 lowercase hex"),
        ("lib/a.py\t" + "0" * 64 + "\t \n", "the reason is empty"),
        ("lib/../a.py\t" + "0" * 64 + "\treason\n", "the path has a '..' component"),
        ("/lib/a.py\t" + "0" * 64 + "\treason\n", "the path has a leading slash"),
        ("\n", "not a row of <path> TAB <sha256> TAB <reason>"),
    ],
    ids=["two-fields", "spaces", "upper-hex", "short-hex", "empty-reason", "dotdot",
         "leading-slash", "empty-line"],
)
def test_allowance_grammar_near_misses_are_refused_by_line(tmp_path, row, reason):
    repo = build(tmp_path, {"notes.md": "clean\n"}, HEADER + row)
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{ALLOWANCE}:2: {reason}" in lines(result), result.stdout


def test_allowance_duplicate_pair_and_crlf_are_refused(tmp_path):
    row = "lib/a.py\t" + sha(TERM) + "\treason\n"
    repo = build(tmp_path, {"lib/a.py": TERM + "\n"}, HEADER + row + row)
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert f"{ALLOWANCE}:3: duplicate pair of line 2" in lines(result)
    repo2 = build(tmp_path / "crlf", {"notes.md": "clean\n"}, HEADER.replace("\n", "\r\n"))
    result2 = run(repo2, NO_PERSON_DENY=DENY)
    assert result2.returncode == 1
    assert f"{ALLOWANCE}:1: carriage return (CRLF line endings are refused)" in lines(result2)


# The license carve: the MIT copyright line of the root LICENSE, and nothing
# else. The holder is read from this repository's own root LICENSE, never
# spelled here, and the deny list for these cases is built from its words.

REPO_LICENSE = SCRIPT.parents[1] / "LICENSE"
COPYRIGHT = re.compile(r"Copyright \(c\) [0-9]{4} (.+)")


def license_case() -> tuple[str, int, str, str]:
    """(the LICENSE text ending in a newline, the copyright line's number,
    the copyright line, a deny expression of the holder's words and DENY)."""
    text = REPO_LICENSE.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        text += "\n"
    rows = text.split("\n")
    found = [number for number, row in enumerate(rows, 1) if COPYRIGHT.fullmatch(row)]
    assert len(found) == 1, "the root LICENSE needs exactly one copyright line"
    line = rows[found[0] - 1]
    words = COPYRIGHT.fullmatch(line).group(1).split()
    deny = "|".join(re.escape(word) for word in words) + "|" + DENY
    return text, found[0], line, deny


def test_the_root_license_copyright_line_passes(tmp_path):
    text, _number, _line, deny = license_case()
    repo = build(tmp_path, {"LICENSE": text})
    result = run(repo, NO_PERSON_DENY=deny)
    assert result.returncode == 0, result.stdout
    assert lines(result) == ["no-person: ok: 3 tracked files scanned; deny list set"]


@pytest.mark.parametrize("path", ["docs/LICENSE", "LICENSE.md", "COPYING", "README.md"])
def test_the_copyright_line_in_any_other_file_fails(tmp_path, path):
    text, number, _line, deny = license_case()
    repo = build(tmp_path, {path: text})
    result = run(repo, NO_PERSON_DENY=deny)
    assert result.returncode == 1
    assert lines(result) == [f"{path}:{number}: deny-list", "no-person: FAIL: 1 finding(s)"]


def test_another_person_word_in_the_license_fails(tmp_path):
    text, _number, line, deny = license_case()
    holder = COPYRIGHT.fullmatch(line).group(1)
    extra = text.count("\n") + 1
    repo = build(tmp_path, {"LICENSE": text + "Maintained by " + holder + "\n" + TERM + "\n"})
    result = run(repo, NO_PERSON_DENY=deny)
    assert result.returncode == 1
    assert lines(result) == [f"LICENSE:{extra}: deny-list", f"LICENSE:{extra + 1}: deny-list",
                             "no-person: FAIL: 2 finding(s)"]


def test_a_second_copy_of_the_copyright_line_fails(tmp_path):
    text, _number, line, deny = license_case()
    extra = text.count("\n") + 1
    repo = build(tmp_path, {"LICENSE": text + line + "\n"})
    result = run(repo, NO_PERSON_DENY=deny)
    assert result.returncode == 1
    assert lines(result) == [f"LICENSE:{extra}: deny-list", "no-person: FAIL: 1 finding(s)"]


NEAR_MISSES = {
    "other-holder": lambda line: line + " and " + TERM.capitalize(),
    "extra-holder-text": lambda line: line + " Jr",
    "upper-c": lambda line: line.replace("(c)", "(C)", 1),
    "lower-copyright": lambda line: line.replace("Copyright", "copyright", 1),
    "double-space": lambda line: line.replace("(c) ", "(c)  ", 1),
    "leading-space": lambda line: " " + line,
    "trailing-space": lambda line: line + " ",
    "carriage-return": lambda line: line + "\r",
    "bullet": lambda line: "- " + line,
    "two-digit-year": lambda line: re.sub(r"\(c\) [0-9]{2}([0-9]{2})", r"(c) \1", line, count=1),
    "no-year": lambda line: re.sub(r"\(c\) [0-9]{4}", "(c)", line, count=1),
}


@pytest.mark.parametrize("name", sorted(NEAR_MISSES))
def test_a_near_miss_of_the_copyright_line_in_the_license_fails(tmp_path, name):
    text, number, line, deny = license_case()
    changed = NEAR_MISSES[name](line)
    assert changed != line
    repo = build(tmp_path, {"LICENSE": text.replace(line + "\n", changed + "\n", 1)})
    result = run(repo, NO_PERSON_DENY=deny)
    assert result.returncode == 1
    assert lines(result) == [f"LICENSE:{number}: deny-list", "no-person: FAIL: 1 finding(s)"]


def test_outside_a_git_work_tree_fails(tmp_path):
    folder = tmp_path / "plain"
    (folder / "scripts").mkdir(parents=True)
    shutil.copyfile(SCRIPT, folder / "scripts" / "check-no-person.py")
    env = {key: value for key, value in os.environ.items()
           if key not in CONFIG_VARS and not key.startswith("GIT_")}
    env["GIT_CEILING_DIRECTORIES"] = str(tmp_path)
    env["NO_PERSON_DENY"] = DENY
    result = subprocess.run([sys.executable, str(folder / "scripts" / "check-no-person.py")],
                            cwd=folder, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout.startswith("no-person: ERROR: git rev-parse failed")


def test_a_script_below_the_top_of_the_work_tree_fails(tmp_path):
    repo = build(tmp_path, {"notes.md": "clean\n"})
    nested = repo / "nested" / "scripts"
    nested.mkdir(parents=True)
    shutil.copyfile(SCRIPT, nested / "check-no-person.py")
    env = {key: value for key, value in os.environ.items()
           if key not in CONFIG_VARS and not key.startswith("GIT_")}
    env["NO_PERSON_DENY"] = DENY
    result = subprocess.run([sys.executable, str(nested / "check-no-person.py")],
                            cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout == (
        f"no-person: ERROR: {(repo / 'nested').resolve()} is not the top of a git work tree\n")


def test_git_missing_from_path_fails(tmp_path):
    repo = build(tmp_path, {"notes.md": "clean\n"})
    empty = tmp_path / "no-git"
    empty.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key not in CONFIG_VARS and not key.startswith("GIT_")}
    env["PATH"] = str(empty)
    env["NO_PERSON_DENY"] = DENY
    result = subprocess.run([sys.executable, str(repo / "scripts" / "check-no-person.py")],
                            cwd=repo, env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert result.stdout.startswith("no-person: ERROR: git is not available")


def test_a_tracked_file_whose_blob_cannot_be_read_fails(tmp_path):
    repo = build(tmp_path, {"notes.md": "clean\n"})
    git(repo, "update-index", "--cacheinfo", "100644," + "1" * 40 + ",notes.md")
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert result.stdout == "no-person: ERROR: git cat-file did not return blob " + "1" * 40 + "\n"


def test_a_submodule_entry_fails_as_unreadable(tmp_path):
    repo = build(tmp_path, {"notes.md": "clean\n"})
    git(repo, "update-index", "--add", "--cacheinfo", "160000," + "2" * 40 + ",vendor/sub")
    result = run(repo, NO_PERSON_DENY=DENY)
    assert result.returncode == 1
    assert "vendor/sub: unreadable (a submodule entry, not a file)" in lines(result)


def test_run_from_a_subfolder_checks_the_repository_that_holds_the_script(tmp_path):
    repo = build(tmp_path, {"lib/a.py": SHAPE_PLANTS["absolute home"] + "\n"})
    env = {key: value for key, value in os.environ.items()
           if key not in CONFIG_VARS and not key.startswith("GIT_")}
    env["NO_PERSON_DENY"] = DENY
    result = subprocess.run([sys.executable, str(repo / "scripts" / "check-no-person.py")],
                            cwd=repo / "lib", env=env, capture_output=True, text=True)
    assert result.returncode == 1
    assert "lib/a.py:1: no-person shape: absolute home" in result.stdout.splitlines()

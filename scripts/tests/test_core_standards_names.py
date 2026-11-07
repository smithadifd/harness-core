"""The core standards name no person, host, home path, private repo or vendor.

Every Markdown file under skills/_lib/standards/ is scanned recursively. Generic
shape checks (home paths, private config paths, decision numbers, private schema
names) and a short list of public chat, secret and model vendors run on every
run. Person, host and private-system words are never spelled in this file: they
come only from the environment variable NO_PERSON_DENY, the same case-insensitive
regular expression scripts/check-no-person.py reads (CI fills it from the
repository secret of that name). When it is unset or empty, the one test that
scans for those words is skipped with a visible reason; the CI no-person job
fails closed without it, so the deny list is still enforced there. Vendor words
and shapes are built from fragments so this file does not carry what it forbids.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
STANDARDS = ROOT / "skills" / "_lib" / "standards"
DENY_VAR = "NO_PERSON_DENY"

# Whole words, matched case-insensitively on word boundaries.
# chat, secret and model vendors: the core says "the override names"
WORDS = [
    "dis" + "cord", "sl" + "ack", "1pass" + "word", "co" + "dex", "fa" + "ble",
    "clau" + "de", "anthro" + "pic", "open" + "ai", "gp" + "t", "gem" + "ini",
    "son" + "net", "op" + "us", "hai" + "ku",
]
WORD_RES = [re.compile(r"\b" + re.escape(w) + r"\b", re.IGNORECASE) for w in WORDS]

# Person-neutral shapes: absolute home paths, `~/` paths into private
# directories, private-system identifiers.
SHAPES = [
    # absolute homes, with or without a trailing slash or file part
    re.compile(r"/Us" + r"ers/[^/\s`'\")]+", re.IGNORECASE),
    re.compile(r"/ho" + r"me/[^/\s`'\")]+", re.IGNORECASE),
    # encoded homes (a home path flattened into a directory name)
    re.compile(r"-Us" + r"ers-[a-z]", re.IGNORECASE),
    # a private-network hostname suffix
    re.compile(r"\b[\w-]+\.ho" + r"me\b", re.IGNORECASE),
    # `~/` paths into private directories
    re.compile(r"~/(?:\.[\w-]+|code|work|Library|Documents|Desktop)\b", re.IGNORECASE),
    # private config: a secrets folder at the home folder's top or nested below it
    re.compile(r"~/(?:[^/\s]+/)*sec" + r"rets\b", re.IGNORECASE),
    re.compile(r"\bADR[ -]?\d{3,4}\b", re.IGNORECASE),
    re.compile(r"\bact\.v\d\b", re.IGNORECASE),
]
FORBIDDEN = WORD_RES + SHAPES


def deny_pattern(environ=os.environ) -> re.Pattern[str] | None:
    """The deny expression from the environment, or None when unset or empty.

    Compiled case-insensitively, as scripts/check-no-person.py compiles it. An
    expression that does not compile, or that matches the empty string, raises
    rather than skip, so a broken deny list never passes as an empty one."""
    value = environ.get(DENY_VAR, "")
    if not value:
        return None
    pattern = re.compile(value, re.IGNORECASE)
    if pattern.search("") is not None:
        raise ValueError(f"{DENY_VAR} matches the empty string")
    return pattern


def offending_lines(name: str, text: str, deny: re.Pattern[str] | None = None) -> list[str]:
    rules = FORBIDDEN if deny is None else [*FORBIDDEN, deny]
    return [
        f"{name}:{n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), 1)
        if any(p.search(line) for p in rules)
    ]


def scan(root: Path, deny: re.Pattern[str] | None = None) -> list[str]:
    return [
        hit
        for f in sorted(root.rglob("*.md"))
        for hit in offending_lines(str(f.relative_to(root)), f.read_text(), deny)
    ]


PLANTS = {
    "home path": "/Us" + "ers/x/notes.md",
    "linux home path": "/ho" + "me/x/notes.md",
    "private config path": "~" + "/.cl" + "aude/secrets/job.env",
    "secrets vendor": "stored in 1Pass" + "word",
    "chat vendor": "posted to Dis" + "cord",
    "model vendor": "reviewed by Co" + "dex",
    "decision number": "see AD" + "R 0020",
    "private schema": "the acme.act" + ".v1 wire",
}


@pytest.mark.parametrize("plant", PLANTS.values(), ids=PLANTS.keys())
@pytest.mark.parametrize("case", [str, str.lower, str.upper])
def test_the_scan_catches_a_plant_in_any_case(plant, case):
    assert offending_lines("x.md", "ok\n" + case(plant) + "\n"), plant


# A made-up deny list standing in for NO_PERSON_DENY: invented names that
# appear in no standard, so the self-tests never spell a real word.
FAKE_DENY = re.compile(r"\b(?:zorblax|quimby-host)\b", re.IGNORECASE)
DENY_PLANTS = {
    "first name": "asked Zorblax",
    "host name": "deployed to quimby-host",
}


@pytest.mark.parametrize("plant", DENY_PLANTS.values(), ids=DENY_PLANTS.keys())
@pytest.mark.parametrize("case", [str, str.lower, str.upper])
def test_the_deny_list_catches_a_plant_in_any_case(plant, case):
    line = "ok\n" + case(plant) + "\n"
    assert offending_lines("x.md", line, FAKE_DENY), plant
    # Only the deny list catches it: no shape or vendor word does.
    assert not offending_lines("x.md", line), plant


def test_deny_pattern_reads_the_environment():
    assert deny_pattern({}) is None
    assert deny_pattern({DENY_VAR: ""}) is None
    pattern = deny_pattern({DENY_VAR: "zorblax|quimby-host"})
    assert pattern is not None and pattern.search("Asked ZORBLAX")
    with pytest.raises(ValueError):
        deny_pattern({DENY_VAR: "zorblax|"})
    with pytest.raises(re.error):
        deny_pattern({DENY_VAR: "(zorblax"})


# Required shapes, independent of SHAPES: one plant each. No plant matches the
# word list, so only a shape can catch it. Deleting a required shape fails
# test_every_required_plant_is_caught_by_a_shape.
REQUIRED = {
    "absolute home, root-terminated": "/Us" + "ers/x",
    "absolute home, trailing slash": "/Us" + "ers/x/",
    "linux home, root-terminated": "/ho" + "me/x",
    "linux home, trailing slash": "/ho" + "me/x/",
    "encoded home": "-Us" + "ers-alice-project",
    "encoded home, root-terminated": "-Us" + "ers-alice",
    "encoded home, slash-suffixed": "-Us" + "ers-alice/memory",
    "private-network hostname": "buildbox.ho" + "me",
    "dot-directory under home": "~" + "/.con" + "fig/notes",
    "work directory under home": "~/wo" + "rk/notes",
    "secrets directory, home root": "~/sec" + "rets/job.env",
    "secrets directory, nested": "~/x/sec" + "rets",
    "decision number": "see AD" + "R 0020",
    "private schema": "the x.act" + ".v1 wire",
}


@pytest.mark.parametrize("plant", REQUIRED.values(), ids=REQUIRED.keys())
def test_every_required_plant_is_caught_by_a_shape(plant):
    assert not any(w.search(plant) for w in WORD_RES), plant
    assert any(s.search(plant) for s in SHAPES), plant
    assert offending_lines("x.md", plant)


@pytest.mark.parametrize("pattern", SHAPES, ids=lambda p: p.pattern)
def test_every_shape_is_the_sole_catcher_of_a_required_plant(pattern):
    sole = [
        p for p in REQUIRED.values()
        if pattern.search(p) and sum(bool(s.search(p)) for s in SHAPES) == 1
    ]
    assert sole, pattern.pattern


@pytest.mark.parametrize("word", WORDS)
def test_every_listed_word_is_caught(word):
    assert offending_lines("x.md", "ok\n" + word.capitalize() + "\n")


def test_the_scan_is_recursive_and_ignores_clean_text(tmp_path):
    (tmp_path / "sub" / "deeper").mkdir(parents=True)
    (tmp_path / "clean.md").write_text("A generic standard.\n")
    (tmp_path / "sub" / "deeper" / "x.md").write_text(PLANTS["home path"])
    (tmp_path / "sub" / "named.md").write_text(DENY_PLANTS["first name"])
    assert [h.split(":")[0] for h in scan(tmp_path)] == ["sub/deeper/x.md"]
    assert [h.split(":")[0] for h in scan(tmp_path, FAKE_DENY)] == [
        "sub/deeper/x.md", "sub/named.md",
    ]


def test_core_standards_name_nobody():
    top = {f.name for f in STANDARDS.glob("*.md")}
    assert top >= {"README.md", "security.md", "trust-ladder.md"}
    found = scan(STANDARDS)
    assert not found, f"private names in core standards: {found}"


def test_core_standards_name_no_denied_word():
    deny = deny_pattern()
    if deny is None:
        pytest.skip(
            f"{DENY_VAR} is unset or empty: the person, host and private-system "
            "words were not checked here; the CI no-person job "
            "(scripts/check-no-person.py) enforces the deny list and fails "
            "without it"
        )
    found = [hit.split(": ", 1)[0] for hit in scan(STANDARDS, deny)]
    assert not found, f"deny-list words in core standards at: {found}"

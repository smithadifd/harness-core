"""The two files the harness plugin ships for the rules folder.

plugins/harness/files/harness.md is the rules default: it binds anyone, so it
names no person, machine or private repository and keeps its core rules.
plugins/harness/files/harness-local.md is the override starter: blank lines
and whole HTML comments only, and nothing in it reads as a grant.

Every private word is pinned here as the sha256 of its lowercased text, never
spelled, so this file passes the no-person check it leans on. Every home path
or host shape is checked through scripts/check-no-person.py's own rules,
imported, never copied.
"""
from __future__ import annotations

import hashlib
import importlib.util
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FILES = ROOT / "plugins" / "harness" / "files"
RULES = FILES / "harness.md"
STARTER = FILES / "harness-local.md"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


setup = _load("harness_setup_for_rules_files", ROOT / "plugins" / "harness" / "setup.py")
no_person = _load("check_no_person_for_rules_files", ROOT / "scripts" / "check-no-person.py")

# The design caps hook-delivered text at 10,000 characters; the rules default
# is a placed file, but it stays well under that cap so it could still travel
# as hook text, with room for a later rule. 9,000 bytes is that ceiling.
RULES_BYTE_CEILING = 9000

# The sha256 of each lowercased word the rules default must not hold: a
# person's names and account names, machine names, private repository names,
# personal tools, services and model names, and private orchestration
# vocabulary.
PRIVATE_WORD_SHA256 = frozenset({
    "d979885447a413abb6d606a5d0f45c3b7809e6fde2c83f0df3426f1fc9bfed97",
    "6627835f988e2c5e50533d491163072d3f4f41f5c8b04630150debb3722ca2dd",
    "95a86be226161e30e5f20e9938d8ed0947429cabacc4f481dded0ad2a45f0d94",
    "c9a77b040b131897c490955283233272f616a6ed51e9f2e3d32cd5d75a8d4588",
    "ce29c26322abc2113a235381220cb924bc6094a013b8633e4fefa648d53ab649",
    "9d1615755a73319e3d2e06775d0e9a2a7e4e63cbb735c62d2833a7a66663853f",
    "fcf56c2c0e488ee1bff443cdcfc0c98e222f37a43756de591e4aaaf667bc0007",
    "dfb316701857783dac69a14d1fe3fd60cff21d56e830baf7f0e3871bd73eee39",
    "f40cc1299629259447e388440ce62167fc6caa3b9dc387640bf2e9621293a73e",
    "24bcedd8ec5a99fe62bcbbd26c1c2842192dea8a71645a5f7308b70e4c60bda0",
    "da415ec45d699eae60a6c3b8e91a8f1a1d185f613dc8c14de6db123e8b9c5b88",
    "548d4fabc56e7b556bbd7d01c3bcb6288fc8de3078dcb38fc3698fb3c26508c9",
    "e1b771ff6831269c53a658057c68d3177b45f47cc5aec706fdd961fbdb2505df",
    "31f61ed3e6e210c5f3f729345968bdbcf267cf0fbfec31eac02ed9966ba1d067",
    "e1ba4807a15d8579f79cfd90a07fc015e6125565c9271eb94aded0b2ebf86163",
    "4e1568bdaf79b70b40e65ed157a3100daf713a9544af4058a1d14b92a5a415cd",
    "8db59feb4d217f26c79d6e76eea6ff80398e8b823e376bb783be870a96cab9e7",
    "693b286515bd1dd00865e7b60e4e53556537bbe4b1cc90ab608d94eb7c56fdc6",
    "57de4cf40144bdf7d00010f2f5557a7d642c2b9705309bfade167dd313e2ca93",
    "5d72436256ada53828b51895a94bb8489e9f1ac4fe937a8024ef1594e7045ff6",
    "e12ce8285efc67c6d93d3a122e2589ed95089bcbb775ba5634d94e2b8385db07",
    "c9ad8f2cc1294afa0ef22fc2c019ff7243cdd272b2147ddca3f34c5036b05768",
    "5a5110ebe1544b31e6d4207a419bc1deb148735f89ae984ff0a2ab809b51f1df",
    "c826e841fb37086fac3114d664c0e277bc498cf35b784f6a1b93d99e6f359ea4",
    "5d2c13d6f9fa421125a9f55e56fd0e4df9319ef30087f8f4675cd6f05135f397",
    "ef7d74898c22a4bf0be437d30987bc945310dcdf1ddb48a6bce085f376b302ae",
    "fe14010b4fe83303852f0467c919ef9a7ca089b91e96e3aad7d426dd87079297",
})

# A word token: letters and digits, joined by single hyphens, dots or
# underscores. Each token is checked whole and part by part, so a private
# name inside a hyphenated or dotted name is still found.
TOKEN = re.compile(r"[a-z0-9]+(?:[-._][a-z0-9]+)*")
PART = re.compile(r"[-._]")

# Words that read as a grant, matched case-insensitively anywhere in the
# starter, comments included: a sample grant must never ship in it.
GRANT_SHAPED = (
    "allow", "bypass", "skip review", "skip the review", "no review",
    "without review", "without a review", "auto-merge", "automerge",
    "dangerously", "skip-permissions", "permission", "may merge",
    "can merge", "pre-approved", "preapproved", "approved:", "grant:",
    "waiver:", "standing grant",
)

# Phrases the rules default must keep, so a rewrite that drops a core rule
# goes red: the fresh-context review in two passes, falsifiable evidence, the
# human merge, CI read per job with every gating job a success and the merge
# pinned to its head, a bug waiver only from the human who merges and never
# from the change's own author, live reach, sandboxed delegated work, no
# bypassed safety check, and the pointer to the override file.
REQUIRED_PHRASES = (
    "fresh-context review",
    "two passes",
    "falsifiable",
    "targeted mutation",
    "A human merges.",
    "a skipped job shows green",
    "--match-head-commit",
    "live reach",
    "Subagents and background jobs run sandboxed by default",
    "Never bypass a safety check",
    "Three review rounds at most",
    "open bug finding",
    "written or explicitly approved by the human who merges",
    "never by the change's author or its builder",
    "Every gating job's conclusion must be success",
    "a skipped, cancelled or neutral gating job does not count as green",
    "harness-local.md",
)


def _rules_text() -> str:
    return RULES.read_bytes().decode("utf-8")


def _private_hits(text: str, pins: frozenset[str] = PRIVATE_WORD_SHA256) -> list[int]:
    """Line numbers of each line holding a token, or part of one, whose
    sha256 is pinned."""
    hits: list[int] = []
    for number, line in enumerate(text.lower().split("\n"), 1):
        for token in TOKEN.findall(line):
            words = {token, *PART.split(token)}
            if any(hashlib.sha256(w.encode("utf-8")).hexdigest() in pins for w in words):
                hits.append(number)
                break
    return hits


# a. The starter is comments only, by setup's own rule, and grants nothing.

def test_the_starter_is_comment_only_by_setups_own_rule():
    text = STARTER.read_bytes().decode("utf-8")
    assert setup.comment_only(text), "a starter line is not blank or one whole HTML comment"
    assert "\r" not in text
    lines = [line for line in text.split("\n") if line.strip(" \t")]
    assert lines, "the starter is empty"
    assert all(setup.html_comment_line(line) for line in lines)
    assert [line for line in lines if line.startswith("#")] == []


def test_setups_check_accepts_the_shipped_starter():
    rules, override = setup.check_proposals()
    assert rules == RULES.read_bytes()
    assert override == STARTER.read_bytes()


def test_the_starter_holds_no_grant_shaped_word():
    lower = STARTER.read_bytes().decode("utf-8").lower()
    found = [word for word in GRANT_SHAPED if word in lower]
    assert found == [], f"grant-shaped words in the starter: {found}"


def test_the_starter_says_it_grants_nothing_and_names_the_default():
    text = STARTER.read_bytes().decode("utf-8")
    assert "grants nothing" in text
    assert "harness.md" in text


# b. The rules default names no person, machine, home path, host or private
#    repository.

def test_the_rules_default_trips_no_shape_rule_of_the_no_person_check():
    data = RULES.read_bytes()
    deny = None
    value = os.environ.get(no_person.DENY_VAR, "")
    if value:
        # The real deny list, when the environment carries it (as CI does),
        # runs too; the hashed words below run either way.
        deny = no_person.compile_deny(value)
    hits = no_person.scan_text("plugins/harness/files/harness.md", data, deny, {}, set())
    assert hits == []


def test_the_rules_default_has_no_home_path_or_home_host():
    text = _rules_text()
    assert "~/" not in text
    for shape in ("/Us" + "ers/", "/ho" + "me/", "." + "ho" + "me"):
        assert shape.lower() not in text.lower(), shape


def test_the_rules_default_names_no_private_word():
    assert _private_hits(_rules_text()) == []


def test_the_private_word_check_finds_a_planted_word():
    # An invented word, pinned the same way, is found whole, inside a
    # hyphenated or dotted name, and in any letter case, so the check above
    # cannot pass vacuously.
    word = "zq" + "plantword"
    pins = PRIVATE_WORD_SHA256 | {hashlib.sha256(word.encode("utf-8")).hexdigest()}
    assert _private_hits(f"one {word} two", pins) == [1]
    assert _private_hits(f"x\nsee the {word.upper()}-tool here", pins) == [2]
    assert _private_hits(f"run tools.{word}.main", pins) == [1]
    assert _private_hits("nothing private here", pins) == []


# c. The rules default stays under its byte ceiling.

def test_the_rules_default_stays_under_its_byte_ceiling():
    size = len(RULES.read_bytes())
    assert size <= RULES_BYTE_CEILING, f"{size} bytes, over the ceiling of {RULES_BYTE_CEILING}"


# d. The rules default still states its core rules.

def test_the_rules_default_states_its_core_rules():
    text = _rules_text()
    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in text]
    assert missing == [], f"the rules default no longer states: {missing}"

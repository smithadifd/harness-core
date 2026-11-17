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

import pytest

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

# Phrases the rules default must keep: the fresh-context review in two
# passes, falsifiable evidence, the human merge, CI read per job with every
# gating job a success and the merge pinned to its head, a bug waiver only
# from the human who merges and never from the change's own author, live
# reach, sandboxed delegated work, no bypassed safety check, and the pointer to
# the override file. A phrase alone does not hold a rule's meaning (a rule can
# be negated and keep every phrase), so these only prove that CORE_RULES below,
# which pins whole lines, still covers each one.
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
    "never by the change's author or its builder.",
    "Every gating job's conclusion must be success",
    "a skipped, cancelled or neutral gating job does not count as green",
    "harness-local.md",
)


# The core rules, pinned as whole lines under their headings, exactly as the
# rules default words them. A phrase check passes when a rule is negated or
# weakened around the phrase ("Every change bound for a merge gets a
# fresh-context review" turned into "No change bound for a merge gets a
# fresh-context review" keeps the phrase). A whole line cannot: a negation, a
# dropped "every", an added "no", a changed "never" or any added clause turns
# the line into one that is not pinned.
#
# Each required line must appear exactly once under its heading, and each core
# section may hold no other line, so a contradicting line added beside a
# required one is caught too. Sections not listed here (command shapes, clean
# up, where people read, tool gotchas) are not core rules and stay free to
# change. A reworded core rule is a deliberate edit to the rules default and
# to this table together.
CORE_RULES: dict[str, tuple[str, ...]] = {
    "": (
        "# Harness rules",
        "Working agreements for every Claude Code session on this machine and every agent it starts. A repository's own `AGENTS.md` or `CLAUDE.md` adds to them, and wins where it is stricter.",
        "**The override file.** `harness-local.md`, in this same rules folder, adds what belongs to one person or one machine: grants, accounts, model choices, a second review family, exceptions to these rules, and stricter local rules. Where it is silent, these rules stand. A second-model-family review applies only when the override names one. With no override, or one that names no second family, the fresh-context review is the whole gate, no code goes to an outside model, and a person merges.",
    ),
    "Review": (
        "- Every change bound for a merge gets a fresh-context review, by a reviewer that did not write it, before the merge. Review in two passes, in order: does the change do what was asked, then does it meet the standards. Report correctness and requirement gaps.",
        "- Three review rounds at most. Round 2 and later review the delta since the last round. At the cap the change goes back to planning unless only nits remain, and nothing merges with unreviewed commits after the last round.",
        "- An open bug finding from any reviewer blocks the merge. Clearing one takes a waiver in the pull request that says why the path cannot be reached, written or explicitly approved by the human who merges (or whoever harness-local.md names as the merge step), never by the change's author or its builder.",
    ),
    "Evidence": (
        "- Evidence must be falsifiable: a command a non-author can rerun, in the environment that gates the change.",
        "- A test counts once it has been seen red under a targeted mutation (production made wrong at the line the test guards) and green after the restore. Running the suite is not enough. Docs-only changes are exempt.",
        "- Reproduce a CI-only failure locally before reasoning about it.",
        "- A handed-down diagnosis is a claim: re-read the file and line before it drives a decision.",
    ),
    "Merge": (
        "- A human merges. What counts as the human step is the override's to say; with none, the session stops with the pull request ready and its review attached, and a person merges.",
        "- CI is green on the final head, on a run created after the pull request was marked ready. Read each job's conclusion (`gh run view --json jobs`): a skipped job shows green. Every gating job's conclusion must be success; a skipped, cancelled or neutral gating job does not count as green.",
        "- Pin the merge to the reviewed head (`gh pr merge --match-head-commit <sha>`), so a commit pushed after the review cannot land with it.",
        "- Ask before anything with live reach, and wait for the answer: deploys, live hooks and wrappers, scheduled jobs, secrets, settings files, and any edit that changes who may approve or merge. This holds even where merging is otherwise granted.",
        "- Never bypass a safety check (`--no-verify`, skipped hooks). Ask before a destructive operation: a force-push, a history rewrite, a hard reset, a dropped table.",
    ),
    "Delegated work": (
        "- Subagents and background jobs run sandboxed by default. A sandbox block is a signal to the session that delegated the work: it widens the sandbox for that one task, with a one-line reason it records, or takes the step itself. It is never a reason to drop the task.",
    ),
}


def _sections(text: str) -> dict[str, list[str]]:
    """The non-blank lines of each "## " section, trailing blanks trimmed. The
    lines before the first "## " heading are the section named "". A heading
    that appears twice has both bodies joined, so a repeated heading cannot
    hide a line."""
    sections: dict[str, list[str]] = {"": []}
    current = ""
    for line in text.split("\n"):
        line = line.rstrip(" \t")
        if line.startswith("## "):
            current = line[3:]
            sections.setdefault(current, [])
        elif line:
            sections[current].append(line)
    return sections


def _brief(line: str) -> str:
    return line if len(line) <= 70 else line[:67] + "..."


def _rule_problems(text: str) -> list[str]:
    """Why the text does not hold the core rules, one message each; an empty
    list when it does."""
    problems: list[str] = []
    sections = _sections(text)
    for heading, required in CORE_RULES.items():
        found = sections.get(heading)
        if found is None:
            problems.append(f"section missing: {heading!r}")
            continue
        for line in required:
            count = found.count(line)
            if count != 1:
                problems.append(f"{heading!r}: required line found {count} times: {_brief(line)}")
        for line in found:
            if line not in required:
                problems.append(f"{heading!r}: line that is not a pinned rule: {_brief(line)}")
    return problems


def _replace(old: str, new: str):
    """A mutation that swaps one exact piece of the rules text, and fails if
    that piece is not there exactly once, so it cannot pass by changing
    nothing."""
    def mutate(text: str) -> str:
        assert text.count(old) == 1, f"mutation anchor is not unique: {old!r}"
        return text.replace(old, new)
    return mutate


# Each mutation negates, weakens or contradicts one core rule. Most keep every
# phrase of REQUIRED_PHRASES, which is the point: the phrase check alone is
# blind to them. The review rule's own negation is the first.
MUTATIONS = {
    "review-negated": _replace("Every change bound for a merge gets", "No change bound for a merge gets"),
    "review-every-dropped": _replace("Every change bound for a merge gets", "A change bound for a merge gets"),
    "review-by-the-author": _replace("by a reviewer that did not write it", "by a reviewer who may have written it"),
    "review-after-the-merge": _replace("before the merge. Review in two passes", "after the merge. Review in two passes"),
    "review-one-pass": _replace("Review in two passes, in order", "Review in one pass"),
    "rounds-at-least": _replace("Three review rounds at most.", "Three review rounds at least."),
    "cap-lets-it-merge": _replace("and nothing merges with unreviewed commits after the last round", "and anything may merge with unreviewed commits after the last round"),
    "bug-does-not-block": _replace("An open bug finding from any reviewer blocks the merge.", "An open bug finding from any reviewer does not block the merge."),
    "waiver-by-the-author": _replace("never by the change's author or its builder.", "or by the change's author or its builder."),
    "waiver-by-anyone": _replace("written or explicitly approved by the human who merges", "written or approved by anyone"),
    "evidence-optional": _replace("Evidence must be falsifiable", "Evidence need not be falsifiable"),
    "suite-is-enough": _replace("Running the suite is not enough.", "Running the suite is enough."),
    "mutation-dropped": _replace(" under a targeted mutation (production made wrong at the line the test guards)", ""),
    "human-never-merges": _replace("- A human merges.", "- A human never merges."),
    "agent-merges": _replace("- A human merges.", "- An agent merges."),
    "skipped-job-is-green": _replace("a skipped, cancelled or neutral gating job does not count as green", "a skipped, cancelled or neutral gating job counts as green"),
    "any-gating-job": _replace("Every gating job's conclusion must be success", "Any gating job's conclusion may be success"),
    "ci-before-ready": _replace("on a run created after the pull request was marked ready", "on any run"),
    "merge-not-pinned": _replace("Pin the merge to the reviewed head", "Do not pin the merge to the reviewed head"),
    "live-reach-no-ask": _replace("Ask before anything with live reach, and wait for the answer", "Do not ask before anything with live reach"),
    "live-reach-grant-wins": _replace(" This holds even where merging is otherwise granted.", ""),
    "safety-check-bypassed": _replace("Never bypass a safety check", "Always bypass a safety check"),
    "sandbox-off": _replace("run sandboxed by default", "run unsandboxed by default"),
    "sandbox-block-drops-task": _replace("It is never a reason to drop the task.", "It is a reason to drop the task."),
    "second-family-always": _replace("applies only when the override names one", "applies always"),
    "outside-model-allowed": _replace("no code goes to an outside model, and a person merges.", "code goes to an outside model, and a person merges."),
    "override-pointer-dropped": _replace("`harness-local.md`, in this same rules folder", "`local.md`, in this same rules folder"),
    "stricter-repo-rules-lose": _replace("and wins where it is stricter", "and loses where it is stricter"),
    "contradicting-line-added": _replace("\n## Evidence", "- Skip the review for a small change.\n\n## Evidence"),
    "required-line-duplicated": _replace(
        "- Reproduce a CI-only failure locally before reasoning about it.\n",
        "- Reproduce a CI-only failure locally before reasoning about it.\n"
        "- Reproduce a CI-only failure locally before reasoning about it.\n",
    ),
    "contradiction-under-a-repeated-heading": lambda text: text + "\n## Review\n- Skip the review for a small change.\n",
    "required-line-removed": _replace("- Reproduce a CI-only failure locally before reasoning about it.\n", ""),
    "section-renamed": _replace("## Merge", "## Merge (optional)"),
}


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


# d. The rules default still states its core rules, with their meaning.

def test_the_rules_default_states_its_core_rules():
    missing = [phrase for phrase in REQUIRED_PHRASES if phrase not in _rules_text()]
    assert missing == [], f"the rules default no longer states: {missing}"


def test_the_rules_default_holds_each_core_rule_line_for_line():
    problems = _rule_problems(_rules_text())
    assert problems == [], "the rules default no longer holds its core rules:\n" + "\n".join(problems)


def test_the_pinned_core_rules_still_cover_every_required_phrase():
    pinned = "\n".join(line for lines in CORE_RULES.values() for line in lines)
    uncovered = [phrase for phrase in REQUIRED_PHRASES if phrase not in pinned]
    assert uncovered == [], f"a required phrase is in no pinned rule line: {uncovered}"


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_a_negated_or_weakened_core_rule_is_found(name):
    text = _rules_text()
    mutated = MUTATIONS[name](text)
    assert mutated != text, f"mutation {name} changed nothing"
    assert _rule_problems(mutated) != [], f"mutation {name} still passes the core-rule check"


def test_the_phrase_check_alone_misses_the_negated_review_rule():
    # The reason the line check exists: this change keeps every required phrase.
    mutated = MUTATIONS["review-negated"](_rules_text())
    assert "No change bound for a merge gets a fresh-context review" in mutated
    assert [phrase for phrase in REQUIRED_PHRASES if phrase not in mutated] == []
    assert _rule_problems(mutated) != []


def test_the_core_rule_check_ignores_trailing_blanks_and_blank_lines():
    text = _rules_text()
    padded = text.replace("\n- ", " \t\n\n- ").replace("\n## ", "\n\n## ")
    assert padded != text
    assert _rule_problems(padded) == []

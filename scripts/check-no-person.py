#!/usr/bin/env python3
"""Fail when a tracked file names a person, a machine, a home path or a private host.

Usage: check-no-person.py

The repository checked is the one that holds this script (the folder above
``scripts/``), whatever the working directory; it must be the top of a git
work tree. There are no options. Every tracked file is read from the git
index (its staged blob, which is the checked-out commit in CI), including
binary files, whose bytes are scanned as text.

Two kinds of rule run over every line of every tracked file:

- Shape rules, held in this file: home folder paths, encoded home paths,
  ``~/`` paths into private folders, a secrets folder under the home folder,
  private-network host names, decision numbers and private schema names (the
  shapes of the toolkit's core standards names test, with the narrowed host
  name shape and the ASCII, case-sensitive word boundaries of its carve), plus
  token shapes and private-key headers. Under ``skills/_lib/ledger/``, the
  public ledger tree vendored unchanged, two shapes do not run in its files:
  "private home folder" and "decision number" (VENDORING.md's carve record
  names the same allowance). The private-network hostname shape has one
  narrower carve: in exactly ``skills/_lib/ledger/verbs/doc.py``, its first
  hit is allowed only when the detected source encoding is exactly UTF-8 or
  UTF-8-SIG, the whole file tokenizes without an error token, the hit text is
  exactly the ``root`` dot ``home`` token pair, and the hit's tokens are NAME
  ``os``, OP ``.``, NAME
  ``open``, OP ``(``, NAME ``root``, OP ``.``, NAME ``home``. The token stream
  and hostname matches use the same decoded text. Any other encoding, a decode
  or tokenize error, a second occurrence, another shape on the line, or any
  other path gives no carve.
- The deny list: person, owner and private-system words come only from the
  environment variable NO_PERSON_DENY, a case-insensitive regular expression
  of terms joined by ``|`` (CI fills it from the repository secret of that
  name).
  No word is spelled in a tracked file. The deny list does not run under
  ``skills/_lib/ledger/``. A match is allowed only when the pair (the file's
  path, the sha256 of the lowercased matched text) is a row of
  ``scripts/no-person-allowance.tsv``; the same text in any other file, or
  any other text, fails. Path names are matched too.
- The license carve: one line is exempt from the deny list, the MIT
  copyright line of the root ``LICENSE`` file. It is that line only when
  the path is exactly ``LICENSE``, the whole line is ``Copyright (c)
  <year> <holder>`` (single spaces, four ASCII digits, no leading or
  trailing space) and the sha256 of the holder text equals
  ``LICENSE_HOLDER_SHA256`` below, so the holder is pinned without being
  spelled here. Only its first occurrence in that file is exempt. The same
  line in any other file, a second copy of it, any other holder or near
  miss of the shape, and every other line of ``LICENSE`` still run the
  deny list; the shape rules run on the exempt line too.

Output, one line per hit, never the matched text and never the variable:

- ``<path>:<line>: no-person shape: <name>`` or ``<path>:<line>: secret
  shape: <name>`` for a shape rule;
- ``<path>:<line>: deny-list`` for a deny-list match the allowance does not
  cover;
- ``<path>: path name: no-person shape: <name>`` for a shape in a path name;
- ``tracked path #<n>: deny-list (path name)`` for a deny-list match in a
  path name, where <n> counts from 1 in ``git ls-files`` order (the path
  itself would print the word);
- ``scripts/no-person-allowance.tsv:<line>: stale allowance`` for an
  allowance row whose pair matched nothing in this scan, so the table cannot
  outlive what it allows;
- ``<path>: unreadable (<reason>)`` for a tracked entry that cannot be read
  as a file (a submodule, a failed read);
- ``no-person: <reason>`` for a configuration failure.

The allowance file's grammar: UTF-8 text, LF line endings, ending in a
newline. A line starting with ``#`` is a comment; every other line is
``<path><TAB><64 lowercase hex><TAB><reason>``, the reason non-empty and
without a tab, the path relative to the repository root with no empty,
``.`` or ``..`` component, no pair listed twice. Any other line is refused
by its line number.

NO_PERSON_DENY unset or empty: the shape rules still run, one WARN line says
the deny list did not run, and the exit follows the shape rules; in CI
(``CI=true`` or ``GITHUB_ACTIONS=true``) it is a failure instead. An
expression that does not compile, or that matches the empty string, is a
failure everywhere.

Exit 0 with one ``no-person: ok`` line when no rule fires; exit 1 on any hit,
a stale allowance row, an unreadable file, a configuration failure or a git
failure. Nothing is ever skipped silently.
"""
from __future__ import annotations

import hashlib
import io
import os
import re
import subprocess
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "no-person:"
DENY_VAR = "NO_PERSON_DENY"
ALLOWANCE = "scripts/no-person-allowance.tsv"
LEDGER_TREE = "skills/_lib/ledger/"
# Two shapes do not run in a file under the public ledger tree: its own
# home-folder paths and decision numbers. Every other shape still does.
LEDGER_SHAPES = frozenset({"private home folder", "decision number"})
LEDGER_DOC_PATH = "skills/_lib/ledger/verbs/doc.py"
LEDGER_DOC_HOME_TOKENS = (
    (tokenize.NAME, "os"), (tokenize.OP, "."), (tokenize.NAME, "open"),
    (tokenize.OP, "("), (tokenize.NAME, "root"), (tokenize.OP, "."),
    (tokenize.NAME, "home"),
)
LEDGER_DOC_HOME_MATCH = "root" + ".home"
# The license carve (see the docstring): exactly this path, exactly this line
# shape, and the holder text pinned by its sha256.
LICENSE_PATH = "LICENSE"
LICENSE_LINE = re.compile(r"Copyright \(c\) [0-9]{4} (\S(?:.*\S)?)")
LICENSE_HOLDER_SHA256 = "c157acfd0136907586269d8508b72f480981d020bf98eda12b47a8a0a82f0093"
_HEX64 = re.compile(r"[0-9a-f]{64}")
_GIT_LOCATION_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})

# Shapes are built from fragments so this file does not carry what it forbids.
# A private-network host name: the whole dotted name, one or more labels of
# letters, digits and hyphens each followed by a dot, then the suffix, not
# followed by a letter, a digit or "(" (a call such as the standard library's
# home-folder lookup). A name starting with the lowercase receiver "self" is
# an attribute access, never a host. The lookbehind and lookahead classes and
# the receiver match case-sensitively (the scoped "(?-i:...)" groups); the
# labels and the suffix match in any case.
HOME_HOST = re.compile(
    r"(?<!(?-i:[A-Za-z0-9_.-]))(?!(?-i:self\.))(?:[A-Za-z0-9-]+\.)+ho" + r"me"
    + r"(?!(?-i:[A-Za-z0-9(]))",
    re.IGNORECASE,
)
# ASCII-only word boundaries that match case-sensitively even inside an
# ignore-case pattern: ignore-case folds four non-ASCII letters into the ASCII
# class, and one of them beside a word would otherwise hide it.
WORD_START = r"(?<!(?-i:[A-Za-z0-9_]))"
WORD_END = r"(?!(?-i:[A-Za-z0-9_]))"
SHAPES = [
    ("absolute home", re.compile(r"/Us" + r"ers/[^/\s`'\")]+", re.IGNORECASE)),
    ("linux home", re.compile(r"/ho" + r"me/[^/\s`'\")]+", re.IGNORECASE)),
    ("encoded home", re.compile(r"-Us" + r"ers-[a-z]", re.IGNORECASE)),
    ("private-network hostname", HOME_HOST),
    ("private home folder",
     re.compile(r"~/(?:\.[\w-]+|co" + r"de|wo" + r"rk|Library|Documents|Desktop)" + WORD_END,
                re.IGNORECASE)),
    ("private config path", re.compile(r"~/(?:[^/\s]+/)*sec" + r"rets" + WORD_END,
                                       re.IGNORECASE)),
    ("decision number", re.compile(WORD_START + r"AD" + r"R[ -]?\d{3,4}" + WORD_END,
                                   re.IGNORECASE)),
    ("private schema", re.compile(WORD_START + r"act\." + r"v\d" + WORD_END, re.IGNORECASE)),
]
SECRET_SHAPES = [
    ("token", re.compile(r"gh" + r"p_[A-Za-z0-9]{20}")),
    ("token", re.compile(r"s" + r"k-[A-Za-z0-9]{20}")),
    ("token", re.compile(r"xo" + r"x[bp]-")),
    ("token", re.compile(r"AK" + r"IA[0-9A-Z]{16}")),
    ("private key", re.compile(r"BEGIN [A-Z ]*PRI" + r"VATE KEY")),
]


class CheckError(RuntimeError):
    """A git failure or an unreadable input (exit 1, as every failure)."""


def _git_env() -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if key not in _GIT_LOCATION_ENV
        and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))
    }
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    return env


def git(args: list[str], root: Path, data: bytes | None = None) -> bytes:
    try:
        done = subprocess.run(["git", *args], cwd=root, env=_git_env(), input=data,
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


def tracked(root: Path) -> list[tuple[str, str, str]]:
    """(path, mode, blob id) for every index entry, in ``git ls-files`` order."""
    out = git(["ls-files", "--stage", "-z"], root)
    entries: list[tuple[str, str, str]] = []
    for record in out.split(b"\0"):
        if not record:
            continue
        meta, _, name = record.partition(b"\t")
        mode, blob, _stage = meta.decode("ascii").split(" ")
        entries.append((name.decode("utf-8", "surrogateescape"), mode, blob))
    return entries


def read_blobs(root: Path, blobs: list[str]) -> dict[str, bytes]:
    """The bytes of each blob id, through one ``git cat-file --batch``."""
    unique = list(dict.fromkeys(blobs))
    if not unique:
        return {}
    out = git(["cat-file", "--batch"], root, ("\n".join(unique) + "\n").encode("ascii"))
    found: dict[str, bytes] = {}
    position = 0
    for blob in unique:
        end = out.index(b"\n", position)
        header = out[position:end].decode("ascii", "replace").split(" ")
        position = end + 1
        if len(header) != 3 or header[0] != blob or header[1] != "blob":
            raise CheckError(f"git cat-file did not return blob {blob}")
        size = int(header[2])
        found[blob] = out[position:position + size]
        position += size + 1
    return found


def compile_deny(value: str) -> re.Pattern[str]:
    """The deny expression, compiled; the message never carries the value."""
    try:
        pattern = re.compile(value, re.IGNORECASE)
    except (re.error, RecursionError, OverflowError, ValueError):
        raise CheckError(f"{DENY_VAR} is not a valid regular expression") from None
    if pattern.search("") is not None:
        raise CheckError(f"{DENY_VAR} matches the empty string")
    return pattern


def digest(matched: str) -> str:
    return hashlib.sha256(matched.lower().encode("utf-8", "surrogateescape")).hexdigest()


def _path_problem(path: str) -> str | None:
    if path.startswith("/"):
        return "has a leading slash"
    for part in path.split("/"):
        if part == "":
            return "has an empty component"
        if part in {".", ".."}:
            return f"has a {part!r} component"
    return None


def parse_allowance(text: str) -> tuple[dict[tuple[str, str], int], list[str]]:
    """((path, hash) -> line number, problems) from the allowance file."""
    if "\r" in text:
        line = text[: text.index("\r")].count("\n") + 1
        return {}, [f"{ALLOWANCE}:{line}: carriage return (CRLF line endings are refused)"]
    if not text.endswith("\n"):
        return {}, [f"{ALLOWANCE}: does not end with a newline"]
    pairs: dict[tuple[str, str], int] = {}
    problems: list[str] = []
    for number, line in enumerate(text[:-1].split("\n"), 1):
        if line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) != 3:
            problems.append(f"{ALLOWANCE}:{number}: not a row of <path> TAB <sha256> TAB <reason>")
            continue
        path, value, reason = fields
        problem = _path_problem(path)
        if problem is not None:
            problems.append(f"{ALLOWANCE}:{number}: the path {problem}")
        elif not _HEX64.fullmatch(value):
            problems.append(f"{ALLOWANCE}:{number}: the hash is not 64 lowercase hex")
        elif not reason.strip():
            problems.append(f"{ALLOWANCE}:{number}: the reason is empty")
        elif (path, value) in pairs:
            problems.append(f"{ALLOWANCE}:{number}: duplicate pair of line {pairs[(path, value)]}")
        else:
            pairs[(path, value)] = number
    return pairs, problems


def license_copyright_line(path: str, line: str) -> bool:
    """True only for the root LICENSE's MIT copyright line naming the pinned holder."""
    if path != LICENSE_PATH:
        return False
    match = LICENSE_LINE.fullmatch(line)
    if match is None:
        return False
    holder = match.group(1).encode("utf-8", "surrogateescape")
    return hashlib.sha256(holder).hexdigest() == LICENSE_HOLDER_SHA256


def ledger_doc_home_spans(
        path: str, data: bytes) -> tuple[str, frozenset[tuple[int, int, int]]]:
    """Decoded scan text and token spans eligible for the document-root carve."""
    empty: frozenset[tuple[int, int, int]] = frozenset()
    if path != LEDGER_DOC_PATH:
        return data.decode("utf-8", errors="surrogateescape"), empty
    try:
        encoding, _consumed = tokenize.detect_encoding(io.BytesIO(data).readline)
        if encoding.lower() not in {"utf-8", "utf-8-sig"}:
            return data.decode("utf-8", errors="surrogateescape"), empty
        text = data.decode(encoding)
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (UnicodeDecodeError, tokenize.TokenError, SyntaxError, IndentationError):
        return data.decode("utf-8", errors="surrogateescape"), empty
    if any(token.type == tokenize.ERRORTOKEN or
           (token.type == tokenize.OP and token.string not in tokenize.EXACT_TOKEN_TYPES)
           for token in tokens):
        # Python 3.14 reports some characters as an unknown OP that earlier
        # tokenizers report as ERRORTOKEN. Both forms fail closed.
        return text, empty

    width = len(LEDGER_DOC_HOME_TOKENS)
    spans: set[tuple[int, int, int]] = set()
    for start in range(len(tokens) - width + 1):
        window = tokens[start:start + width]
        if tuple((token.type, token.string) for token in window) != LEDGER_DOC_HOME_TOKENS:
            continue
        row = window[0].start[0]
        if any(token.start[0] != row or token.end[0] != row for token in window):
            continue
        spans.add((row, window[4].start[1], window[6].end[1]))
    return text, frozenset(spans)


def scan_text(path: str, data: bytes, deny: re.Pattern[str] | None,
              allowed: dict[tuple[str, str], int], used: set[tuple[str, str]]) -> list[str]:
    """One ``<path>:<line>: <rule>`` per rule a line of ``data`` breaks.

    ``data`` normally decodes as UTF-8 with invalid bytes kept as surrogate
    escapes. The document-root carve instead uses its accepted UTF-8 or
    UTF-8-SIG decode for both token and shape positions."""
    text, doc_home_spans = ledger_doc_home_spans(path, data)
    in_ledger = path.startswith(LEDGER_TREE)
    doc_home_used = False
    hits: list[str] = []
    carved = False
    for number, line in enumerate(text.split("\n"), 1):
        rules: list[str] = []
        # The license carve applies once per file, at its first occurrence.
        carve = not carved and license_copyright_line(path, line)
        carved = carved or carve
        for name, pattern in SHAPES:
            if in_ledger and name in LEDGER_SHAPES:
                continue
            if name == "private-network hostname":
                uncarved = False
                for match in pattern.finditer(line):
                    span = (number, match.start(), match.end())
                    if (not doc_home_used and match.group(0) == LEDGER_DOC_HOME_MATCH and
                            span in doc_home_spans):
                        doc_home_used = True
                    else:
                        uncarved = True
                if uncarved:
                    rules.append(f"no-person shape: {name}")
            elif pattern.search(line):
                rules.append(f"no-person shape: {name}")
        for name, pattern in SECRET_SHAPES:
            if pattern.search(line):
                rules.append(f"secret shape: {name}")
        if deny is not None and not in_ledger and not carve:
            for match in deny.finditer(line):
                if not match.group(0):
                    continue
                pair = (path, digest(match.group(0)))
                if pair in allowed:
                    used.add(pair)
                else:
                    rules.append("deny-list")
        for rule in dict.fromkeys(rules):
            hits.append(f"{path}:{number}: {rule}")
    return hits


def check(root: Path, environ: dict[str, str]) -> tuple[list[str], list[str], int]:
    """(findings, warnings, files scanned)."""
    findings: list[str] = []
    warnings: list[str] = []
    in_ci = environ.get("CI") == "true" or environ.get("GITHUB_ACTIONS") == "true"
    value = environ.get(DENY_VAR, "")
    deny: re.Pattern[str] | None = None
    if not value:
        if in_ci:
            findings.append(f"{PREFIX} {DENY_VAR} is unset or empty in CI; the deny list "
                            "cannot be skipped there")
        else:
            warnings.append(f"{PREFIX} WARN: {DENY_VAR} IS UNSET: the deny list did not run; "
                            "only the shape rules were checked")
    else:
        try:
            deny = compile_deny(value)
        except CheckError as exc:
            findings.append(f"{PREFIX} {exc}")
    require_top(root)
    entries = tracked(root)
    by_path = {path: (mode, blob) for path, mode, blob in entries}
    allowed: dict[tuple[str, str], int] = {}
    allowance = by_path.get(ALLOWANCE)
    if allowance is None or allowance[0] not in {"100644", "100755"}:
        findings.append(f"{ALLOWANCE}: unreadable (not tracked as a regular file)")
    readable = [blob for _path, mode, blob in entries if mode != "160000"]
    blobs = read_blobs(root, readable)
    if allowance is not None and allowance[0] in {"100644", "100755"}:
        try:
            allowed, problems = parse_allowance(blobs[allowance[1]].decode("utf-8"))
        except UnicodeDecodeError:
            allowed, problems = {}, [f"{ALLOWANCE}: unreadable (not UTF-8 text)"]
        findings.extend(problems)
    used: set[tuple[str, str]] = set()
    for number, (path, mode, blob) in enumerate(entries, 1):
        for name, pattern in SHAPES:
            if pattern.search(path):
                findings.append(f"{path}: path name: no-person shape: {name}")
        if deny is not None and not path.startswith(LEDGER_TREE) and deny.search(path):
            findings.append(f"tracked path #{number}: deny-list (path name)")
        if mode == "160000":
            findings.append(f"{path}: unreadable (a submodule entry, not a file)")
            continue
        findings.extend(scan_text(path, blobs[blob], deny, allowed, used))
    if deny is not None:
        for pair, line in sorted(allowed.items(), key=lambda item: item[1]):
            if pair not in used:
                findings.append(f"{ALLOWANCE}:{line}: stale allowance (the pair matched "
                                "nothing in this scan)")
    return findings, warnings, len(entries)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if args:
        print(f"{PREFIX} ERROR: takes no arguments", file=sys.stderr)
        return 1
    try:
        findings, warnings, count = check(ROOT, dict(os.environ))
    except CheckError as exc:
        print(f"{PREFIX} ERROR: {exc}")
        return 1
    for warning in warnings:
        print(warning)
    for finding in findings:
        print(finding)
    if findings:
        print(f"{PREFIX} FAIL: {len(findings)} finding(s)")
        return 1
    state = "unset (WARN above)" if warnings else "set"
    print(f"{PREFIX} ok: {count} tracked files scanned; deny list {state}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

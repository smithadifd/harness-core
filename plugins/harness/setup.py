#!/usr/bin/env python3
"""Plan, apply, remove, doctor and ledger-init: the default-tier setup.

The setup skill drives this script. Plan writes nothing and takes no lock.
Apply places the rules default, places the override starter when the person
has none, and adds each approved settings item that is absent: a missing key,
or a deny rule appended after the existing ones. It never changes, removes or
reorders an existing entry, and nothing it proposes grants anything. Remove
takes out exactly what a complete setup record lists, and keeps and names
anything the person has changed since. Ledger-init makes the first ledger
write, which creates the database file when it is absent. Doctor reads and
reports: it writes nothing and takes no lock.

Instance-plan and instance-create are the optional instance folder: a new
folder of the person's choosing, made a local git repository, holding the
template's markdown. It is the person's own: the setup record never lists it,
setup holds no lock for it, and doctor and remove never look at it. A create
that fails rolls back exactly what it made.

The promise, in full, is in KNOWN-LIMITS.md ("Harness setup"). In short:

- a run that completes leaves exactly the documented result;
- an apply that fails in-process rolls back all of its own changes from
  memory, and never deletes or overwrites bytes it did not write;
- an apply that hard-stops (a signal, a crash, power loss) leaves settings.json
  whole and a 0600 backup of it, and the next run refuses by name: setup never
  acts on an unfinished record, and recovery after a hard stop is by hand;
- a remove that stops, for any reason, keeps the complete record, and running
  remove again finishes the job.

Setup installs no signal handler: Ctrl-C, SIGTERM and SIGHUP are hard stops.
Apply and remove each hold one exclusive, non-blocking flock on
.claude/harness-setup.lock, a file that is never unlinked, and the
`claude plugin configure` child that apply starts holds it with them. Below the
home folder, every path is opened relative to its parent's descriptor without
following a symlink.
"""
from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import os
import secrets
import shlex
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable


PLUGIN_ROOT = Path(__file__).resolve().parent
FILES_DIR = PLUGIN_ROOT / "files"
RULES_SOURCE = FILES_DIR / "harness.md"
OVERRIDE_SOURCE = FILES_DIR / "harness-local.md"
INSTANCE_SOURCE = FILES_DIR / "instance"
# Every file of the instance template, as a path inside the instance folder.
# A test holds this list to the files that are in the template folder.
INSTANCE_FILES = (
    ".gitignore",
    "README.md",
    "decisions/README.md",
    "roadmaps/README.md",
    "standards/README.md",
)

CLAUDE_REL = ".claude"
RULES_DIR_REL = ".claude/rules"
RULES_REL = ".claude/rules/harness.md"
OVERRIDE_REL = ".claude/rules/harness-local.md"
SETTINGS_REL = ".claude/settings.json"
LOCK_REL = ".claude/harness-setup.lock"
RECORD_DIRS = (
    ".claude/plugins",
    ".claude/plugins/harness-core",
    ".claude/plugins/harness-core/harness",
)
SETUP_DIR_REL = RECORD_DIRS[-1]
RECORD_REL = ".claude/plugins/harness-core/harness/setup-record.jsonl"
BACKUP_REL = ".claude/plugins/harness-core/harness/settings-backup.json"
CREATABLE_DIRS = (RULES_DIR_REL,) + RECORD_DIRS
RECORD_KIND = "harness-setup"
RECORD_VERSION = 1

LEDGER_PLUGIN = "ledger-hooks@harness-core"
DEFAULT_HARNESS = "claude-code"

# The option names the ledger-hooks plugin declares (its userConfig). Claude
# Code refuses a name the plugin does not declare.
OPTION_DB = "ledger_db"
OPTION_OPERATOR = "ledger_operator"
OPTION_HARNESS = "ledger_harness"
OPTION_KEYS = (OPTION_DB, OPTION_OPERATOR, OPTION_HARNESS)

DB_REL = ".local/share/ledger/ledger.db"

# The first ledger write: one closed topic, keyed by its source so a repeat
# is the same item. It exists to create the database file.
FIRST_WRITE = ("new", "--type", "topic", "--title", "Harness setup",
               "--source", "harness-setup", "--status", "closed")

# The interpreters the ledger command itself looks for, in its order.
PYTHONS = ("python3", "python3.14", "python3.13", "python3.12", "python3.11", "python")

# Deny rules for reading secret files: env files and key material. Nothing
# here is an allow rule, an extra directory, or an auto-allow key.
DENY_RULES: tuple[str, ...] = (
    "Read(.env)",
    "Read(.env.*)",
    "Read(**/.env)",
    "Read(**/.env.*)",
    "Read(**/*.pem)",
    "Read(**/*.key)",
)

SANDBOX_VALUE: dict[str, Any] = {"enabled": True}

# The one sandbox proposal setup may make, as canonical JSON (sorted keys, no
# spaces). The proposal gate compares the proposal with this text exactly, so
# any other value (an extra key, a nested setting, `1` for `true`) refuses.
SAFE_SANDBOX = '{"enabled":true}'

GRANTING_KEYS = frozenset({
    "allow",
    "additionalDirectories",
    "extraDirectories",
    "autoAllow",
    "autoAllowBashIfSandboxed",
})

PREFIX = "setup:"
HEX = frozenset("0123456789abcdef")

# Variables that would make git act on another repository than the instance
# folder; they are dropped from the environment of the one git command.
GIT_LOCATION_ENV = frozenset({
    "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_COMMON_DIR", "GIT_NAMESPACE",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
})

DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
FOLLOW_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC
READ_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
TEMP_FLAGS = os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
LOCK_FLAGS = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
APPEND_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC


class SetupError(RuntimeError):
    """A named refusal of plan, apply or remove (exit 1)."""


# ---------------------------------------------------------------------------
# Small helpers


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_source(path: Path) -> bytes:
    if not path.is_file():
        raise SetupError(f"{path.name} is missing from the plugin files folder")
    return path.read_bytes()


def pairs(items):
    seen = {}
    for key, value in items:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def parse_json_object(raw: bytes, shown: str) -> dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SetupError(f"{shown} is not valid UTF-8 ({exc})") from exc
    try:
        data = json.loads(text, object_pairs_hook=pairs)
    except ValueError as exc:
        raise SetupError(f"{shown} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{shown} is not a JSON object")
    return data


def detect_indent(text: str) -> int | str:
    for line in text.splitlines():
        if not line:
            continue
        stripped = line.lstrip(" \t")
        if stripped == line:
            continue
        prefix = line[: len(line) - len(stripped)]
        if prefix.startswith("\t"):
            return "\t"
        return len(prefix)
    return 2


def dump_settings(data: dict[str, Any], indent: int | str) -> bytes:
    return (json.dumps(data, indent=indent, ensure_ascii=False) + "\n").encode("utf-8")


def json_line(data: dict[str, Any]) -> bytes:
    return (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def same(a: Any, b: Any) -> bool:
    """Type-strict JSON equality, on canonical JSON text: `1`, `1.0` and
    `true` all differ, where Python's `==` holds `1 == 1.0 == True`. Every
    compare that decides whether setup removes or keeps something it
    recorded uses this."""
    return canonical(a) == canonical(b)


def write_all(fd: int, data: bytes) -> None:
    """Write every byte of data, or raise. A write that reports 0 bytes, or
    fails, is an error: a short write never passes as a success."""
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if written <= 0:
            raise OSError(errno.EIO, "os.write wrote nothing (disk full?)")
        view = view[written:]


def sync(fd: int) -> None:
    """fsync, then F_FULLFSYNC where the platform defines it."""
    os.fsync(fd)
    full = getattr(fcntl, "F_FULLFSYNC", None)
    if full is not None:
        fcntl.fcntl(fd, full)


def temp_name(base: str) -> str:
    return f".{base}.harness-{secrets.token_hex(8)}.tmp"


def one_line(text: str) -> str:
    return " / ".join(part.strip() for part in str(text).splitlines() if part.strip())


# ---------------------------------------------------------------------------
# Proposals and plan


def database_path(home: Path) -> Path:
    return home.joinpath(*DB_REL.split("/"))


def ledger_values(home: Path, operator: str, harness: str | None) -> dict[str, str]:
    values = {
        OPTION_DB: str(database_path(home)),
        OPTION_OPERATOR: operator,
    }
    if harness:
        values[OPTION_HARNESS] = harness
    return values


def first_write_command(home: Path) -> list[str]:
    return ["ledger", "--db", str(database_path(home)), *FIRST_WRITE]


def optional_items() -> list[tuple[str, str]]:
    """(id, exact text to be added) for each optional settings proposal."""
    items = [("sandbox", json.dumps({"sandbox": dict(SANDBOX_VALUE)}, indent=2))]
    for rule in DENY_RULES:
        items.append((f"deny:{rule}", rule))
    return items


def proposal_is_granting(kind: str, name: str, value: Any) -> bool:
    if kind == "deny":
        text = str(value)
        return text.startswith("Allow") or text.lower().startswith("allow(")
    if kind == "key":
        return name in GRANTING_KEYS
    return True


def granting_proposals() -> list[str]:
    found = []
    if canonical(SANDBOX_VALUE) != SAFE_SANDBOX:
        found.append("sandbox")
    for rule in DENY_RULES:
        if proposal_is_granting("deny", "deny", rule):
            found.append(f"deny:{rule}")
    return found


def granting_in_settings(settings: dict[str, Any]) -> list[str]:
    found: list[str] = []

    def walk(obj: Any, prefix: str) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                path = f"{prefix}.{key}" if prefix else str(key)
                if key in GRANTING_KEYS:
                    found.append(path)
                walk(value, path)
        elif isinstance(obj, list):
            for index, item in enumerate(obj):
                walk(item, f"{prefix}[{index}]")

    walk(settings, "")
    return found


def html_comment_line(line: str) -> bool:
    """True when line is one whole HTML comment, `<!--` at its first column
    and `-->` at its end, closed nowhere earlier: its text holds no `--` and
    does not start with `>` or `->` (which would close it at once)."""
    if len(line) < 7 or not line.startswith("<!--") or not line.endswith("-->"):
        return False
    inner = line[4:-3]
    return not ("--" in inner or "\r" in inner
                or inner.startswith(">") or inner.startswith("->"))


def comment_only(text: str) -> bool:
    """True when every line is blank (spaces and tabs only) or one whole
    HTML comment: content the rules loader cannot read as an instruction.
    A `#` line is a Markdown heading, which Claude Code reads as an
    instruction, so it is not a comment here."""
    for line in text.split("\n"):
        if line.endswith("\r"):
            line = line[:-1]
        if line.strip(" \t") == "":
            continue
        if not html_comment_line(line):
            return False
    return True


def check_proposals() -> tuple[bytes, bytes]:
    """The fixed proposal table grants nothing and the override starter is
    HTML comments only. Returns the rules and override bytes."""
    grants = granting_proposals()
    if grants:
        raise SetupError("proposals grant: " + ", ".join(grants))
    override = load_source(OVERRIDE_SOURCE)
    if not comment_only(override.decode("utf-8")):
        raise SetupError("override starter has a line that is not a comment")
    return load_source(RULES_SOURCE), override


def approved_ids(approve: list[str], approve_all: bool) -> set[str]:
    known = {ident for ident, _text in optional_items()}
    found: set[str] = set()
    for ident in approve:
        if ident not in known:
            raise SetupError(f"unknown approval {ident!r}")
        found.add(ident)
    if approve_all:
        return known
    return found


def format_plan(home: Path, operator: str, harness: str | None) -> str:
    rules, override = check_proposals()
    values = ledger_values(home, operator, harness)
    lines = [
        f"FILE {RULES_REL}",
        "----",
        rules.decode("utf-8").rstrip("\n"),
        "----",
        f"FILE {OVERRIDE_REL}",
        "----",
        override.decode("utf-8").rstrip("\n"),
        "----",
        f"FILE {RECORD_REL}",
        "record of this apply",
        f"FILE {LOCK_REL}",
        "the lock file, created empty; uninstall keeps it",
        f"FILE {DB_REL}",
        "the ledger database, made by the first ledger write below only when it is "
        "absent; uninstall keeps it",
        "LEDGER",
        f"claude plugin configure {LEDGER_PLUGIN} --values-stdin",
        "----",
        json.dumps(values, indent=2),
        "----",
        "FIRST LEDGER WRITE",
        "----",
        " ".join(shlex.quote(part) for part in first_write_command(home)),
        "----",
        f"SETTINGS {SETTINGS_REL}",
        "changed only by an optional item below that you approve; Claude Code also "
        "stores the ledger options in it",
    ]
    for ident, text in optional_items():
        lines.extend([f"OPTIONAL {ident}", "----", text, "----"])
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# The home tree: every folder below the home is opened relative to its
# parent's descriptor, and nothing below the home follows a symlink.


class Tree:
    def __init__(self, home: Path) -> None:
        self.root = home
        self.fds: dict[str, int] = {}
        self.temps: set[str] = set()
        self.fds[""] = os.open(str(home), os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)

    def close(self) -> None:
        for fd in self.fds.values():
            try:
                os.close(fd)
            except OSError:
                pass
        self.fds.clear()

    def show(self, rel: str) -> str:
        return str(self.root.joinpath(*rel.split("/"))) if rel else str(self.root)

    def refuse_entry(self, parent: int, name: str, rel: str, exc: OSError) -> None:
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except OSError:
            raise exc
        if stat.S_ISLNK(info.st_mode):
            raise SetupError(f"{self.show(rel)} is a symlink") from exc
        raise exc

    def dir(self, rel: str, *, create: bool = False,
            made: Callable[[str], None] | None = None) -> int | None:
        """The descriptor of the folder rel, or None when it is absent and
        create is false. A folder setup makes is reported through made the
        moment it exists, and its parent is fsynced."""
        if rel in self.fds:
            return self.fds[rel]
        parent_rel, _, name = rel.rpartition("/")
        parent = self.dir(parent_rel, create=create, made=made)
        if parent is None:
            return None
        try:
            fd = os.open(name, DIR_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            if not create:
                return None
            os.mkdir(name, 0o700, dir_fd=parent)
            if made is not None:
                made(rel)
            sync(parent)
            try:
                fd = os.open(name, DIR_FLAGS, dir_fd=parent)
            except OSError as exc:
                self.refuse_entry(parent, name, rel, exc)
                raise
        except OSError as exc:
            self.refuse_entry(parent, name, rel, exc)
            raise
        self.fds[rel] = fd
        return fd

    def follow_dir(self, rel: str) -> int:
        """The descriptor of the ledger database's folder rel. This is the one
        folder setup opens by following symlinks, at rel or above it: the
        database path is the person's, as the ledger command treats it. A
        folder that is missing is made with ordinary mkdir, one level at a
        time; the apply and remove walk above never comes here."""
        key = f"follow:{rel}"
        if key in self.fds:
            return self.fds[key]
        path = self.show(rel)
        try:
            try:
                fd = os.open(path, FOLLOW_DIR_FLAGS)
            except FileNotFoundError:
                self.make_followed(rel)
                fd = os.open(path, FOLLOW_DIR_FLAGS)
        except OSError as exc:
            raise SetupError(
                f"cannot open the ledger database folder {path}: {one_line(exc)}") from exc
        self.fds[key] = fd
        return fd

    def make_followed(self, rel: str) -> None:
        """Make each missing level of rel below the home, each 0700. A level
        that exists, as a folder or as a symlink, is left as it is."""
        walked = ""
        for name in rel.split("/"):
            walked = f"{walked}/{name}" if walked else name
            try:
                os.mkdir(self.show(walked), 0o700)
            except FileExistsError:
                continue
            fd = os.open(self.show(walked.rpartition("/")[0]), FOLLOW_DIR_FLAGS)
            try:
                sync(fd)
            finally:
                os.close(fd)

    def parent_of(self, rel: str) -> tuple[int | None, str]:
        parent_rel, _, name = rel.rpartition("/")
        return self.dir(parent_rel), name

    def entry(self, rel: str) -> os.stat_result | None:
        """lstat of rel, or None when it or its folder is absent. A symlink
        refuses by name."""
        parent, name = self.parent_of(rel)
        if parent is None:
            return None
        try:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            raise SetupError(f"{self.show(rel)} is a symlink")
        return info

    def read(self, rel: str) -> tuple[bytes, int] | None:
        """(bytes, mode) of the regular file rel, or None when absent."""
        parent, name = self.parent_of(rel)
        if parent is None:
            return None
        try:
            fd = os.open(name, READ_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            return None
        except OSError as exc:
            self.refuse_entry(parent, name, rel, exc)
            raise
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise SetupError(f"{self.show(rel)} is not a regular file")
            chunks = []
            while True:
                chunk = os.read(fd, 1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks), stat.S_IMODE(info.st_mode)
        finally:
            os.close(fd)

    def make_temp(self, parent: int, parent_rel: str, base: str) -> tuple[str, int]:
        """A new 0600 temp file beside base, created with O_EXCL and never
        through a symlink."""
        for _attempt in range(100):
            name = temp_name(base)
            try:
                fd = os.open(name, TEMP_FLAGS, 0o600, dir_fd=parent)
                self.temps.add(f"{parent_rel}/{name}")
                return name, fd
            except FileExistsError:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode):
                    raise SetupError(f"{self.show(parent_rel + '/' + name)} is a symlink")
                continue
        raise SetupError(f"cannot make a temp file in {self.show(parent_rel)}")

    def unlink(self, rel: str) -> None:
        parent, name = self.parent_of(rel)
        os.unlink(name, dir_fd=parent)
        self.temps.discard(rel)
        sync(parent)

    def drop_temps(self) -> list[str]:
        """Delete the temp files this run made and has not yet deleted.
        Returns the ones it could not delete."""
        left = []
        for rel in sorted(self.temps):
            try:
                self.unlink(rel)
            except FileNotFoundError:
                self.temps.discard(rel)
            except Exception:
                left.append(self.show(rel))
        return left

    def publish(self, rel: str, data: bytes, landed: Callable[[], None]) -> None:
        """Write data as the new file rel: a temp in the same folder, written
        and fsynced in full, then linked into place (which fails if rel
        exists). The target is never partial and never overwritten."""
        parent, name = self.parent_of(rel)
        parent_rel = rel.rpartition("/")[0]
        temp, fd = self.make_temp(parent, parent_rel, name)
        try:
            try:
                write_all(fd, data)
                sync(fd)
            finally:
                os.close(fd)
            os.link(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
            landed()
            sync(parent)
        finally:
            self.unlink(f"{parent_rel}/{temp}")

    def replace_settings(self, data: bytes, base: bytes | None, mode: int,
                         verb: str, landed: Callable[[], None]) -> None:
        """Replace settings.json with data through a temp file that already
        has the given mode, only if settings.json still holds base."""
        parent, name = self.parent_of(SETTINGS_REL)
        temp, fd = self.make_temp(parent, CLAUDE_REL, name)
        placed = False
        try:
            try:
                os.fchmod(fd, mode)
                write_all(fd, data)
                sync(fd)
            finally:
                os.close(fd)
            current = self.read(SETTINGS_REL)
            if (current[0] if current else None) != base:
                raise SetupError(
                    f"{SETTINGS_REL} changed while {verb} was running; "
                    "it was not written")
            os.replace(temp, name, src_dir_fd=parent, dst_dir_fd=parent)
            placed = True
            landed()
            sync(parent)
        finally:
            if placed:
                self.temps.discard(f"{CLAUDE_REL}/{temp}")
            else:
                self.unlink(f"{CLAUDE_REL}/{temp}")

    def rmdir(self, rel: str) -> bool:
        """Remove the folder rel if it is empty. False when it stays."""
        parent, name = self.parent_of(rel)
        if parent is None:
            return True
        try:
            os.rmdir(name, dir_fd=parent)
        except FileNotFoundError:
            return True
        except OSError as exc:
            if exc.errno in (errno.ENOTEMPTY, errno.EEXIST):
                return False
            raise
        fd = self.fds.pop(rel, None)
        if fd is not None:
            os.close(fd)
        sync(parent)
        return True


# ---------------------------------------------------------------------------
# The lock


def take_lock(tree: Tree) -> int:
    """Open .claude/harness-setup.lock without following a symlink, make it
    0600 and take an exclusive flock without waiting. The file is never
    unlinked: release is closing the descriptor."""
    shown = tree.show(LOCK_REL)
    try:
        claude = tree.dir(CLAUDE_REL, create=True)
        try:
            fd = os.open("harness-setup.lock", LOCK_FLAGS, 0o600, dir_fd=claude)
        except OSError as exc:
            tree.refuse_entry(claude, "harness-setup.lock", LOCK_REL, exc)
            raise
    except SetupError:
        raise
    except OSError as exc:
        raise SetupError(f"cannot take the setup lock: {exc}") from exc
    try:
        sync(claude)
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SetupError(
                f"another harness setup run holds the lock {shown}; "
                "nothing was changed") from exc
        held = os.fstat(fd)
        try:
            now = os.stat("harness-setup.lock", dir_fd=claude, follow_symlinks=False)
        except FileNotFoundError:
            now = None
        if now is None or (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
            raise SetupError(
                f"cannot take the setup lock: {shown} was replaced while it was taken")
    except SetupError:
        os.close(fd)
        raise
    except OSError as exc:
        os.close(fd)
        raise SetupError(f"cannot take the setup lock: {exc}") from exc
    return fd


# ---------------------------------------------------------------------------
# The record


def is_hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= HEX


def header_line(backup: str | None) -> bytes:
    return json_line({"record": RECORD_KIND, "version": RECORD_VERSION, "backup": backup})


def parse_line(raw: bytes) -> dict[str, Any] | None:
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def valid_header(head: dict[str, Any] | None) -> bool:
    return (head is not None
            and set(head) == {"record", "version", "backup"}
            and head["record"] == RECORD_KIND
            and type(head["version"]) is int and head["version"] == RECORD_VERSION
            and head["backup"] in (None, BACKUP_REL))


def valid_item(item: Any) -> bool:
    if not isinstance(item, dict):
        return False
    index = item.get("index")
    if type(index) is not int or index < 0:
        return False
    if item.get("kind") == "key":
        return (set(item) == {"kind", "name", "value", "index", "before", "after"}
                and item["name"] == "sandbox" and same(item["value"], SANDBOX_VALUE))
    if item.get("kind") == "deny":
        return (set(item) == {"kind", "value", "index", "before", "after"}
                and any(same(item["value"], rule) for rule in DENY_RULES))
    return False


def valid_body(body: dict[str, Any] | None, head: dict[str, Any]) -> bool:
    if body is None or set(body) != {"complete", "files", "settings", "dirs", "backup_sha256"}:
        return False
    if body["complete"] is not True:
        return False
    files = body["files"]
    if not isinstance(files, list):
        return False
    paths = []
    for item in files:
        if (not isinstance(item, dict) or set(item) != {"path", "sha256"}
                or item["path"] not in (RULES_REL, OVERRIDE_REL)
                or not is_hex64(item["sha256"])):
            return False
        paths.append(item["path"])
    if len(set(paths)) != len(paths) or RULES_REL not in paths:
        return False
    dirs = body["dirs"]
    if (not isinstance(dirs, list) or any(d not in CREATABLE_DIRS for d in dirs)
            or len(set(dirs)) != len(dirs)):
        return False
    settings = body["settings"]
    if (not isinstance(settings, dict)
            or set(settings) != {"created", "sha256", "items",
                                 "created_permissions", "created_deny"}
            or type(settings["created"]) is not bool
            or type(settings["created_permissions"]) is not bool
            or type(settings["created_deny"]) is not bool
            or not isinstance(settings["items"], list)
            or not all(valid_item(item) for item in settings["items"])):
        return False
    if settings["items"]:
        if not is_hex64(settings["sha256"]):
            return False
    elif settings["sha256"] is not None or settings["created"]:
        return False
    if head["backup"] is None:
        return body["backup_sha256"] is None
    return is_hex64(body["backup_sha256"])


def parse_record(raw: bytes) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """(header, body). body is None unless the record is complete: exactly
    a valid header line and a valid complete line, each ending in a newline,
    and nothing else. header is None when line 1 is not a valid header."""
    first, newline, rest = raw.partition(b"\n")
    head = parse_line(first) if newline else None
    if not valid_header(head):
        return None, None
    second, newline, tail = rest.partition(b"\n")
    if not newline or tail:
        return head, None
    body = parse_line(second)
    if not valid_body(body, head):
        return head, None
    return head, body


def hard_stop_refusal(tree: Tree, head: dict[str, Any] | None) -> SetupError:
    backup = tree.entry(BACKUP_REL)
    if backup is not None:
        state = f"{tree.show(BACKUP_REL)} (present)"
    elif head is not None and head["backup"] is None:
        state = "none (settings.json did not exist)"
    else:
        state = f"{tree.show(BACKUP_REL)} (absent: settings.json was not changed)"
    return SetupError(
        "an earlier setup run stopped before it finished; "
        f"record: {tree.show(RECORD_REL)}; settings backup: {state}; "
        'see KNOWN-LIMITS "After a hard stop" to recover by hand')


def read_record(tree: Tree) -> tuple[bytes | None, dict[str, Any] | None, dict[str, Any] | None]:
    """Check the record's folders, the record and the backup for symlinks,
    and read the record."""
    for rel in RECORD_DIRS:
        tree.dir(rel)
    tree.entry(BACKUP_REL)
    found = tree.read(RECORD_REL)
    if found is None:
        return None, None, None
    head, body = parse_record(found[0])
    return found[0], head, body


# ---------------------------------------------------------------------------
# Settings items


def json_type(value: Any) -> str:
    """The JSON type name of a parsed JSON value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, (int, float)):
        return "a number"
    if isinstance(value, str):
        return "a string"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    return type(value).__name__


def wrong_type(settings: dict[str, Any], ids: set[str]) -> str | None:
    """The first settings key an approved item adds to that is present with
    a value of the wrong JSON type (null included), named with its type, or
    None. Presence is membership, never `get`: such a value is never
    overwritten, and apply refuses before its first write."""
    if "sandbox" in ids and "sandbox" in settings and not isinstance(settings["sandbox"], dict):
        return f"sandbox is {json_type(settings['sandbox'])}, not an object"
    if any(f"deny:{rule}" in ids for rule in DENY_RULES) and "permissions" in settings:
        permissions = settings["permissions"]
        if not isinstance(permissions, dict):
            return f"permissions is {json_type(permissions)}, not an object"
        if "deny" in permissions and not isinstance(permissions["deny"], list):
            return f"permissions.deny is {json_type(permissions['deny'])}, not an array"
    return None


def check_types(tree: Tree, found: tuple[bytes, int, dict[str, Any]] | None,
                ids: set[str]) -> None:
    problem = wrong_type(found[2], ids) if found is not None else None
    if problem is not None:
        raise SetupError(f"{tree.show(SETTINGS_REL)}: {problem}; setup does not overwrite it")


def neighbours(seq: list[Any], index: int) -> tuple[Any, Any]:
    return (seq[index - 1] if index > 0 else None,
            seq[index + 1] if index + 1 < len(seq) else None)


def add_items(settings: dict[str, Any],
              ids: set[str]) -> tuple[list[dict[str, Any]], bool, bool]:
    """Add each approved item that is absent. Returns the items with their
    recorded places in the final settings, and whether permissions and
    permissions.deny were made. The caller has checked wrong_type."""
    added_sandbox = "sandbox" in ids and "sandbox" not in settings
    if added_sandbox:
        settings["sandbox"] = dict(SANDBOX_VALUE)
    created_permissions = created_deny = False
    added: list[int] = []
    for rule in DENY_RULES:
        if f"deny:{rule}" not in ids:
            continue
        if "permissions" not in settings:
            settings["permissions"] = {}
            created_permissions = True
        permissions = settings["permissions"]
        if "deny" not in permissions:
            permissions["deny"] = []
            created_deny = True
        deny = permissions["deny"]
        if rule in deny:
            continue
        deny.append(rule)
        added.append(len(deny) - 1)
    items: list[dict[str, Any]] = []
    if added_sandbox:
        keys = list(settings)
        index = keys.index("sandbox")
        before, after = neighbours(keys, index)
        items.append({"kind": "key", "name": "sandbox", "value": dict(SANDBOX_VALUE),
                      "index": index, "before": before, "after": after})
    for index in added:
        deny = settings["permissions"]["deny"]
        before, after = neighbours(deny, index)
        items.append({"kind": "deny", "value": deny[index], "index": index,
                      "before": before, "after": after})
    return items, created_permissions, created_deny


def take_out(settings: dict[str, Any], info: dict[str, Any], shown: str,
             kept: list[str], gone: list[str]) -> None:
    """Take out each recorded item that is still at its recorded place with
    its recorded neighbours, judged on settings as it is now. Anything else
    is kept and named; nothing else in settings is touched."""
    keys = list(settings)
    keys_out: list[str] = []
    for item in info["items"]:
        if item["kind"] != "key":
            continue
        name = item["name"]
        if name not in settings:
            gone.append(f"{shown}: {name}")
            continue
        index = keys.index(name)
        if (same(settings[name], item["value"]) and index == item["index"]
                and neighbours(keys, index) == (item["before"], item["after"])):
            keys_out.append(name)
        else:
            kept.append(f"{shown}: {name} (moved or changed)")
    permissions = settings.get("permissions")
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    matched: list[int] = []
    for item in info["items"]:
        if item["kind"] != "deny":
            continue
        value, index = item["value"], item["index"]
        if not isinstance(deny, list) or not any(same(entry, value) for entry in deny):
            gone.append(f"{shown}: permissions.deny rule {value!r}")
            continue
        before = deny[index - 1] if 0 < index <= len(deny) else None
        after = deny[index + 1] if index + 1 < len(deny) else None
        if (index < len(deny) and same(deny[index], value)
                and sum(same(entry, value) for entry in deny) == 1
                and same(before, item["before"]) and same(after, item["after"])):
            matched.append(index)
        else:
            kept.append(f"{shown}: permissions.deny rule {value!r} (moved or duplicated)")
    for index in sorted(matched, reverse=True):
        del deny[index]
    if isinstance(deny, list) and not deny and info["created_deny"]:
        del permissions["deny"]
    if isinstance(permissions, dict) and not permissions and info["created_permissions"]:
        del settings["permissions"]
    for name in keys_out:
        del settings[name]


# ---------------------------------------------------------------------------
# Apply


def which_claude() -> str:
    found = shutil.which("claude")
    if not found:
        raise SetupError("claude is not on PATH")
    return found


def configure_ledger(claude: str, values: dict[str, str], lock_fd: int) -> None:
    """Run `claude plugin configure`. The child shares the lock descriptor,
    so the lock stays held while it runs even if setup is killed."""
    try:
        done = subprocess.run(
            [claude, "plugin", "configure", LEDGER_PLUGIN, "--values-stdin"],
            input=json.dumps(values), capture_output=True, text=True,
            pass_fds=(lock_fd,))
    except OSError as exc:
        raise SetupError(f"claude plugin configure failed: {exc}") from exc
    if done.returncode != 0:
        detail = one_line(done.stderr or done.stdout or f"exit {done.returncode}")
        raise SetupError(f"claude plugin configure failed: {detail}")


def read_settings(tree: Tree) -> tuple[bytes, int, dict[str, Any]] | None:
    found = tree.read(SETTINGS_REL)
    if found is None:
        return None
    raw, mode = found
    return raw, mode, parse_json_object(raw, tree.show(SETTINGS_REL))


class Changes:
    """What apply has changed, in memory, each added the moment it lands."""

    def __init__(self) -> None:
        self.dirs: list[str] = []
        self.record: dict[str, Any] | None = None
        self.backup: str | None = None
        self.files: dict[str, str] = {}
        self.settings: dict[str, Any] | None = None


def apply_verb(home: Path, operator: str, harness: str | None,
               approve: list[str], approve_all: bool) -> tuple[str, list[str]]:
    tree = Tree(home)
    try:
        lock = take_lock(tree)
        try:
            return apply_locked(tree, lock, operator, harness, approve, approve_all)
        finally:
            os.close(lock)
    finally:
        tree.close()


def apply_locked(tree: Tree, lock: int, operator: str, harness: str | None,
                 approve: list[str], approve_all: bool) -> tuple[str, list[str]]:
    # Preconditions: no writes.
    rules_bytes, override_bytes = check_proposals()
    raw, head, body = read_record(tree)
    if raw is not None:
        if body is not None:
            raise SetupError("already applied; run remove first")
        raise hard_stop_refusal(tree, head)
    if tree.entry(BACKUP_REL) is not None:
        raise SetupError(f"leftover settings backup: {tree.show(BACKUP_REL)}")
    tree.dir(RULES_DIR_REL)
    if tree.entry(RULES_REL) is not None:
        raise SetupError(f"rules default already exists: {tree.show(RULES_REL)}")
    tree.entry(OVERRIDE_REL)
    tree.entry(SETTINGS_REL)
    claude = which_claude()
    ids = approved_ids(approve, approve_all)
    check_types(tree, read_settings(tree), ids)

    # Configure, then the settings bytes apply builds on.
    configure_ledger(claude, ledger_values(tree.root, operator, harness), lock)
    try:
        base = read_settings(tree)
        check_types(tree, base, ids)
    except SetupError as exc:
        raise SetupError(f"{exc}; {CONFIGURE_STAYS}") from exc
    except Exception as exc:
        raise SetupError(f"unexpected error: {type(exc).__name__}: {one_line(exc)}; "
                         f"{CONFIGURE_STAYS}") from exc

    changes = Changes()
    notes: list[str] = []
    try:
        apply_changes(tree, changes, base, ids, rules_bytes, override_bytes, notes)
    except Exception as exc:
        raise rollback(tree, changes, exc) from exc

    # Apply has completed: line 2 is written. The backup is no longer needed.
    warnings: list[str] = []
    if changes.backup is not None:
        try:
            current = tree.read(BACKUP_REL)
            if current is not None and digest(current[0]) == changes.backup:
                tree.unlink(BACKUP_REL)
            elif current is not None:
                warnings.append(f"warning: kept for a manual check: {tree.show(BACKUP_REL)}")
        except Exception as exc:
            warnings.append(
                f"warning: could not delete the settings backup {tree.show(BACKUP_REL)} "
                f"({type(exc).__name__}: {one_line(exc)}); remove deletes it")
    notes.append(f"applied: {tree.show(RECORD_REL)}")
    return "".join(line + "\n" for line in notes), warnings


CONFIGURE_STAYS = "the ledger options claude plugin configure set stay set"


def apply_changes(tree: Tree, changes: Changes, base: tuple[bytes, int, dict[str, Any]] | None,
                  ids: set[str], rules_bytes: bytes, override_bytes: bytes,
                  notes: list[str]) -> None:
    def made(rel: str) -> None:
        changes.dirs.append(rel)

    # Record, then backup. Until the backup is in place nothing outside
    # setup's own folder has changed.
    tree.dir(SETUP_DIR_REL, create=True, made=made)
    line1 = header_line(BACKUP_REL if base is not None else None)

    def record_landed() -> None:
        changes.record = {"line1": line1, "line2": None}

    tree.publish(RECORD_REL, line1, record_landed)
    if base is not None:
        backup_sha = digest(base[0])

        def backup_landed() -> None:
            changes.backup = backup_sha

        tree.publish(BACKUP_REL, base[0], backup_landed)

    # Rules, then the override when the person has none.
    tree.dir(RULES_DIR_REL, create=True, made=made)
    files: list[dict[str, str]] = []
    for rel, data in ((RULES_REL, rules_bytes), (OVERRIDE_REL, override_bytes)):
        if rel == OVERRIDE_REL and tree.entry(OVERRIDE_REL) is not None:
            notes.append(f"kept: {tree.show(OVERRIDE_REL)} (already existed)")
            continue
        sha = digest(data)

        def file_landed(rel: str = rel, sha: str = sha) -> None:
            changes.files[rel] = sha

        try:
            tree.publish(rel, data, file_landed)
        except FileExistsError as exc:
            raise SetupError(f"{tree.show(rel)} appeared while apply was running; "
                             "it was not written") from exc
        files.append({"path": rel, "sha256": sha})

    # Settings, only when an approved item is absent.
    obj = json.loads(json.dumps(base[2])) if base is not None else {}
    items, created_permissions, created_deny = add_items(obj, ids)
    info = {"created": False, "sha256": None, "items": [],
            "created_permissions": False, "created_deny": False}
    if items:
        indent = detect_indent(base[0].decode("utf-8")) if base is not None else 2
        new = dump_settings(obj, indent)
        mode = base[1] if base is not None else 0o600
        info = {"created": base is None, "sha256": digest(new), "items": items,
                "created_permissions": created_permissions, "created_deny": created_deny}

        def settings_landed() -> None:
            changes.settings = {"base": base[0] if base is not None else None,
                                "written": new, "info": info}

        tree.replace_settings(new, base[0] if base is not None else None, mode,
                              "apply", settings_landed)

    # Finish: line 2.
    body = {"complete": True, "files": files, "settings": info,
            "dirs": list(changes.dirs),
            "backup_sha256": changes.backup}
    line2 = json_line(body)
    changes.record["line2"] = line2
    parent, name = tree.parent_of(RECORD_REL)
    fd = os.open(name, APPEND_FLAGS, dir_fd=parent)
    try:
        write_all(fd, line2)
        sync(fd)
    finally:
        os.close(fd)


def record_is_ours(current: bytes, record: dict[str, Any]) -> bool:
    line1, line2 = record["line1"], record["line2"]
    if not current.startswith(line1):
        return False
    rest = current[len(line1):]
    return line2 is not None and line2.startswith(rest) or rest == b""


def rollback(tree: Tree, changes: Changes, exc: Exception) -> SetupError:
    """Undo apply's changes from memory, under I2: a file whose bytes are not
    the bytes setup wrote is kept and named. What to undo never comes from the
    record, and nothing is written to it; its bytes are read only to compare
    them with what this run wrote before it is deleted. If any step fails,
    the record and the backup are kept, so the next run refuses by name."""
    kept: list[str] = []
    failed: list[str] = []

    def attempt(shown: str, action: Callable[[], None]) -> None:
        try:
            action()
        except Exception:
            failed.append(shown)

    if changes.settings is not None:
        attempt(tree.show(SETTINGS_REL), lambda: rollback_settings(tree, changes, kept))
    for rel in (OVERRIDE_REL, RULES_REL):
        if rel in changes.files:
            sha = changes.files[rel]
            attempt(tree.show(rel), lambda rel=rel, sha=sha: delete_if(tree, rel, sha, kept))
    if not failed:
        if changes.record is not None:
            record = changes.record

            def drop_record() -> None:
                current = tree.read(RECORD_REL)
                if current is None:
                    return
                if not record_is_ours(current[0], record):
                    kept.append(tree.show(RECORD_REL))
                    return
                tree.unlink(RECORD_REL)

            attempt(tree.show(RECORD_REL), drop_record)
        if changes.backup is not None and not failed:
            sha = changes.backup
            attempt(tree.show(BACKUP_REL), lambda: delete_if(tree, BACKUP_REL, sha, kept))
    failed.extend(tree.drop_temps())
    for rel in reversed(changes.dirs):
        attempt(tree.show(rel), lambda rel=rel: tree.rmdir(rel))

    if isinstance(exc, SetupError):
        message = str(exc)
    else:
        message = f"unexpected error: {type(exc).__name__}: {one_line(exc)}"
    message = f"{message}; {CONFIGURE_STAYS}"
    if failed:
        backup = tree.show(BACKUP_REL)
        if changes.record is not None and changes.record["line1"] == header_line(None):
            backup = "none (settings.json did not exist)"
        return SetupError(
            f"rollback incomplete; kept: {', '.join(failed + kept)}; "
            f"record: {tree.show(RECORD_REL)}; settings backup: {backup}; "
            f"cause: {message}")
    return SetupError(message + "".join(
        f"; kept for a manual check: {shown}" for shown in kept))


def delete_if(tree: Tree, rel: str, sha: str, kept: list[str]) -> None:
    current = tree.read(rel)
    if current is None:
        return
    if digest(current[0]) != sha:
        kept.append(tree.show(rel))
        return
    tree.unlink(rel)


def rollback_settings(tree: Tree, changes: Changes, kept: list[str]) -> None:
    state = changes.settings
    shown = tree.show(SETTINGS_REL)
    found = tree.read(SETTINGS_REL)
    if found is None:
        kept.append(shown)
        return
    current, mode = found
    if current == state["written"]:
        if state["base"] is None:
            tree.unlink(SETTINGS_REL)
        else:
            tree.replace_settings(state["base"], current, mode, "apply", lambda: None)
        return
    # Changed since apply wrote it: take apply's items out one by one.
    kept.append(shown)
    try:
        obj = parse_json_object(current, shown)
    except SetupError:
        return
    take_out(obj, state["info"], shown, [], [])
    new = dump_settings(obj, detect_indent(current.decode("utf-8")))
    if new != current:
        tree.replace_settings(new, current, mode, "apply", lambda: None)


# ---------------------------------------------------------------------------
# Remove


def remove_verb(home: Path) -> tuple[str, list[str]]:
    tree = Tree(home)
    try:
        lock = take_lock(tree)
        try:
            try:
                return remove_locked(tree)
            except Exception:
                # Remove does not roll back; it only deletes its own temp.
                tree.drop_temps()
                raise
        finally:
            os.close(lock)
    finally:
        tree.close()


def remove_locked(tree: Tree) -> tuple[str, list[str]]:
    raw, head, body = read_record(tree)
    if raw is None:
        if tree.entry(BACKUP_REL) is not None:
            raise SetupError(f"leftover settings backup: {tree.show(BACKUP_REL)}")
        raise SetupError(f"no record at {tree.show(RECORD_REL)}")
    if body is None:
        raise hard_stop_refusal(tree, head)
    tree.dir(RULES_DIR_REL)
    for rel in (RULES_REL, OVERRIDE_REL, SETTINGS_REL):
        tree.entry(rel)
    settings_now = read_settings(tree)

    kept: list[str] = []
    gone: list[str] = []
    shown = tree.show(SETTINGS_REL)

    # Settings.
    info = body["settings"]
    if info["items"]:
        if settings_now is None:
            gone.append(shown)
        else:
            current, mode, obj = settings_now
            take_out(obj, info, shown, kept, gone)
            if info["created"] and obj == {}:
                again = tree.read(SETTINGS_REL)
                if (again[0] if again else None) != current:
                    raise SetupError(
                        f"{SETTINGS_REL} changed while remove was running; "
                        "it was not written")
                tree.unlink(SETTINGS_REL)
            else:
                new = dump_settings(obj, detect_indent(current.decode("utf-8")))
                if new != current:
                    tree.replace_settings(new, current, mode, "remove", lambda: None)

    # Files: the override, then the rules file.
    listed = {item["path"]: item["sha256"] for item in body["files"]}
    for rel in (OVERRIDE_REL, RULES_REL):
        if rel not in listed:
            continue
        found = tree.read(rel)
        if found is None:
            gone.append(tree.show(rel))
        elif digest(found[0]) != listed[rel]:
            kept.append(tree.show(rel))
        else:
            tree.unlink(rel)

    # Backup.
    if head["backup"] is not None:
        found = tree.read(BACKUP_REL)
        if found is not None:
            if digest(found[0]) == body["backup_sha256"]:
                tree.unlink(BACKUP_REL)
            else:
                kept.append(tree.show(BACKUP_REL))

    # Finish: folders off the record's path, then the record, then the
    # record's own folders.
    dirs = list(body["dirs"])
    for rel in sorted((d for d in dirs if d not in RECORD_DIRS),
                      key=lambda d: d.count("/"), reverse=True):
        tree.rmdir(rel)
    current = tree.read(RECORD_REL)
    if current is None or current[0] != raw:
        raise SetupError(f"{tree.show(RECORD_REL)} changed while remove was running")
    parent, name = tree.parent_of(RECORD_REL)
    os.unlink(name, dir_fd=parent)

    # Remove has completed: the record's unlink is its commit point. A
    # failure after it (the folder fsync, pruning) is a named warning, never
    # a failure that would leave a rerun refusing with no record.
    warnings: list[str] = []
    try:
        sync(parent)
    except Exception as exc:
        warnings.append(
            f"warning: could not sync the folder {tree.show(SETUP_DIR_REL)} after deleting "
            f"the record ({type(exc).__name__}: {one_line(exc)})")
    for rel in sorted((d for d in dirs if d in RECORD_DIRS),
                      key=lambda d: d.count("/"), reverse=True):
        try:
            tree.rmdir(rel)
        except Exception as exc:
            warnings.append(
                f"warning: could not remove the empty folder {tree.show(rel)} "
                f"({type(exc).__name__}: {one_line(exc)})")

    lines = [f"kept for a manual check: {item}" for item in kept]
    lines += [f"already gone: {item}" for item in gone]
    lines.append(f"kept: ledger database at {database_path(tree.root)} (person's data)")
    lines.append(f"removed: {tree.show(RECORD_REL)}")
    return "".join(line + "\n" for line in lines), warnings


# ---------------------------------------------------------------------------
# Ledger-init: the first ledger write


def ledger_init_verb(home: Path, operator: str,
                     harness: str | None) -> tuple[str, list[str], int]:
    tree = Tree(home)
    try:
        lock = take_lock(tree)
        try:
            return ledger_init_locked(tree, operator, harness), [], 0
        finally:
            os.close(lock)
    finally:
        tree.close()


def reserve_temp_name(parent: int, base: str) -> str:
    """A name for the temporary database in the folder parent, one that no
    entry has now. The ledger command creates the file."""
    for _attempt in range(100):
        name = temp_name(base)
        try:
            os.stat(name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return name
    raise SetupError("cannot find an unused name for the temporary ledger database")


def confirm_first_write(path: str) -> None:
    """The first write's topic is in the database at path: the log is folded
    into the file, and the item the write creates is read back."""
    source = FIRST_WRITE[FIRST_WRITE.index("--source") + 1]
    try:
        conn = sqlite3.connect(path, timeout=30)
        try:
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            rows = conn.execute(
                "SELECT payload FROM events WHERE kind = 'item.created'").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        raise SetupError(
            f"the first ledger write made a database setup cannot read: {one_line(exc)}") from exc
    for (payload,) in rows:
        try:
            if json.loads(payload).get("source") == source:
                return
        except (TypeError, ValueError, AttributeError):
            continue
    raise SetupError("the first ledger write reported success but the topic is not in the new database")


def drop_temp_database(parent: int, temp: str) -> list[str]:
    """Delete the temporary database and the files SQLite keeps beside it.
    Returns the names it could not delete."""
    left = []
    for suffix in ("", "-wal", "-shm", "-journal"):
        try:
            os.unlink(temp + suffix, dir_fd=parent)
        except FileNotFoundError:
            continue
        except OSError:
            left.append(temp + suffix)
    try:
        sync(parent)
    except OSError:
        pass
    return left


def ledger_init_locked(tree: Tree, operator: str, harness: str | None) -> str:
    """Make the first ledger write when the database file is absent. Setup
    must have completed: the write is part of it. A database that already
    exists is never written to."""
    raw, head, body = read_record(tree)
    if raw is None:
        raise SetupError(f"no record at {tree.show(RECORD_REL)}; run apply first")
    if body is None:
        raise hard_stop_refusal(tree, head)
    database = database_path(tree.root)
    if os.path.lexists(database):
        return (f"kept: ledger database already exists at {database}; "
                "no first write was made\n")
    ledger = shutil.which("ledger")
    if not ledger:
        raise SetupError(
            "the ledger command is not on PATH; it comes with the ledger-hooks "
            "plugin once that plugin is loaded (run /reload-plugins or start a "
            "new session), then run ledger-init again")
    # The write runs against a temporary database that setup owns, in the
    # final path's own folder. The file is published to the final path only
    # after the child has succeeded and the topic is in it, so a failed or
    # timed-out child can never leave a partial file at the final path that
    # the next run would take for a finished database.
    parent_rel, _, base = DB_REL.rpartition("/")
    parent = tree.follow_dir(parent_rel)
    temp = reserve_temp_name(parent, base)
    temp_path = tree.show(f"{parent_rel}/{temp}")
    env = dict(os.environ)
    env["LEDGER_DB"] = temp_path
    env["LEDGER_OPERATOR"] = operator
    env["LEDGER_HARNESS"] = harness or DEFAULT_HARNESS
    command = [ledger, "--db", temp_path, *FIRST_WRITE]
    try:
        try:
            done = subprocess.run(command, env=env, capture_output=True, text=True,
                                  stdin=subprocess.DEVNULL, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise SetupError(f"the first ledger write failed: {one_line(exc)}") from exc
        if done.returncode != 0:
            detail = one_line(done.stderr or done.stdout or f"exit {done.returncode}")
            raise SetupError(f"the first ledger write failed: {detail}")
        try:
            info = os.stat(temp, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            info = None
        if info is None or not stat.S_ISREG(info.st_mode):
            raise SetupError(
                f"the first ledger write reported success but made no database at {database}")
        confirm_first_write(temp_path)
        fd = os.open(temp, READ_FLAGS, dir_fd=parent)
        try:
            sync(fd)
        finally:
            os.close(fd)
        try:
            os.link(temp, base, src_dir_fd=parent, dst_dir_fd=parent)
        except FileExistsError:
            raise SetupError(
                f"{database} appeared while the first ledger write was running; "
                "it was not replaced, and the new database was dropped") from None
        sync(parent)
    finally:
        left = drop_temp_database(parent, temp)
    if left:
        raise SetupError(
            f"the ledger database was created, but these temporary files could not be "
            f"removed: {', '.join(tree.show(f'{parent_rel}/{name}') for name in left)}")
    return f"created: ledger database at {database}\n"


# ---------------------------------------------------------------------------
# Doctor: reads and reports, writes nothing, takes no lock


class Report:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.failed = False

    def add(self, status: str, area: str, text: str) -> None:
        if status == "FAIL":
            self.failed = True
        self.lines.append(f"{status} {area}: {one_line(text)}")

    def guard(self, area: str, action: Callable[[], Any]) -> Any:
        try:
            return action()
        except SetupError as exc:
            self.add("FAIL", area, str(exc))
        except Exception as exc:
            self.add("FAIL", area, f"unexpected error: {type(exc).__name__}: {one_line(exc)}")
        return None


def item_label(item: dict[str, Any]) -> str:
    if item["kind"] == "key":
        return f"settings key {item['name']}"
    return f"deny rule {item['value']}"


def doctor_record(tree: Tree, report: Report) -> dict[str, Any] | None:
    raw, head, body = read_record(tree)
    if raw is None:
        if tree.entry(BACKUP_REL) is not None:
            report.add("FAIL", "record", f"leftover settings backup: {tree.show(BACKUP_REL)}")
        else:
            report.add("FAIL", "record",
                       f"setup has not run: no record at {tree.show(RECORD_REL)}")
        return None
    if body is None:
        report.add("FAIL", "record", str(hard_stop_refusal(tree, head)))
        return None
    report.add("ok", "record", f"setup record is complete: {tree.show(RECORD_REL)}")
    return body


def doctor_files(tree: Tree, body: dict[str, Any] | None, report: Report) -> None:
    listed = {item["path"]: item["sha256"] for item in body["files"]} if body else {}
    for rel in (RULES_REL, OVERRIDE_REL):
        shown = tree.show(rel)
        found = tree.read(rel)
        if rel in listed:
            if found is None:
                report.add("FAIL", "files", f"placed by setup and now missing: {shown}")
            elif digest(found[0]) == listed[rel]:
                report.add("ok", "files", f"placed by setup and unchanged: {shown}")
            else:
                report.add("note", "files",
                           f"changed since setup: {shown}; uninstall keeps it")
        elif found is not None:
            report.add("note", "files",
                       f"present, but setup did not place it: {shown}; uninstall leaves it")
        elif rel == RULES_REL:
            report.add("FAIL", "files", f"missing: {shown}")
        else:
            report.add("note", "files", f"no override file: {shown}")


def doctor_settings(tree: Tree, body: dict[str, Any] | None, report: Report) -> None:
    if body is None:
        return
    info = body["settings"]
    if not info["items"]:
        report.add("ok", "settings", "setup added no settings item")
        return
    shown = tree.show(SETTINGS_REL)
    found = read_settings(tree)
    for item in info["items"]:
        label = item_label(item)
        if found is None:
            report.add("note", "settings", f"{label} is already gone ({shown} is absent)")
            continue
        trial = json.loads(json.dumps(found[2]))
        kept: list[str] = []
        gone: list[str] = []
        take_out(trial, {"items": [item], "created_deny": False,
                         "created_permissions": False}, shown, kept, gone)
        if kept:
            report.add("note", "settings",
                       f"{label} was moved or changed since setup; uninstall keeps it")
        elif gone:
            report.add("note", "settings", f"{label} is already gone; uninstall skips it")
        else:
            report.add("ok", "settings", f"{label} is in place as setup added it")


def doctor_options(home: Path, report: Report) -> Path:
    """Report the ledger plugin's stored options. Returns the database path
    they name, or the default path when none is stored."""
    default = database_path(home)
    claude = shutil.which("claude")
    if not claude:
        report.add("FAIL", "options", "claude is not on PATH; cannot read the stored ledger options")
        return default
    try:
        done = subprocess.run([claude, "plugin", "configure", LEDGER_PLUGIN, "--json"],
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        report.add("FAIL", "options", f"claude plugin configure failed: {one_line(exc)}")
        return default
    if done.returncode != 0:
        detail = one_line(done.stderr or done.stdout or f"exit {done.returncode}")
        report.add("FAIL", "options", f"claude plugin configure failed: {detail}")
        return default
    try:
        data = json.loads(done.stdout)
    except ValueError:
        data = None
    schema = data.get("schema") if isinstance(data, dict) else None
    inputs = data.get("inputs") if isinstance(data, dict) else None
    configured = data.get("configured") if isinstance(data, dict) else None
    if (not isinstance(schema, dict) or not isinstance(inputs, dict)
            or not isinstance(configured, list)):
        report.add("FAIL", "options",
                   "claude plugin configure --json gave output setup cannot read")
        return default
    stored = default
    required = (OPTION_DB, OPTION_OPERATOR)
    for key in OPTION_KEYS:
        status = "FAIL" if key in required else "note"
        value = inputs.get(key)
        if key not in schema:
            report.add(status, "options", f"{LEDGER_PLUGIN} declares no option {key}")
        elif key not in configured or not isinstance(value, str) or not value:
            report.add(status, "options", f"{key} is not set")
        else:
            report.add("ok", "options", f"{key} is set: {value}")
            if key == OPTION_DB:
                stored = Path(value)
                if stored != default:
                    report.add("note", "options",
                               f"{key} is not the default path {default}; the database "
                               "check below uses the stored path")
    return stored


def doctor_database(path: Path, report: Report) -> None:
    try:
        info = os.stat(path)
    except FileNotFoundError:
        report.add("FAIL", "ledger",
                   f"the ledger database is missing: {path}; run ledger-init")
        return
    except OSError as exc:
        report.add("FAIL", "ledger", f"cannot read the ledger database {path}: {one_line(exc)}")
        return
    if not stat.S_ISREG(info.st_mode):
        report.add("FAIL", "ledger", f"the ledger database is not a regular file: {path}")
    elif info.st_size == 0:
        report.add("FAIL", "ledger", f"the ledger database is empty: {path}")
    else:
        report.add("ok", "ledger", f"the ledger database exists: {path} ({info.st_size} bytes)")


def doctor_tools(report: Report) -> None:
    found = None
    for name in PYTHONS:
        path = shutil.which(name)
        if not path:
            continue
        try:
            done = subprocess.run(
                [path, "-I", "-S", "-B", "-c",
                 "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"],
                capture_output=True, stdin=subprocess.DEVNULL, timeout=30)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if done.returncode == 0:
            found = path
            break
    if found:
        report.add("ok", "tools", f"Python 3.11 or later: {found}")
    else:
        report.add("FAIL", "tools",
                   "Python 3.11 or later is not on PATH; the ledger command needs it")
    ledger = shutil.which("ledger")
    if ledger:
        report.add("ok", "tools", f"the ledger command: {ledger}")
    else:
        report.add("note", "tools",
                   "the ledger command is not on PATH in this session; run /reload-plugins "
                   "or start a new session")


def doctor_verb(home: Path) -> tuple[str, list[str], int]:
    tree = Tree(home)
    report = Report()
    try:
        body = report.guard("record", lambda: doctor_record(tree, report))
        report.guard("files", lambda: doctor_files(tree, body, report))
        report.guard("settings", lambda: doctor_settings(tree, body, report))
    finally:
        tree.close()
    stored = report.guard("options", lambda: doctor_options(home, report))
    report.guard("ledger", lambda: doctor_database(stored or database_path(home), report))
    report.guard("tools", lambda: doctor_tools(report))
    counts = {name: sum(line.startswith(name + " ") for line in report.lines)
              for name in ("ok", "note", "FAIL")}
    report.lines.append(f"doctor: {counts['ok']} ok, {counts['note']} note, "
                        f"{counts['FAIL']} FAIL")
    return "".join(line + "\n" for line in report.lines), [], 1 if report.failed else 0


# ---------------------------------------------------------------------------
# The instance folder: the person's own. Nothing here is recorded, placed or
# removed by setup, and doctor and remove never look at it.


def instance_sources() -> list[tuple[str, bytes]]:
    """(path inside the instance folder, bytes) for each template file."""
    found = []
    for rel in INSTANCE_FILES:
        path = INSTANCE_SOURCE.joinpath(*rel.split("/"))
        if not path.is_file():
            raise SetupError(f"instance/{rel} is missing from the plugin files folder")
        found.append((rel, path.read_bytes()))
    return found


def which_git() -> str:
    found = shutil.which("git")
    if not found:
        raise SetupError("git is not on PATH; the instance folder is made a git repository")
    return found


def instance_target(home: Path, raw: str | None) -> tuple[Path, bool]:
    """The instance folder as an absolute path, and whether it exists now (an
    empty folder). The folder that holds it is the person's and is followed
    through symlinks; the instance folder itself is never a symlink, never a
    file, never holds anything and is never under the home's .claude folder.
    Nothing is written."""
    if not raw:
        raise SetupError("--path is required for instance-plan and instance-create")
    if not os.path.isabs(raw):
        raise SetupError("the instance folder must be an absolute path")
    if ".." in raw.split("/"):
        raise SetupError("the instance folder path must not hold a .. part")
    cleaned = Path(os.path.normpath(raw))
    if not cleaned.name:
        raise SetupError("the instance folder cannot be the root folder")
    parent = Path(os.path.realpath(cleaned.parent))
    if not parent.is_dir():
        raise SetupError(f"the folder that should hold the instance folder does not exist: {parent}")
    target = parent / cleaned.name
    claude = home / CLAUDE_REL
    for shut in (claude, Path(os.path.realpath(claude))):
        if target == shut or shut in target.parents:
            raise SetupError(
                f"the instance folder cannot be inside {shut}: that folder is Claude Code's")
    try:
        info = os.lstat(target)
    except FileNotFoundError:
        return target, False
    if stat.S_ISLNK(info.st_mode):
        raise SetupError(f"{target} is a symlink; choose a new folder")
    if not stat.S_ISDIR(info.st_mode):
        raise SetupError(f"{target} exists and is not a folder")
    try:
        entries = os.listdir(target)
    except OSError as exc:
        raise SetupError(f"cannot read {target}: {one_line(exc)}") from exc
    if entries:
        raise SetupError(f"{target} is not empty; the instance folder must be new or empty")
    return target, True


def instance_plan_verb(home: Path, raw: str | None) -> tuple[str, list[str], int]:
    target, existed = instance_target(home, raw)
    files = instance_sources()
    git = which_git()
    lines = [
        f"INSTANCE FOLDER {target}",
        ("an existing empty folder, which stays" if existed
         else "a new folder, made now") + "; folders are mode 0700 and files 0600",
    ]
    for rel, data in files:
        lines.extend([f"INSTANCE FILE {rel}", "----", data.decode("utf-8").rstrip("\n"), "----"])
    lines.extend([
        "GIT REPOSITORY",
        "----",
        f"{shlex.quote(git)} -C {shlex.quote(str(target))} init -q",
        "----",
        "NOT DONE: no commit, no remote and no setting is made, and nothing under "
        f"{home / CLAUDE_REL} changes; setup records nothing about this folder, "
        "and doctor and uninstall never touch it",
    ])
    return "\n".join(lines) + "\n", [], 0


class InstanceWrites:
    """What instance-create has made, in memory, each added as it lands."""

    def __init__(self) -> None:
        self.root_made = False
        self.dirs: list[str] = []
        self.files: list[str] = []


def instance_git_init(git: str, target: Path) -> None:
    env = {key: value for key, value in os.environ.items()
           if key not in GIT_LOCATION_ENV
           and not key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_"))}
    try:
        done = subprocess.run([git, "-C", str(target), "init", "-q"], env=env,
                              capture_output=True, text=True, stdin=subprocess.DEVNULL,
                              timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SetupError(f"git init failed: {one_line(exc)}") from exc
    if done.returncode != 0:
        detail = one_line(done.stderr or done.stdout or f"exit {done.returncode}")
        raise SetupError(f"git init failed: {detail}")


def instance_write(target: Path, existed: bool, files: list[tuple[str, bytes]], git: str,
                   writes: InstanceWrites, fds: dict[str, int]) -> None:
    """Make the folder if it is new, write each file into it (never over an
    entry, never through a symlink), then make it a git repository."""
    if not existed:
        try:
            os.mkdir(str(target), 0o700)
        except FileExistsError:
            raise SetupError(
                f"{target} appeared while setup was running; nothing was written") from None
        writes.root_made = True
        outer = os.open(str(target.parent), FOLLOW_DIR_FLAGS)
        try:
            sync(outer)
        finally:
            os.close(outer)
    try:
        fds[""] = os.open(str(target), DIR_FLAGS)
    except OSError as exc:
        raise SetupError(f"cannot open {target}: {one_line(exc)}") from exc
    if os.listdir(fds[""]):
        raise SetupError(f"{target} is not empty; the instance folder must be new or empty")

    def folder(rel: str) -> int:
        if rel in fds:
            return fds[rel]
        above, _, name = rel.rpartition("/")
        parent = folder(above)
        os.mkdir(name, 0o700, dir_fd=parent)
        writes.dirs.append(rel)
        fds[rel] = os.open(name, DIR_FLAGS, dir_fd=parent)
        return fds[rel]

    for rel, data in files:
        above, _, name = rel.rpartition("/")
        parent = folder(above)
        fd = os.open(name, TEMP_FLAGS, 0o600, dir_fd=parent)
        writes.files.append(rel)
        try:
            write_all(fd, data)
            sync(fd)
        finally:
            os.close(fd)
    for rel in sorted(fds, key=lambda name: name.count("/"), reverse=True):
        sync(fds[rel])
    instance_git_init(git, target)
    try:
        made = os.stat(".git", dir_fd=fds[""], follow_symlinks=False)
    except FileNotFoundError:
        made = None
    if made is None or not stat.S_ISDIR(made.st_mode):
        raise SetupError("git init reported success but made no .git folder")


def instance_rollback(target: Path, writes: InstanceWrites, fds: dict[str, int]) -> list[str]:
    """Undo what instance_write made: each file, then each subfolder it made,
    then the folder itself if it made it. Nothing is deleted recursively. A
    leftover (git's own partial .git, an entry that would not go) is kept and
    named; returns the names."""
    kept: list[str] = []
    root = fds.get("")
    if root is not None:
        for rel in reversed(writes.files):
            above, _, name = rel.rpartition("/")
            try:
                os.unlink(name, dir_fd=fds[above])
            except FileNotFoundError:
                continue
            except OSError:
                kept.append(str(target / rel))
        for rel in reversed(writes.dirs):
            above, _, name = rel.rpartition("/")
            try:
                os.rmdir(name, dir_fd=fds[above])
            except FileNotFoundError:
                continue
            except OSError:
                kept.append(str(target / rel))
        try:
            os.stat(".git", dir_fd=root, follow_symlinks=False)
        except OSError:
            pass
        else:
            kept.append(str(target / ".git"))
    for fd in fds.values():
        try:
            os.close(fd)
        except OSError:
            pass
    fds.clear()
    if writes.root_made:
        try:
            os.rmdir(str(target))
        except FileNotFoundError:
            pass
        except OSError:
            if str(target / ".git") not in kept:
                kept.append(str(target))
    return kept


def instance_create_verb(home: Path, raw: str | None) -> tuple[str, list[str], int]:
    target, existed = instance_target(home, raw)
    files = instance_sources()
    git = which_git()
    writes = InstanceWrites()
    fds: dict[str, int] = {}
    try:
        instance_write(target, existed, files, git, writes, fds)
    except Exception as exc:
        made = writes.root_made or bool(writes.dirs) or bool(writes.files)
        kept = instance_rollback(target, writes, fds)
        if isinstance(exc, SetupError):
            message = str(exc)
        else:
            message = f"unexpected error: {type(exc).__name__}: {one_line(exc)}"
        if kept:
            raise SetupError(f"{message}; rolled back, except for what is kept: "
                             f"{', '.join(kept)}") from exc
        if made:
            raise SetupError(f"{message}; rolled back, nothing was left") from exc
        raise SetupError(message) from exc
    for fd in fds.values():
        os.close(fd)
    lines = [f"created: {target} ({'the empty folder' if existed else 'a new folder'})"]
    lines += [f"created: {target / rel}" for rel, _data in files]
    lines.append(f"created: git repository at {target / '.git'}")
    lines.append("no commit was made and no remote was added; "
                 "setup records nothing about this folder, and doctor and uninstall never touch it")
    return "".join(line + "\n" for line in lines), [], 0


# ---------------------------------------------------------------------------
# Command line


class Parser(argparse.ArgumentParser):
    def error(self, message: str):  # type: ignore[override]
        raise SetupError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(description="Plan, apply or remove the default-tier setup.")
    parser.add_argument("verb",
                        choices=("plan", "apply", "remove", "doctor", "ledger-init",
                                 "instance-plan", "instance-create"))
    parser.add_argument("--home", required=True, help="absolute path of the home folder")
    parser.add_argument("--operator", default=None,
                        help="the ledger operator name (plan, apply and ledger-init)")
    parser.add_argument("--harness", default=DEFAULT_HARNESS,
                        help="the ledger harness name (plan, apply and ledger-init)")
    parser.add_argument("--approve", action="append", default=[], metavar="ID",
                        help="approve one optional settings proposal by id")
    parser.add_argument("--approve-all", action="store_true",
                        help="approve every optional settings proposal")
    parser.add_argument("--path", default=None,
                        help="the absolute path of the instance folder "
                             "(instance-plan and instance-create)")
    return parser


def resolve_home(value: str) -> Path:
    if not os.path.isabs(value):
        raise SetupError("home must be an absolute path")
    home = Path(os.path.realpath(value))
    if not home.is_dir():
        raise SetupError(f"home is not a folder: {home}")
    return home


def run(argv: list[str]) -> tuple[str, list[str], int]:
    """(standard output, warnings, exit status)."""
    args = build_parser().parse_args(argv)
    home = resolve_home(vars(args)["home"])
    if args.verb == "remove":
        out, warnings = remove_verb(home)
        return out, warnings, 0
    if args.verb == "doctor":
        return doctor_verb(home)
    if args.verb == "instance-plan":
        return instance_plan_verb(home, args.path)
    if args.verb == "instance-create":
        return instance_create_verb(home, args.path)
    if not args.operator:
        if args.verb == "ledger-init":
            raise SetupError("--operator is required for ledger-init")
        raise SetupError("--operator is required for plan and apply")
    harness = args.harness or None
    if args.verb == "ledger-init":
        return ledger_init_verb(home, args.operator, harness)
    if args.verb == "plan":
        return format_plan(home, args.operator, harness), [], 0
    out, warnings = apply_verb(home, args.operator, harness, args.approve, args.approve_all)
    return out, warnings, 0


def main(argv: list[str] | None = None) -> int:
    signal.signal(signal.SIGINT, signal.SIG_DFL)
    os.umask(0o077)
    try:
        out, warnings, status = run(sys.argv[1:] if argv is None else argv)
    except SetupError as exc:
        print(f"{PREFIX} {one_line(exc)}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"{PREFIX} unexpected error: {type(exc).__name__}: {one_line(exc)}",
              file=sys.stderr)
        return 1
    sys.stdout.write(out)
    for line in warnings:
        print(f"{PREFIX} {line}", file=sys.stderr)
    return status


if __name__ == "__main__":
    sys.exit(main())

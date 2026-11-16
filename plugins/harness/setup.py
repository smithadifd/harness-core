#!/usr/bin/env python3
"""Plan, apply and remove the default-tier files and settings proposals.

The setup skill drives this script. Plan writes nothing. Apply adds only
absent keys and appends deny rules that are not already present, never
changing, removing or reordering an existing entry, and records each
addition with its position and neighbours. Remove takes out exactly what
apply recorded. A rule the person has since moved or duplicated is kept
and named. Nothing that grants is ever proposed. Apply is all-or-nothing:
every precondition is checked before the first write, and a later failure
rolls back every change this run made: the files, the settings and the setup
record. The record is written before `claude plugin configure` runs, so a
record that cannot be written fails before configure and leaves the home as it
was. A configure that has succeeded is not undone, and does not need to be,
because nothing that can fail runs after it. Apply refuses to overwrite a
settings.json that changed since it was read. Apply keeps the exact bytes it
wrote to each file; rollback leaves a file whose bytes have changed since,
naming it for a manual check, and remove does the same with the record.
Settings are written to a temp file in the same folder and moved into place
with os.replace. Apply and remove each hold an exclusive lock in the home's
.claude folder from their first read to their last write, rollback included;
a run that finds the lock held refuses by name.
"""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


PLUGIN_ROOT = Path(__file__).resolve().parent
FILES_DIR = PLUGIN_ROOT / "files"
RULES_SOURCE = FILES_DIR / "harness.md"
OVERRIDE_SOURCE = FILES_DIR / "harness-local.md"

RULES_REL = ".claude/rules/harness.md"
OVERRIDE_REL = ".claude/rules/harness-local.md"
RECORD_REL = ".claude/plugins/harness-core/harness/setup-record.json"
SETTINGS_REL = ".claude/settings.json"
LOCK_REL = ".claude/harness-setup.lock"
LOCKED = f"another harness setup run holds the lock {LOCK_REL}"
RECORD_DIGEST_KEY = "record_sha256"
RECORD_DIGEST_DRAFT = "0" * 64

LEDGER_PLUGIN = "ledger-hooks@harness-core"
DEFAULT_HARNESS = "claude-code"

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

GRANTING_KEYS = frozenset({
    "allow",
    "additionalDirectories",
    "extraDirectories",
    "autoAllow",
    "autoAllowBashIfSandboxed",
})

PREFIX = "setup:"


class SetupError(RuntimeError):
    """A named failure of plan, apply or remove (exit 1)."""


def database_path(home: Path) -> Path:
    return home / ".local" / "share" / "ledger" / "ledger.db"


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def load_source(path: Path) -> str:
    if not path.is_file():
        raise SetupError(f"{path.name} is missing from the plugin files folder")
    return path.read_text(encoding="utf-8")


def pairs(items):
    seen = {}
    for key, value in items:
        if key in seen:
            raise ValueError(f"duplicate key {key!r}")
        seen[key] = value
    return seen


def read_named(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc


def decode_text(raw: bytes, path: Path) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise SetupError(f"{path} is not valid UTF-8 ({exc})") from exc


def load_json_object(path: Path) -> dict[str, Any]:
    return parse_json_object(read_named(path), path)


def parse_json_object(raw: bytes, path: Path) -> dict[str, Any]:
    text = decode_text(raw, path)
    try:
        data = json.loads(text, object_pairs_hook=pairs)
    except ValueError as exc:
        raise SetupError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} is not a JSON object")
    return data


def dump_json(data: dict[str, Any], *, indent: int | str = 2,
              ensure_ascii: bool = False) -> str:
    return json.dumps(data, indent=indent, ensure_ascii=ensure_ascii) + "\n"


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


def lexists(path: Path) -> bool:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def ledger_values(home: Path, operator: str, harness: str | None) -> dict[str, str]:
    values = {
        "database": str(database_path(home)),
        "operator": operator,
    }
    if harness:
        values["harness"] = harness
    return values


def optional_items() -> list[tuple[str, str]]:
    """(id, exact text to be added) for each optional settings proposal."""
    items = [("sandbox", dump_json({"sandbox": dict(SANDBOX_VALUE)}).rstrip("\n"))]
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
    if proposal_is_granting("key", "sandbox", SANDBOX_VALUE):
        found.append("sandbox")
    if any(key in GRANTING_KEYS for key in SANDBOX_VALUE):
        found.append("sandbox nested grant")
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


def comment_only(text: str) -> bool:
    for line in text.splitlines():
        if line.strip() == "":
            continue
        if not line.startswith("#"):
            return False
    return True


def resolve_home(value: str) -> Path:
    home = Path(value)
    if not home.is_absolute():
        raise SetupError("home must be an absolute path")
    try:
        home = home.resolve()
    except OSError as exc:
        raise SetupError(f"home cannot be resolved: {exc}") from exc
    if not home.is_dir():
        raise SetupError("home is not a directory")
    return home


def rel_path(home: Path, relative: str) -> Path:
    return home.joinpath(*relative.split("/"))


def format_plan(home: Path, operator: str, harness: str | None) -> str:
    rules = load_source(RULES_SOURCE)
    override = load_source(OVERRIDE_SOURCE)
    values = ledger_values(home, operator, harness)
    lines = [
        f"FILE {RULES_REL}",
        "----",
        rules.rstrip("\n"),
        "----",
        f"FILE {OVERRIDE_REL}",
        "----",
        override.rstrip("\n"),
        "----",
        f"FILE {RECORD_REL}",
        "record of this apply",
        "LEDGER",
        f"claude plugin configure {LEDGER_PLUGIN} --values-stdin",
        "----",
        json.dumps(values, indent=2),
        "----",
    ]
    for ident, text in optional_items():
        lines.extend([f"OPTIONAL {ident}", "----", text, "----"])
    return "\n".join(lines) + "\n"


def write_new_file(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def prune_empty(start: Path, stop: Path) -> None:
    parent = start
    while parent != stop:
        if not parent.is_dir():
            break
        try:
            next(parent.iterdir())
            break
        except StopIteration:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
        except OSError:
            break


def unlink_and_prune(path: Path, stop: Path) -> None:
    path.unlink()
    prune_empty(path.parent, stop)


def missing_parents(path: Path, stop: Path) -> list[Path]:
    missing: list[Path] = []
    parent = path.parent
    while parent != stop:
        if lexists(parent):
            break
        missing.append(parent)
        nxt = parent.parent
        if nxt == parent:
            break
        parent = nxt
    missing.reverse()
    return missing


def note_created_dirs(path: Path, home: Path, created_dirs: list[str]) -> None:
    for parent in missing_parents(path, home):
        rel = parent.relative_to(home).as_posix()
        if rel not in created_dirs:
            created_dirs.append(rel)


def current_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except OSError:
        return None


def restore_apply(home: Path, originals: dict[Path, bytes | None],
                  created_dirs: list[str],
                  written: dict[Path, bytes]) -> list[Path]:
    """Undo this run's changes. Each file apply wrote is compared against the
    exact bytes apply wrote to it: one that no longer holds those bytes was
    changed by someone else since, so it is left as it is and returned for
    the caller to name. A file that held bytes before this run and that this
    run never replaced is not touched."""
    kept: list[Path] = []
    for path, prior in reversed(list(originals.items())):
        expected = written.get(path)
        if expected is None and prior is not None:
            continue
        if expected is not None and current_bytes(path) != expected:
            kept.append(path)
            continue
        if prior is None:
            if lexists(path):
                try:
                    path.unlink()
                except OSError:
                    pass
        else:
            try:
                write_settings_bytes(path, prior)
            except (OSError, SetupError):
                pass
    prune_created(home, created_dirs)
    return kept


def with_kept(exc: SetupError, home: Path, kept: list[Path]) -> SetupError:
    if not kept:
        return exc
    names = ", ".join(path.relative_to(home).as_posix() for path in kept)
    return SetupError(
        f"{exc}; changed after apply wrote it, left as it is, "
        f"needs a manual check: {names}")


def prune_created(home: Path, created_dirs: list) -> None:
    rels = [item for item in created_dirs if isinstance(item, str)]
    for rel in sorted(rels, key=lambda item: item.count("/"), reverse=True):
        directory = rel_path(home, rel)
        try:
            directory.rmdir()
        except OSError:
            pass


def which_claude() -> str:
    found = shutil.which("claude")
    if not found:
        raise SetupError("claude is not on PATH")
    return found


def configure_ledger(claude: str, values: dict[str, str]) -> None:
    payload = json.dumps(values)
    done = subprocess.run(
        [claude, "plugin", "configure", LEDGER_PLUGIN, "--values-stdin"],
        input=payload,
        capture_output=True,
        text=True,
    )
    if done.returncode != 0:
        detail = (done.stderr or done.stdout or f"exit {done.returncode}").strip()
        raise SetupError(f"claude plugin configure failed: {detail}")


def read_current(path: Path) -> bytes | None:
    """The bytes at path, or None when nothing is there."""
    if not lexists(path):
        return None
    return read_named(path)


def load_settings(path: Path) -> tuple[dict[str, Any], bool, int | str, bytes | None]:
    raw = read_current(path)
    if raw is None:
        return {}, False, 2, None
    data = parse_json_object(raw, path)
    return data, True, detect_indent(raw.decode("utf-8")), raw


def refuse_settings_link(path: Path) -> None:
    """os.replace would put a file where the link is: refuse instead."""
    if path.is_symlink():
        raise SetupError(
            f"{SETTINGS_REL} is a symbolic link; it is not written through")


def new_file_mode() -> int:
    mask = os.umask(0)
    os.umask(mask)
    return 0o666 & ~mask


def write_settings_bytes(path: Path, data: bytes) -> None:
    """Write data to a temp file in path's folder, then os.replace it into
    place, so a failure before the replace leaves the old bytes whole. The
    file keeps its mode."""
    refuse_settings_link(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        mode = stat.S_IMODE(os.lstat(path).st_mode)
    except FileNotFoundError:
        mode = new_file_mode()
    fd, temp = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


def save_settings(path: Path, data: dict[str, Any],
                  indent: int | str = 2) -> bytes:
    payload = dump_json(data, indent=indent).encode("utf-8")
    write_settings_bytes(path, payload)
    return payload


def approved_ids(approve: list[str], approve_all: bool) -> set[str]:
    known = {ident for ident, _text in optional_items()}
    if approve_all:
        return known
    found: set[str] = set()
    for ident in approve:
        if ident not in known:
            raise SetupError(f"unknown approval {ident!r}")
        found.add(ident)
    return found


def apply_sandbox(settings: dict[str, Any]) -> dict[str, Any] | None:
    if "sandbox" in settings:
        return None
    settings["sandbox"] = dict(SANDBOX_VALUE)
    return {"kind": "key", "name": "sandbox", "value": dict(SANDBOX_VALUE)}


def apply_deny_rules(settings: dict[str, Any], rules: list[str]) -> list[dict[str, Any]]:
    recorded: list[dict[str, Any]] = []
    if not rules:
        return recorded
    permissions = settings.get("permissions")
    if permissions is not None and not isinstance(permissions, dict):
        return recorded
    deny: list[Any] | None
    if permissions is None:
        deny = None
    else:
        deny = permissions.get("deny")
        if deny is not None and not isinstance(deny, list):
            return recorded
    created_permissions = False
    created_deny = False
    for rule in rules:
        if deny is not None and rule in deny:
            continue
        if permissions is None:
            permissions = {}
            settings["permissions"] = permissions
            created_permissions = True
        if deny is None:
            deny = []
            permissions["deny"] = deny
            created_deny = True
        before = deny[-1] if deny else None
        deny.append(rule)
        recorded.append({
            "kind": "deny",
            "value": rule,
            "index": len(deny) - 1,
            "before": before,
            "after": None,
            "created_permissions": created_permissions,
            "created_deny": created_deny,
        })
        created_permissions = False
        created_deny = False
    return recorded


class SetupLock:
    """The exclusive lock one apply or remove run holds from its first read
    to its last write, rollback included."""

    def __init__(self, path: Path, fd: int, made_dir: bool) -> None:
        self.path = path
        self.fd = fd
        self.made_dir = made_dir
        self.prune_dir = made_dir

    def release(self) -> None:
        # Unlink while still holding the lock, so a run that opened this
        # file meanwhile finds it gone once it gets the lock, and refuses.
        try:
            os.unlink(self.path)
        except OSError:
            pass
        os.close(self.fd)
        if self.prune_dir:
            try:
                self.path.parent.rmdir()
            except OSError:
                pass


def claim_lock(fd: int, path: Path) -> None:
    """Take the lock on fd without waiting, or refuse by name. The file
    locked must still be the one at path: a run that opened a lock file
    another run has since released and unlinked does not hold the lock."""
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise SetupError(LOCKED) from exc
    except OSError as exc:
        raise SetupError(f"cannot take the setup lock: {exc}") from exc
    held = os.fstat(fd)
    try:
        now = os.lstat(path)
    except OSError:
        now = None
    if now is None or (now.st_dev, now.st_ino) != (held.st_dev, held.st_ino):
        fcntl.flock(fd, fcntl.LOCK_UN)
        raise SetupError(LOCKED)


def take_lock(home: Path) -> SetupLock:
    path = rel_path(home, LOCK_REL)
    made_dir = False
    if not lexists(path.parent):
        try:
            path.parent.mkdir()
            made_dir = True
        except FileExistsError:
            pass
        except OSError as exc:
            raise SetupError(f"cannot take the setup lock: {exc}") from exc
    flags = os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    try:
        fd = os.open(path, flags, 0o600)
    except OSError as exc:
        if made_dir:
            try:
                path.parent.rmdir()
            except OSError:
                pass
        raise SetupError(f"cannot take the setup lock: {exc}") from exc
    try:
        claim_lock(fd, path)
    except SetupError:
        os.close(fd)
        raise
    return SetupLock(path, fd, made_dir)


@contextlib.contextmanager
def setup_lock(home: Path):
    lock = take_lock(home)
    try:
        yield lock
    finally:
        lock.release()


def record_bytes(record: dict[str, Any]) -> bytes:
    """The record's exact bytes. It carries the digest of those same bytes
    taken with the digest field set to a fixed draft value."""
    body = dict(record)
    body[RECORD_DIGEST_KEY] = RECORD_DIGEST_DRAFT
    body[RECORD_DIGEST_KEY] = digest(dump_json(body).encode("utf-8"))
    return dump_json(body).encode("utf-8")


def record_intact(raw: bytes) -> bool:
    """True when raw is byte for byte what apply wrote."""
    try:
        data = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
    except (UnicodeDecodeError, ValueError):
        return False
    if not isinstance(data, dict) or RECORD_DIGEST_KEY not in data:
        return False
    return record_bytes(data) == raw


def plan_verb(home: Path, operator: str, harness: str | None) -> str:
    grants = granting_proposals()
    if grants:
        raise SetupError("proposals grant: " + ", ".join(grants))
    override = load_source(OVERRIDE_SOURCE)
    if not comment_only(override):
        raise SetupError("override starter has a line that is not a comment")
    return format_plan(home, operator, harness)


def apply_verb(home: Path, operator: str, harness: str | None,
               approve: list[str], approve_all: bool) -> str:
    with setup_lock(home) as lock:
        return apply_locked(home, operator, harness, approve, approve_all, lock)


def apply_locked(home: Path, operator: str, harness: str | None,
                 approve: list[str], approve_all: bool, lock: SetupLock) -> str:
    grants = granting_proposals()
    if grants:
        raise SetupError("proposals grant: " + ", ".join(grants))
    override_text = load_source(OVERRIDE_SOURCE)
    if not comment_only(override_text):
        raise SetupError("override starter has a line that is not a comment")
    rules_text = load_source(RULES_SOURCE)
    rules_path = rel_path(home, RULES_REL)
    override_path = rel_path(home, OVERRIDE_REL)
    record_path = rel_path(home, RECORD_REL)
    settings_path = rel_path(home, SETTINGS_REL)
    if lexists(record_path):
        raise SetupError("already applied; run remove first")
    if lexists(rules_path):
        raise SetupError(f"rules default already exists: {RULES_REL}")
    claude = which_claude()
    ids = approved_ids(approve, approve_all)
    if ids:
        refuse_settings_link(settings_path)
    settings, settings_existed, settings_indent, settings_raw = load_settings(
        settings_path)

    messages: list[str] = []
    files: list[dict[str, Any]] = []
    # The lock made the .claude folder when the home had none: it is this
    # run's to take out again, on rollback and on remove.
    created_dirs: list[str] = [".claude"] if lock.made_dir else []
    originals: dict[Path, bytes | None] = {}
    written: dict[Path, bytes] = {}

    def snapshot(path: Path) -> None:
        if path in originals:
            return
        note_created_dirs(path, home, created_dirs)
        if lexists(path):
            originals[path] = path.read_bytes()
        else:
            originals[path] = None

    try:
        snapshot(rules_path)
        rules_bytes = rules_text.encode("utf-8")
        write_new_file(rules_path, rules_text)
        written[rules_path] = rules_bytes
        files.append({
            "path": RULES_REL,
            "sha256": digest(rules_bytes),
            "ours": True,
        })

        if lexists(override_path):
            messages.append(f"kept: {OVERRIDE_REL} (already existed)")
            files.append({"path": OVERRIDE_REL, "ours": False})
        else:
            snapshot(override_path)
            override_bytes = override_text.encode("utf-8")
            write_new_file(override_path, override_text)
            written[override_path] = override_bytes
            files.append({
                "path": OVERRIDE_REL,
                "sha256": digest(override_bytes),
                "ours": True,
            })

        settings_created = not settings_existed
        settings_records: list[dict[str, Any]] = []
        if "sandbox" in ids:
            added = apply_sandbox(settings)
            if added is not None:
                settings_records.append(added)
        deny_ids = [ident[len("deny:"):] for ident in ids if ident.startswith("deny:")]
        deny_wanted = [rule for rule in DENY_RULES if rule in deny_ids]
        settings_records.extend(apply_deny_rules(settings, deny_wanted))
        settings_written = bool(settings_records)
        if settings_written:
            if read_current(settings_path) != settings_raw:
                raise SetupError(
                    f"{SETTINGS_REL} changed while apply was running; "
                    "it was not written")
            snapshot(settings_path)
            written[settings_path] = save_settings(
                settings_path, settings, indent=settings_indent)

        values = ledger_values(home, operator, harness)
        snapshot(record_path)
        record = {
            "version": 1,
            "files": files,
            "settings": {
                "created": settings_created and settings_written,
                "existed": settings_existed,
                "items": settings_records,
                "sha256": (digest(written[settings_path])
                           if settings_written else None),
            },
            "created_dirs": list(created_dirs),
            "ledger_database": str(database_path(home)),
            "configure": {"plugin": LEDGER_PLUGIN, "values": values},
        }
        record_raw = record_bytes(record)
        write_new_file(record_path, record_raw.decode("utf-8"))
        written[record_path] = record_raw
        configure_ledger(claude, values)
        messages.append(f"applied: {RECORD_REL}")
        return "\n".join(messages) + ("\n" if messages else "")
    except SetupError as exc:
        kept = restore_apply(home, originals, created_dirs, written)
        if kept:
            raise with_kept(exc, home, kept) from exc
        raise
    except OSError as exc:
        kept = restore_apply(home, originals, created_dirs, written)
        raise with_kept(SetupError(f"apply failed: {exc}"), home, kept) from exc


def neighbours_match(deny: list[Any], item: dict[str, Any]) -> bool:
    index = item["index"]
    if not isinstance(index, int) or index < 0 or index >= len(deny):
        return False
    if deny[index] != item["value"]:
        return False
    before = deny[index - 1] if index > 0 else None
    after = deny[index + 1] if index + 1 < len(deny) else None
    return before == item.get("before") and after == item.get("after")


def remove_settings(home: Path, record: dict[str, Any], messages: list[str]) -> None:
    info = record.get("settings") or {}
    items = list(info.get("items") or [])
    settings_path = rel_path(home, SETTINGS_REL)
    if not items:
        return
    if not settings_path.is_file():
        messages.append(f"kept: {SETTINGS_REL} (missing)")
        return
    raw = read_named(settings_path)
    if digest(raw) != info.get("sha256"):
        messages.append(
            f"needs a manual check: {SETTINGS_REL} (changed after apply wrote "
            "it; only entries still at their recorded place were taken out)")
    indent = detect_indent(decode_text(raw, settings_path))
    settings = parse_json_object(raw, settings_path)
    deny_items = [item for item in items if item.get("kind") == "deny"]
    key_items = [item for item in items if item.get("kind") == "key"]
    permissions = settings.get("permissions")
    deny = permissions.get("deny") if isinstance(permissions, dict) else None
    for item in sorted(deny_items, key=lambda row: row.get("index", -1), reverse=True):
        value = item.get("value")
        if isinstance(deny, list) and neighbours_match(deny, item):
            del deny[item["index"]]
            continue
        messages.append(
            f"kept: permissions.deny rule {value!r} (moved or duplicated)")
    for item in key_items:
        name = item.get("name")
        value = item.get("value")
        if name in settings and settings[name] == value:
            del settings[name]
        elif name in settings:
            messages.append(f"kept: settings key {name} (value changed)")
        else:
            messages.append(f"kept: settings key {name} (missing)")
    if isinstance(permissions, dict):
        deny_now = permissions.get("deny")
        if isinstance(deny_now, list) and not deny_now:
            created_deny = any(item.get("created_deny") for item in deny_items)
            if created_deny:
                del permissions["deny"]
        if not permissions:
            created_permissions = any(
                item.get("created_permissions") for item in deny_items)
            if created_permissions:
                del settings["permissions"]
    if info.get("created") and settings == {}:
        settings_path.unlink()
        return
    if items:
        save_settings(settings_path, settings, indent=indent)


def remove_verb(home: Path) -> str:
    with setup_lock(home) as lock:
        return remove_locked(home, lock)


def remove_locked(home: Path, lock: SetupLock) -> str:
    record_path = rel_path(home, RECORD_REL)
    if not record_path.is_file():
        raise SetupError(f"no record at {RECORD_REL}")
    raw = read_named(record_path)
    if not record_intact(raw):
        raise SetupError(
            f"{RECORD_REL} changed after apply wrote it, left as it is, "
            "needs a manual check")
    record = parse_json_object(raw, record_path)
    messages: list[str] = []
    created_dirs = list(record.get("created_dirs") or [])
    if ".claude" in created_dirs:
        lock.prune_dir = True
    if (record.get("settings") or {}).get("items"):
        refuse_settings_link(rel_path(home, SETTINGS_REL))
    for item in record.get("files") or []:
        relative = item.get("path")
        if not isinstance(relative, str):
            continue
        path = rel_path(home, relative)
        if not item.get("ours"):
            messages.append(f"kept: {relative} (already existed)")
            continue
        if not path.is_file():
            messages.append(f"kept: {relative} (missing)")
            continue
        current = digest(path.read_bytes())
        if current != item.get("sha256"):
            messages.append(f"kept: {relative} (edited)")
            continue
        path.unlink()
    remove_settings(home, record, messages)
    db = record.get("ledger_database")
    if db:
        messages.append(f"kept: ledger database at {db} (person's data)")
    record_path.unlink()
    prune_created(home, created_dirs)
    return "\n".join(messages) + ("\n" if messages else "")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Plan, apply or remove the default-tier setup.")
    parser.add_argument("verb", choices=("plan", "apply", "remove"))
    parser.add_argument("--home", required=True, help="absolute fixture or user home")
    parser.add_argument("--operator", default=None)
    parser.add_argument("--harness", default=DEFAULT_HARNESS)
    parser.add_argument("--approve", action="append", default=[], metavar="ID")
    parser.add_argument("--approve-all", action="store_true",
                        help="approve every optional settings proposal")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    try:
        home = resolve_home(vars(args)["home"])
        if args.verb in ("plan", "apply"):
            if not args.operator:
                print(f"{PREFIX} --operator is required for plan and apply", file=sys.stderr)
                return 2
            harness = args.harness or None
            if args.verb == "plan":
                sys.stdout.write(plan_verb(home, args.operator, harness))
            else:
                sys.stdout.write(apply_verb(
                    home, args.operator, harness, args.approve, args.approve_all))
        else:
            sys.stdout.write(remove_verb(home))
    except SetupError as exc:
        print(f"{PREFIX} {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

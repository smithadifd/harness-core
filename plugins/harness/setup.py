#!/usr/bin/env python3
"""Plan, apply and remove the default-tier files and settings proposals.

The setup skill drives this script. Plan writes nothing. Apply adds only
absent keys and appends deny rules that are not already present, never
changing, removing or reordering an existing entry, and records each
addition with its position and neighbours. Remove takes out exactly what
apply recorded. A rule the person has since moved or duplicated is kept
and named. Nothing that grants is ever proposed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
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


def load_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SetupError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(text, object_pairs_hook=pairs)
    except ValueError as exc:
        raise SetupError(f"{path} is not valid JSON ({exc})") from exc
    if not isinstance(data, dict):
        raise SetupError(f"{path} is not a JSON object")
    return data


def dump_json(data: dict[str, Any]) -> str:
    return json.dumps(data, indent=2) + "\n"


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


def load_settings(path: Path) -> tuple[dict[str, Any], bool]:
    if not path.is_file():
        return {}, False
    return load_json_object(path), True


def save_settings(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_json(data), encoding="utf-8")


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
    permissions = settings.get("permissions")
    created_permissions = False
    if permissions is None:
        permissions = {}
        settings["permissions"] = permissions
        created_permissions = True
    elif not isinstance(permissions, dict):
        return recorded
    deny = permissions.get("deny")
    created_deny = False
    if deny is None:
        deny = []
        permissions["deny"] = deny
        created_deny = True
    elif not isinstance(deny, list):
        return recorded
    for rule in rules:
        if rule in deny:
            continue
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
    if not recorded and created_permissions and not permissions:
        del settings["permissions"]
    return recorded


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
    if record_path.is_file():
        raise SetupError("already applied; run remove first")
    if rules_path.exists():
        raise SetupError(f"rules default already exists: {RULES_REL}")
    claude = which_claude()
    ids = approved_ids(approve, approve_all)
    messages: list[str] = []
    files: list[dict[str, Any]] = []

    write_new_file(rules_path, rules_text)
    files.append({
        "path": RULES_REL,
        "sha256": digest(rules_text.encode("utf-8")),
        "ours": True,
    })

    if override_path.exists():
        messages.append(f"kept: {OVERRIDE_REL} (already existed)")
        files.append({"path": OVERRIDE_REL, "ours": False})
    else:
        write_new_file(override_path, override_text)
        files.append({
            "path": OVERRIDE_REL,
            "sha256": digest(override_text.encode("utf-8")),
            "ours": True,
        })

    settings, settings_existed = load_settings(settings_path)
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
        save_settings(settings_path, settings)
        added_names = {item.get("name") for item in settings_records if item.get("kind") == "key"}
        if any(name in GRANTING_KEYS for name in added_names):
            raise SetupError("apply would grant: " + ", ".join(sorted(n for n in added_names if n in GRANTING_KEYS)))

    values = ledger_values(home, operator, harness)
    configure_ledger(claude, values)

    record = {
        "version": 1,
        "files": files,
        "settings": {
            "created": settings_created and settings_written,
            "existed": settings_existed,
            "items": settings_records,
        },
        "ledger_database": str(database_path(home)),
        "configure": {"plugin": LEDGER_PLUGIN, "values": values},
    }
    write_new_file(record_path, dump_json(record))
    messages.append(f"applied: {RECORD_REL}")
    return "\n".join(messages) + ("\n" if messages else "")


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
    settings = load_json_object(settings_path)
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
        unlink_and_prune(settings_path, home)
        return
    if items:
        save_settings(settings_path, settings)


def remove_verb(home: Path) -> str:
    record_path = rel_path(home, RECORD_REL)
    if not record_path.is_file():
        raise SetupError(f"no record at {RECORD_REL}")
    record = load_json_object(record_path)
    messages: list[str] = []
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
        unlink_and_prune(path, home)
    remove_settings(home, record, messages)
    db = record.get("ledger_database")
    if db:
        messages.append(f"kept: ledger database at {db} (person's data)")
    unlink_and_prune(record_path, home)
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
        home = resolve_home(args.home)
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

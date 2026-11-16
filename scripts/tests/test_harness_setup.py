"""Fixture-home tests of the harness plugin's plan, apply and remove script.

Covers design § 6 (override file and settings proposals) and § 8 (uninstall),
and the CI parts of § 7 tests 2 and 4. Homes are pytest tmp_path folders.
A stub `claude` records configure arguments. Nothing granting is proposed.
"""
from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugins" / "harness" / "setup.py"
RULES_SOURCE = ROOT / "plugins" / "harness" / "files" / "harness.md"
OVERRIDE_SOURCE = ROOT / "plugins" / "harness" / "files" / "harness-local.md"

_spec = importlib.util.spec_from_file_location("harness_setup_under_test", SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)

OPERATOR = "operator"
HARNESS = "claude-code"
CLAUDE_STUB = """\
#!/usr/bin/env python3
import json, sys
from pathlib import Path
log = Path(__file__).with_name("claude-log.json")
record = {"argv": sys.argv[1:], "stdin": sys.stdin.read()}
log.write_text(json.dumps(record), encoding="utf-8")
"""


def listing(home: Path) -> dict[str, str]:
    found: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(home):
        base = Path(dirpath)
        if not dirnames and not filenames:
            rel = base.relative_to(home).as_posix()
            if rel != ".":
                found[rel + "/"] = "dir"
        for name in filenames:
            path = base / name
            rel = path.relative_to(home).as_posix()
            if path.is_symlink():
                found[rel] = "link:" + os.readlink(path)
                continue
            found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def install_claude(folder: Path) -> Path:
    bin_dir = folder / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(CLAUDE_STUB, encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def run(verb: str, home: Path, *extra: str, path_prefix: Path | None = None,
        operator: str | None = OPERATOR, harness: str | None = HARNESS) -> subprocess.CompletedProcess[str]:
    env = {key: value for key, value in os.environ.items() if key != "CLAUDE_CONFIG_DIR"}
    if path_prefix is not None:
        env["PATH"] = str(path_prefix) + os.pathsep + env.get("PATH", "")
    cmd = [sys.executable, str(SCRIPT), verb, "--home", str(home)]
    if verb in ("plan", "apply") and operator is not None:
        cmd.extend(["--operator", operator])
    if verb in ("plan", "apply") and harness is not None:
        cmd.extend(["--harness", harness])
    cmd.extend(extra)
    return subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=30)


def apply_all(home: Path, bin_dir: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return run("apply", home, "--approve-all", *extra, path_prefix=bin_dir)


def configure_log(bin_dir: Path) -> dict:
    return json.loads((bin_dir / "claude-log.json").read_text(encoding="utf-8"))


def settings_of(home: Path) -> dict:
    return json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))


def record_of(home: Path) -> dict:
    return json.loads(
        (home / ".claude" / "plugins" / "harness-core" / "harness" / "setup-record.json")
        .read_text(encoding="utf-8"))


# § 6: plan prints the two files, the three ledger options, and each optional
# settings key as the exact text to be added. Plan writes nothing.

def test_plan_writes_nothing(tmp_path):
    before = listing(tmp_path)
    done = run("plan", tmp_path)
    assert done.returncode == 0, done.stderr
    assert listing(tmp_path) == before


def test_plan_prints_two_files_as_exact_text(tmp_path):
    done = run("plan", tmp_path)
    assert done.returncode == 0, done.stderr
    rules = RULES_SOURCE.read_text(encoding="utf-8").rstrip("\n")
    override = OVERRIDE_SOURCE.read_text(encoding="utf-8").rstrip("\n")
    assert f"FILE {mod.RULES_REL}\n----\n{rules}\n----\n" in done.stdout
    assert f"FILE {mod.OVERRIDE_REL}\n----\n{override}\n----\n" in done.stdout
    assert f"FILE {mod.RECORD_REL}\nrecord of this apply\n" in done.stdout


def test_plan_prints_three_ledger_options_as_exact_text(tmp_path):
    done = run("plan", tmp_path)
    assert done.returncode == 0, done.stderr
    values = mod.ledger_values(tmp_path.resolve(), OPERATOR, HARNESS)
    assert values["database"] == str(tmp_path.resolve() / ".local" / "share" / "ledger" / "ledger.db")
    assert values["operator"] == OPERATOR
    assert values["harness"] == HARNESS
    assert json.dumps(values, indent=2) in done.stdout
    assert f"claude plugin configure {mod.LEDGER_PLUGIN} --values-stdin" in done.stdout


def test_plan_prints_each_optional_key_as_exact_text(tmp_path):
    done = run("plan", tmp_path)
    assert done.returncode == 0, done.stderr
    for ident, text in mod.optional_items():
        assert f"OPTIONAL {ident}\n----\n{text}\n----\n" in done.stdout
    assert "OPTIONAL sandbox" in done.stdout
    for rule in mod.DENY_RULES:
        assert f"OPTIONAL deny:{rule}" in done.stdout


def test_plan_never_prints_a_grant(tmp_path):
    done = run("plan", tmp_path)
    assert done.returncode == 0, done.stderr
    lower = done.stdout.lower()
    assert "allow(" not in lower
    assert "additionaldirectories" not in lower
    assert "extradirectories" not in lower
    assert "autoallow" not in lower


# § 6: apply places files, skips an existing override, refuses an existing
# rules default, adds only absent keys, appends deny rules, records neighbours.

def test_apply_places_both_files_when_absent(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    assert (home / ".claude" / "rules" / "harness.md").read_text(encoding="utf-8") == (
        RULES_SOURCE.read_text(encoding="utf-8"))
    assert (home / ".claude" / "rules" / "harness-local.md").read_text(encoding="utf-8") == (
        OVERRIDE_SOURCE.read_text(encoding="utf-8"))


def test_apply_skips_existing_override_and_records_not_ours(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    override = home / ".claude" / "rules" / "harness-local.md"
    override.parent.mkdir(parents=True)
    override.write_text("# mine\n", encoding="utf-8")
    before = override.read_bytes()
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    assert override.read_bytes() == before
    assert "kept: .claude/rules/harness-local.md (already existed)" in done.stdout
    record = record_of(home)
    entry = next(item for item in record["files"] if item["path"] == mod.OVERRIDE_REL)
    assert entry["ours"] is False
    assert "sha256" not in entry


def test_apply_stops_when_rules_default_exists_and_writes_nothing(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    rules = home / ".claude" / "rules" / "harness.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("# already\n", encoding="utf-8")
    seeded = home / ".claude" / "skills" / "config.json"
    seeded.parent.mkdir(parents=True)
    seeded.write_text("{}\n", encoding="utf-8")
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "rules default already exists" in done.stderr
    assert listing(home) == before
    assert not (home / ".claude" / "plugins").exists()


def test_apply_adds_only_absent_keys_and_never_changes_a_present_key(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = {
        "sandbox": {"enabled": False},
        "model": "kept",
        "permissions": {"deny": ["Read(seeded)"]},
    }
    settings.write_text(json.dumps(original, indent=2) + "\n", encoding="utf-8")
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    data = settings_of(home)
    assert data["sandbox"] == {"enabled": False}
    assert data["model"] == "kept"
    assert data["permissions"]["deny"][0] == "Read(seeded)"
    assert data["permissions"]["deny"][1:] == list(mod.DENY_RULES)


def test_apply_appends_deny_rules_without_reordering_and_skips_present(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    already = mod.DENY_RULES[0]
    settings.write_text(json.dumps({
        "permissions": {"deny": ["Read(seeded)", already, "Read(other)"]},
    }) + "\n", encoding="utf-8")
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    deny = settings_of(home)["permissions"]["deny"]
    assert deny[:3] == ["Read(seeded)", already, "Read(other)"]
    assert already not in deny[3:]
    assert deny[3:] == [rule for rule in mod.DENY_RULES if rule != already]


def test_apply_records_position_and_neighbours(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "permissions": {"deny": ["Read(seeded)"]},
    }) + "\n", encoding="utf-8")
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    items = [item for item in record_of(home)["settings"]["items"] if item["kind"] == "deny"]
    assert items[0]["value"] == mod.DENY_RULES[0]
    assert items[0]["index"] == 1
    assert items[0]["before"] == "Read(seeded)"
    assert items[0]["after"] is None
    assert items[1]["index"] == 2
    assert items[1]["before"] == mod.DENY_RULES[0]
    assert items[1]["after"] is None


def test_apply_writes_nothing_under_skill_config(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    seeded = home / ".claude" / "skills" / "config.json"
    seeded.parent.mkdir(parents=True)
    seeded.write_text('{"kept": true}\n', encoding="utf-8")
    other = home / ".claude" / "rules" / "other.md"
    other.parent.mkdir(parents=True)
    other.write_text("# other\n", encoding="utf-8")
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    assert seeded.read_text(encoding="utf-8") == '{"kept": true}\n'
    assert other.read_text(encoding="utf-8") == "# other\n"


def test_apply_configure_stub_receives_planned_options(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    log = configure_log(bin_dir)
    assert log["argv"] == ["plugin", "configure", mod.LEDGER_PLUGIN, "--values-stdin"]
    assert json.loads(log["stdin"]) == mod.ledger_values(home.resolve(), OPERATOR, HARNESS)


def test_apply_without_approvals_does_not_write_settings(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"model": "kept"}\n', encoding="utf-8")
    before = settings.read_bytes()
    done = run("apply", home, path_prefix=bin_dir)
    assert done.returncode == 0, done.stderr
    assert settings.read_bytes() == before
    assert not (home / ".claude" / "settings.json").read_text(encoding="utf-8") == ""


# § 7 test 2 CI: plan, apply with every optional, remove restores the home.
# A second case edits the override between apply and remove.

def test_remove_restores_an_empty_home(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    before = listing(home)
    planned = run("plan", home)
    assert planned.returncode == 0, planned.stderr
    assert listing(home) == before
    applied = apply_all(home, bin_dir)
    assert applied.returncode == 0, applied.stderr
    assert listing(home) != before
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert listing(home) == before
    assert f"kept: ledger database at {home.resolve() / '.local' / 'share' / 'ledger' / 'ledger.db'} (person's data)" in removed.stdout


def test_remove_names_and_leaves_an_edited_override(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    before = listing(home)
    assert apply_all(home, bin_dir).returncode == 0
    override = home / ".claude" / "rules" / "harness-local.md"
    override.write_text(override.read_text(encoding="utf-8") + "# edited\n", encoding="utf-8")
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert "kept: .claude/rules/harness-local.md (edited)" in removed.stdout
    after = listing(home)
    extra = {path: digest for path, digest in after.items() if path not in before}
    assert list(extra) == [".claude/rules/harness-local.md"]
    assert override.is_file()


def test_remove_keeps_a_moved_deny_rule_and_names_it(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    data = settings_of(home)
    first = data["permissions"]["deny"].pop(0)
    data["permissions"]["deny"].append(first)
    (home / ".claude" / "settings.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert f"kept: permissions.deny rule {first!r} (moved or duplicated)" in removed.stdout
    remaining = settings_of(home)["permissions"]["deny"]
    assert first in remaining


def test_remove_keeps_a_duplicated_deny_rule_and_names_it(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    data = settings_of(home)
    data["permissions"]["deny"].append(mod.DENY_RULES[0])
    (home / ".claude" / "settings.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert f"kept: permissions.deny rule {mod.DENY_RULES[0]!r} (moved or duplicated)" in removed.stdout
    remaining = settings_of(home)["permissions"]["deny"]
    assert remaining.count(mod.DENY_RULES[0]) >= 1


def test_remove_takes_out_a_rule_still_at_its_recorded_place(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    assert settings_of(home)["permissions"]["deny"] == list(mod.DENY_RULES)
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert "moved or duplicated" not in removed.stdout
    assert not (home / ".claude" / "settings.json").exists()


def test_remove_names_and_keeps_the_ledger_database(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    db = home / ".local" / "share" / "ledger" / "ledger.db"
    db.parent.mkdir(parents=True)
    db.write_text("person-data\n", encoding="utf-8")
    before = db.read_bytes()
    assert apply_all(home, bin_dir).returncode == 0
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert db.read_bytes() == before
    assert f"kept: ledger database at {db.resolve()} (person's data)" in removed.stdout


def test_remove_without_a_record_fails_and_leaves_files(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    rules = home / ".claude" / "rules" / "harness.md"
    rules.parent.mkdir(parents=True)
    rules.write_text("x\n", encoding="utf-8")
    before = listing(home)
    done = run("remove", home)
    assert done.returncode == 1
    assert "no record" in done.stderr
    assert listing(home) == before


# § 7 test 4 CI: grants start empty. Override starter is comments only.

def test_grants_start_empty_after_apply_with_every_optional(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    data = settings_of(home)
    assert mod.granting_in_settings(data) == []
    assert "allow" not in data.get("permissions", {})
    assert "additionalDirectories" not in data.get("permissions", {})
    assert "autoAllow" not in data
    assert "autoAllowBashIfSandboxed" not in data.get("sandbox", {})
    assert data["sandbox"] == {"enabled": True}
    assert data["permissions"]["deny"] == list(mod.DENY_RULES)


def test_override_starter_has_no_non_comment_line():
    text = OVERRIDE_SOURCE.read_text(encoding="utf-8")
    assert mod.comment_only(text)
    for line in text.splitlines():
        if line.strip():
            assert line.startswith("#")


def test_i5_every_proposed_settings_path_is_checked_for_grants():
    """I5: a sibling key added to the one proposals list is driven through
    the grants rule. The test reads that list; it does not duplicate it."""
    assert mod.granting_proposals() == []
    for ident, _text in mod.optional_items():
        if ident == "sandbox":
            assert "sandbox" not in mod.GRANTING_KEYS
            assert not any(key in mod.GRANTING_KEYS for key in mod.SANDBOX_VALUE)
        else:
            assert ident.startswith("deny:")
            rule = ident[len("deny:"):]
            assert not mod.proposal_is_granting("deny", "deny", rule)
    for rule in mod.DENY_RULES:
        assert not rule.startswith("Allow")
        assert "allow(" not in rule.lower()


def test_proposals_contain_no_grant():
    assert "allow" not in mod.GRANTING_KEYS or "sandbox" not in mod.GRANTING_KEYS
    assert set(mod.SANDBOX_VALUE) == {"enabled"}
    assert mod.SANDBOX_VALUE["enabled"] is True
    for rule in mod.DENY_RULES:
        assert rule.startswith("Read(")


def test_apply_does_not_add_a_grant_beside_a_seeded_allow(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "permissions": {
            "allow": ["Read(seeded)"],
            "additionalDirectories": ["/tmp/seeded"],
            "deny": [],
        },
        "autoAllow": True,
    }) + "\n", encoding="utf-8")
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    data = settings_of(home)
    assert data["permissions"]["allow"] == ["Read(seeded)"]
    assert data["permissions"]["additionalDirectories"] == ["/tmp/seeded"]
    assert data["autoAllow"] is True
    assert data["permissions"]["deny"] == list(mod.DENY_RULES)
    # Seeded grants remain; none of them were added by apply.
    added = [item for item in record_of(home)["settings"]["items"] if item.get("kind") == "key"]
    assert all(item["name"] != "allow" for item in added)
    assert all(item["name"] != "autoAllow" for item in added)


FAILING_CLAUDE_STUB = """\
#!/usr/bin/env python3
import sys
sys.stderr.write("configure boom\\n")
sys.exit(3)
"""


def install_claude_failing(folder: Path) -> Path:
    bin_dir = folder / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(FAILING_CLAUDE_STUB, encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def test_apply_rolls_back_when_configure_fails(tmp_path):
    bin_dir = install_claude_failing(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    seeded = home / "seeded.txt"
    seeded.write_text("keep\n", encoding="utf-8")
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "claude plugin configure failed" in done.stderr
    assert listing(home) == before
    record = home / ".claude" / "plugins" / "harness-core" / "harness" / "setup-record.json"
    assert not record.exists()


def test_apply_refuses_invalid_settings_json_before_any_write(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{not json", encoding="utf-8")
    extra = home / "seeded.txt"
    extra.write_text("keep\n", encoding="utf-8")
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "not valid JSON" in done.stderr
    assert listing(home) == before
    record = home / ".claude" / "plugins" / "harness-core" / "harness" / "setup-record.json"
    assert not record.exists()


def test_apply_sandbox_only_does_not_create_permissions(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"model": "kept"}, indent=2, ensure_ascii=False) + "\n"
    settings.write_text(original, encoding="utf-8")
    done = run("apply", home, "--approve", "sandbox", path_prefix=bin_dir)
    assert done.returncode == 0, done.stderr
    data = settings_of(home)
    assert data["sandbox"] == {"enabled": True}
    assert data["model"] == "kept"
    assert "permissions" not in data
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert settings.read_bytes() == original.encode("utf-8")


def test_apply_sandbox_only_on_absent_settings_removes_the_file(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    done = run("apply", home, "--approve", "sandbox", path_prefix=bin_dir)
    assert done.returncode == 0, done.stderr
    assert "permissions" not in settings_of(home)
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert not (home / ".claude" / "settings.json").exists()


def test_apply_refuses_dangling_symlink_at_rules_path(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    rules_dir = home / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    rules = rules_dir / "harness.md"
    rules.symlink_to("missing-target.md")
    dest = rules_dir / "missing-target.md"
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "rules default already exists" in done.stderr
    assert rules.is_symlink()
    assert rules.readlink().as_posix() == "missing-target.md"
    assert not dest.exists()
    assert not (home / ".claude" / "plugins").exists()


def test_apply_second_time_after_rules_deleted_keeps_record(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    rules = home / ".claude" / "rules" / "harness.md"
    rules.unlink()
    record_path = home / ".claude" / "plugins" / "harness-core" / "harness" / "setup-record.json"
    before = record_path.read_bytes()
    again = apply_all(home, bin_dir)
    assert again.returncode == 1
    assert "already applied; run remove first" in again.stderr
    assert record_path.read_bytes() == before


def test_remove_keeps_a_rules_folder_that_existed(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    rules_dir = home / ".claude" / "rules"
    rules_dir.mkdir(parents=True)
    assert list(rules_dir.iterdir()) == []
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert rules_dir.is_dir()
    assert list(rules_dir.iterdir()) == []
    assert not (home / ".claude" / "rules" / "harness.md").exists()


def test_settings_write_keeps_non_ascii(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = json.dumps({"model": "café"}, indent=2, ensure_ascii=False) + "\n"
    settings.write_text(original, encoding="utf-8")
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    text = settings.read_text(encoding="utf-8")
    assert "café" in text
    assert "\\u00e9" not in text
    assert settings_of(home)["model"] == "café"


# Round 3: the record comes before configure, a concurrent settings.json edit
# is never overwritten on save or on rollback, a non-UTF-8 settings.json is a
# named refusal, and the module docstring states the rollback scope.

def test_apply_record_write_failure_happens_before_configure(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    plugins = home / ".claude" / "plugins"
    plugins.mkdir(parents=True)
    # A dangling link where the record's folder must be made: the folder
    # cannot be created, so the record cannot be written.
    (plugins / "harness-core").symlink_to("nowhere")
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "apply failed" in done.stderr
    assert not (bin_dir / "claude-log.json").exists()
    assert listing(home) == before


@pytest.mark.parametrize("existing", [True, False])
def test_apply_refuses_when_settings_change_between_load_and_save(
        tmp_path, monkeypatch, existing):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    if existing:
        settings.parent.mkdir(parents=True)
        settings.write_text('{"model": "kept"}\n', encoding="utf-8")
    other = b'{"model": "edited elsewhere"}\n'
    real = mod.write_new_file

    def racing(path, content):
        real(path, content)
        if path.name == "harness-local.md":
            settings.parent.mkdir(parents=True, exist_ok=True)
            settings.write_bytes(other)

    monkeypatch.setattr(mod, "write_new_file", racing)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(mod.SetupError, match="changed while apply was running"):
        mod.apply_verb(home.resolve(), OPERATOR, HARNESS, [], True)
    assert settings.read_bytes() == other
    assert not (bin_dir / "claude-log.json").exists()
    assert listing(home) == {".claude/settings.json": hashlib.sha256(other).hexdigest()}


def install_claude_editing_settings(folder: Path, settings_path: Path,
                                    payload: bytes) -> Path:
    bin_dir = folder / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "from pathlib import Path\n"
        f"Path({str(settings_path)!r}).write_bytes({payload!r})\n"
        "sys.stderr.write('configure boom\\n')\n"
        "sys.exit(3)\n",
        encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    return bin_dir


@pytest.mark.parametrize("existing", [True, False])
def test_rollback_keeps_a_settings_edit_made_after_apply_wrote_it(tmp_path, existing):
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    if existing:
        settings.parent.mkdir(parents=True)
        settings.write_text('{"model": "kept"}\n', encoding="utf-8")
    other = b'{"model": "edited while configure ran"}\n'
    bin_dir = install_claude_editing_settings(tmp_path, settings, other)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "configure boom" in done.stderr
    assert ".claude/settings.json" in done.stderr
    assert "needs a manual check" in done.stderr
    assert settings.read_bytes() == other
    assert listing(home) == {".claude/settings.json": hashlib.sha256(other).hexdigest()}


def test_apply_refuses_non_utf8_settings_by_name_before_any_write(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b'\xff\xfe{"model": "kept"}\n')
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "is not valid UTF-8" in done.stderr
    assert "Traceback" not in done.stderr
    assert listing(home) == before
    assert not (bin_dir / "claude-log.json").exists()


def test_apply_refuses_unreadable_settings_by_name_before_any_write(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".claude" / "settings.json").mkdir(parents=True)
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "cannot read" in done.stderr
    assert "Traceback" not in done.stderr
    assert listing(home) == before


def test_module_docstring_states_record_first_and_rollback_scope():
    doc = " ".join(mod.__doc__.split())
    assert "rolls back every change this run made: the files, the settings and the setup record" in doc
    assert "The record is written before `claude plugin configure` runs" in doc
    assert "A configure that has succeeded is not undone" in doc


# Round 4: one apply or remove run at a time under an exclusive lock, the
# exact written bytes compared on rollback and on remove, and settings written
# through a temp file and os.replace.

LOCK_NAME = "another harness setup run holds the lock"


def hold_lock(home: Path) -> int:
    lock = home / ".claude" / "harness-setup.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


@pytest.mark.parametrize("verb", ["apply", "remove"])
def test_a_second_run_is_refused_by_name_while_the_lock_is_held(tmp_path, verb):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    extra: tuple[str, ...] = ()
    if verb == "remove":
        assert apply_all(home, bin_dir).returncode == 0
        (bin_dir / "claude-log.json").unlink()
    else:
        # Without the lock, apply would stop here and name the rules file.
        rules = home / ".claude" / "rules" / "harness.md"
        rules.parent.mkdir(parents=True)
        rules.write_text("# already\n", encoding="utf-8")
        extra = ("--approve-all",)
    fd = hold_lock(home)
    try:
        before = listing(home)
        done = run(verb, home, *extra, path_prefix=bin_dir)
        assert done.returncode == 1
        assert LOCK_NAME in done.stderr
        assert "rules default already exists" not in done.stderr
        assert listing(home) == before
        assert not (bin_dir / "claude-log.json").exists()
    finally:
        os.close(fd)


def install_claude_running_setup(folder: Path, home: Path) -> Path:
    """A configure stub that starts a second apply and a second remove
    against the same home while the first apply is still running."""
    bin_dir = folder / "bin"
    bin_dir.mkdir()
    claude = bin_dir / "claude"
    claude.write_text(
        "#!/usr/bin/env python3\n"
        "import json, subprocess\n"
        "from pathlib import Path\n"
        f"base = [{sys.executable!r}, {str(SCRIPT)!r}]\n"
        "results = {}\n"
        "for verb, extra in (('apply', ['--operator', 'second', '--approve-all']), ('remove', [])):\n"
        f"    done = subprocess.run(base + [verb, '--home', {str(home)!r}] + extra,\n"
        "                          capture_output=True, text=True)\n"
        "    results[verb] = {'returncode': done.returncode, 'stderr': done.stderr}\n"
        "Path(__file__).with_name('second-runs.json').write_text(json.dumps(results))\n",
        encoding="utf-8")
    claude.chmod(claude.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def test_a_run_started_while_apply_holds_the_lock_is_refused_by_name(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    bin_dir = install_claude_running_setup(tmp_path, home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    results = json.loads((bin_dir / "second-runs.json").read_text(encoding="utf-8"))
    for verb in ("apply", "remove"):
        assert results[verb]["returncode"] == 1, results[verb]
        assert LOCK_NAME in results[verb]["stderr"], results[verb]
    assert (home / mod.RULES_REL).is_file()
    assert (home / mod.RECORD_REL).is_file()
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    assert listing(home) == {}


def test_claim_refuses_a_lock_file_released_and_unlinked_since_it_was_opened(tmp_path):
    path = tmp_path / "harness-setup.lock"
    path.write_bytes(b"")
    fd = os.open(path, os.O_RDWR)
    try:
        # The holder released the lock and unlinked the file after this run
        # opened it: getting the lock on that old file is not holding it.
        path.unlink()
        with pytest.raises(mod.SetupError, match=LOCK_NAME):
            mod.claim_lock(fd, path)
        # A new lock file at the path is still not the one this run opened.
        path.write_bytes(b"")
        with pytest.raises(mod.SetupError, match=LOCK_NAME):
            mod.claim_lock(fd, path)
    finally:
        os.close(fd)
    fresh = os.open(path, os.O_RDWR)
    try:
        mod.claim_lock(fresh, path)
    finally:
        os.close(fresh)


def test_a_symlink_at_the_lock_path_refuses_and_is_not_followed(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = tmp_path / "elsewhere.lock"
    (home / ".claude" / "harness-setup.lock").symlink_to(target)
    before = listing(home)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "cannot take the setup lock" in done.stderr
    assert not target.exists()
    assert listing(home) == before
    assert not (bin_dir / "claude-log.json").exists()


@pytest.mark.parametrize("case", ["applied", "refused", "rolled back", "removed"])
def test_the_lock_is_released_and_its_file_removed_on_every_exit_path(tmp_path, case):
    home = tmp_path / "home"
    home.mkdir()
    if case == "rolled back":
        bin_dir = install_claude_failing(tmp_path)
    else:
        bin_dir = install_claude(tmp_path)
    if case == "refused":
        rules = home / ".claude" / "rules" / "harness.md"
        rules.parent.mkdir(parents=True)
        rules.write_text("# already\n", encoding="utf-8")
    if case == "removed":
        assert apply_all(home, bin_dir).returncode == 0
        done = run("remove", home)
    else:
        done = apply_all(home, bin_dir)
    assert done.returncode == (1 if case in ("refused", "rolled back") else 0), done.stderr
    assert LOCK_NAME not in done.stderr
    lock = home / mod.LOCK_REL
    assert not lock.exists()
    assert not lock.is_symlink()
    os.close(hold_lock(home))


@pytest.mark.parametrize("rel", [mod.RULES_REL, mod.OVERRIDE_REL, mod.RECORD_REL])
def test_rollback_keeps_a_written_file_edited_after_apply_wrote_it(tmp_path, rel):
    home = tmp_path / "home"
    home.mkdir()
    target = home / rel
    other = b"# edited while configure ran\n"
    bin_dir = install_claude_editing_settings(tmp_path, target, other)
    done = apply_all(home, bin_dir)
    assert done.returncode == 1
    assert "configure boom" in done.stderr
    assert "needs a manual check" in done.stderr
    assert rel in done.stderr
    assert target.read_bytes() == other
    assert listing(home) == {rel: hashlib.sha256(other).hexdigest()}


@pytest.mark.parametrize("edit", ["content", "bytes"])
def test_remove_refuses_and_names_a_record_changed_after_apply_wrote_it(tmp_path, edit):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    record_path = home / mod.RECORD_REL
    if edit == "content":
        data = record_of(home)
        data["files"] = [item for item in data["files"] if item["path"] != mod.RULES_REL]
        record_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    else:
        record_path.write_bytes(record_path.read_bytes() + b"\n")
    before = listing(home)
    done = run("remove", home)
    assert done.returncode == 1
    assert f"{mod.RECORD_REL} changed after apply wrote it, left as it is, needs a manual check" in done.stderr
    assert listing(home) == before


@pytest.mark.parametrize("edited", [True, False])
def test_remove_names_a_settings_file_changed_after_apply_wrote_it(tmp_path, edited):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    settings = home / ".claude" / "settings.json"
    if edited:
        data = settings_of(home)
        data["model"] = "added later"
        settings.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    removed = run("remove", home)
    assert removed.returncode == 0, removed.stderr
    note = f"needs a manual check: {mod.SETTINGS_REL} (changed after apply wrote it"
    if edited:
        assert note in removed.stdout
        assert settings_of(home) == {"model": "added later"}
    else:
        assert note not in removed.stdout
        assert not settings.exists()


def test_a_settings_write_that_fails_before_the_replace_leaves_the_original_bytes(
        tmp_path, monkeypatch):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = b'{"model": "kept"}\n'
    settings.write_bytes(original)
    before = listing(home)
    sources: list[Path] = []
    real = os.replace

    def failing(src, dst, *args, **kwargs):
        if Path(dst).name == "settings.json":
            sources.append(Path(src))
            raise OSError("injected failure before the replace")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(mod.os, "replace", failing)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(mod.SetupError, match="injected failure before the replace"):
        mod.apply_verb(home.resolve(), OPERATOR, HARNESS, [], True)
    assert sources
    assert all(src.parent.resolve() == settings.parent.resolve() for src in sources)
    assert settings.read_bytes() == original
    assert listing(home) == before
    assert not (bin_dir / "claude-log.json").exists()


def test_rollback_leaves_settings_alone_when_its_write_never_landed(tmp_path, monkeypatch):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b'{"model": "kept"}\n')
    other = b'{"model": "edited by another tool"}\n'
    calls: list[Path] = []
    real = os.replace

    def racing(src, dst, *args, **kwargs):
        if Path(dst).name == "settings.json" and not calls:
            calls.append(Path(src))
            Path(dst).write_bytes(other)
            raise OSError("injected failure before the replace")
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(mod.os, "replace", racing)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(mod.SetupError, match="injected failure before the replace"):
        mod.apply_verb(home.resolve(), OPERATOR, HARNESS, [], True)
    assert settings.read_bytes() == other
    assert listing(home) == {".claude/settings.json": hashlib.sha256(other).hexdigest()}


def test_rollback_restores_settings_through_a_temp_file_and_replace(tmp_path, monkeypatch):
    bin_dir = install_claude_failing(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = b'{"model": "kept"}\n'
    settings.write_bytes(original)
    before = listing(home)
    calls: list[tuple[Path, Path]] = []
    real = os.replace

    def spying(src, dst, *args, **kwargs):
        calls.append((Path(src), Path(dst)))
        return real(src, dst, *args, **kwargs)

    monkeypatch.setattr(mod.os, "replace", spying)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(mod.SetupError, match="claude plugin configure failed"):
        mod.apply_verb(home.resolve(), OPERATOR, HARNESS, [], True)
    targets = [dst.resolve() for _src, dst in calls]
    # One replace for apply's write, one for rollback's restore.
    assert targets == [settings.resolve(), settings.resolve()]
    assert all(src.parent.resolve() == settings.parent.resolve() for src, _dst in calls)
    assert settings.read_bytes() == original
    assert listing(home) == before


def test_settings_write_keeps_the_file_mode(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"model": "kept"}\n', encoding="utf-8")
    os.chmod(settings, 0o640)
    done = apply_all(home, bin_dir)
    assert done.returncode == 0, done.stderr
    assert settings_of(home)["sandbox"] == {"enabled": True}
    assert stat.S_IMODE(settings.stat().st_mode) == 0o640


def test_apply_refuses_a_symlinked_settings_file_before_any_write(tmp_path, monkeypatch):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    target = tmp_path / "kept-settings.json"
    target.write_bytes(b'{"model": "kept"}\n')
    link = home / ".claude" / "settings.json"
    link.symlink_to(target)
    before = listing(home)
    writes: list[Path] = []
    real = mod.write_new_file

    def spying(path, content):
        writes.append(path)
        real(path, content)

    monkeypatch.setattr(mod, "write_new_file", spying)
    monkeypatch.setenv("PATH", str(bin_dir) + os.pathsep + os.environ.get("PATH", ""))
    with pytest.raises(mod.SetupError, match="is a symbolic link; it is not written through"):
        mod.apply_verb(home.resolve(), OPERATOR, HARNESS, [], True)
    assert writes == []
    assert link.is_symlink()
    assert target.read_bytes() == b'{"model": "kept"}\n'
    assert listing(home) == before
    assert not (bin_dir / "claude-log.json").exists()


def test_remove_refuses_a_symlinked_settings_file_before_any_write(tmp_path):
    bin_dir = install_claude(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    assert apply_all(home, bin_dir).returncode == 0
    settings = home / ".claude" / "settings.json"
    target = tmp_path / "moved-settings.json"
    target.write_bytes(settings.read_bytes())
    settings.unlink()
    settings.symlink_to(target)
    kept = target.read_bytes()
    before = listing(home)
    removed = run("remove", home)
    assert removed.returncode == 1
    assert "is a symbolic link; it is not written through" in removed.stderr
    assert listing(home) == before
    assert settings.is_symlink()
    assert target.read_bytes() == kept

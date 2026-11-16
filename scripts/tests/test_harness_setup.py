"""Fixture-home tests of the harness plugin's plan, apply and remove script.

Covers design § 6 (override file and settings proposals) and § 8 (uninstall),
and the CI parts of § 7 tests 2 and 4. Homes are pytest tmp_path folders.
A stub `claude` records configure arguments. Nothing granting is proposed.
"""
from __future__ import annotations

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

"""The CI parts of the four install tests, with no Claude Code and no model.

Each test runs plugins/harness/setup.py as a child process against a fixture
home. `claude` and `ledger` are stub commands on a PATH that holds nothing
else (see harness_fixtures.py). The walk parts, which need a real account,
are the checkpoint's own observation and are not here.

1. A plan lists every write before it happens: the plan writes nothing, and
   the files that differ after setup are exactly the files the plan listed.
2. Uninstall restores the account, apart from a short list that is ours.
3. The install never edits a file the person already had.
4. Grants start empty.

The README and the setup skill are also held to what the script does, so a
newcomer's instructions cannot drift from it.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from pathlib import Path

import pytest

from harness_fixtures import (
    HARNESS, OPERATOR, README, ROOT, SEEDED_SETTINGS, SKILL, child_env,
    added_dirs, ancestors, approve_args, at, changed_files, load_setup, make_bin,
    make_home, plan_configure, plan_files, plan_option_ids, plan_settings_path,
    setup_run, setup_run_traced, show_stored, sha, snap,
)

setup = load_setup()
ALL_IDS = [ident for ident, _text in setup.optional_items()]
RULES, OVERRIDE, RECORD, LOCK, DB = (setup.RULES_REL, setup.OVERRIDE_REL, setup.RECORD_REL,
                                     setup.LOCK_REL, setup.DB_REL)
SETTINGS = setup.SETTINGS_REL
DB_FOLDERS = {".local", ".local/share", ".local/share/ledger"}

# What a person's account keeps after uninstall, and why: the setup lock is a
# file setup never unlinks, and the ledger database and the folders made to
# hold it are the person's data.
OURS = {LOCK, DB} | DB_FOLDERS

SKILL_CONFIG = ".claude/skill-config/ticket.json"
OWN_RULES = ".claude/rules/mine.md"


def fixture(tmp_path, *, settings=SEEDED_SETTINGS, extra=None, name="case"):
    folder = tmp_path / name
    folder.mkdir()
    bin_dir = make_bin(folder)
    home = make_home(folder, settings=settings)
    for rel, text in (extra or {}).items():
        target = at(home, rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return home, bin_dir


def do(verb, home, bin_dir, *extra, **kw):
    done = setup_run(verb, home, *extra, bin_dir=bin_dir, **kw)
    return done


def set_up(home, bin_dir, ids=ALL_IDS):
    """Plan, apply, store the options the stub was given, make the first ledger write."""
    plan = do("plan", home, bin_dir)
    assert plan.rc == 0, plan.err
    applied = do("apply", home, bin_dir, *approve_args(ids))
    assert applied.rc == 0, applied.err
    show_stored(bin_dir)
    init = do("ledger-init", home, bin_dir)
    assert init.rc == 0, init.err
    return plan, applied, init


def record_body(home):
    lines = at(home, RECORD).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    return json.loads(lines[1])


# ---------------------------------------------------------------------------
# Test 1: a dry run lists every write before it happens


@pytest.mark.parametrize("settings", [SEEDED_SETTINGS, None], ids=["seeded-settings", "empty-home"])
def test_install_1_the_files_that_differ_after_setup_are_exactly_the_files_the_plan_listed(
        tmp_path, settings):
    home, bin_dir = fixture(tmp_path, settings=settings)
    before = snap(home)
    plan = do("plan", home, bin_dir)
    assert plan.rc == 0, plan.err
    assert snap(home) == before, "the plan wrote something"

    listed = set(plan_files(plan.out))
    assert listed == {RULES, OVERRIDE, RECORD, LOCK, DB}
    settings_path = plan_settings_path(plan.out)
    assert settings_path == SETTINGS

    applied = do("apply", home, bin_dir, *approve_args(ALL_IDS))
    assert applied.rc == 0, applied.err
    show_stored(bin_dir)
    init = do("ledger-init", home, bin_dir)
    assert init.rc == 0, init.err
    after = snap(home)

    # The comparison is against the filesystem, never against the record: a
    # write that setup made and did not record is still seen here.
    differ = changed_files(before, after)
    assert differ == listed | {settings_path}, (
        f"unplanned: {sorted(differ - listed - {settings_path})}; "
        f"planned but not written: {sorted(listed - differ)}")
    # Every folder that is new is on the way to a planned file.
    expected_dirs = {name for path in differ for name in ancestors(path)} - set(before)
    assert added_dirs(before, after) == expected_dirs


def test_install_1_the_record_apply_leaves_equals_the_plan(tmp_path):
    home, bin_dir = fixture(tmp_path)
    plan, _applied, _init = set_up(home, bin_dir)
    body = record_body(home)
    placed = {item["path"] for item in body["files"]}
    with_content = [path for path in plan_files(plan.out) if f"FILE {path}\n----\n" in plan.out]
    assert placed == set(with_content) == {RULES, OVERRIDE}
    for item in body["files"]:
        source = ROOT / "plugins" / "harness" / "files" / item["path"].rsplit("/", 1)[1]
        assert item["sha256"] == sha(source.read_bytes())
    recorded = {"sandbox" if item["kind"] == "key" else f"deny:{item['value']}"
                for item in body["settings"]["items"]}
    assert recorded == set(plan_option_ids(plan.out))


def test_install_1_the_stub_got_the_options_and_the_ledger_command_the_plan_listed(tmp_path):
    home, bin_dir = fixture(tmp_path)
    plan, _applied, _init = set_up(home, bin_dir)
    argv, values = plan_configure(plan.out)
    assert (bin_dir / "claude-argv").read_text(encoding="utf-8").split() == argv
    assert json.loads((bin_dir / "claude-stdin").read_text(encoding="utf-8")) == values
    assert values == {"ledger_db": str(at(home, DB)), "ledger_operator": OPERATOR,
                      "ledger_harness": HARNESS}

    lines = plan.out.splitlines()
    block = lines.index("FIRST LEDGER WRITE")
    assert lines[block + 1] == "----" and lines[block + 3] == "----"
    command = shlex.split(lines[block + 2])
    # The command the stub got is the plan's command run against a temporary
    # database in the final database's own folder (published to the final
    # path only after the write succeeded); every other argument is the plan's.
    assert command[1] == "--db" and command[2] == str(at(home, DB))
    argv = (bin_dir / "ledger-argv").read_text(encoding="utf-8").splitlines()
    assert argv[0] == "--db" and argv[2:] == command[3:]
    temp = Path(argv[1])
    assert temp.parent == at(home, DB).parent
    assert re.fullmatch(r"\.ledger\.db\.harness-[0-9a-f]{16}\.tmp", temp.name), temp.name
    assert (bin_dir / "ledger-env").read_text(encoding="utf-8").splitlines() == [
        str(temp), OPERATOR, HARNESS]
    assert not temp.exists(), "the temporary database was not removed"


def test_install_1_the_first_ledger_write_makes_the_database_and_a_repeat_writes_nothing(tmp_path):
    home, bin_dir = fixture(tmp_path)
    assert not at(home, DB).exists()
    set_up(home, bin_dir)
    first = at(home, DB).read_bytes()
    assert first[:15] == b"SQLite format 3"
    again = do("ledger-init", home, bin_dir)
    assert again.rc == 0, again.err
    assert "kept: ledger database already exists" in again.out
    assert at(home, DB).read_bytes() == first


# ---------------------------------------------------------------------------
# Test 2: uninstall restores the account


@pytest.mark.parametrize("settings", [SEEDED_SETTINGS, None], ids=["seeded-settings", "empty-home"])
def test_install_2_remove_leaves_only_what_is_ours(tmp_path, settings):
    home, bin_dir = fixture(tmp_path, settings=settings)
    before = snap(home)
    set_up(home, bin_dir)
    removed = do("remove", home, bin_dir)
    assert removed.rc == 0, removed.err
    after = snap(home)
    assert {name for name in before if after.get(name) != before[name]} == set(), (
        "a file or folder that was there before is gone or changed")
    extra = OURS | ({".claude"} if settings is None else set())
    assert set(after) - set(before) == extra
    assert f"kept: ledger database at {at(home, DB)}" in removed.out


def test_install_2_an_edited_override_is_named_and_left_and_is_the_only_extra(tmp_path):
    home, bin_dir = fixture(tmp_path)
    before = snap(home)
    set_up(home, bin_dir)
    override = at(home, OVERRIDE)
    override.write_text(override.read_text(encoding="utf-8") + "my own rule\n", encoding="utf-8")
    removed = do("remove", home, bin_dir)
    assert removed.rc == 0, removed.err
    assert f"kept for a manual check: {override}" in removed.out
    after = snap(home)
    assert set(after) - set(before) == OURS | {".claude/rules", OVERRIDE}
    assert not at(home, RULES).exists()
    assert {name for name in before if after.get(name) != before[name]} == set()


# ---------------------------------------------------------------------------
# Test 3: the install never edits a file the person already had


def seeded_extra():
    return {OWN_RULES: "my own rules\n", SKILL_CONFIG: '{"topics_dir": "mine"}\n'}


def seeded_paths(home):
    return {str(at(home, rel)) for rel in snap(home)}


def test_install_3_setup_opens_no_file_the_person_already_had(tmp_path):
    home, bin_dir = fixture(tmp_path, extra=seeded_extra())
    before = snap(home)
    protected = seeded_paths(home)
    applied = setup_run_traced("apply", home, bin_dir=bin_dir)
    assert applied.rc == 0, applied.err
    init = setup_run_traced("ledger-init", home, bin_dir=bin_dir)
    assert init.rc == 0, init.err
    plan = setup_run_traced("plan", home, bin_dir=bin_dir)
    assert plan.rc == 0, plan.err
    touched = {event["path"] for done in (applied, init, plan) for event in done.events}
    assert touched, "the trace is empty, so it proves nothing"
    assert str(at(home, LOCK)) in touched
    assert touched & protected == set(), sorted(touched & protected)
    after = snap(home)
    for rel in (SETTINGS, OWN_RULES, SKILL_CONFIG):
        assert after[rel] == before[rel], rel


def test_install_3_a_seeded_file_at_the_rules_default_stops_setup_with_nothing_written(tmp_path):
    home, bin_dir = fixture(tmp_path, extra={RULES: "my own harness rules\n"})
    before = snap(home)
    done = do("apply", home, bin_dir)
    assert done.rc == 1
    assert done.err.startswith("setup: rules default already exists:")
    after = snap(home)
    assert set(after) - set(before) == {LOCK}
    assert {name for name in before if after[name] != before[name]} == set()
    assert not (bin_dir / "claude-stdin").exists(), "configure ran before the refusal"


def test_install_3_a_seeded_override_is_skipped_marked_not_ours_and_left_by_remove(tmp_path):
    home, bin_dir = fixture(tmp_path, extra={OVERRIDE: "<!-- mine -->\nbe terse\n"})
    seeded = at(home, OVERRIDE).read_bytes()
    applied = do("apply", home, bin_dir)
    assert applied.rc == 0, applied.err
    assert f"kept: {at(home, OVERRIDE)} (already existed)" in applied.out
    assert at(home, OVERRIDE).read_bytes() == seeded
    assert {item["path"] for item in record_body(home)["files"]} == {RULES}
    removed = do("remove", home, bin_dir)
    assert removed.rc == 0, removed.err
    assert at(home, OVERRIDE).read_bytes() == seeded
    assert not at(home, RULES).exists()


def test_install_3_approved_items_are_added_and_nothing_else_in_settings_changes(tmp_path):
    home, bin_dir = fixture(tmp_path)
    done = do("apply", home, bin_dir, *approve_args(ALL_IDS))
    assert done.rc == 0, done.err
    now = json.loads(at(home, SETTINGS).read_text(encoding="utf-8"))
    seeded = SEEDED_SETTINGS
    assert set(now) == set(seeded) | {"sandbox"}
    assert now["sandbox"] == {"enabled": True}
    assert now["model"] == seeded["model"]
    assert now["additionalDirectories"] == seeded["additionalDirectories"]
    assert now["permissions"]["allow"] == seeded["permissions"]["allow"]
    assert set(now["permissions"]) == set(seeded["permissions"])
    added = [ident[len("deny:"):] for ident in ALL_IDS if ident.startswith("deny:")]
    assert now["permissions"]["deny"] == seeded["permissions"]["deny"] + added


def test_install_3_with_no_approval_the_settings_file_is_byte_identical(tmp_path):
    home, bin_dir = fixture(tmp_path)
    before = at(home, SETTINGS).read_bytes()
    set_up(home, bin_dir, ids=[])
    assert at(home, SETTINGS).read_bytes() == before


# ---------------------------------------------------------------------------
# Test 4: grants start empty

GRANT_KEYS = ("allow", "additionalDirectories", "extraDirectories", "autoAllow",
              "autoAllowBashIfSandboxed")
ALLOW_DECISION = [
    re.compile(r"""permissionDecision["']?\s*[:=]\s*["']?(allow|approve)""", re.IGNORECASE),
    re.compile(r"""["']decision["']\s*:\s*["'](allow|approve)""", re.IGNORECASE),
]


def grants_in(value, prefix=""):
    """Every grant in a settings value: path -> value."""
    found = {}
    if isinstance(value, dict):
        for key, item in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if key in GRANT_KEYS:
                found[path] = item
            found.update(grants_in(item, path))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found.update(grants_in(item, f"{prefix}[{index}]"))
    return found


def comment_lines_only(text):
    for line in text.splitlines():
        line = line.strip()
        if line and not (line.startswith("<!--") and line.endswith("-->")):
            return False
    return True


def hooks_that_allow(plugins_root):
    """Hook files under plugins_root/*/hooks that can return an allow decision."""
    found = []
    for path in sorted(plugins_root.glob("*/hooks/**/*")):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(pattern.search(text) for pattern in ALLOW_DECISION):
                found.append(str(path.relative_to(plugins_root)))
    return found


def test_install_4_apply_with_every_proposal_approved_adds_no_grant(tmp_path):
    home, bin_dir = fixture(tmp_path)
    seeded = grants_in(SEEDED_SETTINGS)
    assert seeded, "the seeded settings must hold grants of their own"
    set_up(home, bin_dir)
    now = json.loads(at(home, SETTINGS).read_text(encoding="utf-8"))
    assert grants_in(now) == seeded


def test_install_4_a_fresh_home_ends_with_no_grant_at_all(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    set_up(home, bin_dir)
    now = json.loads(at(home, SETTINGS).read_text(encoding="utf-8"))
    assert now and grants_in(now) == {}


def test_install_4_the_override_starter_placed_has_only_comment_lines(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    set_up(home, bin_dir)
    placed = at(home, OVERRIDE).read_text(encoding="utf-8")
    assert placed.strip() and comment_lines_only(placed)
    assert comment_lines_only("<!-- a -->\n\n  <!-- b -->\n")
    assert not comment_lines_only("<!-- a -->\nallow everything\n")


def test_install_4_no_plugin_hook_returns_allow(tmp_path):
    plugins = tmp_path / "plugins"
    (plugins / "good" / "hooks").mkdir(parents=True)
    (plugins / "bad" / "hooks").mkdir(parents=True)
    (plugins / "good" / "hooks" / "deny.sh").write_text(
        'printf \'{"hookSpecificOutput": {"permissionDecision": "deny"}}\'\n', encoding="utf-8")
    (plugins / "bad" / "hooks" / "allow.sh").write_text(
        'printf \'{"hookSpecificOutput": {"permissionDecision": "allow"}}\'\n', encoding="utf-8")
    (plugins / "bad" / "hooks" / "legacy.json").write_text('{"decision": "approve"}\n',
                                                          encoding="utf-8")
    assert hooks_that_allow(plugins) == ["bad/hooks/allow.sh", "bad/hooks/legacy.json"]
    assert hooks_that_allow(ROOT / "plugins") == []


# ---------------------------------------------------------------------------
# The README and the setup skill match the script


def section(text, heading):
    """The lines under a heading, up to the next heading."""
    lines = text.splitlines()
    start = lines.index(heading) + 1
    end = next((index for index in range(start, len(lines)) if lines[index].startswith("#")),
               len(lines))
    return lines[start:end]


def table_paths(lines):
    return {match.group(1) for line in lines
            if (match := re.match(r"\| `([^`]+)` \|", line))}


def test_the_readme_lists_the_files_the_plan_lists(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    plan = do("plan", home, bin_dir)
    text = README.read_text(encoding="utf-8")
    assert table_paths(section(text, "#### What setup writes")) == set(plan_files(plan.out))


def test_the_readme_lists_what_uninstall_leaves_as_the_test_does():
    text = README.read_text(encoding="utf-8")
    assert table_paths(section(text, "#### What is left after uninstall")) == OURS


def test_the_readme_carries_the_install_and_the_three_uninstall_steps():
    text = README.read_text(encoding="utf-8")
    for command in (
            "claude plugin marketplace add https://github.com/<owner>/harness-core.git#vX.Y.Z",
            "claude plugin install harness@harness-core",
            "/harness:setup",
            "/harness:setup doctor",
            "/harness:setup uninstall",
            "claude plugin uninstall harness@harness-core --prune -y",
            "claude plugin marketplace remove harness-core"):
        assert command in text, command
    steps = section(text, "### Uninstall")
    order = [next(index for index, line in enumerate(steps) if marker in line)
             for marker in ("/harness:setup uninstall", "uninstall harness@harness-core",
                            "marketplace remove harness-core")]
    assert order == sorted(order)


def test_the_skill_names_every_verb_and_never_approves_everything_at_once():
    text = SKILL.read_text(encoding="utf-8")
    front = text.split("---", 2)[1]
    assert re.search(r"^name: setup$", front, re.MULTILINE)
    for verb in ("plan", "apply", "doctor", "ledger-init", "remove", "instance-plan",
                 "instance-create"):
        assert f"setup.py\" {verb} " in text or f"setup.py {verb} " in text, verb
    # The one mention is the rule against it; no command in the skill uses it.
    assert [line for line in text.splitlines()
            if "--approve-all" in line and "Never pass" not in line] == []
    assert text.count("--approve-all") == 1
    assert "--approve" in text
    assert "start a new session" in text.lower()


def test_the_readme_names_every_optional_setting_the_plan_proposes():
    text = README.read_text(encoding="utf-8")
    for rule in setup.DENY_RULES:
        assert f"`{rule}`" in text, rule
    assert '`{"enabled": true}`' in text
    for key in ("ledger_db", "ledger_operator", "ledger_harness"):
        assert f"`{key}`" in text, key


SKILL_COMMAND = 'python3 "${CLAUDE_SKILL_DIR}/../../setup.py"'


def skill_commands(text):
    return [line.strip() for line in text.splitlines() if line.strip().startswith(SKILL_COMMAND)]


def test_the_skill_single_quotes_every_value_the_model_substitutes():
    text = SKILL.read_text(encoding="utf-8")
    seen = {"--operator": 0, "--harness": 0, "--approve": 0, "--path": 0}
    commands = skill_commands(text)
    assert len(commands) == 7
    for command in commands:
        for flag, value in re.findall(r"(--operator|--harness|--approve|--path) (\S+)", command):
            assert re.fullmatch(r"'<[a-z]+>'", value), f"unquoted {flag} value {value}: {command}"
            seen[flag] += 1
        bare = re.sub(r"'<[a-z]+>'", "", command)
        assert "<" not in bare and ">" not in bare, f"unquoted placeholder: {command}"
    assert seen == {"--operator": 3, "--harness": 3, "--approve": 2, "--path": 2}
    # The skill says why.
    assert "parentheses" in text and "deny:Read(.env)" in text


def test_the_documented_apply_command_runs_through_bash_with_a_deny_id(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    template = next(command for command in skill_commands(SKILL.read_text(encoding="utf-8"))
                    if " apply " in command)
    ids = iter(["deny:Read(.env)", "sandbox"])
    command = template.replace("<operator>", OPERATOR).replace("<harness>", HARNESS)
    command = re.sub(r"<id>", lambda _match: next(ids), command)
    env = child_env(home, bin_dir)
    env["CLAUDE_SKILL_DIR"] = str(SKILL.parent)
    done = subprocess.run(["/bin/bash", "-c", command], capture_output=True, text=True,
                          env=env, timeout=120)
    assert done.returncode == 0, done.stderr
    stored = json.loads(at(home, SETTINGS).read_text(encoding="utf-8"))
    assert stored["permissions"]["deny"] == ["Read(.env)"]
    assert stored["sandbox"]["enabled"] is True

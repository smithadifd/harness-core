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

The --db option is held to the same promises: a ledger database the person
keeps at another path is listed by the plan, used as it is and stored as the
ledger_db option, and no second database is made.

The README and the setup skill are also held to what the script does, so a
newcomer's instructions cannot drift from it.
"""
from __future__ import annotations

import json
import re
import shlex
import sqlite3
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

# What the real ledger leaves beside its database, which the stub the fixtures
# use does not make: the README's leftovers table names them too.
LEDGER_LEFTOVERS = {".local/share/ledger/search.db",
                    ".local/share/ledger/ledger.db.session-models"}

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
    key_ids = {"sandbox": "sandbox", "promptSuggestionEnabled": "prompt-suggestions"}
    recorded = {key_ids[item["name"]] if item["kind"] == "key" else f"deny:{item['value']}"
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
    assert set(now) == set(seeded) | {"sandbox", "promptSuggestionEnabled"}
    assert now["sandbox"] == {"enabled": True}
    assert now["promptSuggestionEnabled"] is False
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
# The --db option: a ledger the person keeps at another path is used, and no
# second database is made.

SQLITE_SIDE_FILES = ("-wal", "-shm", "-journal")


def database_files(folder):
    """Every SQLite database file, or file SQLite keeps beside one, under folder."""
    return {path for path in folder.rglob("*")
            if path.is_file() and (path.suffix == ".db"
                                   or path.name.endswith(SQLITE_SIDE_FILES))}


def seed_database(path):
    path.parent.mkdir(parents=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE kept (note TEXT)")
        conn.execute("INSERT INTO kept VALUES ('the person keeps this ledger')")
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def test_db_a_database_seeded_elsewhere_is_used_and_no_new_database_is_made(tmp_path):
    home, bin_dir = fixture(tmp_path)
    elsewhere = tmp_path / "elsewhere"
    seeded = elsewhere / "my-ledger.db"
    seeded_bytes = seed_database(seeded)
    before_home, before_elsewhere = snap(home), snap(elsewhere)
    given = ("--db", str(seeded))

    plan = do("plan", home, bin_dir, *given)
    assert plan.rc == 0, plan.err
    assert snap(home) == before_home and snap(elsewhere) == before_elsewhere, (
        "the plan wrote something")
    # The plan lists the given path, and nothing at the default path.
    assert plan_files(plan.out) == [RULES, OVERRIDE, RECORD, LOCK, str(seeded)]
    _argv, values = plan_configure(plan.out)
    assert values["ledger_db"] == str(seeded)
    lines = plan.out.splitlines()
    command = shlex.split(lines[lines.index("FIRST LEDGER WRITE") + 2])
    assert command[1:3] == ["--db", str(seeded)]

    applied = do("apply", home, bin_dir, *given, *approve_args(ALL_IDS))
    assert applied.rc == 0, applied.err
    # The stored ledger_db option is the given path.
    assert show_stored(bin_dir)["ledger_db"] == str(seeded)
    init = do("ledger-init", home, bin_dir, *given)
    assert init.rc == 0, init.err
    assert init.out == (f"kept: ledger database already exists at {seeded}; "
                        "no first write was made\n")
    assert not (bin_dir / "ledger-argv").exists(), "the ledger command ran"

    # The seeded database is used as it is, and no database file is new
    # anywhere: not at the default path, not beside the seeded one.
    assert seeded.read_bytes() == seeded_bytes
    assert snap(elsewhere) == before_elsewhere
    assert database_files(tmp_path) == {seeded}
    assert not at(home, DB).exists()
    after_home = snap(home)
    assert changed_files(before_home, after_home) == {RULES, OVERRIDE, RECORD, LOCK, SETTINGS}
    assert not any(name.startswith(".local") for name in after_home)


def test_db_a_new_path_gets_the_first_write_there_and_doctor_checks_it(tmp_path):
    home, bin_dir = fixture(tmp_path)
    chosen = tmp_path / "other" / "nested" / "ledger.db"
    given = ("--db", str(chosen))
    plan = do("plan", home, bin_dir, *given)
    assert plan.rc == 0, plan.err
    assert str(chosen) in plan_files(plan.out)
    assert do("apply", home, bin_dir, *given).rc == 0
    show_stored(bin_dir)
    init = do("ledger-init", home, bin_dir, *given)
    assert init.rc == 0, init.err
    assert init.out == f"created: ledger database at {chosen}\n"
    assert chosen.read_bytes()[:15] == b"SQLite format 3"
    assert database_files(tmp_path) == {chosen}
    assert not at(home, DB).exists()
    doctor = do("doctor", home, bin_dir)
    assert doctor.rc == 0, doctor.out
    assert f"ok options: ledger_db is set: {chosen}" in doctor.out.splitlines()
    assert any(line.startswith(f"ok ledger: the ledger database exists: {chosen} ")
               for line in doctor.out.splitlines())


@pytest.mark.parametrize("verb", ["plan", "apply", "ledger-init"])
@pytest.mark.parametrize("raw", ["ledger.db", "./ledger/ledger.db", ""],
                         ids=["bare-name", "dot-relative", "empty"])
def test_db_a_relative_path_is_refused_with_nothing_written(tmp_path, verb, raw):
    home, bin_dir = fixture(tmp_path)
    before = snap(home)
    done = do(verb, home, bin_dir, "--db", raw)
    assert done.rc == 1
    assert done.err == "setup: --db must be an absolute path\n"
    assert done.out == ""
    assert snap(home) == before
    assert database_files(tmp_path) == set()
    assert not (bin_dir / "claude-stdin").exists(), "configure ran before the refusal"


def test_the_skill_passes_the_same_db_path_to_plan_apply_and_ledger_init():
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert "add `--db '<path>'`" in text
    assert "to the `plan`, `apply` and `ledger-init` commands" in text


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


def marketplace_manifest():
    return json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))


def marketplace_owner():
    entry = next(e for e in marketplace_manifest()["plugins"] if e["name"] == "sdlc")
    return re.fullmatch(r"https://github\.com/([^/]+)/skills\.git", entry["source"]["url"]).group(1)


OWN_URL = f"https://github.com/{marketplace_owner()}/harness-core.git"


def test_the_readme_lists_the_files_the_plan_lists(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    plan = do("plan", home, bin_dir)
    text = README.read_text(encoding="utf-8")
    assert table_paths(section(text, "#### What setup writes")) == set(plan_files(plan.out))


def test_the_readme_lists_what_uninstall_leaves_as_the_test_does():
    text = README.read_text(encoding="utf-8")
    assert table_paths(section(text, "#### What is left after uninstall")) == OURS | LEDGER_LEFTOVERS


def test_the_readme_carries_the_install_and_the_three_uninstall_steps():
    text = README.read_text(encoding="utf-8")
    for command in (
            f"claude plugin marketplace add {OWN_URL}#vX.Y.Z",
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


def test_the_skill_says_what_the_prompt_suggestions_item_is_for():
    step = " ".join(skill_step(SKILL.read_text(encoding="utf-8"), 3).split())
    item = step[step.index("`prompt-suggestions`"):]
    assert "suggested reply already typed into the prompt box" in item
    assert "pressing Enter sends it" in item
    assert "`promptSuggestionEnabled` set to `false`" in item
    assert "No leaves Claude Code's suggestions as they are" in item
    assert "uninstall takes it out again" in item


def test_the_readme_names_every_optional_setting_the_plan_proposes():
    text = README.read_text(encoding="utf-8")
    for rule in setup.DENY_RULES:
        assert f"`{rule}`" in text, rule
    assert '`{"enabled": true}`' in text
    for key in ("ledger_db", "ledger_operator", "ledger_harness"):
        assert f"`{key}`" in text, key


def test_the_readme_names_the_prompt_suggestions_setting_and_how_it_is_applied_and_removed():
    text = one_line(README.read_text(encoding="utf-8"))
    assert "(`promptSuggestionEnabled` set to `false`)" in text
    bullet = text[text.index("**Prompt suggestions off.**"):]
    bullet = bullet.split("Claude Code 2.1.326 makes auto mode")[0]
    assert "Yes sets `promptSuggestionEnabled` to `false`" in bullet
    assert "No leaves the suggestions as they are" in bullet
    assert "applied only on your yes" in bullet
    assert "only when you have not set that key yourself" in bullet
    assert "uninstall takes it out again" in bullet


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


# ---------------------------------------------------------------------------
# The walk's README and skill friction: each claim a newcomer meets is pinned.

NUMBER_WORDS = {1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six"}


def skill_step(text, number):
    """The text of one numbered step of the skill's Set up section."""
    body = text.split("## Set up", 1)[1].split("## Instance folder", 1)[0]
    pieces = re.split(r"^(\d+)\. \*\*", body, flags=re.MULTILINE)
    steps = {int(pieces[i]): pieces[i + 1] for i in range(1, len(pieces) - 1, 2)}
    return steps[number]


def test_the_readme_install_commands_name_this_repository_not_a_placeholder():
    text = README.read_text(encoding="utf-8")
    assert "<owner>" not in text
    adds = [line for line in text.splitlines() if line.startswith("claude plugin marketplace add ")]
    assert adds == [f"claude plugin marketplace add {OWN_URL}#vX.Y.Z"] * 2


def test_the_readme_names_the_sdlc_pin_the_marketplace_holds():
    source = next(e for e in marketplace_manifest()["plugins"] if e["name"] == "sdlc")["source"]
    text = README.read_text(encoding="utf-8")
    status = text[text.index("**Status at this tag.**"):].split("\n\n", 3)
    status = "\n\n".join(status[:2])
    assert f"`{source['ref']}`" in status and source["sha"] in status


def test_the_readme_count_of_what_uninstall_removes_is_the_marketplaces_plugin_count():
    plugins = marketplace_manifest()["plugins"]
    word = NUMBER_WORDS[len(plugins)]
    text = README.read_text(encoding="utf-8")
    assert f"none of the {word}" in text
    for other in NUMBER_WORDS.values():
        if other != word:
            assert f"none of the {other}" not in text, other
    steps = "\n".join(section(text, "### Uninstall"))
    for plugin in plugins:
        assert f"`{plugin['name']}`" in steps, plugin["name"]


def test_the_readme_first_use_says_how_to_file_stop_resume_and_where_ledger_runs():
    lines = "\n".join(section(README.read_text(encoding="utf-8"), "### First use"))
    assert "`ticket` skill" in lines
    assert "Resume the ticket" in lines
    assert "not of\n  your own shell" in lines or "not of your own shell" in lines.replace("\n  ", " ")
    for command in ("ledger resume", "ledger decisions", "ledger events --item",
                    "ledger export-item", "ledger search"):
        assert f"`{command}" in lines, command


def test_the_readme_says_where_it_talks_about_grants_that_auto_mode_is_the_default():
    text = README.read_text(encoding="utf-8")
    grants = "\n".join(section(text, "#### What setup writes"))
    assert "Setup never proposes anything that grants" in grants
    assert "auto mode" in grants and "2.1.326" in grants and "shift+tab" in grants


def test_the_readme_says_an_ask_may_not_stop_a_push_in_auto_mode_and_makes_no_timing_claim():
    text = README.read_text(encoding="utf-8")
    guard = "\n".join(section(text, "### The git guard")).replace("\n  ", " ")
    bullet = guard[guard.index("**Auto mode.**"):].split("- **Never allows.**")[0]
    assert "a push was seen to run after a guard ask with no recorded answer" in bullet
    assert "not yet confirmed" in bullet
    assert "Use `deny` for a hard stop" in bullet
    # No timing claim: no figure of seconds in the bullet, and the old sentence is gone.
    assert not re.search(r"\bseconds?\b", bullet)
    assert not re.search(r"\d", bullet)
    assert "unanswered" not in bullet
    assert "13 to 14" not in text
    # The ticket that tracks the fix is cited in the pull request, not here.
    assert "DT-RAILS-AUTOMODE-ASK1" not in text


def rails_defaults():
    manifest = json.loads((ROOT / "plugins" / "rails" / ".claude-plugin" / "plugin.json")
                          .read_text(encoding="utf-8"))
    return {key: spec["default"] for key, spec in manifest["userConfig"].items()}


def test_the_readme_rails_install_stores_all_three_options_at_their_defaults():
    text = README.read_text(encoding="utf-8")
    commands = [line.strip() for line in text.splitlines()
                if "claude plugin configure rails@harness-core --values-stdin" in line
                and line.strip().startswith("printf ")]
    assert len(commands) == 1
    assert "| claude plugin configure rails@harness-core --values-stdin" in commands[0]
    payload = shlex.split(commands[0])[1]
    assert json.loads(payload) == rails_defaults()
    assert "claude plugin install rails@harness-core" in text.splitlines()[
        next(i for i, line in enumerate(text.splitlines()) if line.strip() == commands[0]) - 1]


def test_the_readme_does_not_call_the_rails_options_optional_without_the_hook_error():
    text = README.read_text(encoding="utf-8")
    flat = text.replace("\n  ", " ").replace("\n", " ")
    assert "setting them is optional" not in flat
    assert "you need not set its options" not in flat
    options = flat[flat.index("**Options.**"):].split("- **Never allows.**")[0]
    assert "Plugin option push_to_main isn't set" in options
    assert "non-blocking" in options
    install = flat[flat.index("- `rails`, a folder"):flat.index("`sdlc` and `ledger-hooks` are each fixed")]
    assert "Plugin option push_to_main isn't set" in install


def test_known_limits_no_longer_carries_the_project_allow_hook_line():
    text = (ROOT / "KNOWN-LIMITS.md").read_text(encoding="utf-8")
    assert "a project's own PreToolUse hook that returns allow" not in text
    assert "L5a" not in text


def test_the_skill_asks_for_the_operator_by_typed_text_not_a_picker():
    step = skill_step(SKILL.read_text(encoding="utf-8"), 1)
    flat = " ".join(step.split())
    assert "Type it as your next message" in flat
    assert "Never ask for it with a picker" in flat


def test_the_skill_shows_the_plan_in_its_own_reply_before_the_yes():
    text = SKILL.read_text(encoding="utf-8")
    plan = " ".join(skill_step(text, 2).split())
    assert "Copy the whole output into your own reply" in plan
    for part in ("`FILE`", "`LEDGER`", "`FIRST LEDGER WRITE`", "`OPTIONAL`"):
        assert part in plan, part
    assert "before you ask the yes" in plan
    assert "already be in your own reply" in " ".join(skill_step(text, 3).split())
    # The rule that holds in every mode: "show" means copy into the reply.
    assert "copy the output into your own reply" in " ".join(text.split())


def test_the_skill_claims_no_removal_the_remove_output_does_not_name(tmp_path):
    text = " ".join(SKILL.read_text(encoding="utf-8").split())
    assert "It does not name the placed files or settings items it took out, so do not say it removed them" in text
    # What the script's remove output does name is what the skill lists.
    home, bin_dir = fixture(tmp_path, settings=None)
    set_up(home, bin_dir)
    removed = do("remove", home, bin_dir)
    assert removed.rc == 0, removed.err
    names = {line.split(":", 1)[0] for line in removed.out.splitlines()}
    assert names <= {"kept for a manual check", "already gone", "kept", "removed"}
    assert RULES not in removed.out and OVERRIDE not in removed.out


# ---------------------------------------------------------------------------
# The second walk's README friction (v0.1.5): each claim a newcomer meets is pinned.

CHANGELOG = ROOT / "CHANGELOG.md"


def one_line(text):
    return " ".join(text.split())


def changelog_text():
    return CHANGELOG.read_text(encoding="utf-8")


def changelog_headings():
    return re.findall(r"^## (.+)$", changelog_text(), re.MULTILINE)


def changelog_top_entry():
    return changelog_text().split("\n## ")[1]


def changelog_entry(heading):
    return next(part for part in changelog_text().split("\n## ")
                if part.splitlines()[0] == heading)


def plugin_version(name):
    manifest = ROOT / "plugins" / name / ".claude-plugin" / "plugin.json"
    return json.loads(manifest.read_text(encoding="utf-8"))["version"]


def test_the_changelog_has_a_release_entry_on_top_and_no_unreleased_heading():
    headings = changelog_headings()
    assert re.fullmatch(r"v\d+\.\d+\.\d+", headings[0]), headings[0]
    assert not [heading for heading in headings if "unreleased" in heading.lower()]


def test_the_readme_names_the_current_release_tag_and_one_command_to_read_it():
    readme = README.read_text(encoding="utf-8")
    text = one_line(readme)
    found = re.search(r"use the current one, which is `(v\d+\.\d+\.\d+)`\.", text)
    assert found, "the install block does not name the current tag"
    assert found.group(1) == changelog_headings()[0]
    assert "Each release moves it" in text and "Tags page" in text
    assert f"git ls-remote --tags {OWN_URL}" in readme.splitlines()


def test_the_readme_prerequisites_name_a_git_name_and_email():
    intro = one_line("\n".join(section(README.read_text(encoding="utf-8"),
                                       "## Install the default tier (alpha)")))
    needs = intro[intro.index("You need Claude Code"):].split("network access")[0]
    assert "git config --global user.name" in needs
    assert "git config --global user.email" in needs
    assert "makes commits" in needs


def test_the_readme_says_to_ask_a_session_to_paste_a_ledger_commands_output():
    first_use = one_line("\n".join(section(README.read_text(encoding="utf-8"), "### First use")))
    bullet = first_use[first_use.index("**The `ledger` command.**"):]
    assert "ask the session to paste the command's output into its reply" in bullet
    assert "collapses a tool's output" in bullet


def test_the_readme_says_what_to_answer_to_the_sandbox_switch_and_the_deny_rules():
    assert len(setup.DENY_RULES) == 6
    written = one_line("\n".join(section(README.read_text(encoding="utf-8"),
                                         "#### What setup writes")))
    sandbox = written[written.index("**The sandbox switch.**"):written.index("**The six deny rules.**")]
    deny = written[written.index("**The six deny rules.**"):written.index("Claude Code 2.1.326 makes")]
    assert "No is safe" in sandbox and "you give up that confinement" in sandbox
    assert "Yes is recommended" in deny
    assert "No is safe: you give up that stop" in deny


def test_the_readme_version_labels_match_the_manifests_and_the_pin():
    text = one_line(README.read_text(encoding="utf-8"))
    own = re.search(r"`harness` and `rails` show (\d+\.\d+\.\d+), the version in their manifests",
                    text)
    assert own, "the README does not say what harness and rails show"
    assert own.group(1) == plugin_version("harness") == plugin_version("rails")
    skills = re.search(r"`sdlc` shows (\d+\.\d+\.\d+), which is the skills release `(v[^`]+)` "
                       r"at the pinned commit", text)
    assert skills, "the README does not say what sdlc shows"
    source = next(e for e in marketplace_manifest()["plugins"] if e["name"] == "sdlc")["source"]
    assert skills.group(2) == source["ref"]
    # The skills plugin's own version is not in this repository; the CHANGELOG records which
    # version sits at each pinned commit, and the README must agree with it for the pin it names.
    recorded = {sha: version for version, sha in re.findall(
        r"`sdlc` plugin at (\d+\.\d+\.\d+), commit `([0-9a-f]{40})`", one_line(changelog_text()))}
    assert recorded[source["sha"]] == skills.group(1)


def test_the_readme_gives_the_command_that_confirms_the_rules_files_are_gone():
    lines = section(README.read_text(encoding="utf-8"), "### Uninstall")
    assert [line for line in lines if line.startswith("ls ")] == [f"ls $HOME/{RULES} $HOME/{OVERRIDE}"]
    assert "No such file or directory" in one_line("\n".join(lines))


def test_the_readme_marks_search_db_as_present_only_if_ledger_search_ran():
    rows = [line for line in section(README.read_text(encoding="utf-8"),
                                     "#### What is left after uninstall")
            if line.startswith("| `.local/share/ledger/search.db`")]
    assert len(rows) == 1
    assert "only if `ledger search` ever ran" in rows[0]


def test_the_release_entry_corrects_the_v0_1_4_known_limits_claim_and_moves_no_plugin():
    entry = one_line(changelog_entry("v0.1.5"))
    assert "KNOWN-LIMITS.md" in entry and "the file only gained the five lines" in entry
    stays = re.search(r"`harness` and `rails` stay at (\d+\.\d+\.\d+)", entry)
    assert stays, "the entry does not say the plugin versions stay"
    # A past entry records the version its tag carried, not the current manifest's.
    assert stays.group(1) == "0.1.1"


# ---------------------------------------------------------------------------
# The third walk's README friction (v0.1.6): each claim a newcomer meets is pinned.


def readme_section(heading):
    return one_line("\n".join(section(README.read_text(encoding="utf-8"), heading)))


def test_the_readme_says_the_setup_plan_may_be_collapsed_and_ctrl_o_shows_it():
    text = one_line(README.read_text(encoding="utf-8"))
    setup_text = readme_section("### Set up")
    assert "The plan may appear collapsed under \"Ran 1 shell command\"" in setup_text
    assert "press ctrl+o to show it" in setup_text
    assert "before you answer the question about the files" in setup_text
    assert "without expanding anything" not in text


def test_the_readme_names_the_background_work_dialog_and_which_choice_to_make():
    first_use = readme_section("### First use")
    bullet = first_use[first_use.index("**Leaving while a build runs.**"):]
    bullet = bullet.split("**A diff panel may open.**")[0]
    assert "`/exit`" in bullet and "\"Background work is running\"" in bullet
    for choice in ("Exit and stop tasks", "Move to background and exit", "Stay"):
        assert f"\"{choice}\"" in bullet, choice
    assert "Choose \"Exit and stop tasks\"" in bullet and "stops the build" in bullet


def test_the_readme_says_what_resume_does_and_claims_no_question():
    first_use = readme_section("### First use")
    bullet = first_use[first_use.index("**Stop and resume.**"):].split("**Leaving while")[0]
    assert ("If the earlier session was ended with `/exit`, the skill reopens the ticket by its id "
            "and carries on from where it stopped, and asks nothing") in bullet
    assert ("If the earlier session crashed instead, the skill asks you to confirm that session "
            "is gone before it releases the old claim") in bullet
    assert "choose release and resume" not in one_line(README.read_text(encoding="utf-8"))


def test_the_readme_says_a_diff_panel_may_open_and_slash_diff_closes_it():
    first_use = readme_section("### First use")
    bullet = first_use[first_use.index("**A diff panel may open.**"):]
    bullet = bullet.split("**The `ledger` command.**")[0]
    assert "diff side panel on its own" in bullet
    assert "Type `/diff` to close it" in bullet


def test_the_readme_says_uninstall_asks_yes_or_no_before_it_removes_anything():
    uninstall = readme_section("### Uninstall")
    step = uninstall[uninstall.index("1. In a session"):uninstall.index("2. In a terminal")]
    assert "asks you to answer yes or no before it removes anything" in step
    assert step.index("shows what it will remove") < step.index("yes or no") < step.index(
        "and then removes it")


def test_the_readme_ledger_hooks_version_matches_the_marketplace_pin():
    text = one_line(README.read_text(encoding="utf-8"))
    found = re.search(r"`ledger-hooks` shows (\d+\.\d+\.\d+), which is the ledger release "
                      r"`(v[^`]+)` at the pinned commit", text)
    assert found, "the README does not say what ledger-hooks shows"
    source = next(e for e in marketplace_manifest()["plugins"]
                  if e["name"] == "ledger-hooks")["source"]
    assert found.group(2) == source["ref"]
    assert f"v{found.group(1)}" == source["ref"]
    # The plugin list sentence names every plugin the list shows, ledger-hooks among them.
    assert "The list shows `harness`, `sdlc` and `ledger-hooks` enabled." in text


def test_the_v0_1_6_entry_covers_the_walk_fixes_and_moves_no_plugin():
    headings = changelog_headings()
    first = headings.index("v0.1.6")
    assert headings[first:first + 2] == ["v0.1.6", "v0.1.5"]
    entry = one_line(changelog_entry("v0.1.6"))
    for claim in ("collapsed under \"Ran 1 shell command\"", "ctrl+o",
                  "\"Background work is running\"", "release and resume",
                  "if the earlier session crashed, the skill still asks",
                  "`/diff` closes it", "asks a yes or no question",
                  "`ledger-hooks` shows, 0.4.0"):
        assert claim in entry, claim
    stays = re.search(r"`harness` and `rails` stay at (\d+\.\d+\.\d+)", entry)
    assert stays, "the entry does not say the plugin versions stay"
    # A past entry records the version its tag carried, not the current manifest's.
    assert stays.group(1) == "0.1.1"


# ---------------------------------------------------------------------------
# The v0.2.0 release: the entry names each plugin's move, and the manifests agree with it.


def test_the_v0_2_0_entry_names_each_plugin_move_and_the_manifests_carry_it():
    headings = changelog_headings()
    assert headings[headings.index("v0.2.0"):][:2] == ["v0.2.0", "v0.1.6"]
    entry = one_line(changelog_entry("v0.2.0"))
    assert "`harness` from 0.1.1 to 0.2.0" in entry and "`rails` from 0.1.1 to 0.2.0" in entry
    if headings[0] == "v0.2.0":
        # While this is the newest entry, the manifests carry the versions it names.
        assert plugin_version("harness") == plugin_version("rails") == "0.2.0"
    for section_name in ("`harness` 0.2.0:", "`rails` 0.2.0:"):
        assert section_name in entry, section_name
    for number in ("#21", "#22", "#23", "#24", "#25", "#26", "#27"):
        assert number in entry, number
    assert "No pin move" in entry
    if headings[0] == "v0.2.0":
        # While this is the newest entry, the pins are the ones it names.
        sources = {e["name"]: e["source"] for e in marketplace_manifest()["plugins"]
                   if isinstance(e["source"], dict)}
        assert sources["sdlc"]["ref"] == "v1.5.0" and sources["ledger-hooks"]["ref"] == "v0.6.1"


# ---------------------------------------------------------------------------
# The v0.3.0 release: the entry names the pin move, and neither plugin moves.


def test_the_v0_3_0_entry_names_the_pin_move_and_neither_plugin_moves():
    headings = changelog_headings()
    assert headings[headings.index("v0.3.0"):][:2] == ["v0.3.0", "v0.2.0"]
    entry = one_line(changelog_entry("v0.3.0"))
    stays = re.search(r"`harness` and `rails` stay at (\d+\.\d+\.\d+)", entry)
    assert stays, "the entry does not say the plugin versions stay"
    assert stays.group(1) == "0.2.0"
    if headings[0] == "v0.3.0":
        assert plugin_version("harness") == plugin_version("rails") == "0.2.0"
        sources = {e["name"]: e["source"] for e in marketplace_manifest()["plugins"]
                   if isinstance(e["source"], dict)}
        assert sources["sdlc"]["ref"] == "v1.5.0" and sources["ledger-hooks"]["ref"] == "v0.6.1"
    for claim in ("`v0.4.0` to `v0.6.1`", "`v0.1.13` to `v0.6.1`",
                  "The search index format is 4", "Fable 5.2",
                  "No plugin change"):
        assert claim in entry, claim
    for number in ("#29", "#30"):
        assert number in entry, number


# ---------------------------------------------------------------------------
# The v0.3.1 release: the entry names the pin move, and neither plugin moves.


def test_the_v0_3_1_entry_names_the_pin_move_and_neither_plugin_moves():
    headings = changelog_headings()
    assert headings[headings.index("v0.3.1"):][:2] == ["v0.3.1", "v0.3.0"]
    entry = one_line(changelog_entry("v0.3.1"))
    stays = re.search(r"`harness` and `rails` stay at (\d+\.\d+\.\d+)", entry)
    assert stays, "the entry does not say the plugin versions stay"
    assert stays.group(1) == "0.2.0"
    if headings[0] == "v0.3.1":
        assert plugin_version("harness") == plugin_version("rails") == "0.2.0"
        sources = {e["name"]: e["source"] for e in marketplace_manifest()["plugins"]
                   if isinstance(e["source"], dict)}
        assert sources["sdlc"]["ref"] == "v1.5.0" and sources["ledger-hooks"]["ref"] == "v0.6.3"
    for claim in ("`v0.6.1` to `v0.6.3`", "The search index format is 4",
                  "No plugin change", "No new SQL migration", "schema 10",
                  "Ledger v0.6.2", "Ledger v0.6.3"):
        assert claim in entry, claim


def test_the_readme_and_changelog_do_not_use_the_word_builder():
    for path in (README, CHANGELOG):
        assert "builder" not in path.read_text(encoding="utf-8").lower(), path.name

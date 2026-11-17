"""Tests of the doctor and ledger-init verbs of plugins/harness/setup.py.

Doctor reads and reports: it writes nothing, takes no lock and opens no file
for writing. Ledger-init makes the first ledger write, which creates the
database file, and leaves a database that already exists alone. `claude` and
`ledger` are stub commands on a PATH that holds nothing else.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from harness_fixtures import (
    OPERATOR, SEEDED_SETTINGS, at, load_setup, make_bin, make_home, put_stub, setup_run,
    setup_run_traced, show_json, show_stored, snap,
)

setup = load_setup()
ALL_IDS = [ident for ident, _text in setup.optional_items()]
RULES, OVERRIDE, RECORD, DB, SETTINGS = (setup.RULES_REL, setup.OVERRIDE_REL, setup.RECORD_REL,
                                         setup.DB_REL, setup.SETTINGS_REL)


def fixture(tmp_path, *, settings=SEEDED_SETTINGS, name="case", **bin_flags):
    folder = tmp_path / name
    folder.mkdir()
    return make_home(folder, settings=settings), make_bin(folder, **bin_flags)


def args_for(ids):
    out = []
    for ident in ids:
        out += ["--approve", ident]
    return out


def finish_setup(home, bin_dir, ids=ALL_IDS):
    applied = setup_run("apply", home, *args_for(ids), bin_dir=bin_dir)
    assert applied.rc == 0, applied.err
    show_stored(bin_dir)
    init = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert init.rc == 0, init.err


def doctor(home, bin_dir):
    done = setup_run("doctor", home, bin_dir=bin_dir)
    assert done.err == "", done.err
    return done.rc, done.out.splitlines()


def lines_of(lines, status, area):
    return [line for line in lines if line.startswith(f"{status} {area}: ")]


# ---------------------------------------------------------------------------
# Doctor, after a complete setup


def test_doctor_reports_the_placed_files_the_stored_options_and_the_database(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    rc, lines = doctor(home, bin_dir)
    assert rc == 0, lines
    assert lines_of(lines, "ok", "record") == [f"ok record: setup record is complete: {at(home, RECORD)}"]
    assert sorted(lines_of(lines, "ok", "files")) == sorted([
        f"ok files: placed by setup and unchanged: {at(home, RULES)}",
        f"ok files: placed by setup and unchanged: {at(home, OVERRIDE)}"])
    assert len(lines_of(lines, "ok", "settings")) == 1 + len(setup.DENY_RULES)
    assert lines_of(lines, "ok", "options") == [
        f"ok options: ledger_db is set: {at(home, DB)}",
        f"ok options: ledger_operator is set: {OPERATOR}",
        "ok options: ledger_harness is set: claude-code"]
    database = lines_of(lines, "ok", "ledger")
    assert len(database) == 1 and f"the ledger database exists: {at(home, DB)} (" in database[0]
    assert lines_of(lines, "FAIL", "ledger") == []
    assert lines[-1] == f"doctor: {len(lines) - 1} ok, 0 note, 0 FAIL"


def test_doctor_writes_nothing_takes_no_lock_and_opens_nothing_for_writing(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    before = snap(home)
    done = setup_run_traced("doctor", home, bin_dir=bin_dir)
    assert done.rc == 0, done.err
    # Starting `claude` opens /dev/null for its input; nothing else is opened for writing.
    assert [event for event in done.events if event["path"] != "/dev/null"] == [], done.events
    assert snap(home) == before


def test_doctor_before_setup_fails_by_name_and_still_checks_the_rest(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    (bin_dir / "claude-show.json").write_text(show_json(None), encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert f"FAIL record: setup has not run: no record at {at(home, RECORD)}" in lines
    assert f"FAIL files: missing: {at(home, RULES)}" in lines
    assert any(line.startswith("FAIL options: ledger_db is not set") for line in lines)
    assert any(line.startswith("FAIL ledger: the ledger database is missing") for line in lines)
    assert not at(home, ".claude").exists(), "doctor made a folder"


def test_doctor_names_an_unfinished_record(tmp_path):
    home, bin_dir = fixture(tmp_path)
    folder = at(home, setup.SETUP_DIR_REL)
    folder.mkdir(parents=True)
    (folder / "setup-record.jsonl").write_bytes(setup.header_line(None))
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert any(line.startswith("FAIL record: an earlier setup run stopped before it finished")
               for line in lines)


# ---------------------------------------------------------------------------
# Doctor: the ledger database


def test_doctor_fails_when_the_database_file_is_missing(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    at(home, DB).unlink()
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert lines_of(lines, "FAIL", "ledger") == [
        f"FAIL ledger: the ledger database is missing: {at(home, DB)}; run ledger-init"]


def test_doctor_fails_when_the_database_file_is_empty(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    at(home, DB).write_bytes(b"")
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert lines_of(lines, "FAIL", "ledger") == [
        f"FAIL ledger: the ledger database is empty: {at(home, DB)}"]


def test_doctor_checks_the_database_the_stored_option_names(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    elsewhere = home / "elsewhere.db"
    elsewhere.write_bytes(b"SQLite format 3\0" + b"x" * 100)
    at(home, DB).unlink()
    (bin_dir / "claude-show.json").write_text(
        show_json({"ledger_db": str(elsewhere), "ledger_operator": OPERATOR}), encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert rc == 0, lines
    assert any(line.startswith("note options: ledger_db is not the default path")
               for line in lines)
    assert lines_of(lines, "ok", "ledger")[0].startswith(
        f"ok ledger: the ledger database exists: {elsewhere} (")


# ---------------------------------------------------------------------------
# Doctor: the stored options


@pytest.mark.parametrize("stored, declared, expect", [
    ({}, ("ledger_db", "ledger_operator", "ledger_harness"),
     ["FAIL options: ledger_db is not set", "FAIL options: ledger_operator is not set",
      "note options: ledger_harness is not set"]),
    ({"ledger_db": "/x/ledger.db", "ledger_operator": "me"}, ("ledger_db", "ledger_operator", "ledger_harness"),
     ["ok options: ledger_db is set: /x/ledger.db", "ok options: ledger_operator is set: me",
      "note options: ledger_harness is not set"]),
    ({}, (),
     ["FAIL options: ledger-hooks@harness-core declares no option ledger_db",
      "FAIL options: ledger-hooks@harness-core declares no option ledger_operator",
      "note options: ledger-hooks@harness-core declares no option ledger_harness"]),
], ids=["nothing-stored", "harness-left-unset", "plugin-declares-no-options"])
def test_doctor_reports_each_stored_option(tmp_path, stored, declared, expect):
    home, bin_dir = fixture(tmp_path, settings=None)
    (bin_dir / "claude-show.json").write_text(show_json(stored, declared), encoding="utf-8")
    _rc, lines = doctor(home, bin_dir)
    assert [line for line in lines if " options: " in line
            and "not the default path" not in line] == expect


def test_doctor_fails_by_name_when_claude_is_not_on_the_path(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None, claude=False)
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert "FAIL options: claude is not on PATH; cannot read the stored ledger options" in lines


def test_doctor_names_a_claude_that_cannot_be_started(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    put_stub(bin_dir, "claude", "#!/nonexistent/interpreter\n")
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert any(line.startswith("FAIL options: claude plugin configure failed: ")
               for line in lines)


def test_doctor_fails_by_name_when_configure_fails_or_prints_nonsense(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None)
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert "FAIL options: claude plugin configure failed: no stored options" in lines
    (bin_dir / "claude-show.json").write_text("not json", encoding="utf-8")
    _rc, lines = doctor(home, bin_dir)
    assert "FAIL options: claude plugin configure --json gave output setup cannot read" in lines


# ---------------------------------------------------------------------------
# Doctor: files and settings items


def test_doctor_notes_an_edited_file_and_fails_a_missing_one(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    override = at(home, OVERRIDE)
    override.write_text(override.read_text(encoding="utf-8") + "mine\n", encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert rc == 0
    assert f"note files: changed since setup: {override}; uninstall keeps it" in lines
    at(home, RULES).unlink()
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert f"FAIL files: placed by setup and now missing: {at(home, RULES)}" in lines


def test_doctor_notes_a_settings_item_that_is_gone_moved_or_changed(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    path = at(home, SETTINGS)
    data = json.loads(path.read_text(encoding="utf-8"))
    del data["sandbox"]
    data["permissions"]["deny"].reverse()
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert rc == 0, lines
    assert "note settings: settings key sandbox is already gone; uninstall skips it" in lines
    moved = [line for line in lines if line.startswith("note settings: deny rule ")]
    assert moved and all("was moved or changed since setup; uninstall keeps it" in line
                         for line in moved)


def test_doctor_notes_an_override_the_person_already_had(tmp_path):
    home, bin_dir = fixture(tmp_path)
    at(home, OVERRIDE).parent.mkdir(parents=True)
    at(home, OVERRIDE).write_text("<!-- mine -->\n", encoding="utf-8")
    finish_setup(home, bin_dir, ids=[])
    rc, lines = doctor(home, bin_dir)
    assert rc == 0, lines
    assert (f"note files: present, but setup did not place it: {at(home, OVERRIDE)}; "
            "uninstall leaves it") in lines


def test_doctor_says_when_setup_added_no_settings_item(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir, ids=[])
    rc, lines = doctor(home, bin_dir)
    assert rc == 0, lines
    assert lines_of(lines, "ok", "settings") == ["ok settings: setup added no settings item"]


def test_doctor_notes_settings_items_when_the_settings_file_is_gone(tmp_path):
    home, bin_dir = fixture(tmp_path)
    finish_setup(home, bin_dir)
    at(home, SETTINGS).unlink()
    rc, lines = doctor(home, bin_dir)
    assert rc == 0, lines
    gone = lines_of(lines, "note", "settings")
    assert len(gone) == 1 + len(setup.DENY_RULES)
    assert all("is already gone" in line and "is absent" in line for line in gone)


def test_doctor_names_a_leftover_settings_backup_with_no_record(tmp_path):
    home, bin_dir = fixture(tmp_path)
    folder = at(home, setup.SETUP_DIR_REL)
    folder.mkdir(parents=True)
    (folder / "settings-backup.json").write_text("{}\n", encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert (f"FAIL record: leftover settings backup: {at(home, setup.BACKUP_REL)}") in lines


# ---------------------------------------------------------------------------
# Doctor: tools


def test_doctor_fails_when_no_python_3_11_is_on_the_path(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None, python=False)
    (bin_dir / "claude-show.json").write_text(show_json(None), encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert rc == 1
    assert ("FAIL tools: Python 3.11 or later is not on PATH; the ledger command needs it"
            in lines)
    put_stub(bin_dir, "python3", "#!/bin/sh\nexit 1\n")
    _rc, lines = doctor(home, bin_dir)
    assert any(line.startswith("FAIL tools: Python 3.11") for line in lines)


def test_doctor_notes_a_ledger_command_that_is_not_on_the_path(tmp_path):
    home, bin_dir = fixture(tmp_path, settings=None, ledger=False)
    (bin_dir / "claude-show.json").write_text(show_json(None), encoding="utf-8")
    rc, lines = doctor(home, bin_dir)
    assert ("note tools: the ledger command is not on PATH in this session; run "
            "/reload-plugins or start a new session") in lines
    assert lines_of(lines, "ok", "tools")[0].startswith("ok tools: Python 3.11 or later: ")


# ---------------------------------------------------------------------------
# Ledger-init


def applied_home(tmp_path, **bin_flags):
    home, bin_dir = fixture(tmp_path, **bin_flags)
    done = setup_run("apply", home, bin_dir=bin_dir)
    assert done.rc == 0, done.err
    return home, bin_dir


def test_ledger_init_creates_the_database_with_one_closed_topic(tmp_path):
    home, bin_dir = applied_home(tmp_path)
    assert not at(home, DB).exists()
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 0, done.err
    assert done.out == f"created: ledger database at {at(home, DB)}\n"
    listing = subprocess.run([str(bin_dir / "ledger"), "--db", str(at(home, DB)), "query"],
                             capture_output=True, text=True, env={"PATH": str(bin_dir)})
    assert listing.returncode == 0, listing.stderr
    rows = [line.split("\t") for line in listing.stdout.splitlines()]
    assert len(rows) == 1 and rows[0][1:] == ["topic", "closed", "Harness setup"]
    stat = at(home, DB).stat()
    assert stat.st_mode & 0o077 == 0, "the database is readable by others"


def test_ledger_init_leaves_a_database_that_already_exists_untouched(tmp_path):
    home, bin_dir = applied_home(tmp_path)
    at(home, DB).parent.mkdir(parents=True)
    at(home, DB).write_bytes(b"the person's own ledger")
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 0, done.err
    assert done.out == (f"kept: ledger database already exists at {at(home, DB)}; "
                        "no first write was made\n")
    assert at(home, DB).read_bytes() == b"the person's own ledger"
    assert not (bin_dir / "ledger-argv").exists(), "the ledger command ran"


def test_ledger_init_needs_a_complete_setup_record(tmp_path):
    home, bin_dir = fixture(tmp_path)
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 1
    assert done.err == f"setup: no record at {at(home, RECORD)}; run apply first\n"
    assert not at(home, DB).exists()


def test_ledger_init_refuses_by_name_when_the_ledger_command_is_missing(tmp_path):
    home, bin_dir = applied_home(tmp_path, ledger=False)
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 1
    assert done.err.startswith("setup: the ledger command is not on PATH;")
    assert not at(home, DB).exists()


def test_ledger_init_names_a_failed_first_write(tmp_path):
    home, bin_dir = applied_home(tmp_path)
    put_stub(bin_dir, "ledger", "#!/bin/sh\necho 'cannot open database' >&2\nexit 3\n")
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 1
    assert done.err == "setup: the first ledger write failed: cannot open database\n"


def test_ledger_init_names_a_ledger_command_that_cannot_be_started(tmp_path):
    home, bin_dir = applied_home(tmp_path)
    put_stub(bin_dir, "ledger", "#!/nonexistent/interpreter\n")
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 1
    assert done.err.startswith("setup: the first ledger write failed: ")
    assert not at(home, DB).exists()


def test_ledger_init_does_not_trust_a_success_that_made_no_database(tmp_path):
    home, bin_dir = applied_home(tmp_path)
    put_stub(bin_dir, "ledger", "#!/bin/sh\nexit 0\n")
    done = setup_run("ledger-init", home, bin_dir=bin_dir)
    assert done.rc == 1
    assert done.err == (f"setup: the first ledger write reported success but made no "
                        f"database at {at(home, DB)}\n")


def test_ledger_init_needs_an_operator(tmp_path):
    home, bin_dir = applied_home(tmp_path)
    done = setup_run("ledger-init", home, bin_dir=bin_dir, operator=None)
    assert done.rc == 1
    assert done.err == "setup: --operator is required for ledger-init\n"

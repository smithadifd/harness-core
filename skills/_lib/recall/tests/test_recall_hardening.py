"""Round-2 hardening: every ``cwd`` counts, strict link resolution, safe terminal output,
concurrent reads, the stored re-checks, OMP timestamps and path expansion.

Test names avoid the word "room": pytest names ``tmp_path`` after the test,
and the guard refuses any path that holds that word.
"""
from __future__ import annotations

import io
import json
import os
import sqlite3
import time
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from ledger.cli import main
from ledger.store import claim_database
from ledger.verbs import EXIT_OK, EXIT_USAGE
from recall import index as recall_index
from recall import ledger_verb
from recall.extract import CLAUDE, OMP
from recall.guard import Decoder, Guard, encode

from recall_fixtures import claude_user, write_jsonl

needs_permissions = pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root ignores directory modes")


def _layout(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".omp" / "agent" / "sessions").mkdir(parents=True)
    (home / "workspaces" / "home").mkdir(parents=True)
    (home / "code" / "demo").mkdir(parents=True)
    (home / "rooms" / "y" / "sub").mkdir(parents=True)
    return home


def _guard(home: Path) -> Guard:
    return Guard(home, "home", workspace_names=["home"])


def _refresh(tmp_path: Path, home: Path):
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    stats = recall_index.refresh(conn, _guard(home), recall_index.default_sources(home))
    return conn, stats


def _all_hits(conn) -> list[str]:
    return [row[0] for row in conn.execute("SELECT text FROM text_index")]


# 1. Every record's cwd counts, not only the first.

def test_session_that_moves_into_the_instances_indexes_nothing(tmp_path):
    home = _layout(tmp_path)
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl", [
        claude_user("alpha start", cwd=str(home / "code" / "demo")),
        claude_user("zebra inside instance", cwd=str(home / "rooms" / "y")),
    ])
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert stats.files_read == []
    assert _all_hits(conn) == []  # the earlier "alpha start" row was rolled back too
    assert conn.execute("SELECT count(*) FROM files").fetchone() == (0,)


def test_a_previously_indexed_file_that_moves_later_loses_its_rows(tmp_path):
    home = _layout(tmp_path)
    name = encode(str(home / "code" / "demo"))
    path = write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                       [claude_user("alpha start", cwd=str(home / "code" / "demo"))])
    conn, _ = _refresh(tmp_path, home)
    assert _all_hits(conn) == ["alpha start"]
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(claude_user("zebra later", cwd=str(home / "rooms" / "y"))) + "\n")
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    stats = recall_index.refresh(conn, _guard(home), recall_index.default_sources(home))
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert stats.files_removed == 1
    assert _all_hits(conn) == []


def test_every_stored_cwd_is_resolved_again_on_later_runs(tmp_path):
    """A session visited the home folder's ``proj`` after starting in its ``code/demo``.
    ``proj`` later becomes a link into ``~/rooms``: the unchanged file loses its rows."""
    home = _layout(tmp_path)
    (home / "proj").mkdir()
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl", [
        claude_user("alpha start", cwd=str(home / "code" / "demo")),
        claude_user("zebra visited", cwd=str(home / "proj")),
    ])
    conn, stats = _refresh(tmp_path, home)
    assert len(stats.files_read) == 1
    (home / "proj").rmdir()
    os.symlink(home / "rooms" / "y", home / "proj")
    assert recall_index.purge_refused(conn, _guard(home)) == 1
    assert _all_hits(conn) == []


# 2. Strict resolution: an unreadable parent never hides a link into ~/rooms.

@needs_permissions
def test_unreadable_parent_hides_no_link_option_a(tmp_path):
    home = _layout(tmp_path)
    locked = home / "locked"
    locked.mkdir()
    os.symlink(home / "rooms" / "y", locked / "lnk")
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                [claude_user("zebra hidden", cwd=str(locked / "lnk" / "sub"))])
    os.chmod(locked, 0)
    try:
        conn, stats = _refresh(tmp_path, home)
    finally:
        os.chmod(locked, 0o700)
    assert stats.files_refused == [(CLAUDE, name, "unresolvable path")]
    assert _all_hits(conn) == []


@needs_permissions
def test_unreadable_parent_hides_no_link_option_b(tmp_path):
    """No ``cwd``: the folder ``~/locked-dir/lnk/sub`` cannot be walked below
    ``~/locked-dir`` (mode 000), so what lies below is unknown and the file is refused."""
    home = _layout(tmp_path)
    locked = home / "locked-dir"
    locked.mkdir()
    os.symlink(home / "rooms" / "y", locked / "lnk")
    name = encode(str(locked / "lnk" / "sub"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl", [claude_user("zebra hidden")])
    os.chmod(locked, 0)
    try:
        conn, stats = _refresh(tmp_path, home)
    finally:
        os.chmod(locked, 0o700)
    assert stats.files_refused == [(CLAUDE, name, "unresolvable path")]
    assert _all_hits(conn) == []


def test_missing_historical_path_is_still_admitted(tmp_path):
    """A project folder deleted since (ENOENT) is not an unreadable one."""
    home = _layout(tmp_path)
    gone = home / "code" / "deleted-since"
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                [claude_user("zebra old", cwd=str(gone))])
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == []
    assert _all_hits(conn) == ["zebra old"]


def test_dangling_link_into_the_instances_is_refused(tmp_path):
    """``~/proj`` links to a ``~/rooms`` folder that no longer exists."""
    home = _layout(tmp_path)
    os.symlink(home / "rooms" / "gone", home / "proj")
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                [claude_user("zebra dangling", cwd=str(home / "proj" / "x"))])
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert _all_hits(conn) == []


# 5-7. The stored re-checks and the source-root refusal.

def test_name_basis_rows_are_decided_again(tmp_path):
    """Indexed without a ``cwd`` under ``~/a-b/c``; a link ``~/a`` into ``~/rooms``
    appears later, so the name now has a reading through it: its rows go."""
    home = _layout(tmp_path)
    (home / "a-b" / "c").mkdir(parents=True)
    name = encode(str(home / "a-b" / "c"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl", [claude_user("zebra sibling")])
    conn, stats = _refresh(tmp_path, home)
    assert len(stats.files_read) == 1
    os.symlink(home / "rooms" / "y", home / "a")
    stats = recall_index.refresh(conn, _guard(home), recall_index.default_sources(home))
    assert stats.files_removed == 1
    assert _all_hits(conn) == []


def test_stored_transcript_path_is_checked_again(tmp_path):
    home = _layout(tmp_path)
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                [claude_user("zebra", cwd=str(home / "code" / "demo"))])
    conn, _ = _refresh(tmp_path, home)
    conn.execute("UPDATE files SET path = ?", (str(home / "rooms" / "y" / "s.jsonl"),))
    assert recall_index.purge_refused(conn, _guard(home)) == 1
    assert _all_hits(conn) == []


def test_transcript_root_inside_the_instances_is_refused_unlisted(tmp_path, recorder):
    home = _layout(tmp_path)
    root = home / "rooms" / "y" / ".claude" / "projects"
    write_jsonl(root / encode(str(home / "code" / "demo")) / "s.jsonl", [claude_user("zebra")])
    found = recall_index.discover(_guard(home), {CLAUDE: root})
    assert found.files == []
    assert found.refused == [(CLAUDE, str(root), "room instance")]
    assert not [p for p in recorder.touched() if p.startswith(str(root))]


# 3. Terminal output.

@pytest.fixture
def ledger_db(tmp_path) -> str:
    db = tmp_path / "ledger" / "ledger.db"
    db.parent.mkdir()
    claim_database(db, "home")
    return str(db)


def _invoke(monkeypatch, home: Path, argv: list[str]):
    monkeypatch.setenv("HOME", str(home))
    for key in ("LEDGER_DB", "LEDGER_HARNESS"):
        monkeypatch.delenv(key, raising=False)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, plugin_modules=(ledger_verb,))
    return code, out.getvalue(), err.getvalue()


def test_control_sequences_print_as_visible_escapes(monkeypatch, tmp_path, ledger_db):
    home = _layout(tmp_path)
    name = encode(str(home / "code" / "demo"))
    write_jsonl(home / ".claude" / "projects" / name / "s.jsonl", [claude_user(
        "zebra \x1b[2JFORGED\x9b31m‮SCREEN\r\n    (recalled words are fine)",
        ts="2026-10-03T09:00:00Z", cwd=str(home / "code" / "de\x1b[1Amo"))])
    code, out, err = _invoke(monkeypatch, home, ["--db", ledger_db, "recall", "zebra"])
    assert code == EXIT_OK, err
    for raw in ("\x1b", "\x9b", "‮", "\r"):
        assert raw not in out
    assert "\\x1b[2JFORGED\\x9b31m\\u202eSCREEN\\x0d" in out
    assert "~" "/code/de\\x1b[1Amo" in out
    code, out, _ = _invoke(monkeypatch, home,
                           ["--db", ledger_db, "--json", "recall", "zebra", "--no-refresh"])
    assert "\x1b[2J" in json.loads(out)["data"]["hits"][0]["excerpt"]  # JSON keeps the text


# 9. Path expansion.

@pytest.mark.parametrize("flag", ["--index", "--project"])
def test_unknown_account_expansion_is_one_line_usage_error(monkeypatch, tmp_path, ledger_db,
                                                           flag):
    home = _layout(tmp_path)
    code, out, err = _invoke(monkeypatch, home, [
        "--db", ledger_db, "recall", "zebra", flag, "~mi12b_no_such_account/x"])
    assert code == EXIT_USAGE, (out, err)
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1
    assert "cannot expand" in err


# 8. OMP timestamps.

def test_omp_numeric_and_nested_timestamps_are_normalised(tmp_path):
    home = _layout(tmp_path)
    sessions = home / ".omp" / "agent" / "sessions" / "-code-demo"
    write_jsonl(sessions / "2026_a.jsonl", [
        {"type": "session", "id": "a", "cwd": str(home / "code" / "demo")},
        {"type": "message", "timestamp": 1759309200000, "message": {
            "role": "user", "content": "zebra numeric"}},
        {"type": "message", "message": {
            "role": "user", "timestamp": "2026-10-02T08:00:00.000Z",
            "content": "zebra nested"}},
        {"type": "message", "timestamp": 1759309260, "message": {
            "role": "user", "content": "zebra seconds"}},
    ])
    conn, _ = _refresh(tmp_path, home)
    hits, _ = recall_index.search(conn, '"zebra"', limit=5, recent=True)
    assert [(h.ts, h.excerpt) for h in hits] == [
        ("2026-10-02T08:00:00.000Z", "[zebra] nested"),
        ("2025-10-01T09:01:00.000Z", "[zebra] seconds"),
        ("2025-10-01T09:00:00.000Z", "[zebra] numeric"),
    ]
    assert {h.source for h in hits} == {OMP}


def test_mixed_timestamp_forms_sort_newest_first(tmp_path):
    """An exact-second epoch number and an ISO string 500 ms later, plus an offset
    form: one fixed-width UTC form, so ``--recent`` orders by time."""
    home = _layout(tmp_path)
    sessions = home / ".omp" / "agent" / "sessions" / "-code-demo"
    write_jsonl(sessions / "2026_a.jsonl", [
        {"type": "session", "id": "a", "cwd": str(home / "code" / "demo")},
        {"type": "message", "timestamp": 1759309200, "message": {
            "role": "user", "content": "zebra numeric"}},
        {"type": "message", "message": {
            "role": "user", "timestamp": "2025-10-01T09:00:00.5Z", "content": "zebra fractional"}},
        {"type": "message", "timestamp": "2025-10-01T11:00:00.250+02:00", "message": {
            "role": "user", "content": "zebra offset"}},
        {"type": "message", "timestamp": "not a time", "message": {
            "role": "user", "content": "zebra undated"}},
    ])
    conn, _ = _refresh(tmp_path, home)
    hits, _ = recall_index.search(conn, '"zebra"', limit=5, recent=True)
    assert [(h.ts, h.excerpt) for h in hits] == [
        ("2025-10-01T09:00:00.500Z", "[zebra] fractional"),
        ("2025-10-01T09:00:00.250Z", "[zebra] offset"),
        ("2025-10-01T09:00:00.000Z", "[zebra] numeric"),
        ("", "[zebra] undated"),
    ]


def test_index_from_the_previous_timestamp_format_is_refused_cleanly(tmp_path):
    """Version 1 stored timestamps in mixed forms: refused, with the rebuild step named."""
    _refused_at_version(tmp_path, "1")


def test_index_from_the_millisecond_timestamp_format_is_refused_cleanly(tmp_path):
    """Version 2 cut timestamps to milliseconds: refused, with the rebuild step named."""
    _refused_at_version(tmp_path, "2")


def _refused_at_version(tmp_path, version):
    path = tmp_path / "cache" / "recall-home.db"
    recall_index.open_index(path, "home").close()
    conn = sqlite3.connect(path)
    conn.execute("UPDATE meta SET value = ? WHERE key = 'schema_version'", (version,))
    conn.commit()
    conn.close()
    with pytest.raises(recall_index.RecallIndexError, match="delete it and run again"):
        recall_index.open_index(path, "home")


def test_records_in_one_millisecond_sort_newest_first(tmp_path):
    """Two stamps 800 us apart inside one millisecond keep their order under ``--recent``,
    written newest first (so a tie would sort the older one up); the display stays at milliseconds."""
    home = _layout(tmp_path)
    sessions = home / ".omp" / "agent" / "sessions" / "-code-demo"
    write_jsonl(sessions / "2026_a.jsonl", [
        {"type": "session", "id": "a", "cwd": str(home / "code" / "demo")},
        {"type": "message", "timestamp": "2026-10-02T09:00:00.000900Z", "message": {
            "role": "user", "content": "zebra newer"}},
        {"type": "message", "timestamp": "2026-10-02T09:00:00.000900123Z", "message": {
            "role": "user", "content": "zebra nanos"}},
        {"type": "message", "timestamp": "2026-10-02T09:00:00.000100Z", "message": {
            "role": "user", "content": "zebra older"}},
    ])
    conn, _ = _refresh(tmp_path, home)
    hits, _ = recall_index.search(conn, '"zebra"', limit=5, recent=True)
    assert [h.excerpt for h in hits] == ["[zebra] nanos", "[zebra] newer", "[zebra] older"]
    assert {h.ts for h in hits} == {"2026-10-02T09:00:00.000Z"}


# Round 3: the cwd key is judged by the parsed record, whatever its JSON spelling.

_ESCAPED_KEY = '"\\u0063wd"'


def _escaped_cwd_line(record: dict) -> str:
    line = json.dumps(record)
    assert '"cwd"' in line
    return line.replace('"cwd"', _ESCAPED_KEY) + "\n"


def test_escaped_cwd_key_in_a_later_record_is_still_checked(tmp_path):
    home = _layout(tmp_path)
    name = encode(str(home / "code" / "demo"))
    path = home / ".claude" / "projects" / name / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(
        json.dumps(claude_user("alpha start", cwd=str(home / "code" / "demo"))) + "\n"
        + _escaped_cwd_line(claude_user("zebra protected", cwd=str(home / "rooms" / "y"))),
        encoding="utf-8")
    assert json.loads(path.read_text().splitlines()[1])["cwd"] == str(home / "rooms" / "y")
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert _all_hits(conn) == []


def test_escaped_cwd_key_in_the_first_record_is_still_read(tmp_path):
    """Option A reads the parsed first ``cwd``, so the file is filed under it (basis
    ``cwd``), not under a reading of its folder name."""
    home = _layout(tmp_path)
    (home / "proj").mkdir()
    name = encode(str(home / "code" / "demo"))
    path = home / ".claude" / "projects" / name / "s.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(_escaped_cwd_line(claude_user("zebra escaped", cwd=str(home / "proj"))),
                    encoding="utf-8")
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == []
    assert conn.execute("SELECT project, basis FROM files").fetchall() == [
        (str(home / "proj"), "cwd")]


# 4. Concurrent readers.

_WRITER = """
import sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from recall import index
conn = index.open_index(Path(sys.argv[2]), "home")
conn.execute("PRAGMA wal_autocheckpoint = 4")
deadline = time.monotonic() + float(sys.argv[3])
n = 0
print("ready", flush=True)
while time.monotonic() < deadline:
    n += 1
    conn.execute("BEGIN IMMEDIATE")
    rid = conn.execute("INSERT INTO records (file_id, ts, kind, speaker)"
                       " VALUES (1, 't', 'user', 'person')").lastrowid
    conn.execute("INSERT INTO text_index (rowid, text) VALUES (?, ?)",
                 (rid, ("growing words %d " % n) * 40))
    conn.execute("COMMIT")
conn.close()
"""


def test_read_only_check_survives_a_concurrent_checkpointing_writer(tmp_path):
    """Another run (a separate process) grows the index, checkpointing as it goes,
    while this one validates it: no false refusal."""
    import subprocess
    import sys

    path = tmp_path / "cache" / "recall-home.db"
    recall_index.open_index(path, "home").close()
    lib = str(Path(recall_index.__file__).resolve().parents[1])
    writer = subprocess.Popen([sys.executable, "-c", _WRITER, lib, str(path), "3"],
                              stdout=subprocess.PIPE, text=True)
    try:
        assert writer.stdout.readline().strip() == "ready"
        refusals, checks = [], 0
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            checks += 1
            try:
                recall_index._validate_read_only(path, "home")
            except recall_index.RecallIndexError as exc:
                refusals.append(str(exc))
    finally:
        writer.wait(timeout=30)
    assert writer.returncode == 0
    assert checks > 50
    assert refusals == []


def test_unreadable_index_message_does_not_claim_it_is_not_an_index(tmp_path, monkeypatch):
    folder = tmp_path / "cache"
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    path = folder / "recall-home.db"
    path.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4080)
    os.chmod(path, 0o600)
    monkeypatch.setattr(recall_index, "READ_ONLY_ATTEMPTS", 2)
    with pytest.raises(recall_index.RecallIndexError) as caught:
        recall_index.open_index(path, "home")
    assert "could not be read" in str(caught.value)
    assert "not a recall index" not in str(caught.value)


# Optional: a dead run's staging file.

def test_stale_staging_file_from_a_dead_run_is_replaced(tmp_path):
    folder = tmp_path / "cache"
    folder.mkdir(mode=0o700)
    os.chmod(folder, 0o700)
    stale = folder / f".recall-home.db.{os.getpid()}.new"
    stale.write_bytes(b"left by a dead run")
    recall_index.open_index(folder / "recall-home.db", "home").close()
    assert not stale.exists()

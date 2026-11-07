"""The index: mtime-incremental refresh, per-system file, purge of gone or refused files."""
from __future__ import annotations

import json
import os
import sqlite3
import stat
from pathlib import Path

import pytest

from recall import classify
from recall import index as recall_index
from recall.extract import CLAUDE, OMP
from recall.guard import Guard

from recall_fixtures import claude_project, claude_user, write_jsonl


def _sources(home: Path) -> dict[str, Path]:
    return recall_index.default_sources(home)


def _bump(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))


def test_second_run_rereads_only_changed_files(home, tmp_path, recorder):
    projects = home / ".claude" / "projects"
    project = claude_project(projects, home / "code" / "demo")
    one = write_jsonl(project / "s1.jsonl", [claude_user("alpha one")])
    two = write_jsonl(project / "s2.jsonl", [claude_user("alpha two")])
    three = write_jsonl(project / "s3.jsonl", [claude_user("alpha three")])
    guard = Guard(home, "home")
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")

    first = recall_index.refresh(conn, guard, _sources(home))
    assert sorted(first.files_read) == sorted(map(str, (one, two, three)))

    _bump(two, json.dumps(claude_user("beta added", ts="2026-10-02T00:00:00Z")) + "\n")
    recorder.opened.clear()
    second = recall_index.refresh(conn, guard, _sources(home))
    assert second.files_read == [str(two)]
    assert recorder.opened == [str(two)]
    assert second.files_unchanged == 2

    hits, _ = recall_index.search(conn, '"beta"', limit=5)
    assert len(hits) == 1
    hits, _ = recall_index.search(conn, '"alpha"', limit=5)
    assert len(hits) == 3  # the changed file's old row was replaced, not duplicated

    third = recall_index.refresh(conn, guard, _sources(home))
    assert third.files_read == []


def test_gone_file_rows_are_deleted(home, tmp_path):
    project = claude_project(home / ".claude" / "projects", home / "code" / "demo")
    gone = write_jsonl(project / "s1.jsonl", [claude_user("gamma")])
    guard = Guard(home, "home")
    conn = recall_index.open_index(tmp_path / "recall-home.db", "home")
    recall_index.refresh(conn, guard, _sources(home))
    gone.unlink()
    stats = recall_index.refresh(conn, guard, _sources(home))
    assert stats.files_removed == 1
    assert recall_index.search(conn, '"gamma"', limit=5)[0] == []


def test_rows_purged_when_their_directory_becomes_refused(home, tmp_path):
    """A directory the guard now refuses loses its rows, even with no refresh."""
    projects = home / ".claude" / "projects"
    write_jsonl(claude_project(projects, home / "workspaces" / "home" / "lab") / "s.jsonl",
                [claude_user("delta")])
    conn = recall_index.open_index(tmp_path / "recall-home.db", "home")
    recall_index.refresh(conn, Guard(home, "home", workspace_names=["home"]), _sources(home))
    assert len(recall_index.search(conn, '"delta"', limit=5)[0]) == 1
    # A workspace named ``home-lab`` appears: the ``lab`` folder inside the ``home``
    # workspace now encodes like another system's workspace, so its rows go.
    removed = recall_index.purge_refused(
        conn, Guard(home, "home", workspace_names=["home", "home-lab"]))
    assert removed == 1
    assert recall_index.search(conn, '"delta"', limit=5)[0] == []


def test_subagent_transcript_hit_is_labelled_agent(home, tmp_path):
    projects = home / ".claude" / "projects"
    project = claude_project(projects, home / "code" / "demo")
    write_jsonl(project / "s1.jsonl", [claude_user("epsilon typed", origin={"kind": "human"})])
    write_jsonl(project / "s1" / "subagents" / "agent-a1.jsonl",
                [claude_user("epsilon brief", origin={"kind": "human"})])
    sessions = home / ".omp" / "agent" / "sessions" / "-code-demo"
    write_jsonl(sessions / "2026_x" / "Scout.jsonl", [
        {"type": "message", "timestamp": "t", "message": {
            "role": "user", "attribution": "user", "content": "epsilon omp brief"}}])
    conn = recall_index.open_index(tmp_path / "recall-home.db", "home")
    recall_index.refresh(conn, Guard(home, "home"), _sources(home))
    hits, _ = recall_index.search(conn, '"epsilon"', limit=10)
    got = sorted((h.excerpt, h.speaker, h.source) for h in hits)
    assert got == [
        ("[epsilon] brief", classify.AGENT, CLAUDE),
        ("[epsilon] omp brief", classify.AGENT, OMP),
        ("[epsilon] typed", classify.PERSON, CLAUDE),
    ]
    assert {h.project for h in hits} == {str(home / "code" / "demo")}


def test_index_file_is_private_and_bound_to_its_system(tmp_path):
    path = tmp_path / "cache" / "recall-home.db"
    recall_index.open_index(path, "home").close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    with pytest.raises(recall_index.RecallIndexError):
        recall_index.open_index(path, "work")


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    os.chmod(path, 0o700)
    return path


def test_wide_index_folder_refused(tmp_path):
    folder = tmp_path / "shared"
    folder.mkdir()
    os.chmod(folder, 0o755)
    with pytest.raises(recall_index.RecallIndexError, match="0755"):
        recall_index.open_index(folder / "recall-home.db", "home")
    assert not (folder / "recall-home.db").exists()


def test_wide_index_file_refused(tmp_path):
    path = _private_dir(tmp_path / "cache") / "recall-home.db"
    recall_index.open_index(path, "home").close()
    os.chmod(path, 0o644)
    with pytest.raises(recall_index.RecallIndexError, match="0644"):
        recall_index.open_index(path, "home")


@pytest.mark.parametrize("umask", [0o022, 0o277])
def test_created_modes_are_exact_whatever_the_umask(tmp_path, umask):
    old = os.umask(umask)
    try:
        path = tmp_path / "made" / "recall-home.db"
        recall_index.open_index(path, "home").close()
    finally:
        os.umask(old)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700


def test_file_without_recall_tables_refused(tmp_path):
    """A meta row alone does not make a recall index."""
    path = _private_dir(tmp_path / "cache") / "recall-home.db"
    conn = sqlite3.connect(str(path))
    conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.executemany("INSERT INTO meta VALUES (?, ?)",
                     [("system", "home"), ("schema_version", recall_index.SCHEMA_VERSION)])
    conn.commit()
    conn.close()
    os.chmod(path, 0o600)
    with pytest.raises(recall_index.RecallIndexError, match="not a recall index"):
        recall_index.open_index(path, "home")


def test_ledger_database_is_refused_untouched(tmp_path):
    from ledger.store import claim_database

    db = _private_dir(tmp_path / "ledger") / "ledger.db"
    claim_database(db, "home")
    os.chmod(db, 0o600)
    before = db.read_bytes()
    with pytest.raises(recall_index.RecallIndexError, match="not a recall index"):
        recall_index.open_index(db, "home")
    assert db.read_bytes() == before
    conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True)
    names = {row[0] for row in conn.execute("SELECT name FROM sqlite_master")}
    conn.close()
    assert "text_index" not in names and "records" not in names


def test_metadata_less_index_refused_and_not_relabelled(tmp_path):
    path = _private_dir(tmp_path / "cache") / "recall-home.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(recall_index._SCHEMA)
    conn.execute("INSERT INTO text_index (rowid, text) VALUES (1, 'old words')")
    conn.commit()
    conn.close()
    os.chmod(path, 0o600)
    with pytest.raises(recall_index.RecallIndexError, match="records no system"):
        recall_index.open_index(path, "work")
    conn = sqlite3.connect(str(path))
    assert conn.execute("SELECT count(*) FROM meta").fetchone() == (0,)
    conn.close()


def test_other_systems_index_refused_and_not_relabelled(tmp_path):
    path = _private_dir(tmp_path / "cache") / "recall-work.db"
    recall_index.open_index(path, "work").close()
    with pytest.raises(recall_index.RecallIndexError, match="belongs to system 'work'"):
        recall_index.open_index(path, "home")
    conn = sqlite3.connect(str(path))
    assert dict(conn.execute("SELECT key, value FROM meta"))["system"] == "work"
    conn.close()


def test_first_create_race_falls_through_to_the_winners_index(tmp_path, monkeypatch):
    """Another run links its index into place between our check and our link."""
    folder = _private_dir(tmp_path / "cache")
    path = folder / "recall-home.db"
    winner = _private_dir(tmp_path / "winner") / "recall-home.db"
    recall_index.open_index(winner, "home").close()
    real_link = recall_index._link

    def racing_link(source, target):
        os.link(winner, target)  # the other run wins
        real_link(source, target)  # ours now raises FileExistsError

    monkeypatch.setattr(recall_index, "_link", racing_link)
    conn = recall_index.open_index(path, "home")
    assert dict(conn.execute("SELECT key, value FROM meta"))["system"] == "home"
    conn.close()
    leftovers = sorted(p.name for p in folder.iterdir() if p.name.endswith(".new"))
    assert leftovers == []  # the staging file is cleaned up


def test_unlistable_root_keeps_existing_hits(home, tmp_path, monkeypatch):
    project = claude_project(home / ".claude" / "projects", home / "code" / "demo")
    write_jsonl(project / "s1.jsonl", [claude_user("kappa")])
    guard = Guard(home, "home")
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    recall_index.refresh(conn, guard, _sources(home))
    real = recall_index._scandir
    root = str(home / ".claude" / "projects")

    def denied(path):
        if str(path) == root:
            raise PermissionError(13, "Permission denied", path)
        return real(path)

    monkeypatch.setattr(recall_index, "_scandir", denied)
    stats = recall_index.refresh(conn, guard, _sources(home))
    assert stats.files_removed == 0 and stats.incomplete == 1
    assert len(recall_index.search(conn, '"kappa"', limit=5)[0]) == 1


def test_lookalike_schema_refused_untouched(tmp_path):
    """Plain tables with recall's names and a correct meta row are not a recall index."""
    path = _private_dir(tmp_path / "cache") / "recall-home.db"
    conn = sqlite3.connect(str(path))
    conn.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE files (path TEXT);"
        "CREATE TABLE records (id INTEGER PRIMARY KEY);"
        "CREATE TABLE text_index (text TEXT);")
    conn.executemany("INSERT INTO meta VALUES (?, ?)",
                     [("system", "home"), ("schema_version", recall_index.SCHEMA_VERSION)])
    conn.commit()
    conn.close()
    os.chmod(path, 0o600)
    before = path.read_bytes()
    with pytest.raises(recall_index.RecallIndexError, match="not a recall index"):
        recall_index.open_index(path, "home")
    assert path.read_bytes() == before


def test_file_swapped_between_checks_and_open_is_refused(tmp_path, monkeypatch):
    """Another system's index swapped in after the file checks is never handed back."""
    folder = _private_dir(tmp_path / "cache")
    path = folder / "recall-home.db"
    recall_index.open_index(path, "home").close()
    other = _private_dir(tmp_path / "other") / "recall-work.db"
    recall_index.open_index(other, "work").close()

    def swap(target, stage):
        if stage == "checked":
            os.replace(other, target)

    monkeypatch.setattr(recall_index, "_gap", swap)
    with pytest.raises(recall_index.RecallIndexError):
        conn = recall_index.open_index(path, "home")
        conn.close()


def test_index_path_naming_a_fifo_refused_without_hanging(tmp_path):
    """Run in a child under a timeout: opening a FIFO with SQLite would block."""
    import subprocess
    import sys

    folder = _private_dir(tmp_path / "cache")
    fifo = folder / "recall-home.db"
    os.mkfifo(fifo, 0o600)
    lib = Path(recall_index.__file__).resolve().parents[1]
    code = (
        "import sys; from pathlib import Path\n"
        f"sys.path.insert(0, {str(lib)!r})\n"
        "from recall import index\n"
        "try:\n"
        f"    index.open_index(Path({str(fifo)!r}), 'home')\n"
        "except index.RecallIndexError as exc:\n"
        "    print(exc); sys.exit(3)\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=15)
    assert proc.returncode == 3, proc.stderr
    assert "not a regular file" in proc.stdout


def test_file_that_cannot_be_statted_keeps_its_rows(home, tmp_path, monkeypatch):
    project = claude_project(home / ".claude" / "projects", home / "code" / "demo")
    locked = write_jsonl(project / "s1.jsonl", [claude_user("lambda")])
    guard = Guard(home, "home")
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    recall_index.refresh(conn, guard, _sources(home))
    real_stat = os.stat

    def stat_denied(path, *args, **kwargs):
        if str(path) == str(locked):
            raise PermissionError(13, "Permission denied", str(path))
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(recall_index.os, "stat", stat_denied)
    stats = recall_index.refresh(conn, guard, _sources(home))
    monkeypatch.undo()
    assert stats.files_removed == 0
    assert len(recall_index.search(conn, '"lambda"', limit=5)[0]) == 1


def test_removed_root_rows_go_but_unlistable_root_rows_stay(home, tmp_path, monkeypatch):
    projects = home / ".claude" / "projects"
    write_jsonl(claude_project(projects, home / "code" / "demo") / "s1.jsonl",
                [claude_user("mu")])
    guard = Guard(home, "home")
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    recall_index.refresh(conn, guard, _sources(home))
    # Unlistable: kept (covered in depth by test_unlistable_root_keeps_existing_hits).
    real = recall_index._scandir

    def denied(path):
        if str(path) == str(projects):
            raise PermissionError(13, "Permission denied", path)
        return real(path)

    monkeypatch.setattr(recall_index, "_scandir", denied)
    assert recall_index.refresh(conn, guard, _sources(home)).files_removed == 0
    monkeypatch.undo()
    # Removed: the whole root is gone, so its rows go.
    projects.rename(home / ".claude" / "projects-moved-away")
    stats = recall_index.refresh(conn, guard, _sources(home))
    assert stats.files_removed == 1 and stats.incomplete == 0
    assert recall_index.search(conn, '"mu"', limit=5)[0] == []


def test_removed_project_folder_rows_go(home, tmp_path):
    projects = home / ".claude" / "projects"
    gone = claude_project(projects, home / "code" / "demo")
    write_jsonl(gone / "s1.jsonl", [claude_user("nu")])
    guard = Guard(home, "home")
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    recall_index.refresh(conn, guard, _sources(home))
    gone.rename(tmp_path / "elsewhere")
    stats = recall_index.refresh(conn, guard, _sources(home))
    assert stats.files_removed == 1
    assert recall_index.search(conn, '"nu"', limit=5)[0] == []


def test_fts_query_quotes_words():
    assert recall_index.fts_query('model "routing" OR') == '"model" """routing""" "OR"'

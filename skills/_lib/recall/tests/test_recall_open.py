"""Opening an existing index: read-only validation first, then the file-identity check."""
from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from recall import index as recall_index


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True)
    os.chmod(path, 0o700)
    return path


def _snapshot(path: Path) -> dict[str, str | None]:
    """Hash of the main file and each sidecar (``None`` when absent)."""
    out = {}
    for suffix in ("", "-wal", "-shm"):
        part = Path(f"{path}{suffix}")
        out[suffix or "main"] = (hashlib.sha256(part.read_bytes()).hexdigest()
                                 if part.exists() else None)
    return out


def _wal_impostor(folder: Path, name: str, *, with_shm: bool) -> Path:
    """A WAL database whose committed tables still sit in its ``-wal`` (never checkpointed).

    Built in a scratch folder and copied out while the writer is still open, the
    way a crashed or still-running writer leaves one behind.
    """
    work = _private_dir(folder.parent / f"{name}-build")
    live = work / "live.db"
    conn = sqlite3.connect(live, isolation_level=None)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA wal_autocheckpoint = 0")
    conn.execute("CREATE TABLE events (id INTEGER PRIMARY KEY, body TEXT)")
    conn.execute("INSERT INTO events (body) VALUES ('not recall data')")
    target = folder / name
    for suffix in ("", "-wal", "-shm"):
        if suffix == "-shm" and not with_shm:
            continue
        shutil.copyfile(f"{live}{suffix}", f"{target}{suffix}")
        os.chmod(f"{target}{suffix}", 0o600)
    conn.close()
    return target


@pytest.mark.parametrize("with_shm", [True, False], ids=["wal+shm", "wal-only"])
def test_refused_impostor_wal_database_keeps_its_bytes_and_sidecars(tmp_path, with_shm):
    folder = _private_dir(tmp_path / "cache")
    path = _wal_impostor(folder, "recall-home.db", with_shm=with_shm)
    before = _snapshot(path)
    with pytest.raises(recall_index.RecallIndexError, match="not a recall index"):
        recall_index.open_index(path, "home")
    assert _snapshot(path) == before


def _valid_index(folder: Path, name: str, system: str = "home") -> Path:
    path = _private_dir(folder) / name
    recall_index.open_index(path, system).close()
    return path


def _replacement(kind: str, tmp_path: Path, target: Path) -> Path:
    """A file that passes the schema and system checks, ready to be swapped onto ``target``."""
    other = _valid_index(tmp_path / "other", "recall-home.db")
    if kind == "same-system-0644":
        os.chmod(other, 0o644)
        return other
    link = target.with_name("swap-link")  # a link to a valid, owner-only index
    os.symlink(other, link)
    return link


@pytest.mark.parametrize("stage", ["checked", "opened"])
@pytest.mark.parametrize("kind", ["same-system-0644", "symlink"])
def test_identity_swap_that_passes_the_content_checks_is_refused(tmp_path, monkeypatch,
                                                                  kind, stage):
    """Only the identity check stands between these swaps and an accepted index.

    ``checked``: swapped after the file checks, before the read-only open.
    ``opened``: swapped after the read-write open, before it is validated.
    """
    path = _valid_index(tmp_path / "cache", "recall-home.db")
    replacement = _replacement(kind, tmp_path, path)

    def swap(target, at):
        if at == stage:
            os.replace(replacement, target)

    monkeypatch.setattr(recall_index, "_gap", swap)
    with pytest.raises(recall_index.RecallIndexError, match="was replaced"):
        recall_index.open_index(path, "home").close()


def test_impostor_swapped_in_after_read_only_validation_is_never_opened_read_write(
        tmp_path, monkeypatch):
    """The identity check runs before the read-write open, so a WAL impostor swapped in
    after the read-only validation is refused untouched, not checkpointed."""
    path = _valid_index(tmp_path / "cache", "recall-home.db")
    impostor = _wal_impostor(_private_dir(tmp_path / "staging"), "impostor.db", with_shm=True)
    snapshots = {}

    def swap(target, at):
        if at == "validated":
            for suffix in ("-wal", "-shm"):
                os.replace(f"{impostor}{suffix}", f"{target}{suffix}")
            os.replace(impostor, target)
            snapshots["before"] = _snapshot(target)

    monkeypatch.setattr(recall_index, "_gap", swap)
    with pytest.raises(recall_index.RecallIndexError, match="was replaced"):
        recall_index.open_index(path, "home").close()
    assert _snapshot(path) == snapshots["before"]


def test_valid_index_with_a_live_wal_opens(tmp_path):
    """Another run holds the index open with unwritten WAL pages: still accepted."""
    path = _valid_index(tmp_path / "cache", "recall-home.db")
    writer = recall_index.open_index(path, "home")
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("INSERT INTO text_index (rowid, text) VALUES (1, 'pending words')")
    try:
        reader = recall_index.open_index(path, "home")
        assert reader.execute("SELECT count(*) FROM text_index").fetchone() == (1,)
        reader.close()
    finally:
        writer.close()


def test_wal_content_is_validated_on_the_read_write_connection(tmp_path):
    """The read-only check reads the main file only. A file whose main file is this
    system's index but whose unwritten WAL pages relabel it is refused on the
    read-write connection, before anything is written through it."""
    path = _valid_index(tmp_path / "cache", "recall-home.db")
    writer = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, isolation_level=None)
    writer.execute("PRAGMA wal_autocheckpoint = 0")
    writer.execute("UPDATE meta SET value = 'work' WHERE key = 'system'")
    try:
        with pytest.raises(recall_index.RecallIndexError, match="belongs to system 'work'"):
            recall_index.open_index(path, "home").close()
    finally:
        writer.close()

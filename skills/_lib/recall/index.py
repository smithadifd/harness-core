"""The recall index: discovery, admission, the mtime-incremental refresh, and search.

``discover`` lists the transcript files recall may consider. It visits each
project or session directory by name and asks the guard about the name
first: a refused name (``~/rooms``, a ``room`` word, another system's
workspace) is reported by name and reason and is never listed or opened.
The name check is a first deny, never an allow. Symlinks are never followed.

``refresh`` brings the index up to date with those files. A file whose size
and modification time match what the index recorded is not opened. A new or
changed file is opened once, and admitted or refused before any of it is
indexed (``admit``):

- **Option A:** the session's recorded ``cwd`` (the first record carrying
  one) is checked by the guard as given and with every link resolved.
- **Option B,** only when the file has no usable ``cwd``: every reading of
  the folder name is checked (``recall.guard.Decoder``).

An admitted file is read in full from the same open handle and its rows
replaced, in one transaction. While it is read, every record's ``cwd`` is
checked too: a session can move into ``~/rooms`` part way through, and one
refused ``cwd`` rolls the whole file back, so none of it is indexed. Rows of
a file that is gone, or that the guard now refuses (by name, by every stored
``cwd`` resolved again, or by option B again), are deleted, so the index
never holds more than the transcripts it mirrors. A directory that cannot be listed (permission, I/O) is not a
deletion: rows beneath it are kept until a later run can list it.

Trade-off of option A: a session reached through a link into ``~/rooms``
whose folder name does not say so is opened so that its ``cwd`` can be read
before it is refused. For that case the promise is "never indexed", not
"never read".

The index file is created ``0600`` in a ``0700`` folder and records the
system it was built for. An existing file is checked (regular, owner-only)
and then validated through a read-only, immutable open that touches neither
the file nor its ``-wal``/``-shm`` sidecars: recall's exact schema and a
meta row naming this system (read again a bounded number of times, because
the lock-free read can overlap another run's checkpoint). Only a file that
passes is opened read-write,
and it is validated again on that connection and must still be the same file
(device and inode) it was before the first check.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Iterator

from recall.classify import is_agent_file
from recall.extract import CLAUDE, OMP, first_cwd, read_records, usable_cwd
from recall.guard import Decoder, Guard, encode, omp_absolute_encoding

# 2: timestamps stored in one fixed-width UTC form. An index from version 1
# is refused with "delete it and run again to rebuild".
SCHEMA_VERSION = "3"
INDEX_DIR = Path(".cache") / "harness"

# How a file's project path was decided: its recorded cwd (option A), or the
# readings of its folder name (option B).
BASIS_CWD = "cwd"
BASIS_NAME = "name"


class RecallIndexError(Exception):
    """The index cannot be used for this system."""


def default_index_path(home: Path, system: str) -> Path:
    return home / INDEX_DIR / f"recall-{system}.db"


def default_sources(home: Path) -> dict[str, Path]:
    return {
        CLAUDE: home / ".claude" / "projects",
        OMP: home / ".omp" / "agent" / "sessions",
    }


# Seams the tests wrap to record every directory listed and file opened.
def _scandir(path: str):
    return os.scandir(path)


def _open_transcript(path: str) -> IO[str]:
    return open(path, "r", encoding="utf-8", errors="replace")


@dataclass(frozen=True)
class TranscriptFile:
    path: str
    source: str
    dir_name: str
    session: str
    agent_file: bool


@dataclass
class Discovery:
    files: list[TranscriptFile] = field(default_factory=list)
    refused: list[tuple[str, str, str]] = field(default_factory=list)  # (source, dir name, reason)
    # Directories that could not be listed (for any reason but "does not
    # exist"). Rows of files beneath them are kept: unreadable is not deleted.
    incomplete: list[str] = field(default_factory=list)


def _entries(path: str, found: Discovery) -> list[os.DirEntry]:
    try:
        with _scandir(path) as it:
            return sorted(it, key=lambda entry: entry.name)
    except FileNotFoundError:
        return []
    except OSError:
        found.incomplete.append(path)
        return []


def _jsonl_files(directory: str, parts: tuple[str, ...],
                 found: Discovery) -> Iterator[tuple[str, tuple[str, ...]]]:
    """Every ``*.jsonl`` regular file under ``directory``; links are skipped."""
    for entry in _entries(directory, found):
        if entry.is_symlink() or entry.name.startswith("."):
            continue
        if entry.is_dir(follow_symlinks=False):
            yield from _jsonl_files(entry.path, parts + (entry.name,), found)
        elif entry.is_file(follow_symlinks=False) and entry.name.endswith(".jsonl"):
            yield entry.path, parts


def _refusal(guard: Guard, source: str, name: str) -> str | None:
    if source == CLAUDE:
        return guard.refusal_for_claude_dir(name)
    return guard.refusal_for_omp_dir(name)


def _encoded(guard: Guard, source: str, name: str) -> str:
    # The guard's home folder, read by name so no line holds the attribute
    # access a private-hostname scan reads as a host.
    return name if source == CLAUDE else omp_absolute_encoding(name, getattr(guard, "home"))


def discover(guard: Guard, sources: dict[str, Path]) -> Discovery:
    """Transcript files in folders whose names the guard does not refuse.

    Nothing here opens a transcript; ``admit`` decides each file.
    """
    found = Discovery()
    for source, root in sources.items():
        root_refusal = guard.refusal_for_path(root)
        if root_refusal is not None:
            found.refused.append((source, str(root), root_refusal))
            continue
        for entry in _entries(str(root), found):
            if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                continue
            name = entry.name
            reason = _refusal(guard, source, name)
            if reason is not None:
                found.refused.append((source, name, reason))
                continue
            for top in _entries(entry.path, found):
                if top.is_symlink() or top.name.startswith("."):
                    continue
                if top.is_file(follow_symlinks=False) and top.name.endswith(".jsonl"):
                    found.files.append(TranscriptFile(
                        top.path, source, name, top.name[:-6], is_agent_file((), top.name)))
                elif top.is_dir(follow_symlinks=False):
                    if source == CLAUDE:
                        sub = os.path.join(top.path, "subagents")
                        if not os.path.isdir(sub) or os.path.islink(sub):
                            continue
                        walk = _jsonl_files(sub, ("subagents",), found)
                    else:
                        walk = _jsonl_files(top.path, ("subagents",), found)
                    for path, _parts in walk:
                        base = os.path.basename(path)
                        found.files.append(TranscriptFile(path, source, name, base[:-6], True))
    return found


@dataclass(frozen=True)
class Admission:
    """Whether one transcript may be indexed, and the project it is filed under."""

    project: str | None
    basis: str
    reason: str | None


def admit(guard: Guard, decoder: Decoder, tf: TranscriptFile, handle: IO[str]) -> Admission:
    """Decide ``tf`` from its open ``handle``, before any of it is indexed.

    Option A when the file records a usable ``cwd``; option B otherwise. The
    folder-name check already ran in ``discover`` and is not repeated here as
    an allow: a file reaches this point only after its folder name passed.
    """
    cwd = first_cwd(handle)
    if usable_cwd(cwd):
        return Admission(cwd, BASIS_CWD, guard.refusal_for_project(cwd))
    decision = decoder.decide(_encoded(guard, tf.source, tf.dir_name))
    return Admission(decision.project, BASIS_NAME, decision.reason)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS files (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    dir_name TEXT NOT NULL,
    project TEXT NOT NULL,
    basis TEXT NOT NULL,
    cwds TEXT NOT NULL,
    session TEXT NOT NULL,
    size INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    records INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    id INTEGER PRIMARY KEY,
    file_id INTEGER NOT NULL,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    speaker TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS records_file ON records(file_id);
CREATE VIRTUAL TABLE IF NOT EXISTS text_index USING fts5(text, tokenize = 'unicode61');
"""


def _canonical_schema() -> set[tuple[str, str, str, str | None]]:
    """Every schema object a fresh recall index holds, as ``sqlite_master`` records it.

    Built in memory from ``_SCHEMA``, so the comparison covers each table's
    columns and constraints, the index, the FTS5 virtual table and its shadow
    tables, exactly as SQLite stores them.
    """
    mem = sqlite3.connect(":memory:")
    try:
        mem.executescript(_SCHEMA)
        return set(mem.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
    finally:
        mem.close()


def _owner_only(st: os.stat_result) -> bool:
    return st.st_uid == os.getuid() and stat.S_IMODE(st.st_mode) & 0o077 == 0


def _prepare_dir(directory: Path) -> None:
    """Create the index folder ``0700``, or refuse an existing one others can read."""
    try:
        directory.mkdir(mode=0o700, parents=True)
        os.chmod(directory, 0o700)  # mkdir's mode is masked by the umask
    except FileExistsError:
        pass
    st = os.stat(directory)
    if not stat.S_ISDIR(st.st_mode):
        raise RecallIndexError(f"index folder {directory} is not a directory")
    if not _owner_only(st):
        raise RecallIndexError(
            f"index folder {directory} has mode {stat.S_IMODE(st.st_mode):04o}; recall "
            f"needs a folder only you can read (chmod 700 it, or pick another --index)")


def _check_file(path: Path) -> os.stat_result:
    """A regular, owner-only file; checked before SQLite opens it (a FIFO would block)."""
    st = os.stat(path, follow_symlinks=False)
    if not stat.S_ISREG(st.st_mode):
        raise RecallIndexError(f"index {path} is not a regular file")
    if not _owner_only(st):
        raise RecallIndexError(
            f"index {path} has mode {stat.S_IMODE(st.st_mode):04o}; recall needs 0600 "
            f"(chmod 600 it, or pick another --index)")
    return st


class _NotRecallIndex(RecallIndexError):
    """The file's schema is not recall's."""


class _Unreadable(RecallIndexError):
    """SQLite could not read the file (possibly a transient torn read)."""


def _validate_connection(conn: sqlite3.Connection, path: Path, system: str) -> None:
    """Refuse, reading only, a connection whose file is not this system's recall index."""
    try:
        objects = set(conn.execute("SELECT type, name, tbl_name, sql FROM sqlite_master"))
        if objects != _canonical_schema():
            raise _NotRecallIndex(f"{path} is not a recall index; refusing to write to it")
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    except sqlite3.Error as exc:
        raise _Unreadable(
            f"index {path} could not be read ({exc}); refusing to write to it. If another "
            f"recall run is using it, run again") from exc
    if "system" not in meta:
        raise RecallIndexError(f"index {path} records no system; refusing to write to it")
    if meta["system"] != system:
        raise RecallIndexError(
            f"index {path} belongs to system {meta['system']!r}, not {system!r}")
    if meta.get("schema_version") != SCHEMA_VERSION:
        raise RecallIndexError(
            f"index {path} has schema {meta.get('schema_version')!r}; "
            f"delete it and run again to rebuild")


READ_ONLY_ATTEMPTS = 25
READ_ONLY_PAUSE = 0.02


def _validate_read_only(path: Path, system: str) -> None:
    """Validate ``path`` without any write access to it or its sidecars.

    ``immutable=1`` makes SQLite read the main file only: no locks, no
    ``-wal`` replay, no ``-shm`` created or rebuilt, no checkpoint on close.
    (A plain ``mode=ro`` open still creates and rewrites ``-shm``.) A recall
    index's schema and meta row are written when it is created, before it is
    linked into place, so they are always in the main file.

    Taking no locks, the read can overlap another run's checkpoint and see a
    torn page. So an unreadable file or a schema mismatch is read again, up to
    ``READ_ONLY_ATTEMPTS`` times, before it is refused; a file that reads
    cleanly but names another system is refused at once.
    """
    for attempt in range(READ_ONLY_ATTEMPTS):
        try:
            conn = sqlite3.connect(f"{path.as_uri()}?mode=ro&immutable=1", uri=True)
        except sqlite3.Error as exc:
            raise RecallIndexError(f"index {path} cannot be opened: {exc}") from exc
        try:
            _validate_connection(conn, path, system)
            return
        except (_NotRecallIndex, _Unreadable):
            if attempt + 1 == READ_ONLY_ATTEMPTS:
                raise
        finally:
            conn.close()
        time.sleep(READ_ONLY_PAUSE)


def _same_file(path: Path, before: os.stat_result) -> None:
    """Refuse when ``path`` is no longer the file checked as ``before``.

    A swap that passes the content checks (this system's index at mode 0644,
    or a link to a valid index) is caught here and nowhere else.
    """
    try:
        after = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise RecallIndexError(f"index {path} was replaced while it was being opened") from exc
    if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
        raise RecallIndexError(f"index {path} was replaced while it was being opened")


def _link(source: Path, target: Path) -> None:
    """Hard-link the built index into place (a seam the race test wraps)."""
    os.link(source, target)


def _create(path: Path, system: str) -> bool:
    """Build a new index beside ``path`` and link it into place.

    The staging file is created ``0600`` with ``O_EXCL`` and fully initialized
    before it appears under the real name, so no other run sees a half-built
    index. Returns ``False`` when ``path`` appeared meanwhile (another first
    run won the race); the caller then validates that file like any other.
    """
    staging = path.with_name(f".{path.name}.{os.getpid()}.new")
    for suffix in ("", "-wal", "-shm"):
        try:  # left by a dead run that had this pid; no live run owns it
            os.unlink(f"{staging}{suffix}")
        except FileNotFoundError:
            pass
    fd = os.open(staging, os.O_CREAT | os.O_WRONLY | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        os.chmod(staging, 0o600)
        conn = sqlite3.connect(str(staging), isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.executescript(_SCHEMA)
            conn.executemany("INSERT INTO meta (key, value) VALUES (?, ?)",
                             [("system", system), ("schema_version", SCHEMA_VERSION)])
        finally:
            conn.close()
        try:
            _link(staging, path)
        except FileExistsError:
            return False
        return True
    finally:
        for suffix in ("", "-wal", "-shm"):
            try:
                os.unlink(f"{staging}{suffix}")
            except FileNotFoundError:
                pass


def _gap(path: Path, stage: str) -> None:
    """Nothing; the seams between the open's steps, which tests wrap to swap files.

    ``stage`` is ``"checked"`` (after the file checks, before the read-only
    open), ``"validated"`` (after read-only validation, before the identity
    check that guards the read-write open) or ``"opened"`` (after the
    read-write open, before it is validated).
    """


def open_index(path: Path, system: str) -> sqlite3.Connection:
    """Open (creating if needed) the index for ``system``; refuse anything else.

    Schema statements run only on a file this call created. Every file, new or
    existing, goes through the same steps before anything writes through the
    connection this returns:

    1. a regular owner-only file (checked before SQLite opens it);
    2. read-only, immutable validation: recall's exact schema and a meta row
       naming ``system``; a refused file and its sidecars are left untouched;
    3. still the same file (device and inode) as in step 1, then the
       read-write open;
    4. the same validation again on the read-write connection, and still the
       same file.
    """
    _prepare_dir(path.parent)
    if not os.path.lexists(path):
        _create(path, system)  # False: another run created it first
    before = _check_file(path)
    _gap(path, "checked")
    _validate_read_only(path, system)
    _gap(path, "validated")
    _same_file(path, before)
    try:
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=30,
                               isolation_level=None)
    except sqlite3.Error as exc:
        raise RecallIndexError(f"index {path} cannot be opened: {exc}") from exc
    try:
        _gap(path, "opened")
        _validate_connection(conn, path, system)
        _same_file(path, before)
        conn.execute("PRAGMA synchronous = NORMAL")
    except BaseException:
        conn.close()
        raise
    return conn


@dataclass
class ScanStats:
    files_seen: int = 0
    files_read: list[str] = field(default_factory=list)
    files_unchanged: int = 0
    files_removed: int = 0
    records_added: int = 0
    refused: list[tuple[str, str, str]] = field(default_factory=list)  # folders, by name
    files_refused: list[tuple[str, str, str]] = field(default_factory=list)  # files, admitted no
    incomplete: int = 0
    seconds: float = 0.0


def _delete_file_rows(conn: sqlite3.Connection, file_id: int) -> None:
    conn.execute("DELETE FROM text_index WHERE rowid IN "
                 "(SELECT id FROM records WHERE file_id = ?)", (file_id,))
    conn.execute("DELETE FROM records WHERE file_id = ?", (file_id,))
    conn.execute("DELETE FROM files WHERE id = ?", (file_id,))


def _unchanged(known: tuple[int, int] | None, size: int, mtime_ns: int) -> bool:
    return known is not None and known == (size, mtime_ns)


class CwdRefused(Exception):
    """A record's ``cwd`` is refused: the whole file is, and nothing of it is kept."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def _index_file(conn: sqlite3.Connection, guard: Guard, tf: TranscriptFile,
                admission: Admission, handle: IO[str], size: int, mtime_ns: int) -> int:
    """Replace ``tf``'s rows with the records read from ``handle``, in one transaction.

    Every record's ``cwd`` is checked as it is read, not just the first: a
    session can move into ``~/rooms`` part way through. One refused ``cwd``
    raises ``CwdRefused`` and rolls the whole file back, so none of it is
    indexed. The distinct ``cwd`` values are stored so that later runs can
    check them again without opening the file.
    """
    seen: set[str] = set()
    if admission.basis == BASIS_CWD and admission.project is not None:
        seen.add(admission.project)

    def check_cwd(value: str) -> None:
        if value in seen or not usable_cwd(value):
            return
        reason = guard.refusal_for_project(value)
        if reason is not None:
            raise CwdRefused(reason)
        seen.add(value)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT id, size, mtime_ns FROM files WHERE path = ?",
                           (tf.path,)).fetchone()
        if row is not None:
            if _unchanged((row[1], row[2]), size, mtime_ns):
                conn.execute("COMMIT")  # another run indexed it meanwhile
                return 0
            _delete_file_rows(conn, row[0])
        cur = conn.execute(
            "INSERT INTO files (path, source, dir_name, project, basis, cwds, session,"
            " size, mtime_ns, records) VALUES (?, ?, ?, ?, ?, '[]', ?, ?, ?, 0)",
            (tf.path, tf.source, tf.dir_name, admission.project, admission.basis,
             tf.session, size, mtime_ns))
        file_id = cur.lastrowid
        count = 0
        for rec in read_records(handle, tf.source, agent_file=tf.agent_file,
                                on_cwd=check_cwd):
            rid = conn.execute(
                "INSERT INTO records (file_id, ts, kind, speaker) VALUES (?, ?, ?, ?)",
                (file_id, rec.ts, rec.kind, rec.speaker)).lastrowid
            conn.execute("INSERT INTO text_index (rowid, text) VALUES (?, ?)", (rid, rec.text))
            count += 1
        conn.execute("UPDATE files SET records = ?, cwds = ? WHERE id = ?",
                     (count, json.dumps(sorted(seen)), file_id))
        conn.execute("COMMIT")
        return count
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _stored_cwds(cwds: str) -> list[str]:
    try:
        values = json.loads(cwds)
    except ValueError:
        return []
    return [v for v in values if isinstance(v, str)] if isinstance(values, list) else []


def _stored_refusal(guard: Guard, decoder: Decoder, source: str, dir_name: str,
                    project: str, basis: str, cwds: str, path: str) -> str | None:
    """Why an indexed file is refused now: its folder name, its basis and every
    ``cwd`` it recorded checked again (links resolved as they are now), then
    the transcript's own path."""
    reason = _refusal(guard, source, dir_name)
    if reason is None and basis == BASIS_NAME:
        reason = decoder.decide(_encoded(guard, source, dir_name)).reason
    # Every cwd the file recorded; for basis cwd this includes ``project``.
    for cwd in [project, *_stored_cwds(cwds)] if basis == BASIS_CWD else _stored_cwds(cwds):
        if reason is not None:
            break
        reason = guard.refusal_for_project(cwd)
    if reason is None:
        reason = guard.refusal_for_encoded(encode(path))
    return reason


def purge_refused(conn: sqlite3.Connection, guard: Guard, decoder: Decoder | None = None) -> int:
    """Delete rows of files the guard now refuses. Reads no transcript."""
    decoder = decoder or Decoder(guard)
    removed = 0
    for file_id, source, dir_name, project, basis, cwds, path in conn.execute(
            "SELECT id, source, dir_name, project, basis, cwds, path FROM files").fetchall():
        if _stored_refusal(guard, decoder, source, dir_name, project, basis, cwds,
                           path) is not None:
            conn.execute("BEGIN IMMEDIATE")
            _delete_file_rows(conn, file_id)
            conn.execute("COMMIT")
            removed += 1
    return removed


def refresh(conn: sqlite3.Connection, guard: Guard, sources: dict[str, Path],
            decoder: Decoder | None = None) -> ScanStats:
    started = time.monotonic()
    decoder = decoder or Decoder(guard)
    stats = ScanStats()
    stats.files_removed += purge_refused(conn, guard, decoder)
    found = discover(guard, sources)
    stats.refused = found.refused
    known = {path: (file_id, size, mtime_ns) for file_id, path, size, mtime_ns in
             conn.execute("SELECT id, path, size, mtime_ns FROM files").fetchall()}
    current = set()
    for tf in found.files:
        try:
            st = os.stat(tf.path, follow_symlinks=False)
        except FileNotFoundError:
            continue
        except OSError:
            current.add(tf.path)  # unreadable is not gone: keep its rows
            continue
        if not stat.S_ISREG(st.st_mode):
            continue
        current.add(tf.path)
        stats.files_seen += 1
        entry = known.get(tf.path)
        if _unchanged(None if entry is None else (entry[1], entry[2]), st.st_size, st.st_mtime_ns):
            stats.files_unchanged += 1  # admitted before; purge_refused checked it again
            continue
        try:
            with _open_transcript(tf.path) as handle:
                admission = admit(guard, decoder, tf, handle)
                if admission.reason is not None:
                    # Refused: never indexed, and any rows it had are deleted below.
                    current.discard(tf.path)
                    stats.files_refused.append((tf.source, tf.dir_name, admission.reason))
                    continue
                handle.seek(0)
                stats.records_added += _index_file(
                    conn, guard, tf, admission, handle, st.st_size, st.st_mtime_ns)
        except CwdRefused as refused:
            # A later record moved somewhere refused: rolled back, nothing kept.
            current.discard(tf.path)
            stats.files_refused.append((tf.source, tf.dir_name, refused.reason))
            continue
        except OSError:
            # Gone or unreadable between the stat and the read: its old rows were
            # rolled back untouched, and the next run tries it again.
            continue
        stats.files_read.append(tf.path)
    stats.incomplete = len(found.incomplete)
    unlisted = tuple(d.rstrip(os.sep) + os.sep for d in found.incomplete)
    for path, (file_id, _size, _mtime) in known.items():
        if path not in current and not path.startswith(unlisted):
            conn.execute("BEGIN IMMEDIATE")
            _delete_file_rows(conn, file_id)
            conn.execute("COMMIT")
            stats.files_removed += 1
    stats.seconds = time.monotonic() - started
    return stats


@dataclass(frozen=True)
class Hit:
    ts: str
    project: str
    speaker: str
    kind: str
    source: str
    session: str
    excerpt: str


def fts_query(words: str) -> str:
    """Quote each whitespace-separated word as an FTS5 phrase (all must match)."""
    terms = [term for term in words.split() if term]
    return " ".join('"' + term.replace('"', '""') + '"' for term in terms)


def search(conn: sqlite3.Connection, expression: str, *, limit: int,
           speaker: str | None = None, kind: str | None = None,
           project: str | None = None, recent: bool = False) -> tuple[list[Hit], bool]:
    clauses = ["text_index MATCH ?"]
    params: list[object] = [expression]
    if speaker:
        clauses.append("r.speaker = ?")
        params.append(speaker)
    if kind:
        clauses.append("r.kind = ?")
        params.append(kind)
    if project:
        clauses.append("instr(f.project, ?) > 0")
        params.append(project)
    # Records in the same microsecond (or with no timestamp) fall back to the
    # newest-indexed row, so the order is deterministic.
    order = "r.ts DESC, r.id DESC" if recent else "rank"
    sql = (
        "SELECT r.ts, f.project, r.speaker, r.kind, f.source, f.session, "
        "snippet(text_index, 0, '[', ']', ' ... ', 32) "
        "FROM text_index JOIN records r ON r.id = text_index.rowid "
        "JOIN files f ON f.id = r.file_id "
        f"WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ?"
    )
    params.append(limit + 1)
    rows = conn.execute(sql, params).fetchall()
    # Stored to the microsecond; shown to the millisecond.
    hits = [Hit(row[0][:23] + "Z" if row[0] else row[0], *row[1:]) for row in rows[:limit]]
    return hits, len(rows) > limit


def index_bytes(path: Path) -> int:
    total = 0
    for suffix in ("", "-wal", "-shm"):
        try:
            total += os.stat(f"{path}{suffix}").st_size
        except OSError:
            pass
    return total

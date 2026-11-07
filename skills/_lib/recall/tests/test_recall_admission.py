"""Admission: option A (the recorded ``cwd``) and option B (every reading of the folder name).

Test names avoid the word "room": pytest names ``tmp_path`` after the test,
and the guard refuses any path that holds that word.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from recall import index as recall_index
from recall.extract import CLAUDE, OMP
from recall.guard import AMBIGUOUS, Decision, Decoder, Guard, encode

from recall_fixtures import claude_user, write_jsonl


def _layout(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".claude" / "projects").mkdir(parents=True)
    (home / ".omp" / "agent" / "sessions").mkdir(parents=True)
    (home / "workspaces" / "home").mkdir(parents=True)
    (home / "code" / "demo").mkdir(parents=True)
    (home / "rooms" / "y").mkdir(parents=True)
    return home


def _refresh(tmp_path: Path, home: Path, listed: list[str] | None = None):
    def listdir(directory):
        if listed is not None:
            listed.append(directory)
        return Decoder._default_listdir(directory)

    guard = Guard(home, "home", workspace_names=["home"])
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    stats = recall_index.refresh(conn, guard, recall_index.default_sources(home),
                                 Decoder(guard, listdir=listdir))
    return conn, stats


def _files(conn) -> list[tuple[str, str, str]]:
    return sorted((os.path.basename(path), project, basis) for path, project, basis in
                  conn.execute("SELECT path, project, basis FROM files"))


@pytest.mark.parametrize("with_cwd", [False, True], ids=["no-cwd", "cwd"])
@pytest.mark.parametrize("layout", ["a/b/c beside a-b/c", "lnk/sub-z beside lnk-sub/z"])
def test_sibling_capture_layout_is_refused(tmp_path, recorder, layout, with_cwd):
    """``~/a`` links into ``~/rooms``, a real ``~/a-b`` sits beside it, and the session
    ran in ``~/a/b/c``. Its folder name also reads as the allowed ``~/a-b/c``; the
    session is refused whether it records its ``cwd`` (option A) or not (option B)."""
    home = _layout(tmp_path)
    if layout.startswith("a/"):
        link, sibling, ran_in = "a", home / "a-b" / "c", home / "a" / "b" / "c"
        (home / "rooms" / "y" / "b" / "c").mkdir(parents=True)
    else:
        link, sibling, ran_in = "lnk", home / "lnk-sub" / "z", home / "lnk" / "sub-z"
        (home / "rooms" / "y" / "sub-z").mkdir(parents=True)
    os.symlink(home / "rooms" / "y", home / link)
    sibling.mkdir(parents=True)
    name = encode(str(ran_in))
    assert name == encode(str(sibling))  # the capture: one name, two real folders
    extra = {"cwd": str(ran_in)} if with_cwd else {}
    session = write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                          [claude_user("zebra captured", **extra)])
    listed: list[str] = []
    conn, stats = _refresh(tmp_path, home, listed)
    assert stats.files_read == []
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert recall_index.search(conn, '"zebra"', limit=5)[0] == []
    assert recorder.opened == [str(session)]
    real_rooms = os.path.realpath(home / "rooms")
    assert not [d for d in listed if os.path.realpath(d).startswith(real_rooms)], listed


def test_cwd_resolving_into_the_instances_is_not_indexed(tmp_path, recorder):
    """Option A: the folder name reads as an ordinary project, but the session's
    recorded ``cwd`` resolves (through a link) into ``~/rooms``: not indexed."""
    home = _layout(tmp_path)
    os.symlink(home / "rooms" / "y", home / "proj")
    projects = home / ".claude" / "projects"
    name = encode(str(home / "code" / "demo"))
    linked = write_jsonl(projects / name / "linked.jsonl",
                         [claude_user("zebra via link", cwd=str(home / "proj"))])
    direct = write_jsonl(projects / name / "direct.jsonl",
                         [claude_user("zebra direct", cwd=str(home / "rooms" / "y"))])
    write_jsonl(projects / name / "fine.jsonl",
                [claude_user("zebra fine", cwd=str(home / "code" / "demo"))])
    conn, stats = _refresh(tmp_path, home)
    assert [os.path.basename(p) for p in stats.files_read] == ["fine.jsonl"]
    assert sorted(stats.files_refused) == [(CLAUDE, name, "room instance")] * 2
    hits, _ = recall_index.search(conn, '"zebra"', limit=5)
    assert [hit.excerpt for hit in hits] == ["[zebra] fine"]
    assert _files(conn) == [("fine.jsonl", str(home / "code" / "demo"), "cwd")]
    assert {str(linked), str(direct)} <= set(recorder.opened)  # read for cwd only


def test_omp_session_cwd_resolving_into_the_instances_is_not_indexed(tmp_path):
    """OMP records ``cwd`` on its ``session`` record; option A applies to it the same way."""
    home = _layout(tmp_path)
    os.symlink(home / "rooms" / "y", home / "proj")
    sessions = home / ".omp" / "agent" / "sessions" / "-code-demo"
    write_jsonl(sessions / "2026_a.jsonl", [
        {"type": "title", "v": 1, "title": "t"},
        {"type": "session", "id": "a", "cwd": str(home / "proj")},
        {"type": "message", "timestamp": "t", "message": {
            "role": "user", "attribution": "user", "content": "zebra omp linked"}}])
    write_jsonl(sessions / "2026_b.jsonl", [
        {"type": "session", "id": "b", "cwd": str(home / "code" / "demo")},
        {"type": "message", "timestamp": "t", "message": {
            "role": "user", "attribution": "user", "content": "zebra omp fine"}}])
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == [(OMP, "-code-demo", "room instance")]
    hits, _ = recall_index.search(conn, '"zebra"', limit=5)
    assert [hit.excerpt for hit in hits] == ["[zebra] omp fine"]


def test_room_named_folder_stays_a_first_deny_whatever_its_cwd(tmp_path, recorder):
    """A folder refused by name is never listed, even when its files record an allowed cwd."""
    home = _layout(tmp_path)
    folder = home / ".claude" / "projects" / encode(str(home / "rooms" / "y"))
    session = write_jsonl(folder / "s.jsonl",
                          [claude_user("zebra", cwd=str(home / "code" / "demo"))])
    conn, stats = _refresh(tmp_path, home)
    assert stats.refused == [(CLAUDE, folder.name, "room instance")]
    assert stats.files_read == [] and stats.files_refused == []
    assert not [p for p in recorder.touched() if p.startswith(str(folder))]
    assert str(session) not in recorder.opened


def test_transcript_without_cwd_falls_to_option_b(tmp_path):
    """No usable ``cwd``: the folder name decides. One real reading is admitted under it;
    a name with two real readings is refused, and the same name with a ``cwd`` is not."""
    home = _layout(tmp_path)
    (home / "code" / "x-y").mkdir()
    (home / "code" / "x" / "y").mkdir(parents=True)
    projects = home / ".claude" / "projects"
    plain = projects / encode(str(home / "code" / "demo"))
    write_jsonl(plain / "nocwd.jsonl", [claude_user("zebra plain")])
    write_jsonl(plain / "relcwd.jsonl", [claude_user("zebra relative", cwd="code/demo")])
    twice = projects / encode(str(home / "code" / "x-y"))
    write_jsonl(twice / "nocwd.jsonl", [claude_user("zebra twice")])
    write_jsonl(twice / "withcwd.jsonl", [claude_user("zebra known", cwd=str(home / "code" / "x-y"))])
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_refused == [(CLAUDE, twice.name, AMBIGUOUS)]
    assert _files(conn) == [
        ("nocwd.jsonl", str(home / "code" / "demo"), "name"),
        ("relcwd.jsonl", str(home / "code" / "demo"), "name"),  # a relative cwd is not usable
        ("withcwd.jsonl", str(home / "code" / "x-y"), "cwd"),
    ]


def test_option_b_refuses_more_than_one_matching_reading(tmp_path):
    home = _layout(tmp_path)
    (home / "code" / "x-y").mkdir()
    (home / "code" / "x" / "y").mkdir(parents=True)
    guard = Guard(home, "home", workspace_names=["home"])
    decoder = Decoder(guard)
    assert decoder.decide(encode(str(home / "code" / "x-y"))) == Decision(None, AMBIGUOUS)
    assert decoder.decide(encode(str(home / "code" / "demo"))) == \
        Decision(str(home / "code" / "demo"), None)


def test_option_b_refuses_when_a_partial_reading_reaches_a_link_into_the_instances(tmp_path):
    """The only complete reading is allowed, but another reading stops at a link into
    ``~/rooms``: refused, never one branch picked."""
    home = _layout(tmp_path)
    os.symlink(home / "rooms" / "y", home / "a")
    (home / "a-b").mkdir()
    guard = Guard(home, "home", workspace_names=["home"])
    assert Decoder(guard).decide(encode(str(home / "a-b"))) == Decision(None, "room instance")


def test_option_b_walk_too_large_is_refused(tmp_path):
    home = _layout(tmp_path)
    guard = Guard(home, "home", workspace_names=["home"])
    decision = Decoder(guard, max_steps=2).decide(encode(str(home / "code" / "demo")))
    assert decision.reason == "undecodable folder name"
    assert Decoder(guard).decide("not-absolute").reason == "undecodable folder name"


def test_stored_cwd_is_resolved_again_on_every_run(tmp_path, recorder):
    """A file admitted under ``~/proj`` loses its rows once ``~/proj`` becomes a link into
    ``~/rooms``, without the unchanged transcript being opened again."""
    home = _layout(tmp_path)
    (home / "proj").mkdir()
    name = encode(str(home / "proj"))
    session = write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                          [claude_user("zebra moved", cwd=str(home / "proj"))])
    conn, stats = _refresh(tmp_path, home)
    assert stats.files_read == [str(session)]
    (home / "proj").rmdir()
    os.symlink(home / "rooms" / "y", home / "proj")
    recorder.opened.clear()
    guard = Guard(home, "home", workspace_names=["home"])
    removed = recall_index.purge_refused(conn, guard)
    assert removed == 1
    assert recorder.opened == []
    assert recall_index.search(conn, '"zebra"', limit=5)[0] == []


def test_changed_file_now_refused_loses_its_rows(tmp_path):
    """A file indexed under an allowed cwd is rewritten with a cwd into ``~/rooms``."""
    home = _layout(tmp_path)
    name = encode(str(home / "code" / "demo"))
    path = write_jsonl(home / ".claude" / "projects" / name / "s.jsonl",
                       [claude_user("zebra first", cwd=str(home / "code" / "demo"))])
    conn, _ = _refresh(tmp_path, home)
    assert len(recall_index.search(conn, '"zebra"', limit=5)[0]) == 1
    write_jsonl(path, [claude_user("zebra second", cwd=str(home / "rooms" / "y"))])
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    guard = Guard(home, "home", workspace_names=["home"])
    stats = recall_index.refresh(conn, guard, recall_index.default_sources(home))
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert stats.files_removed == 1
    assert recall_index.search(conn, '"zebra"', limit=5)[0] == []

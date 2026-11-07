"""The guard refuses room instances and other systems' workspaces before any read."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from recall import index as recall_index
from recall.extract import CLAUDE
from recall.guard import Decision, Decoder, Guard, RoomsPathError, encode, omp_absolute_encoding

from recall_fixtures import claude_user, write_jsonl

# A home folder for a neutral user, and its Claude-encoded form. Each is
# joined from parts so no line holds a home path literal.
HOME_DIR = Path("/Us" "ers/example")
ENC_HOME = "-Us" "ers-example"


def _no_listing(_directory):
    return []


def test_encoded_rooms_project_dir_is_never_read(tmp_path, recorder):
    """A ``<encoded home>-rooms-x`` project dir is refused by name: never listed, never opened."""
    projects = tmp_path / "projects"
    rooms = write_jsonl(projects / f"{ENC_HOME}-rooms-x" / "s1.jsonl",
                        [claude_user("zebra private room words")])
    write_jsonl(projects / f"{ENC_HOME}-code-demo" / "s2.jsonl",
                [claude_user("zebra code words")])
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    found = recall_index.discover(guard, {CLAUDE: projects})
    assert [Path(f.path).name for f in found.files] == ["s2.jsonl"]
    assert found.refused == [(CLAUDE, f"{ENC_HOME}-rooms-x", "room instance")]
    touched = recorder.touched()
    assert touched, "the recorder saw nothing; the seam is not wired"
    assert not [p for p in touched if p.startswith(str(rooms.parent))], touched
    assert str(rooms) not in recorder.opened


def test_rooms_dir_rows_never_reach_the_index(tmp_path, recorder):
    projects = tmp_path / "projects"
    room = write_jsonl(projects / f"{ENC_HOME}-rooms-x" / "s1.jsonl", [claude_user("zebra room")])
    write_jsonl(projects / f"{ENC_HOME}-code-demo" / "s2.jsonl", [claude_user("zebra code")])
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    stats = recall_index.refresh(conn, guard, {CLAUDE: projects},
                                 Decoder(guard, listdir=_no_listing))
    hits, _ = recall_index.search(conn, '"zebra"', limit=10)
    assert [Path(p).name for p in stats.files_read] == ["s2.jsonl"]
    assert [hit.excerpt for hit in hits] == ["[zebra] code"]
    assert not [p for p in recorder.touched() if p.startswith(str(room.parent))]


@pytest.mark.parametrize("name", [
    f"{ENC_HOME}-rooms",
    f"{ENC_HOME}-rooms-kitchen-table",
    f"{ENC_HOME}-Rooms-x",  # case-insensitive file system
    f"-private-tmp-claude-501-{ENC_HOME}-rooms-x-0000-scratchpad",
])
def test_rooms_names_refused(name):
    guard = Guard(HOME_DIR, "home", workspace_names=["home", "work"])
    assert guard.refusal_for_claude_dir(name) == "room instance"


@pytest.mark.parametrize("name", [
    f"{ENC_HOME}-r1-spike-room",
    f"{ENC_HOME}-roomkit",
    f"{ENC_HOME}-code-rooms",
    f"{ENC_HOME}-code-ROOM-notes",
])
def test_any_room_named_transcript_dir_refused(name):
    """The spike's ``*room*`` exclusion: any transcript dir whose name holds ``room``."""
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    assert guard.refusal_for_claude_dir(name) == "room name"


def test_room_word_applies_to_omp_dirs_too():
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    assert guard.refusal_for_omp_dir("-r1-spike-room") == "room name"
    assert guard.refusal_for_omp_dir("--private-tmp-roomy--") == "room name"


def test_spike_room_dir_is_never_read(tmp_path, recorder):
    """``<encoded home>-r1-spike-room`` is refused by name: never listed, never opened."""
    projects = tmp_path / "projects"
    spike = write_jsonl(projects / f"{ENC_HOME}-r1-spike-room" / "s1.jsonl",
                        [claude_user("zebra spike words")])
    write_jsonl(projects / f"{ENC_HOME}-code-demo" / "s2.jsonl", [claude_user("zebra")])
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    found = recall_index.discover(guard, {CLAUDE: projects})
    assert [Path(f.path).name for f in found.files] == ["s2.jsonl"]
    assert found.refused == [(CLAUDE, f"{ENC_HOME}-r1-spike-room", "room name")]
    assert not [p for p in recorder.touched() if p.startswith(str(spike.parent))]


def test_ordinary_dirs_allowed():
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-code-claude-toolkit") is None
    assert guard.refusal_for_omp_dir("-code-claude-toolkit") is None


def test_workspace_encoding_collision_does_not_merge_ownership():
    """``home_lab`` (this system) and ``home-lab`` (another) encode alike: refused."""
    guard = Guard(HOME_DIR, "home_lab", workspace_names=["home_lab", "home-lab"])
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-home-lab") == \
        "another system's workspace"
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-home-lab-x") == \
        "another system's workspace"
    alone = Guard(HOME_DIR, "home_lab", workspace_names=["home_lab"])
    assert alone.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-home-lab") is None


def _refresh_listing(tmp_path, home):
    """Refresh a fresh index over ``home``'s transcripts, recording the decoder's listings."""
    listed = []

    def listdir(directory):
        listed.append(directory)
        return Decoder._default_listdir(directory)

    guard = Guard(home, "home", workspace_names=["home"])
    conn = recall_index.open_index(tmp_path / "idx" / "recall-home.db", "home")
    stats = recall_index.refresh(conn, guard, recall_index.default_sources(home),
                                 Decoder(guard, listdir=listdir))
    return conn, stats, listed


def test_symlinked_folder_into_rooms_is_refused(tmp_path, recorder):
    """A session launched in ``~/my-link`` -> ``~/rooms/y`` is refused by its resolved path.

    It records no ``cwd``, so option B decides it: opened only to look for a
    ``cwd``, never indexed.
    """
    home = tmp_path / "home"
    (home / "rooms" / "y").mkdir(parents=True)
    (home / "rooms" / "y" / "secret-folder").mkdir()
    os.symlink(home / "rooms" / "y", home / "my-link")
    projects = home / ".claude" / "projects"
    session = write_jsonl(projects / encode(str(home / "my-link")) / "s.jsonl",
                          [claude_user("zebra behind a link")])
    conn, stats, listed = _refresh_listing(tmp_path, home)
    assert stats.files_read == []
    assert stats.files_refused == [(CLAUDE, encode(str(home / "my-link")), "room instance")]
    assert recall_index.search(conn, '"zebra"', limit=5)[0] == []
    assert recorder.opened == [str(session)]  # read for its cwd, then refused
    # The decoder never listed the room folder, by its own path or through the link.
    assert not [d for d in listed
                if os.path.realpath(d).startswith(os.path.realpath(home / "rooms"))]


def test_session_below_a_link_into_the_instance_folder(tmp_path, recorder):
    """``~/lnk`` -> ``~/rooms/y`` and a session in ``~/lnk/sub``: refused, and the
    decoder never lists the linked folder (it would have to, to find ``sub``)."""
    # The test name avoids the word "room": pytest names tmp_path after it.
    home = tmp_path / "home"
    (home / "rooms" / "y" / "sub").mkdir(parents=True)
    os.symlink(home / "rooms" / "y", home / "lnk")
    projects = home / ".claude" / "projects"
    name = encode(str(home / "lnk" / "sub"))
    session = write_jsonl(projects / name / "s.jsonl", [claude_user("zebra below a link")])
    conn, stats, listed = _refresh_listing(tmp_path, home)
    assert stats.files_read == []
    assert stats.files_refused == [(CLAUDE, name, "room instance")]
    assert recall_index.search(conn, '"zebra"', limit=5)[0] == []
    assert recorder.opened == [str(session)]
    assert str(home) in listed, "the decoder walk never reached the home folder"
    assert not [d for d in listed
                if os.path.realpath(d).startswith(os.path.realpath(home / "rooms"))], listed


def test_omp_rooms_session_dir_refused():
    guard = Guard(HOME_DIR, "home", workspace_names=["home"])
    assert guard.refusal_for_omp_dir("-rooms-kitchen-table") == "room instance"
    assert guard.refusal_for_omp_dir(f"--private-tmp-{ENC_HOME}-rooms-x--") == "room instance"
    assert guard.refusal_for_omp_dir("-code-claude-toolkit") is None
    assert omp_absolute_encoding("-code-skills-.claude", HOME_DIR) == encode(
        str(HOME_DIR / "code" / "skills" / ".claude"))


def test_other_systems_workspace_refused():
    guard = Guard(HOME_DIR, "home", workspace_names=["home", "work", "home-lab"])
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-home") is None
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-home-scratch") is None
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-work") == \
        "another system's workspace"
    # ``home-lab`` and ``home`` both match: ambiguous, so refused.
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-home-lab") == \
        "another system's workspace"
    assert guard.refusal_for_claude_dir(f"{ENC_HOME}-workspaces-gone") == "unknown workspace"
    assert guard.refusal_for_omp_dir("-workspaces-work") == "another system's workspace"
    assert guard.refusal_for_omp_dir("-workspaces-home") is None


def test_rooms_path_refused(tmp_path):
    """A ``~/rooms/**`` path is refused, as given, by case, and through a symlink."""
    home = tmp_path / "home"
    (home / "rooms" / "x").mkdir(parents=True)
    link = tmp_path / "innocent"
    os.symlink(home / "rooms" / "x", link)
    guard = Guard(home, "home", workspace_names=["home"])
    for path in (home / "rooms", home / "rooms" / "x" / "recall.db",
                 home / "Rooms" / "y", link / "recall.db"):
        with pytest.raises(RoomsPathError):
            guard.refuse_path(path, "index path")
    guard.refuse_path(home / "code" / "x", "index path")


def test_symlinked_project_dir_not_followed(tmp_path, recorder):
    home = tmp_path / "home"
    room = write_jsonl(home / "rooms" / "x" / "s.jsonl", [claude_user("zebra")])
    projects = home / ".claude" / "projects"
    projects.mkdir(parents=True)
    os.symlink(room.parent, projects / "-innocent-name")
    guard = Guard(home, "home", workspace_names=["home"])
    found = recall_index.discover(guard, {CLAUDE: projects})
    assert found.files == []
    assert str(room) not in recorder.opened


def test_decoder_resolves_dashes_and_dots(tmp_path):
    home = tmp_path / "home"
    (home / "code" / "claude-toolkit" / ".skills-upstream").mkdir(parents=True)
    (home / "code" / "claude").mkdir(parents=True)
    guard = Guard(home, "home", workspace_names=["home"])
    decoder = Decoder(guard)
    target = home / "code" / "claude-toolkit" / ".skills-upstream"
    assert decoder.decide(encode(str(target))) == Decision(str(target), None)
    assert decoder.decide(encode(str(home / "code" / "claude-toolkit"))) == \
        Decision(str(home / "code" / "claude-toolkit"), None)
    # A folder that is gone (no reading matches a real folder) is labelled plainly.
    assert decoder.decide("-nowhere-at-all") == Decision("/nowhere/at/all", None)


def test_decoder_never_lists_the_instance_folder(tmp_path):
    # Renamed from test_decoder_never_lists_rooms: pytest names tmp_path after the
    # test, and a tmp_path holding the word "room" is refused before ~/rooms is reached.
    home = tmp_path / "home"
    (home / "rooms" / "x").mkdir(parents=True)
    listed = []

    def listdir(directory):
        listed.append(directory)
        return Decoder._default_listdir(directory)

    guard = Guard(home, "home", workspace_names=["home"])
    decision = Decoder(guard, listdir=listdir).decide(encode(str(home / "rooms" / "x")))
    assert decision == Decision(None, "room instance")
    assert listed, "the decoder listed nothing; the seam is not wired"
    assert not [d for d in listed if d.startswith(str(home / "rooms"))]


def test_bad_system_name_refused():
    with pytest.raises(ValueError):
        Guard(HOME_DIR, "../x")

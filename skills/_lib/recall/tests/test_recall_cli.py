"""``ledger recall`` end to end, under a temporary HOME.

The verb is a ledger plugin (``recall.ledger_verb``); the ledger CLI runs it
here the way the ledger command does, passed in as a plugin
module.
"""
from __future__ import annotations

import io
import json
import os
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

import pytest

from ledger.cli import main
from ledger.store import claim_database, open_ledger
from ledger.verbs import EXIT_NOT_FOUND, EXIT_OK, EXIT_REFUSED
from recall import DISCLAIMER, ledger_verb

from recall_fixtures import claude_assistant, claude_project, claude_tool_result, claude_user, write_jsonl


def invoke(monkeypatch, home: Path, argv: list[str]):
    monkeypatch.setenv("HOME", str(home))
    for key in ("LEDGER_DB", "LEDGER_HARNESS"):
        monkeypatch.delenv(key, raising=False)
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(argv, plugin_modules=(ledger_verb,))
    return code, out.getvalue(), err.getvalue()


@pytest.fixture
def ledger_db(tmp_path) -> str:
    db = tmp_path / "ledger" / "ledger.db"
    db.parent.mkdir()
    claim_database(db, "home")
    return str(db)


@pytest.fixture
def corpus(home):
    project = claude_project(home / ".claude" / "projects", home / "code" / "demo")
    write_jsonl(project / "s1.jsonl", [
        claude_user("we settled the zebra crossing question", ts="2026-10-03T09:00:00.000Z",
                    origin={"kind": "human"}, promptSource="typed"),
        claude_assistant("noted: zebra crossing stays", ts="2026-10-03T09:00:05.000Z"),
        claude_tool_result("grep found zebra in three files", ts="2026-10-03T09:00:06.000Z"),
    ])
    write_jsonl(project / "s1" / "subagents" / "agent-b2.jsonl", [
        claude_user("brief: zebra crossing build", ts="2026-10-03T09:01:00.000Z",
                    isSidechain=True, agentId="b2")])
    return home


def test_expected_hit_with_speaker_label(monkeypatch, corpus, ledger_db):
    code, out, err = invoke(monkeypatch, corpus,
                            ["--db", ledger_db, "recall", "zebra", "settled"])
    assert code == EXIT_OK, err
    lines = out.splitlines()
    assert lines[0] == "2026-10-03T09:00:00.000Z\t~" "/code/demo\tspeaker=person\tkind=user\tclaude"
    assert "[zebra]" in lines[1] and "[settled]" in lines[1]
    assert lines[2] == f"    ({DISCLAIMER})"


def test_disclaimer_on_every_hit(monkeypatch, corpus, ledger_db):
    code, out, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "recall", "zebra"])
    assert code == EXIT_OK, err
    headers = [line for line in out.splitlines() if "\tspeaker=" in line]
    assert len(headers) == 4
    assert out.count(f"({DISCLAIMER})") == len(headers)
    lines = out.splitlines()
    for at, line in enumerate(lines):
        if "\tspeaker=" in line:
            assert lines[at + 2] == f"    ({DISCLAIMER})"
            ts, project, speaker = line.split("\t")[:3]
            assert ts.startswith("2026-10-03T") and project == "~" "/code/demo"
    speakers = sorted(line.split("\t")[2] for line in headers)
    assert speakers == ["speaker=agent", "speaker=assistant", "speaker=person", "speaker=tool"]

    code, out, _ = invoke(monkeypatch, corpus,
                          ["--db", ledger_db, "--json", "recall", "zebra", "--no-refresh"])
    payload = json.loads(out)
    assert code == EXIT_OK and payload["ok"]
    assert len(payload["data"]["hits"]) == 4
    for hit in payload["data"]["hits"]:
        assert hit["disclaimer"] == DISCLAIMER
        assert hit["ts"] and hit["project"] and hit["speaker"]


def test_index_lands_in_cache_named_for_the_system(monkeypatch, corpus, ledger_db):
    code, out, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "--json", "recall"])
    assert code == EXIT_OK, err
    data = json.loads(out)["data"]
    assert data["index"]["path"] == str(
        (corpus / ".cache" / "harness" / "recall-home.db").resolve())
    assert data["scan"]["files_read"] == 2
    code, out, _ = invoke(monkeypatch, corpus, ["--db", ledger_db, "--json", "recall"])
    assert json.loads(out)["data"]["scan"]["files_read"] == 0


def test_rooms_index_or_project_path_refused(monkeypatch, corpus, ledger_db):
    (corpus / "rooms" / "x").mkdir(parents=True)
    code, _, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "recall", "zebra",
                                                "--index", str(corpus / "rooms" / "x" / "i.db")])
    assert code == EXIT_REFUSED
    assert "room instance" in err
    assert not (corpus / "rooms" / "x" / "i.db").exists()
    code, _, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "recall", "zebra",
                                                "--project", "~/rooms/x"])
    assert code == EXIT_REFUSED
    assert "room instance" in err


def test_e2e_instance_transcripts_never_indexed(monkeypatch, corpus, ledger_db, recorder):
    # The test name avoids the word "room": pytest names tmp_path after it, and the
    # guard refuses any transcript folder whose path holds that word.
    room = write_jsonl(claude_project(corpus / ".claude" / "projects", corpus / "rooms" / "x")
                       / "r.jsonl", [claude_user("zebra from the room", origin={"kind": "human"})])
    code, out, err = invoke(monkeypatch, corpus,
                            ["--db", ledger_db, "--json", "recall", "zebra", "room"])
    assert code == EXIT_NOT_FOUND, out
    assert not [p for p in recorder.touched() if p.startswith(str(room.parent))]
    code, out, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "--json", "recall", "zebra"])
    assert code == EXIT_OK, err  # the ordinary project beside it is still indexed
    assert len(json.loads(out)["data"]["hits"]) == 4


def test_unclaimed_ledger_refused(monkeypatch, corpus, tmp_path):
    db = tmp_path / "plain.db"
    open_ledger(db).close()
    code, _, err = invoke(monkeypatch, corpus, ["--db", str(db), "recall", "zebra"])
    assert code == EXIT_REFUSED
    assert "not claimed" in err


def test_unwritable_index_folder_is_one_line_exit_3(monkeypatch, corpus, ledger_db, tmp_path):
    locked = tmp_path / "locked"
    locked.mkdir()
    os.chmod(locked, 0o500)
    try:
        code, out, err = invoke(monkeypatch, corpus, [
            "--db", ledger_db, "recall", "zebra", "--index", str(locked / "sub" / "i.db")])
    finally:
        os.chmod(locked, 0o700)
    assert code == EXIT_REFUSED
    assert "Traceback" not in err and len(err.strip().splitlines()) == 1
    assert "cannot open the recall index" in err


def test_index_pointed_at_the_ledger_is_refused_untouched(monkeypatch, corpus, tmp_path):
    folder = tmp_path / "private"
    folder.mkdir()
    os.chmod(folder, 0o700)
    db = folder / "ledger.db"
    claim_database(db, "home")
    os.chmod(db, 0o600)
    before = db.read_bytes()
    code, _, err = invoke(monkeypatch, corpus,
                          ["--db", str(db), "recall", "zebra", "--index", str(db)])
    assert code == EXIT_REFUSED
    assert "not a recall index" in err
    assert db.read_bytes() == before


def test_excerpt_is_printed_verbatim(monkeypatch, home, ledger_db):
    project = claude_project(home / ".claude" / "projects", home / "code" / "demo")
    write_jsonl(project / "s1.jsonl", [claude_user("alpha   beta\n  second line",
                                                   origin={"kind": "human"})])
    code, out, err = invoke(monkeypatch, home, ["--db", ledger_db, "recall", "alpha"])
    assert code == EXIT_OK, err
    lines = out.splitlines()
    assert lines[1:4] == ["    [alpha]   beta", "      second line", f"    ({DISCLAIMER})"]


def test_no_hits_is_not_found(monkeypatch, corpus, ledger_db):
    code, _, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "recall", "okapi"])
    assert code == EXIT_NOT_FOUND
    assert "no recall hits" in err


def test_e2e_session_whose_cwd_resolves_into_the_instances_is_not_indexed(
        monkeypatch, corpus, ledger_db):
    """Option A end to end: an ordinary-looking folder, a ``cwd`` through a link into
    ``~/rooms``; counted under ``files_refused`` by reason, never named, never hit."""
    (corpus / "rooms" / "y").mkdir(parents=True)
    os.symlink(corpus / "rooms" / "y", corpus / "proj")
    write_jsonl(claude_project(corpus / ".claude" / "projects", corpus / "proj") / "p.jsonl",
                [claude_user("zebra okapi linked", cwd=str(corpus / "proj"),
                             origin={"kind": "human"})])
    code, out, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "--json", "recall", "okapi"])
    assert code == EXIT_NOT_FOUND, out
    code, out, err = invoke(monkeypatch, corpus, ["--db", ledger_db, "--json", "recall"])
    scan = json.loads(out)["data"]["scan"]
    assert code == EXIT_OK, err
    assert scan["files_refused"] == {"room instance": 1}
    assert "proj" not in out

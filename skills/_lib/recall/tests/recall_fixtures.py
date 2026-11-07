"""Helpers for the recall tests: synthetic transcript records and a read recorder.

Every transcript here is invented. Nothing reads the real home directory: the
tests point ``HOME`` at a temporary folder, and library calls take ``home``
explicitly.
"""
from __future__ import annotations

import json
from pathlib import Path

from recall import index as recall_index
from recall.guard import encode


def write_jsonl(path: Path, records: list[dict]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")
    return path


def claude_user(text: str, ts: str = "2026-10-01T10:00:00.000Z", **extra) -> dict:
    record = {"type": "user", "timestamp": ts, "sessionId": "s1", "isSidechain": False,
              "userType": "external", "entrypoint": "cli",
              "message": {"role": "user", "content": text}}
    record.update(extra)
    return record


def claude_assistant(text: str, ts: str = "2026-10-01T10:00:05.000Z") -> dict:
    return {"type": "assistant", "timestamp": ts, "sessionId": "s1",
            "message": {"role": "assistant", "content": [
                {"type": "thinking", "thinking": "hidden"},
                {"type": "text", "text": text}]}}


def claude_tool_result(text: str, ts: str = "2026-10-01T10:00:06.000Z") -> dict:
    return {"type": "user", "timestamp": ts, "sessionId": "s1",
            "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": [{"type": "text", "text": text}]}]}}


def claude_project(projects: Path, folder: Path | str) -> Path:
    """The project directory Claude Code would use for ``folder``."""
    return projects / encode(str(folder))


class Recorder:
    """Records every directory listed and file opened by the indexer."""

    def __init__(self, monkeypatch):
        self.listed: list[str] = []
        self.opened: list[str] = []
        real_scandir = recall_index._scandir
        real_open = recall_index._open_transcript

        def scandir(path):
            self.listed.append(str(path))
            return real_scandir(path)

        def open_transcript(path):
            self.opened.append(str(path))
            return real_open(path)

        monkeypatch.setattr(recall_index, "_scandir", scandir)
        monkeypatch.setattr(recall_index, "_open_transcript", open_transcript)

    def touched(self) -> list[str]:
        return self.listed + self.opened

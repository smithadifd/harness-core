"""The speaker classifier: a ``role: user`` record is a person only on positive provenance."""
from __future__ import annotations

import io
import json

import pytest

from recall import classify
from recall.extract import CLAUDE, OMP, read_records

from recall_fixtures import claude_assistant, claude_tool_result, claude_user


def _speakers(records, source=CLAUDE, agent_file=False):
    handle = io.StringIO("".join(json.dumps(r) + "\n" for r in records))
    return [(rec.kind, rec.speaker) for rec in read_records(handle, source, agent_file=agent_file)]


def test_agent_to_agent_user_record_in_subagent_file_is_agent():
    """An instruction to a subagent, stored as ``role: user`` in a subagent transcript, is labelled agent."""
    brief = claude_user("Build the widget. The owner decided X.", isSidechain=True,
                        agentId="a575ff0c", origin={"kind": "human"}, promptSource="typed")
    assert _speakers([brief], agent_file=True) == [("user", classify.AGENT)]


@pytest.mark.parametrize("extra", [
    {"isSidechain": True},
    {"agentId": "a575ff0c"},
    {"sessionId": "agent-a575ff0c"},
    {"entrypoint": "sdk-cli"},
    {"origin": {"kind": "peer", "from": "other-seat"}},
    {"origin": {"kind": "task-notification"}},
    {"origin": {"kind": "coordinator"}},
    {"promptSource": "sdk"},
])
def test_agent_signals_label_agent_not_person(extra):
    record = claude_user("please do the thing", **extra)
    assert _speakers([record]) == [("user", classify.AGENT)]


def test_agent_signal_beats_a_human_origin():
    record = claude_user("brief", isSidechain=True, origin={"kind": "human"}, promptSource="typed")
    assert _speakers([record]) == [("user", classify.AGENT)]


def test_typed_human_prompt_is_person():
    assert _speakers([claude_user("hello", origin={"kind": "human"}, promptSource="typed")]) == \
        [("user", classify.PERSON)]
    assert _speakers([claude_user("hello", promptSource="typed")]) == [("user", classify.PERSON)]


@pytest.mark.parametrize("record", [
    claude_user("x", isMeta=True, origin={"kind": "human"}),
    claude_user("x", promptSource="system", origin={"kind": "human"}),
    claude_user("<system-reminder>be careful</system-reminder>", origin={"kind": "human"}),
    claude_user("<local-command-stdout>ok</local-command-stdout>", origin={"kind": "human"}),
    claude_user("summary", isCompactSummary=True),
])
def test_harness_records_are_system(record):
    assert _speakers([record]) == [("user", classify.SYSTEM)]


def test_no_provenance_is_unverified():
    assert _speakers([claude_user("old style prompt")]) == [("user", classify.UNVERIFIED)]


def test_assistant_and_tool_kinds():
    got = _speakers([claude_assistant("I will look"), claude_tool_result("file body")])
    assert got == [("assistant", classify.ASSISTANT), ("tool_result", classify.TOOL)]


def test_omp_records():
    records = [
        {"type": "message", "timestamp": "t", "message": {
            "role": "user", "attribution": "user", "content": [{"type": "text", "text": "a"}]}},
        {"type": "message", "timestamp": "t", "message": {
            "role": "user", "attribution": "agent", "content": [{"type": "text", "text": "b"}]}},
        {"type": "message", "timestamp": "t", "message": {
            "role": "assistant", "content": [{"type": "thinking", "thinking": "z"},
                                             {"type": "text", "text": "c"}]}},
        {"type": "message", "timestamp": "t", "message": {
            "role": "toolResult", "content": [{"type": "text", "text": "d"}]}},
        {"type": "custom_message", "timestamp": "t", "attribution": "agent",
         "customType": "irc:incoming", "content": "e"},
        {"type": "custom", "customType": "tool_execution_start", "data": {}},
    ]
    assert _speakers(records, OMP) == [
        ("user", classify.UNVERIFIED), ("user", classify.AGENT),
        ("assistant", classify.ASSISTANT), ("tool_result", classify.TOOL),
        ("custom", classify.AGENT),
    ]
    assert _speakers(records[:1], OMP, agent_file=True) == [("user", classify.AGENT)]


def test_broken_lines_are_skipped():
    handle = io.StringIO('{"type": "user", "message": \n[1]\n' + json.dumps(claude_user("ok")) + "\n")
    assert [r.text for r in read_records(handle, CLAUDE, agent_file=False)] == ["ok"]

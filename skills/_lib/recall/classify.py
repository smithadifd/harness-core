"""Who spoke a transcript record: the speaker-kind classifier.

``role: user`` does not mean a person typed it. In a Claude Code transcript a
user-role record is also an instruction to a subagent, a subagent's
report, a peer session's message, a scheduled task or a harness reminder. The
classifier labels a user-role record ``person`` only on positive provenance,
and lets every agent or harness signal win over it:

1. ``agent`` when the record comes from another agent: the file sits in a
   ``subagents/`` folder or its name or session id starts ``agent-``; the
   record has ``isSidechain: true`` or an ``agentId``; it came through the SDK
   entrypoint (a program drove the session); or its ``origin.kind`` is
   anything but ``human`` (``peer``, ``task-notification``, ``coordinator``).
2. ``system`` when the harness wrote it: ``isMeta``, a compact summary,
   ``promptSource: system``, or text that opens with a harness tag
   (``<system-reminder>``, ``<task-notification>``, a local-command echo).
3. ``person`` when ``origin.kind`` is ``human``, or, with no origin recorded,
   ``promptSource`` is ``typed``.
4. ``unverified`` otherwise: a user-role record with no provenance either way
   (older Claude Code records). Every OMP user message is ``unverified`` or
   ``agent``: OMP records ``attribution: agent`` for agent-written input, but
   its ``attribution: user`` also covers a launcher's brief, so it is not
   proof that a person typed the words.

Text typed into a session's terminal by another program (a launcher sending
a brief through a terminal multiplexer) is recorded exactly like a person's
typing; no field tells them apart, so such a record reads ``person``. That is
why every hit also carries the disclaimer.

Non-user records: ``assistant`` for model replies, ``tool`` for tool results.
"""
from __future__ import annotations

from typing import Any, Mapping

PERSON = "person"
AGENT = "agent"
SYSTEM = "system"
UNVERIFIED = "unverified"
ASSISTANT = "assistant"
TOOL = "tool"
SPEAKERS = (PERSON, AGENT, SYSTEM, UNVERIFIED, ASSISTANT, TOOL)

HARNESS_PREFIXES = (
    "<system-reminder",
    "<task-notification",
    "<local-command-",
    "<command-name",
    "<command-message",
    "<command-args",
    "<bash-stdout",
    "<bash-stderr",
    "Caveat: The messages below were generated",
    "This session is being continued from a previous conversation",
    "[Request interrupted",
)


def is_agent_file(parts: tuple[str, ...], name: str) -> bool:
    """A transcript file that only ever holds another agent's session."""
    return "subagents" in parts or name.startswith("agent-")


def classify_claude_user(record: Mapping[str, Any], text: str, *, agent_file: bool) -> str:
    """Speaker kind of a Claude Code user-role record that carries typed text."""
    if agent_file:
        return AGENT
    if record.get("isSidechain") is True:
        return AGENT
    if isinstance(record.get("agentId"), str) and record.get("agentId"):
        return AGENT
    session = record.get("sessionId")
    if isinstance(session, str) and session.startswith("agent-"):
        return AGENT
    entrypoint = record.get("entrypoint")
    if isinstance(entrypoint, str) and entrypoint.startswith("sdk"):
        return AGENT
    origin = record.get("origin")
    origin_kind = origin.get("kind") if isinstance(origin, Mapping) else None
    if origin_kind is not None and origin_kind != "human":
        return AGENT
    source = record.get("promptSource")
    if source == "sdk":
        return AGENT
    if record.get("isMeta") is True or record.get("isCompactSummary") is True:
        return SYSTEM
    if record.get("isVisibleInTranscriptOnly") is True:
        return SYSTEM
    if source == "system":
        return SYSTEM
    if text.lstrip().startswith(HARNESS_PREFIXES):
        return SYSTEM
    if origin_kind == "human":
        return PERSON
    if origin_kind is None and source == "typed":
        return PERSON
    return UNVERIFIED


def classify_omp_user(message: Mapping[str, Any], *, agent_file: bool) -> str:
    """Speaker kind of an OMP user-role message."""
    if agent_file:
        return AGENT
    if message.get("attribution") == "agent":
        return AGENT
    return UNVERIFIED


def classify_omp_custom(record: Mapping[str, Any], *, agent_file: bool) -> str:
    """Speaker kind of an OMP ``custom_message`` (injected results and peer messages)."""
    if agent_file or record.get("attribution") == "agent":
        return AGENT
    return SYSTEM

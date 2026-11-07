"""Turn one transcript file into indexable records.

A record is one piece of text with its timestamp, record kind and speaker
kind. Record kinds:

- ``user``: text in a user-role record (a person, an agent or the harness;
  ``recall.classify`` decides which).
- ``assistant``: a model reply's text blocks (thinking and tool calls are
  not indexed).
- ``tool_result``: the text a tool returned.
- ``custom``: an OMP injected message (async results, peer messages).

Each record's text is capped at ``MAX_TEXT`` characters, which bounds the
index on very large tool outputs. Lines that are not JSON objects (a line
still being written, a corrupt line) are skipped.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import IO, Any, Callable, Iterator, Mapping

from recall import classify

MAX_TEXT = 16_000

CLAUDE = "claude"
OMP = "omp"
KINDS = ("user", "assistant", "tool_result", "custom")


@dataclass(frozen=True)
class Record:
    ts: str
    kind: str
    speaker: str
    text: str


def _text_blocks(content: Any, block_type: str = "text") -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts = []
    for block in content:
        if isinstance(block, Mapping) and block.get("type") == block_type:
            value = block.get("text")
            if isinstance(value, str):
                parts.append(value)
        elif isinstance(block, str):
            parts.append(block)
    return "\n".join(parts)


def _cap(text: str) -> str:
    text = text.strip()
    return text[:MAX_TEXT]


def _canonical(moment: datetime) -> str:
    """One fixed-width UTC form, ``YYYY-MM-DDTHH:MM:SS.uuuuuuZ`` (microseconds,
    all the precision the parser keeps; longer fractions are cut to it), so that
    the index's text ordering (``--recent``) is time ordering."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)  # a zone-less stamp is read as UTC
    moment = moment.astimezone(timezone.utc)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.") + f"{moment.microsecond:06d}Z"


def _ts_value(value: Any) -> str | None:
    """An ISO timestamp string, or epoch seconds or milliseconds, as ``_canonical``.

    A string that is not an ISO timestamp is not a timestamp: ``None``.
    """
    try:
        if isinstance(value, str):
            text = value.strip()
            if text.endswith(("Z", "z")):
                text = text[:-1] + "+00:00"
            return _canonical(datetime.fromisoformat(text))
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            seconds = value / 1000.0 if value > 10_000_000_000 else value
            return _canonical(datetime.fromtimestamp(seconds, tz=timezone.utc))
    except (OSError, OverflowError, ValueError):
        return None
    return None


def _ts(record: Mapping[str, Any], message: Any = None) -> str:
    """A record's timestamp; OMP may put it inside ``message``, and may write it
    as epoch seconds or milliseconds (the forms ``session-handoff/mix_report.py``
    also reads)."""
    value = _ts_value(record.get("timestamp"))
    if value is None and isinstance(message, Mapping):
        value = _ts_value(message.get("timestamp"))
    return value or ""


def claude_records(record: Mapping[str, Any], *, agent_file: bool) -> Iterator[Record]:
    kind = record.get("type")
    message = record.get("message")
    if kind not in ("user", "assistant") or not isinstance(message, Mapping):
        return
    role = message.get("role")
    content = message.get("content")
    ts = _ts(record)
    if role == "assistant":
        text = _cap(_text_blocks(content))
        if text:
            yield Record(ts, "assistant", classify.ASSISTANT, text)
        return
    if role != "user":
        return
    if isinstance(content, list):
        for block in content:
            if isinstance(block, Mapping) and block.get("type") == "tool_result":
                text = _cap(_text_blocks(block.get("content")))
                if text:
                    yield Record(ts, "tool_result", classify.TOOL, text)
    text = _cap(_text_blocks(content))
    if text:
        speaker = classify.classify_claude_user(record, text, agent_file=agent_file)
        yield Record(ts, "user", speaker, text)


def omp_records(record: Mapping[str, Any], *, agent_file: bool) -> Iterator[Record]:
    kind = record.get("type")
    message = record.get("message")
    ts = _ts(record, message)
    if kind == "custom_message":
        text = _cap(_text_blocks(record.get("content")))
        if text:
            yield Record(ts, "custom", classify.classify_omp_custom(record, agent_file=agent_file), text)
        return
    if kind != "message" or not isinstance(message, Mapping):
        return
    role = message.get("role")
    text = _cap(_text_blocks(message.get("content")))
    if not text:
        return
    if role == "user":
        yield Record(ts, "user", classify.classify_omp_user(message, agent_file=agent_file), text)
    elif role == "assistant":
        yield Record(ts, "assistant", classify.ASSISTANT, text)
    elif role == "toolResult":
        yield Record(ts, "tool_result", classify.TOOL, text)


def first_cwd(handle: IO[str]) -> str | None:
    """The ``cwd`` of the first record that carries one, or ``None``.

    Claude Code writes ``cwd`` on its message and attachment records, and OMP
    on the ``session`` record near the top of the file, so this normally
    stops within the first few lines. Only a record's top-level ``cwd`` field
    counts; the word inside message text does not. A first ``cwd`` that is
    not a string ends the search with ``None``.
    """
    for line in handle:
        # Every line is parsed: the key can be spelled ``"\u0063wd"`` and is
        # still ``cwd``, so no test on the raw text decides this.
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict) and "cwd" in record:
            value = record["cwd"]
            return value if isinstance(value, str) else None
    return None


def usable_cwd(value: str | None) -> bool:
    """An absolute path: the only ``cwd`` option A trusts."""
    return isinstance(value, str) and value.startswith("/") and "\x00" not in value


# Cheap substring tests that let most framing lines skip the JSON parse.
_CLAUDE_HINT = '"message"'
_OMP_HINTS = ('"message"', '"custom_message"')


def read_records(handle: IO[str], source: str, *, agent_file: bool,
                 on_cwd: Callable[[str], None] | None = None) -> Iterator[Record]:
    """Every indexable record in ``handle``.

    ``on_cwd`` is called with every record's top-level ``cwd`` string, before
    that record's text is yielded, so a caller can stop the file (by raising)
    the moment a session moves somewhere refused.
    """
    hints = (_CLAUDE_HINT,) if source == CLAUDE else _OMP_HINTS
    for line in handle:
        # With a cwd guard, every line is parsed and judged by its parsed
        # top-level ``cwd``, whatever its JSON spelling (``"\u0063wd"``).
        wants_cwd = on_cwd is not None
        if not wants_cwd and not any(hint in line for hint in hints):
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        if wants_cwd and isinstance(record.get("cwd"), str):
            on_cwd(record["cwd"])
        if source == CLAUDE:
            yield from claude_records(record, agent_file=agent_file)
        else:
            yield from omp_records(record, agent_file=agent_file)

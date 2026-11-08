"""Ticket-source interface with opt-in Notion and refusing implementations.

The command-line entry is a separate ``ticket_source.py`` script in the toolkit.
This module loads no code itself: a caller that wants the Notion source loads
the Notion helpers and passes a loader for them."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from typing import Callable, Iterator, Mapping, Protocol


Transport = Callable[[str, str, dict | None], dict]
# Returns the caller's loaded Notion helpers module.
NotionTasksLoader = Callable[[], object]


_NOTION_LOCK = threading.RLock()


@dataclass(frozen=True)
class Ticket:
    id: str
    title: str
    body_md: str
    lane: str | None
    type: str | None
    sot_ref: str
    url: str | None


@dataclass(frozen=True)
class Subtask:
    """A tracker item created under an anchor: its id and, when known, its URL."""

    id: str
    url: str | None


class TicketSource(Protocol):
    """Read and write one tracker.

    ``create_subtask`` creates one tracker item under the anchor item
    ``anchor_id`` and returns it; ``comment`` posts one comment on a tracker
    item and returns the comment's id. Neither edits the anchor itself or the
    tracker's own breakdown of it. A caller that must write at most once
    turns off its transport's retries around these calls.
    """

    def pull(self, lane: str) -> list[Ticket]: ...

    def get(self, ticket_id: str) -> Ticket: ...

    def file(self, ticket: Ticket) -> str: ...

    def create_subtask(self, anchor_id: str, title: str, body_md: str) -> Subtask: ...

    def comment(self, ticket_id: str, text: str) -> str: ...


class TicketSourceRefused(RuntimeError):
    """A named, safe refusal to select or use a ticket source."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class NotionTicketSource:
    """Adapt the existing Notion helpers to the ticket-source interface."""

    def __init__(
        self,
        transport: Transport | None = None,
        db_id: str | None = None,
        *,
        notion_tasks: NotionTasksLoader | None,
    ):
        if notion_tasks is None:
            raise TicketSourceRefused("notion_tasks_path_unset")
        try:
            self._notion = notion_tasks()
        except (ImportError, OSError, RuntimeError):
            raise TicketSourceRefused("notion_helper_unavailable") from None
        self._transport = transport or self._notion.api
        self._db_id = db_id or self._notion.CFG["tasks_database_id"]

    @contextmanager
    def _using_transport(self) -> Iterator[None]:
        with _NOTION_LOCK:
            previous = self._notion.api
            self._notion.api = self._transport
            try:
                yield
            finally:
                self._notion.api = previous

    def _from_page(self, page: dict) -> Ticket:
        with self._using_transport():
            shaped = self._notion.page_to_dict(
                page, with_body=True, with_comments=False
            )
            lane = self._notion.page_lane(page)
            ticket_type = self._notion.page_type(page)
        return Ticket(
            id=shaped["id"],
            title=shaped["title"],
            body_md=shaped.get("body") or shaped.get("description", ""),
            lane=lane,
            type=ticket_type,
            sot_ref=shaped.get("sot_ref", ""),
            url=shaped.get("url"),
        )

    def pull(self, lane: str) -> list[Ticket]:
        with self._using_transport():
            pages = self._notion.query_todo_pages(self._db_id, lane=lane)
        return [self._from_page(page) for page in pages]

    def get(self, ticket_id: str) -> Ticket:
        page = self._transport("GET", f"/pages/{ticket_id}", None)
        return self._from_page(page)

    def file(self, ticket: Ticket) -> str:
        props = self._notion.CFG["props"]
        if ticket.lane is not None:
            lane_options = self._notion.CFG.get("lane_options") or []
            if ticket.lane not in lane_options:
                raise TicketSourceRefused("invalid_notion_lane")

        ticket_type = None
        if ticket.type is not None:
            ticket_type = self._notion.canonical_type_value(ticket.type)
            type_prop = props.get("type")
            type_options = self._notion.CFG.get("type_options") or []
            if self._notion.type_write_error(
                ticket_type, type_options, type_prop=type_prop, schema=None
            ):
                raise TicketSourceRefused("invalid_notion_type")
            schema = self._transport("GET", f"/databases/{self._db_id}", None)
            if self._notion.type_write_error(
                ticket_type, type_options, type_prop=type_prop, schema=schema
            ):
                raise TicketSourceRefused("invalid_notion_type")

        properties = {
            props["name"]: {
                "title": [{"type": "text", "text": {"content": ticket.title}}]
            },
            props["description"]: {
                "rich_text": [
                    {"type": "text", "text": {"content": part}}
                    for part in self._notion.chunk(ticket.body_md)
                ]
            },
        }
        if ticket.lane is not None:
            properties[props["lane"]] = {"select": {"name": ticket.lane}}
        if ticket_type is not None:
            properties[props["type"]] = {"select": {"name": ticket_type}}
        if ticket.sot_ref and props.get("sot_ref"):
            properties[props["sot_ref"]] = {
                "rich_text": [
                    {"type": "text", "text": {"content": part}}
                    for part in self._notion.chunk(ticket.sot_ref)
                ]
            }
        payload = {
            "parent": {"database_id": self._db_id},
            "properties": properties,
        }
        if self._notion.DRY_RUN:
            print(
                f"[dry-run] would create page: {json.dumps(payload)}",
                file=sys.stderr,
            )
            raise TicketSourceRefused("notion_dry_run")
        response = self._transport("POST", "/pages", payload)
        new_id = response.get("id")
        if not new_id:
            raise TicketSourceRefused("notion_create_missing_id")
        return new_id

    def create_subtask(self, anchor_id: str, title: str, body_md: str) -> Subtask:
        """Create a page under the anchor page ``anchor_id`` and return it.

        The tasks database has no sub-item relation, so the subtask is a child
        page of the anchor: ``title`` as its title and ``body_md`` as
        paragraphs. The anchor's properties and its database row are not
        touched.
        """
        payload = {
            "parent": {"page_id": anchor_id},
            "properties": {
                "title": {
                    "title": [
                        {"type": "text", "text": {"content": part}}
                        for part in self._notion.chunk(title)
                    ]
                }
            },
        }
        children = [
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": part}}]},
            }
            for part in self._notion.chunk(body_md)
            if part
        ]
        if children:
            payload["children"] = children
        if self._notion.DRY_RUN:
            print(
                f"[dry-run] would create subtask: {json.dumps(payload)}",
                file=sys.stderr,
            )
            raise TicketSourceRefused("notion_dry_run")
        response = self._transport("POST", "/pages", payload)
        new_id = response.get("id")
        if not new_id:
            raise TicketSourceRefused("notion_create_missing_id")
        return Subtask(id=new_id, url=response.get("url"))

    def comment(self, ticket_id: str, text: str) -> str:
        """Post one comment on the page ``ticket_id``; return the comment's id."""
        if not text.strip():
            raise TicketSourceRefused("comment_text_empty")
        payload = {
            "parent": {"page_id": ticket_id},
            "rich_text": [
                {"type": "text", "text": {"content": part}}
                for part in self._notion.chunk(text)
            ],
        }
        if self._notion.DRY_RUN:
            print(
                f"[dry-run] would comment: {json.dumps(payload)}",
                file=sys.stderr,
            )
            raise TicketSourceRefused("notion_dry_run")
        response = self._transport("POST", "/comments", payload)
        comment_id = response.get("id")
        if not comment_id:
            raise TicketSourceRefused("notion_comment_missing_id")
        return comment_id


class WorkTrackerStub:
    """Refuse until a work-tracker transport and field mapping are defined."""

    @staticmethod
    def _refuse() -> None:
        raise TicketSourceRefused("work_tracker_not_implemented")

    def pull(self, lane: str) -> list[Ticket]:
        self._refuse()

    def get(self, ticket_id: str) -> Ticket:
        self._refuse()

    def file(self, ticket: Ticket) -> str:
        self._refuse()

    def create_subtask(self, anchor_id: str, title: str, body_md: str) -> Subtask:
        self._refuse()

    def comment(self, ticket_id: str, text: str) -> str:
        self._refuse()


def get_ticket_source(
    env: Mapping[str, str] = os.environ,
    *,
    notion_tasks: NotionTasksLoader | None,
) -> TicketSource | None:
    selected = env.get("TICKET_SOURCE")
    if selected is None or not selected.strip():
        return None
    if selected == "notion":
        return NotionTicketSource(notion_tasks=notion_tasks)
    if selected == "work-stub":
        return WorkTrackerStub()
    raise TicketSourceRefused(f"unknown_ticket_source:{selected}")


# --- Write-back policy -------------------------------------------------------
# One setting per tracker source, ``trackers.<source>.writeback``, kept in the
# toolkit-wide override file ``trackers.json``:
#     {"<source>": {"writeback": "auto" | "ask" | "off"}}
# ``auto`` lets a write to that tracker proceed, ``ask`` holds each write until
# it is named, ``off`` refuses. An absent file, source or key is ``off``.

WRITEBACK_VALUES = ("auto", "ask", "off")
DEFAULT_WRITEBACK = "off"
TRACKERS_OVERRIDE_NAME = "trackers"


class WritebackPolicyError(ValueError):
    """The write-back setting could not be read as a valid policy."""


def _overrides_module():
    spec = importlib.util.spec_from_file_location(
        "ticket_source_overrides", os.path.join(os.path.dirname(os.path.abspath(__file__)), "overrides.py")
    )
    if spec is None or spec.loader is None:
        raise WritebackPolicyError("cannot load the overrides reader")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def writeback_policy_file() -> str:
    """Return the path of the file that holds the write-back settings."""
    return str(_overrides_module().toolkit_override_path(TRACKERS_OVERRIDE_NAME))


def writeback_policy(source: str) -> str:
    """Return ``auto``, ``ask`` or ``off`` for one tracker source.

    The file is read on every call, so a change takes effect on the next
    apply. An unreadable file, a non-object entry or an unknown value raises
    :class:`WritebackPolicyError` naming the key and the file.
    """
    key = f"trackers.{source}.writeback"
    overrides = _overrides_module()
    path = overrides.toolkit_override_path(TRACKERS_OVERRIDE_NAME)
    try:
        document = overrides.load_toolkit_overrides(TRACKERS_OVERRIDE_NAME, {})
    except overrides.OverrideError as exc:
        raise WritebackPolicyError(f"{key}: {exc}") from exc
    entry = document.get(source)
    if entry is None:
        return DEFAULT_WRITEBACK
    if not isinstance(entry, dict):
        raise WritebackPolicyError(
            f"{key} in {path} must sit in a JSON object, got {type(entry).__name__}"
        )
    value = entry.get("writeback", DEFAULT_WRITEBACK)
    if value not in WRITEBACK_VALUES:
        raise WritebackPolicyError(
            f"{key} in {path} must be one of {', '.join(WRITEBACK_VALUES)}, got {value!r}"
        )
    return value

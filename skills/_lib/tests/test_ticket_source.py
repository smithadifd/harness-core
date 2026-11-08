import importlib.util
import sys
import types
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).resolve().parents[1] / "ticket_source.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("ticket_source_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


ticket_source = _load_module()


def test_notion_source_requires_a_helper_path_with_a_named_refusal():
    with pytest.raises(ticket_source.TicketSourceRefused) as raised:
        ticket_source.NotionTicketSource(notion_tasks=None)

    assert raised.value.reason == "notion_tasks_path_unset"
    with pytest.raises(TypeError, match="notion_tasks"):
        ticket_source.NotionTicketSource()


def test_flag_selection_requires_a_helper_path_with_a_named_refusal():
    with pytest.raises(ticket_source.TicketSourceRefused) as raised:
        ticket_source.get_ticket_source(
            {"TICKET_SOURCE": "notion"}, notion_tasks=None
        )

    assert raised.value.reason == "notion_tasks_path_unset"


def _notion_helpers(response, *, dry_run=False):
    calls = []

    def transport(method, path, body):
        calls.append((method, path, body))
        return response

    helpers = types.SimpleNamespace(
        api=transport,
        CFG={"tasks_database_id": "db-under-test"},
        DRY_RUN=dry_run,
        chunk=lambda text, n=1900: [text[i:i + n] for i in range(0, len(text), n)] or [""],
    )
    return ticket_source.NotionTicketSource(notion_tasks=lambda: helpers), calls


def test_create_subtask_posts_one_child_page_under_the_anchor():
    source, calls = _notion_helpers({"id": "sub-1", "url": "https://www.notion.so/sub1"})

    subtask = source.create_subtask("anchor-page", "Child task", "child body")

    assert subtask == ticket_source.Subtask(id="sub-1", url="https://www.notion.so/sub1")
    assert calls == [("POST", "/pages", {
        "parent": {"page_id": "anchor-page"},
        "properties": {"title": {"title": [{"type": "text", "text": {"content": "Child task"}}]}},
        "children": [{"object": "block", "type": "paragraph", "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": "child body"}}]}}],
    })]


def test_create_subtask_refuses_a_dry_run_and_a_reply_without_an_id():
    source, calls = _notion_helpers({"id": "sub-1"}, dry_run=True)
    with pytest.raises(ticket_source.TicketSourceRefused) as raised:
        source.create_subtask("anchor-page", "Child task", "child body")
    assert raised.value.reason == "notion_dry_run"
    assert calls == []

    source, calls = _notion_helpers({})
    with pytest.raises(ticket_source.TicketSourceRefused) as raised:
        source.create_subtask("anchor-page", "Child task", "child body")
    assert raised.value.reason == "notion_create_missing_id"
    assert len(calls) == 1


def test_comment_posts_one_comment_on_the_page_and_creates_nothing():
    source, calls = _notion_helpers({"id": "comment-1"})

    assert source.comment("anchor-page", "Split step 3?") == "comment-1"
    assert calls == [("POST", "/comments", {
        "parent": {"page_id": "anchor-page"},
        "rich_text": [{"type": "text", "text": {"content": "Split step 3?"}}],
    })]


@pytest.mark.parametrize("text, response, dry_run, reason, sent", [
    ("  \n", {"id": "comment-1"}, False, "comment_text_empty", 0),
    ("Split step 3?", {"id": "comment-1"}, True, "notion_dry_run", 0),
    ("Split step 3?", {}, False, "notion_comment_missing_id", 1),
])
def test_comment_refuses_by_name(text, response, dry_run, reason, sent):
    source, calls = _notion_helpers(response, dry_run=dry_run)
    with pytest.raises(ticket_source.TicketSourceRefused) as raised:
        source.comment("anchor-page", text)
    assert raised.value.reason == reason
    assert len(calls) == sent


def test_the_work_stub_refuses_subtasks_and_comments():
    stub = ticket_source.WorkTrackerStub()
    for call in (lambda: stub.create_subtask("anchor", "title", "body"),
                 lambda: stub.comment("anchor", "text")):
        with pytest.raises(ticket_source.TicketSourceRefused) as raised:
            call()
        assert raised.value.reason == "work_tracker_not_implemented"

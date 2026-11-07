import importlib.util
import sys
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

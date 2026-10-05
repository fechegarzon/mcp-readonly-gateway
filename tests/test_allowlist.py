import copy

import pytest

from mcp_readonly_gateway import Denied

from .conftest import BASE_POLICY


def test_tool_missing_from_allowlist_is_denied_for_everyone(make_gateway, read_audit):
    tools = copy.deepcopy(BASE_POLICY["tools"])
    del tools["search_messages"]
    callers = {"analyst": {"tools": ["list_messages", "read_message"]}}
    gateway = make_gateway("analyst", {"tools": tools, "callers": callers})

    with pytest.raises(Denied) as exc:
        gateway.call("search_messages", {"query": "rate"})

    assert exc.value.code == "tool_not_allowed"
    assert "list_messages" in str(exc.value)  # tells the model what it *can* use
    assert read_audit()[-1]["decision"] == "deny"


def test_unknown_tool_name_is_denied(make_gateway):
    with pytest.raises(Denied) as exc:
        make_gateway().call("send_email", {"to": "a@example.com"})
    assert exc.value.code == "tool_not_allowed"


def test_visible_tools_are_allowlist_intersected_with_scope(make_gateway):
    assert make_gateway("analyst").visible_tools() == [
        "gateway_info",
        "list_messages",
        "search_messages",
        "read_message",
    ]
    assert "create_draft" in make_gateway("ops-lead").visible_tools()


def test_info_tool_is_always_available(make_gateway):
    info = make_gateway("analyst").call("gateway_info")
    assert info["caller"] == "analyst"
    assert info["readable_labels"] == ["agent-inbox"]
    assert info["tools"]["list_messages"]["session_budget_left"] == 5

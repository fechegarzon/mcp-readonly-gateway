import pytest

from mcp_readonly_gateway import Denied

DRAFT = {"to": "a@example.com", "subject": "s", "body": "b", "confirmed": True}


def test_analyst_cannot_draft_even_with_confirmation(make_gateway, read_audit):
    gateway = make_gateway("analyst")
    with pytest.raises(Denied) as exc:
        gateway.call("create_draft", DRAFT)
    assert exc.value.code == "out_of_scope"
    assert "do not retry" in exc.value.message
    assert gateway.provider.drafts == []
    assert read_audit()[-1]["caller"] == "analyst"


def test_ops_lead_can_draft(make_gateway):
    assert make_gateway("ops-lead").call("create_draft", DRAFT)["status"] == "draft"


def test_unknown_caller_gets_nothing(make_gateway):
    gateway = make_gateway("intern")
    assert gateway.visible_tools() == ["gateway_info"]
    for tool, args in [("gateway_info", {}), ("list_messages", {})]:
        with pytest.raises(Denied) as exc:
            gateway.call(tool, args)
        assert exc.value.code == "unknown_caller"


def test_scopes_do_not_share_budgets(make_gateway):
    # Each caller runs in its own server process, so each has its own session budget.
    a, b = make_gateway("analyst"), make_gateway("ops-lead")
    a.call("list_messages", {"limit": 3})
    a.call("list_messages", {"limit": 3})
    assert b.call("list_messages", {"limit": 3})["count"] == 3

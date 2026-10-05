import pytest

from mcp_readonly_gateway import Denied


def test_listing_a_private_label_is_denied(make_gateway):
    with pytest.raises(Denied) as exc:
        make_gateway().call("list_messages", {"label": "hr-private"})
    assert exc.value.code == "label_not_allowed"
    assert "'agent-inbox'" in exc.value.message


def test_default_label_is_the_readable_one(make_gateway):
    result = make_gateway().call("list_messages", {"limit": 3})
    assert result["label"] == "agent-inbox"
    assert all("agent-inbox" in m["labels"] for m in result["messages"])


def test_hidden_and_missing_messages_look_the_same(make_gateway):
    gateway = make_gateway()
    with pytest.raises(Denied) as hidden:
        gateway.call("read_message", {"message_id": "m-3001"})  # exists, hr-private
    with pytest.raises(Denied) as missing:
        gateway.call("read_message", {"message_id": "m-9999"})  # does not exist
    assert hidden.value.code == missing.value.code == "not_found_or_hidden"
    assert hidden.value.message.replace("m-3001", "") == missing.value.message.replace("m-9999", "")


def test_search_never_returns_messages_outside_the_label(make_gateway):
    # "rate lock" appears in a private HR message and in one agent-inbox message.
    result = make_gateway().call("search_messages", {"query": "rate lock"})
    assert [m["id"] for m in result["messages"]] == ["m-1003"]


def test_label_gate_holds_even_if_the_provider_ignores_labels(make_gateway):
    gateway = make_gateway()
    everything = list(gateway.provider._messages)
    gateway.provider.search_messages = lambda query, labels, limit: everything[:limit]
    result = gateway.call("search_messages", {"query": "anything", "limit": 2})
    assert all("agent-inbox" in m["labels"] for m in result["messages"])

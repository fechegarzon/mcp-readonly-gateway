import pytest

from mcp_readonly_gateway import Denied


def test_limit_is_clamped_to_max_results(make_gateway):
    result = make_gateway().call("list_messages", {"limit": 50})
    assert result["count"] == 3
    assert "capped at 3" in result["note"]


def test_session_budget_runs_out_then_denies(make_gateway, read_audit):
    gateway = make_gateway()
    first = gateway.call("list_messages", {"limit": 3})
    second = gateway.call("list_messages", {"limit": 3})  # only 2 left in the budget of 5
    assert (first["count"], second["count"]) == (3, 2)

    with pytest.raises(Denied) as exc:
        gateway.call("list_messages", {"limit": 1})
    assert exc.value.code == "budget_exhausted"
    assert "budget of 5" in exc.value.message
    assert gateway.call("gateway_info")["tools"]["list_messages"]["session_budget_left"] == 0
    assert [r["items"] for r in read_audit() if r["decision"] == "allow"][:2] == [3, 2]


def test_budget_is_per_tool(make_gateway):
    gateway = make_gateway()
    gateway.call("list_messages", {"limit": 3})
    gateway.call("list_messages", {"limit": 3})
    # list_messages is spent, search is not
    assert gateway.call("search_messages", {"query": "application"})["count"] == 2


def test_read_message_body_is_truncated(make_gateway):
    result = make_gateway().call("read_message", {"message_id": "m-1001"})
    assert len(result["body"]) == 60
    assert result["body_truncated"] is True


def test_read_budget_counts_reads(make_gateway):
    gateway = make_gateway()
    for _ in range(3):
        gateway.call("read_message", {"message_id": "m-1002"})
    with pytest.raises(Denied) as exc:
        gateway.call("read_message", {"message_id": "m-1002"})
    assert exc.value.code == "budget_exhausted"


@pytest.mark.parametrize(
    ("tool", "args", "code"),
    [
        ("list_messages", {"limit": 0}, "bad_arguments"),
        ("search_messages", {"query": "   "}, "bad_arguments"),
        ("search_messages", {"query": "x" * 201}, "bad_arguments"),
        ("read_message", {"id": "m-1001"}, "bad_arguments"),
    ],
)
def test_bad_arguments_get_a_clear_denial(make_gateway, tool, args, code):
    with pytest.raises(Denied) as exc:
        make_gateway().call(tool, args)
    assert exc.value.code == code

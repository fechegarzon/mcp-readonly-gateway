import pytest

from mcp_readonly_gateway import Denied

DRAFT = {"to": "dana.rivera@example.com", "subject": "Form needed", "body": "Please sign."}


def test_draft_without_confirmation_is_denied_and_nothing_is_saved(make_gateway):
    gateway = make_gateway("ops-lead")
    with pytest.raises(Denied) as exc:
        gateway.call("create_draft", DRAFT)
    assert exc.value.code == "confirmation_required"
    assert "confirmed=true" in exc.value.message
    assert gateway.provider.drafts == []


@pytest.mark.parametrize("value", [False, "true", 1, None])
def test_only_a_real_true_counts_as_confirmation(make_gateway, value):
    with pytest.raises(Denied):
        make_gateway("ops-lead").call("create_draft", {**DRAFT, "confirmed": value})


def test_confirmed_draft_is_saved_not_sent(make_gateway):
    gateway = make_gateway("ops-lead")
    result = gateway.call("create_draft", {**DRAFT, "confirmed": True})
    assert result["status"] == "draft"
    assert "Nothing was sent" in result["note"]
    assert [d.to for d in gateway.provider.drafts] == ["dana.rivera@example.com"]


def test_writes_disabled_beats_confirmation(make_gateway):
    gateway = make_gateway("ops-lead", {"writes": {"enabled": False}})
    assert "create_draft" not in gateway.visible_tools()
    with pytest.raises(Denied) as exc:
        gateway.call("create_draft", {**DRAFT, "confirmed": True})
    assert exc.value.code == "writes_disabled"


def test_recipient_outside_allowed_domains_is_denied(make_gateway):
    gateway = make_gateway("ops-lead")
    with pytest.raises(Denied) as exc:
        gateway.call("create_draft", {**DRAFT, "to": "someone@example.net", "confirmed": True})
    assert exc.value.code == "recipient_not_allowed"
    assert gateway.provider.drafts == []


def test_multiple_recipients_are_rejected(make_gateway):
    with pytest.raises(Denied) as exc:
        make_gateway("ops-lead").call(
            "create_draft", {**DRAFT, "to": "a@example.com, b@example.com", "confirmed": True}
        )
    assert exc.value.code == "bad_arguments"


def test_draft_budget(make_gateway):
    gateway = make_gateway("ops-lead")
    for _ in range(2):
        gateway.call("create_draft", {**DRAFT, "confirmed": True})
    with pytest.raises(Denied) as exc:
        gateway.call("create_draft", {**DRAFT, "confirmed": True})
    assert exc.value.code == "budget_exhausted"

import json

import pytest

from mcp_readonly_gateway import AuditLog, Denied
from mcp_readonly_gateway.audit import args_hash, redact_text


def test_every_call_writes_one_line(make_gateway, read_audit):
    gateway = make_gateway()
    gateway.call("list_messages", {"limit": 2})
    with pytest.raises(Denied):
        gateway.call("list_messages", {"label": "hr-private"})

    allow, deny = read_audit()
    assert allow["decision"] == "allow"
    assert allow["items"] == 2
    assert deny["decision"] == "deny"
    assert deny["code"] == "label_not_allowed"
    for record in (allow, deny):
        assert record["caller"] == "analyst"
        assert record["tool"] == "list_messages"
        assert record["args_hash"].startswith("sha256:")
        assert record["latency_ms"] >= 0
        assert record["ts"].endswith("Z") or "+00:00" in record["ts"]


def test_pii_in_arguments_is_redacted(make_gateway, audit_path):
    gateway = make_gateway("ops-lead")
    gateway.call(
        "create_draft",
        {
            "to": "dana.rivera@example.com",
            "subject": "Call me at +1 (555) 010-0142",
            "body": "Or text 300 555 0142. Closing date 2026-10-06.",
            "confirmed": True,
        },
    )
    raw = audit_path.read_text()
    assert "dana.rivera@example.com" not in raw
    assert "010-0142" not in raw
    assert "300 555 0142" not in raw

    record = json.loads(raw.splitlines()[-1])
    assert record["args"]["to"] == "[email]"
    assert record["args"]["subject"] == "Call me at [phone]"
    # Dates are not phone numbers.
    assert record["args"]["body"] == "Or text [phone]. Closing date 2026-10-06."


def test_results_are_never_logged(make_gateway, audit_path):
    make_gateway().call("read_message", {"message_id": "m-1001"})
    assert "pay stubs" not in audit_path.read_text().lower()


def test_hash_matches_repeated_calls_without_storing_values(make_gateway, read_audit):
    gateway = make_gateway()
    gateway.call("search_messages", {"query": "appraisal"})
    gateway.call("search_messages", {"query": "appraisal"})
    gateway.call("search_messages", {"query": "title"})
    first, second, third = (r["args_hash"] for r in read_audit())
    assert first == second != third


def test_keyed_hash(tmp_path):
    assert args_hash({"a": 1}, key=b"k").startswith("hmac-sha256:")
    assert args_hash({"a": 1}, key=b"k") != args_hash({"a": 1}, key=b"other")


def test_log_is_append_only_across_instances(tmp_path):
    path = tmp_path / "audit.jsonl"
    for _ in range(2):
        AuditLog(path).record(caller="c", tool="t", args={}, decision="allow", latency_ms=1.0)
    assert len(path.read_text().splitlines()) == 2


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mail a.b+c@sub.example.co", "mail [email]"),
        ("+57 300 555 0142", "[phone]"),
        ("(555) 010-0177 ext", "[phone] ext"),
        ("invoice 7731", "invoice 7731"),
        ("2026-10-04", "2026-10-04"),
        ("A-2231", "A-2231"),
    ],
)
def test_redact_text(text, expected):
    assert redact_text(text) == expected


def test_long_strings_are_cut():
    assert redact_text("x" * 500).endswith("...[+300 chars]")


def test_provider_failures_are_logged_as_errors(make_gateway, read_audit):
    gateway = make_gateway()

    def broken(label, limit):
        raise ConnectionError("upstream timed out for dana.rivera@example.com")

    gateway.provider.list_messages = broken
    with pytest.raises(ConnectionError):
        gateway.call("list_messages", {})
    record = read_audit()[-1]
    assert record["decision"] == "error"
    assert record["reason"] == "ConnectionError"  # type only; the message may hold PII

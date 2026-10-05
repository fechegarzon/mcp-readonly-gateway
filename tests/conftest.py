from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from mcp_readonly_gateway import AuditLog, Gateway, Policy
from mcp_readonly_gateway.providers import FixtureMailbox

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"

BASE_POLICY: dict[str, Any] = {
    "version": 1,
    "provider": {"kind": "fixture", "options": {"path": str(EXAMPLES / "mailbox.json")}},
    "labels": {"readable": ["agent-inbox"]},
    "writes": {"enabled": True},
    "tools": {
        "list_messages": {"max_results": 3, "max_items": 5},
        "search_messages": {"max_results": 2, "max_items": 10},
        "read_message": {"max_items": 3, "max_body_chars": 60},
        "create_draft": {"max_items": 2, "allowed_recipient_domains": ["example.com"]},
    },
    "callers": {
        "analyst": {"tools": ["list_messages", "search_messages", "read_message"]},
        "ops-lead": {"tools": ["list_messages", "search_messages", "read_message", "create_draft"]},
    },
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
def audit_path(tmp_path: Path) -> Path:
    return tmp_path / "audit" / "gateway.jsonl"


@pytest.fixture
def make_gateway(audit_path: Path) -> Callable[..., Gateway]:
    """Build a gateway for `caller`. `changes` is applied on top of BASE_POLICY."""

    def _make(caller: str = "analyst", changes: dict[str, Any] | None = None) -> Gateway:
        data = copy.deepcopy(BASE_POLICY)
        for key, value in (changes or {}).items():
            data[key] = value
        data["audit"] = {"path": str(audit_path)}
        policy = Policy.from_dict(data, base_dir=EXAMPLES)
        provider = FixtureMailbox.from_file(EXAMPLES / "mailbox.json")
        return Gateway(policy, provider, AuditLog(audit_path), caller)

    return _make


@pytest.fixture
def read_audit(audit_path: Path) -> Callable[[], list[dict[str, Any]]]:
    def _read() -> list[dict[str, Any]]:
        if not audit_path.exists():
            return []
        return [json.loads(line) for line in audit_path.read_text().splitlines()]

    return _read

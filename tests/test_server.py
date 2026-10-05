"""End to end through the MCP protocol, in-process and over real stdio."""

import json
import os
import sys

import pytest
from mcp import Client, StdioServerParameters

from mcp_readonly_gateway import build_server

from .conftest import EXAMPLES

pytestmark = pytest.mark.anyio


async def test_only_scoped_tools_are_listed(make_gateway):
    async with Client(build_server(make_gateway("analyst"))) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"gateway_info", "list_messages", "search_messages", "read_message"}
    assert tools["read_message"].annotations.read_only_hint is True
    assert "at most 3 per call" in tools["list_messages"].description


async def test_denial_reaches_the_model_as_a_tool_error(make_gateway):
    async with Client(build_server(make_gateway("analyst"))) as client:
        result = await client.call_tool("list_messages", {"label": "hr-private"})
    assert result.is_error is True
    assert "DENIED [label_not_allowed]" in result.content[0].text


async def test_unregistered_tool_is_unknown_to_the_client(make_gateway, read_audit):
    async with Client(build_server(make_gateway("analyst"))) as client:
        result = await client.call_tool("create_draft", {"to": "a@example.com"})
    assert result.is_error is True
    # Never reached the gateway: the tool doesn't exist for this caller.
    assert read_audit() == []


async def test_stdio_round_trip(tmp_path):
    policy = tmp_path / "policy.yaml"
    policy.write_text(
        (EXAMPLES / "policy.yaml")
        .read_text()
        .replace("path: mailbox.json", f"path: {EXAMPLES / 'mailbox.json'}")
        .replace("path: ../.audit/gateway.jsonl", "path: audit.jsonl")
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_readonly_gateway", "--config", str(policy)],
        env={**os.environ, "GATEWAY_CALLER": "ops-lead"},
    )
    async with Client(params) as client:
        listed = await client.call_tool("list_messages", {"limit": 2})
        draft = await client.call_tool(
            "create_draft", {"to": "a@example.com", "subject": "s", "body": "b"}
        )

    assert listed.structured_content["count"] == 2
    assert draft.is_error is True
    assert "confirmation_required" in draft.content[0].text

    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text().splitlines()]
    assert [r["decision"] for r in records] == ["allow", "deny"]

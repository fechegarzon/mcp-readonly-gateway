"""Walk through the gateway as an agent would see it. No API keys, no network.

    uv run python examples/demo.py

Each step is a real MCP tool call against the server, connected in-process.
At the end it prints the audit records this run wrote.
"""

from __future__ import annotations

import json
from pathlib import Path

import anyio
from mcp import Client

from mcp_readonly_gateway import Gateway, build_server, load_policy

HERE = Path(__file__).resolve().parent


async def step(client: Client, caller: str, tool: str, args: dict) -> None:
    result = await client.call_tool(tool, args)
    print(f"\n[{caller}] {tool}({json.dumps(args)})")
    if result.is_error:
        print(f"  -> {result.content[0].text}")
        return
    data = result.structured_content or {}
    if "messages" in data:
        for m in data["messages"]:
            print(f"  -> {m['id']}  {m['subject']}")
        if "note" in data:
            print(f"  -> note: {data['note']}")
    else:
        print("  -> " + json.dumps(data, indent=2).replace("\n", "\n     "))


async def main() -> None:
    policy = load_policy(HERE / "policy.yaml")
    audit_lines_before = _count_lines(policy.audit_path)

    analyst = Gateway.from_policy(policy, "analyst")
    async with Client(build_server(analyst, log_level="WARNING")) as client:
        await step(client, "analyst", "list_messages", {"limit": 3})
        await step(client, "analyst", "search_messages", {"query": "rate lock"})
        await step(client, "analyst", "list_messages", {"label": "hr-private"})
        await step(client, "analyst", "read_message", {"message_id": "m-3001"})
        await step(client, "analyst", "create_draft", {"to": "x@example.com", "subject": "s"})

    lead = Gateway.from_policy(policy, "ops-lead")
    draft = {
        "to": "dana.rivera@example.com",
        "subject": "Employment verification form",
        "body": "Hi Dana, we still need the signed form. Call +1 (555) 010-0100 with questions.",
    }
    async with Client(build_server(lead, log_level="WARNING")) as client:
        await step(client, "ops-lead", "create_draft", draft)
        await step(client, "ops-lead", "create_draft", {**draft, "confirmed": True})

    print(f"\nAudit records written to {policy.audit_path}:")
    lines = policy.audit_path.read_text().splitlines()[audit_lines_before:]
    for line in lines:
        r = json.loads(line)
        print(
            f"  {r['caller']:<9} {r['tool']:<16} {r['decision']:<5} "
            f"{r.get('code', ''):<22} args={json.dumps(r['args'])}"
        )


def _count_lines(path: Path) -> int:
    return len(path.read_text().splitlines()) if path.exists() else 0


if __name__ == "__main__":
    anyio.run(main)

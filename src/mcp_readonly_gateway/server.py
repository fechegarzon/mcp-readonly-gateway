"""MCP wiring. Thin on purpose: all decisions live in `Gateway`.

Only the tools this caller may use are registered, so the model never sees
the rest. The gateway still checks every call (defense in depth), and that
check is what the tests exercise.

No `from __future__ import annotations` here: the SDK reads the tool
signatures at registration time, and some annotations use local values.
"""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from ._version import __version__
from .gateway import DEFAULT_LIMIT, Denied, Gateway
from .policy import INFO_TOOL

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

READ_ONLY = ToolAnnotations(read_only_hint=True, open_world_hint=False)
DRAFT_ONLY = ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
)

INSTRUCTIONS = """\
This server gives read access to a curated slice of a mailbox, plus drafts.
Call gateway_info first to see your tools, labels, and remaining budget.
Denied calls start with DENIED [code] and say what to do next; follow that
instead of retrying the same call. Nothing here can send, delete, or move mail.
"""


def build_server(gateway: Gateway, log_level: LogLevel = "INFO") -> MCPServer:
    server = MCPServer(
        name="mcp-readonly-gateway",
        instructions=INSTRUCTIONS,
        version=__version__,
        log_level=log_level,
    )
    policy = gateway.policy
    visible = set(gateway.visible_tools())
    labels = ", ".join(policy.readable_labels)

    def run(tool: str, **args: Any) -> dict[str, Any]:
        try:
            return gateway.call(tool, args)
        except Denied as denied:
            raise ToolError(str(denied)) from denied

    def info() -> dict[str, Any]:
        return run(INFO_TOOL)

    server.add_tool(
        info,
        name=INFO_TOOL,
        description="Show who you are, which tools and labels you can use, and your "
        "remaining budget. Free to call.",
        annotations=READ_ONLY,
    )

    if "list_messages" in visible:

        def list_messages(
            label: Annotated[str | None, Field(description=f"One of: {labels}.")] = None,
            limit: Annotated[int, Field(ge=1, description="How many to return.")] = DEFAULT_LIMIT,
        ) -> dict[str, Any]:
            return run("list_messages", label=label, limit=limit)

        server.add_tool(
            list_messages,
            description=f"List recent messages (newest first) under a readable label ({labels}). "
            f"Returns metadata and a short snippet.{_limit_hint(gateway, 'list_messages')}",
            annotations=READ_ONLY,
        )

    if "search_messages" in visible:

        def search_messages(
            query: Annotated[str, Field(description="A few words; all must match.")],
            limit: Annotated[int, Field(ge=1, description="How many to return.")] = DEFAULT_LIMIT,
        ) -> dict[str, Any]:
            return run("search_messages", query=query, limit=limit)

        server.add_tool(
            search_messages,
            description=f"Search sender, subject, and body. Only messages under {labels} are "
            f"searched.{_limit_hint(gateway, 'search_messages')}",
            annotations=READ_ONLY,
        )

    if "read_message" in visible:

        def read_message(
            message_id: Annotated[str, Field(description="An id from list or search results.")],
        ) -> dict[str, Any]:
            return run("read_message", message_id=message_id)

        server.add_tool(
            read_message,
            description=f"Read one message in full.{_limit_hint(gateway, 'read_message')}",
            annotations=READ_ONLY,
        )

    if "create_draft" in visible:

        def create_draft(
            to: Annotated[str, Field(description="Exactly one recipient email address.")],
            subject: str,
            body: str,
            confirmed: Annotated[
                bool,
                Field(description="Set to true only after the user approved this exact draft."),
            ] = False,
        ) -> dict[str, Any]:
            return run("create_draft", to=to, subject=subject, body=body, confirmed=confirmed)

        server.add_tool(
            create_draft,
            description="Save an email as a draft for a person to review and send. It never "
            "sends. Show the draft to the user first; call with confirmed=true only after they "
            f"approve it.{_limit_hint(gateway, 'create_draft')}",
            annotations=DRAFT_ONLY,
        )

    return server


def _limit_hint(gateway: Gateway, tool: str) -> str:
    limits = gateway.policy.tools[tool]
    parts = []
    if limits.max_results is not None:
        parts.append(f"at most {limits.max_results} per call")
    if limits.max_items is not None:
        parts.append(f"{limits.max_items} per session")
    if limits.max_body_chars is not None:
        parts.append(f"bodies cut at {limits.max_body_chars} characters")
    if limits.allowed_recipient_domains is not None:
        parts.append(f"recipients @{', @'.join(limits.allowed_recipient_domains)} only")
    return f" Limits: {'; '.join(parts)}." if parts else ""

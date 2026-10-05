"""The gateway: every tool call goes through `Gateway.call`.

Checks run in a fixed order, cheapest and broadest first:

1. Is the caller known?
2. Is the tool on the allowlist?
3. Is the tool in this caller's scope?
4. Write tools only: are writes enabled, and did the caller pass confirmed=true?
5. Tool-specific checks: label gate, recipient domains, per-call and per-session limits.

Every call, allowed or denied, writes one audit record. Denials raise
`Denied` with a message written for the model: what was blocked, why, and
what to do instead.
"""

from __future__ import annotations

import inspect
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from .audit import AuditLog
from .policy import INFO_TOOL, TOOL_CATALOG, Policy
from .providers import MailProvider, Message, load_provider

DEFAULT_LIMIT = 10
MAX_QUERY_CHARS = 200


class Denied(Exception):
    """The gateway refused a call. `str(exc)` is safe to show to the model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message

    def __str__(self) -> str:
        return f"DENIED [{self.code}] {self.message}"


class Gateway:
    def __init__(
        self,
        policy: Policy,
        provider: MailProvider,
        audit: AuditLog,
        caller: str,
        clock: Callable[[], float] = time.perf_counter,
    ) -> None:
        self.policy = policy
        self.provider = provider
        self.audit = audit
        self.caller = caller
        self._clock = clock
        self._used: Counter[str] = Counter()
        self._handlers: dict[str, Callable[..., tuple[dict[str, Any], int]]] = {
            INFO_TOOL: self._gateway_info,
            "list_messages": self._list_messages,
            "search_messages": self._search_messages,
            "read_message": self._read_message,
            "create_draft": self._create_draft,
        }

    @classmethod
    def from_policy(cls, policy: Policy, caller: str) -> Gateway:
        """Build the provider and audit log the policy describes."""
        provider = load_provider(policy.provider_kind, policy.provider_options, policy.base_dir)
        return cls(policy, provider, AuditLog(policy.audit_path), caller)

    # ------------------------------------------------------------------ entry point

    def visible_tools(self) -> list[str]:
        """Tools to register with the MCP server for this caller."""
        return [INFO_TOOL, *self.policy.tools_for(self.caller)]

    def call(self, tool: str, args: dict[str, Any] | None = None) -> dict[str, Any]:
        args = dict(args or {})
        start = self._clock()
        try:
            self._authorize(tool, args)
            handler = self._handlers[tool]
            try:
                inspect.signature(handler).bind(**args)
            except TypeError as exc:
                # Wrong or missing argument names. The MCP layer validates the
                # schema first, so this mostly protects direct Python callers.
                raise Denied("bad_arguments", f"{tool}: {exc}.") from exc
            result, items = handler(**args)
        except Denied as denied:
            self.audit.record(
                caller=self.caller,
                tool=tool,
                args=args,
                decision="deny",
                code=denied.code,
                reason=denied.message,
                latency_ms=self._elapsed_ms(start),
            )
            raise
        except Exception as exc:
            self.audit.record(
                caller=self.caller,
                tool=tool,
                args=args,
                decision="error",
                reason=type(exc).__name__,
                latency_ms=self._elapsed_ms(start),
            )
            raise
        self.audit.record(
            caller=self.caller,
            tool=tool,
            args=args,
            decision="allow",
            items=items,
            latency_ms=self._elapsed_ms(start),
        )
        return result

    # ------------------------------------------------------------------ checks

    def _authorize(self, tool: str, args: dict[str, Any]) -> None:
        scope = self.policy.callers.get(self.caller)
        if scope is None:
            raise Denied(
                "unknown_caller",
                f"Caller {self.caller!r} has no access. Ask an admin to add it under "
                "`callers` in the gateway policy. Do not retry.",
            )
        if tool == INFO_TOOL:
            return
        if tool not in TOOL_CATALOG or tool not in self.policy.tools:
            raise Denied(
                "tool_not_allowed",
                f"{tool!r} is not enabled on this gateway. Available to you: "
                f"{', '.join(self.policy.tools_for(self.caller)) or 'none'}.",
            )
        if tool not in scope.tools:
            raise Denied(
                "out_of_scope",
                f"{tool!r} is not in your scope (caller {self.caller!r}). You can use: "
                f"{', '.join(self.policy.tools_for(self.caller)) or 'none'}. "
                "If the user needs this, tell them who to ask; do not retry.",
            )
        if TOOL_CATALOG[tool] == "write":
            if not self.policy.writes_enabled:
                raise Denied(
                    "writes_disabled",
                    "Write tools are turned off on this gateway. Give the user the text "
                    "so they can act on it themselves.",
                )
            if args.get("confirmed") is not True:
                raise Denied(
                    "confirmation_required",
                    f"{tool} needs confirmed=true. Show the user exactly what will be "
                    "saved, wait for their explicit approval, then call again with "
                    "confirmed=true. Never set it on your own.",
                )

    def _take(self, tool: str, requested: int) -> int:
        """Clamp a request to the per-call and per-session limits. Returns the allowance."""
        if requested < 1:
            raise Denied("bad_arguments", "limit must be at least 1.")
        limits = self.policy.tools[tool]
        allowed = requested
        if limits.max_results is not None:
            allowed = min(allowed, limits.max_results)
        if limits.max_items is not None:
            remaining = limits.max_items - self._used[tool]
            if remaining <= 0:
                raise Denied(
                    "budget_exhausted",
                    f"{tool} has used its budget of {limits.max_items} items for this "
                    "session. Work with what you already have, or ask the user to "
                    "narrow the request and start a new session.",
                )
            allowed = min(allowed, remaining)
        return allowed

    def _spend(self, tool: str, items: int) -> None:
        self._used[tool] += items

    def _is_readable(self, message: Message) -> bool:
        return bool(message.labels & set(self.policy.readable_labels))

    def _elapsed_ms(self, start: float) -> float:
        return (self._clock() - start) * 1000

    # ------------------------------------------------------------------ tools

    def _gateway_info(self) -> tuple[dict[str, Any], int]:
        tools = {}
        for name in self.policy.tools_for(self.caller):
            limits = self.policy.tools[name]
            entry: dict[str, Any] = {"kind": TOOL_CATALOG[name]}
            if limits.max_results is not None:
                entry["max_results_per_call"] = limits.max_results
            if limits.max_items is not None:
                entry["session_budget"] = limits.max_items
                entry["session_budget_left"] = max(limits.max_items - self._used[name], 0)
            if limits.max_body_chars is not None:
                entry["max_body_chars"] = limits.max_body_chars
            if limits.allowed_recipient_domains is not None:
                entry["allowed_recipient_domains"] = list(limits.allowed_recipient_domains)
            tools[name] = entry
        info = {
            "caller": self.caller,
            "provider": self.provider.name,
            "readable_labels": list(self.policy.readable_labels),
            "writes": "drafts only, after user confirmation"
            if any(TOOL_CATALOG[name] == "write" for name in tools)
            else "none",
            "tools": tools,
        }
        return info, 0

    def _list_messages(
        self, label: str | None = None, limit: int = DEFAULT_LIMIT
    ) -> tuple[dict[str, Any], int]:
        readable = self.policy.readable_labels
        label = label or readable[0]
        if label not in readable:
            raise Denied(
                "label_not_allowed",
                f"You can only read messages labeled {_quoted(readable)}. Omit `label` "
                "or use one of those. Messages under other labels are off limits; do "
                "not try to reach them through search.",
            )
        allowance = self._take("list_messages", limit)
        messages = [
            m for m in self.provider.list_messages(label, allowance) if self._is_readable(m)
        ]
        self._spend("list_messages", len(messages))
        result = {
            "label": label,
            "count": len(messages),
            "messages": [m.summary() for m in messages],
        }
        if allowance < limit:
            result["note"] = f"Asked for {limit}, capped at {allowance} by policy."
        return result, len(messages)

    def _search_messages(
        self, query: str, limit: int = DEFAULT_LIMIT
    ) -> tuple[dict[str, Any], int]:
        query = query.strip()
        if not query:
            raise Denied("bad_arguments", "query is empty. Pass a few words to search for.")
        if len(query) > MAX_QUERY_CHARS:
            raise Denied(
                "bad_arguments",
                f"query is {len(query)} characters; the limit is {MAX_QUERY_CHARS}. "
                "Use a few specific words instead of pasting text.",
            )
        allowance = self._take("search_messages", limit)
        readable = self.policy.readable_labels
        # The provider is asked to filter by label, and we filter again here.
        # A bug in one provider should not be enough to leak a hidden message.
        messages = [
            m
            for m in self.provider.search_messages(query, readable, allowance)
            if self._is_readable(m)
        ]
        self._spend("search_messages", len(messages))
        result: dict[str, Any] = {
            "query": query,
            "searched_labels": list(readable),
            "count": len(messages),
            "messages": [m.summary() for m in messages],
        }
        if allowance < limit:
            result["note"] = f"Asked for {limit}, capped at {allowance} by policy."
        return result, len(messages)

    def _read_message(self, message_id: str) -> tuple[dict[str, Any], int]:
        self._take("read_message", 1)
        message = self.provider.get_message(message_id)
        if message is None or not self._is_readable(message):
            # Same answer for "missing" and "hidden", so the model can't probe
            # for the existence of messages it isn't allowed to see.
            raise Denied(
                "not_found_or_hidden",
                f"No readable message with id {message_id!r}. Only messages labeled "
                f"{_quoted(self.policy.readable_labels)} can be read. Use list_messages "
                "or search_messages to find valid ids.",
            )
        self._spend("read_message", 1)
        body = message.body
        max_chars = self.policy.tools["read_message"].max_body_chars
        truncated = max_chars is not None and len(body) > max_chars
        if truncated:
            body = body[:max_chars]
        result = {
            **message.summary(),
            "to": list(message.to),
            "body": body,
            "body_truncated": truncated,
        }
        result.pop("snippet")
        return result, 1

    def _create_draft(
        self, to: str, subject: str, body: str, confirmed: bool = False
    ) -> tuple[dict[str, Any], int]:
        domains = self.policy.tools["create_draft"].allowed_recipient_domains
        address = to.strip()
        if "@" not in address or "," in address or ";" in address:
            raise Denied("bad_arguments", "`to` must be exactly one email address.")
        domain = address.rsplit("@", 1)[1].lower()
        if domains is not None and domain not in domains:
            raise Denied(
                "recipient_not_allowed",
                f"Drafts can only be addressed to {_quoted(domains)}. Tell the user the "
                "draft could not be created for this recipient.",
            )
        self._take("create_draft", 1)
        draft = self.provider.create_draft(address, subject, body)
        self._spend("create_draft", 1)
        result = {
            "draft_id": draft.id,
            "status": draft.status,
            "to": draft.to,
            "subject": draft.subject,
            "note": "Saved as a draft. Nothing was sent. A person reviews and sends it.",
        }
        return result, 1


def _quoted(values: tuple[str, ...] | list[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)

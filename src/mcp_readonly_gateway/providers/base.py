"""The provider interface: the only thing the gateway knows about the backend.

A provider wraps one third-party API (a mailbox, a CRM, a ticketing system).
The interface is deliberately small. There is no `send`, `delete`, or
`update` here, so no provider can expose them through the gateway, no matter
what the underlying API supports.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self


@dataclass(frozen=True)
class Message:
    id: str
    thread_id: str
    sender: str
    to: tuple[str, ...]
    subject: str
    body: str
    labels: frozenset[str]
    received_at: str

    def summary(self, snippet_chars: int = 140) -> dict[str, Any]:
        """Metadata plus a short snippet. Used by list and search results."""
        snippet = " ".join(self.body.split())[:snippet_chars]
        return {
            "id": self.id,
            "thread_id": self.thread_id,
            "from": self.sender,
            "subject": self.subject,
            "snippet": snippet,
            "labels": sorted(self.labels),
            "received_at": self.received_at,
        }


@dataclass(frozen=True)
class Draft:
    id: str
    to: str
    subject: str
    body: str
    created_at: str
    status: str = field(default="draft")


class MailProvider(ABC):
    """Read-mostly access to a mailbox-like backend."""

    #: Short name shown in `gateway_info` and in logs.
    name: str = "provider"

    @classmethod
    def from_options(cls, options: dict[str, Any], base_dir: Path) -> Self:
        """Build the provider from the `provider.options` block of the policy file.

        `base_dir` is the directory of the policy file, so providers can resolve
        relative paths the same way no matter where the server was started.
        """
        return cls(**options)

    @abstractmethod
    def list_messages(self, label: str, limit: int) -> list[Message]:
        """Newest first. Only messages that carry `label`."""

    @abstractmethod
    def search_messages(self, query: str, labels: Sequence[str], limit: int) -> list[Message]:
        """Newest first. Only messages that carry at least one of `labels`."""

    @abstractmethod
    def get_message(self, message_id: str) -> Message | None:
        """Return the message, or None if it does not exist."""

    @abstractmethod
    def create_draft(self, to: str, subject: str, body: str) -> Draft:
        """Save a draft. Never sends anything."""

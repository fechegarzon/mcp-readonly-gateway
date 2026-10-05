"""An offline provider backed by a JSON file of synthetic messages.

It stands in for a real mailbox API so the tests and the demo run with no
network and no credentials. A real provider (Gmail, Outlook, a CRM) would
implement the same four methods against its SDK.
"""

from __future__ import annotations

import itertools
import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Self

from .base import Draft, MailProvider, Message


class FixtureMailbox(MailProvider):
    name = "fixture-mailbox"

    def __init__(self, messages: Iterable[Message]) -> None:
        # Newest first, like most mail APIs.
        self._messages = sorted(messages, key=lambda m: m.received_at, reverse=True)
        self._by_id = {m.id: m for m in self._messages}
        self._drafts: list[Draft] = []
        self._draft_ids = itertools.count(1)

    @classmethod
    def from_options(cls, options: dict[str, Any], base_dir: Path) -> Self:
        path = Path(options["path"])
        if not path.is_absolute():
            path = base_dir / path
        return cls.from_file(path)

    @classmethod
    def from_file(cls, path: Path) -> Self:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls(_parse_message(item) for item in raw["messages"])

    @property
    def drafts(self) -> list[Draft]:
        """Drafts saved during this session. Tests and the demo inspect these."""
        return list(self._drafts)

    def list_messages(self, label: str, limit: int) -> list[Message]:
        return [m for m in self._messages if label in m.labels][:limit]

    def search_messages(self, query: str, labels: Sequence[str], limit: int) -> list[Message]:
        terms = query.lower().split()
        wanted = set(labels)
        hits = [
            m for m in self._messages if m.labels & wanted and all(t in _haystack(m) for t in terms)
        ]
        return hits[:limit]

    def get_message(self, message_id: str) -> Message | None:
        return self._by_id.get(message_id)

    def create_draft(self, to: str, subject: str, body: str) -> Draft:
        draft = Draft(
            id=f"draft-{next(self._draft_ids):04d}",
            to=to,
            subject=subject,
            body=body,
            created_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
        self._drafts.append(draft)
        return draft


def _haystack(message: Message) -> str:
    return f"{message.sender} {message.subject} {message.body}".lower()


def _parse_message(item: dict[str, Any]) -> Message:
    return Message(
        id=item["id"],
        thread_id=item.get("thread_id", item["id"]),
        sender=item["from"],
        to=tuple(item.get("to", [])),
        subject=item["subject"],
        body=item["body"],
        labels=frozenset(item.get("labels", [])),
        received_at=item["received_at"],
    )

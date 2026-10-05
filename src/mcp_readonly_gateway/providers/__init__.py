"""Providers wrap a third-party API behind the small `MailProvider` interface."""

from __future__ import annotations

import importlib
from pathlib import Path
from typing import Any

from .base import Draft, MailProvider, Message
from .fixture import FixtureMailbox

#: Short aliases usable in the policy file instead of a full import path.
BUILTIN_PROVIDERS: dict[str, type[MailProvider]] = {
    "fixture": FixtureMailbox,
}


def load_provider(kind: str, options: dict[str, Any], base_dir: Path) -> MailProvider:
    """Instantiate a provider from a builtin alias or a `package.module:Class` path."""
    cls = BUILTIN_PROVIDERS.get(kind) or _import_class(kind)
    if not (isinstance(cls, type) and issubclass(cls, MailProvider)):
        raise TypeError(f"{kind!r} is not a MailProvider subclass")
    return cls.from_options(options, base_dir)


def _import_class(path: str) -> Any:
    module_name, sep, attr = path.partition(":")
    if not sep:
        known = ", ".join(sorted(BUILTIN_PROVIDERS))
        raise ValueError(f"Unknown provider {path!r}. Use one of [{known}] or 'module:Class'.")
    return getattr(importlib.import_module(module_name), attr)


__all__ = [
    "BUILTIN_PROVIDERS",
    "Draft",
    "FixtureMailbox",
    "MailProvider",
    "Message",
    "load_provider",
]

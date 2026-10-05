"""Policy: what an agent may call, how much, on which data, and on whose behalf.

The policy is plain YAML so a reviewer who doesn't read Python can still audit
it. Loading is strict: unknown keys, unknown tools, and scopes that point at
tools outside the allowlist are all errors. A typo should fail at startup,
not silently widen or narrow access.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

#: Every tool the gateway knows how to serve, and whether it reads or writes.
#: This lives in code, not config: the policy can turn a tool off, but it
#: cannot relabel a write tool as a read tool.
TOOL_CATALOG: dict[str, str] = {
    "list_messages": "read",
    "search_messages": "read",
    "read_message": "read",
    "create_draft": "write",
}

#: Always registered and never counted against limits. A model that can't see
#: its own limits will guess, and guessing is how agents hit walls in loops.
INFO_TOOL = "gateway_info"


class PolicyError(ValueError):
    """The policy file is invalid."""


@dataclass(frozen=True)
class ToolPolicy:
    max_results: int | None = None
    """Upper bound on items returned by a single call. Larger requests are clamped."""

    max_items: int | None = None
    """Budget: total items this tool may return (or create) per session."""

    max_body_chars: int | None = None
    """read_message only: message bodies are cut to this length."""

    allowed_recipient_domains: tuple[str, ...] | None = None
    """create_draft only: drafts may only be addressed to these domains."""


@dataclass(frozen=True)
class CallerScope:
    tools: frozenset[str]


@dataclass(frozen=True)
class Policy:
    tools: dict[str, ToolPolicy]
    callers: dict[str, CallerScope]
    readable_labels: tuple[str, ...]
    writes_enabled: bool = False
    provider_kind: str = "fixture"
    provider_options: dict[str, Any] = field(default_factory=dict)
    audit_path: Path = Path("audit.jsonl")
    base_dir: Path = Path(".")

    @classmethod
    def from_dict(cls, data: dict[str, Any], base_dir: Path | None = None) -> Policy:
        base_dir = (base_dir or Path.cwd()).resolve()
        _only_keys(
            data, {"version", "provider", "labels", "writes", "tools", "callers", "audit"}, ""
        )

        if data.get("version", 1) != 1:
            raise PolicyError(f"Unsupported policy version {data['version']!r}; expected 1.")

        tools = {name: _tool_policy(name, raw) for name, raw in _mapping(data, "tools").items()}

        labels = _mapping(data, "labels")
        _only_keys(labels, {"readable"}, "labels")
        readable = tuple(labels.get("readable") or ())
        if not readable:
            raise PolicyError("labels.readable must list at least one label.")

        writes = _mapping(data, "writes")
        _only_keys(writes, {"enabled"}, "writes")
        writes_enabled = writes.get("enabled", False)
        if not isinstance(writes_enabled, bool):
            raise PolicyError("writes.enabled must be true or false.")

        callers: dict[str, CallerScope] = {}
        for caller_id, raw in _mapping(data, "callers").items():
            if not isinstance(raw, dict):
                raise PolicyError(f"callers.{caller_id} must be a mapping.")
            _only_keys(raw, {"tools"}, f"callers.{caller_id}")
            scope = frozenset(raw.get("tools") or ())
            outside = scope - tools.keys()
            if outside:
                raise PolicyError(
                    f"callers.{caller_id} lists {sorted(outside)}, which are not in the "
                    "tools allowlist. Add them to `tools` first, or remove them here."
                )
            callers[str(caller_id)] = CallerScope(tools=scope)
        if not callers:
            raise PolicyError("Define at least one caller under `callers`.")

        provider = _mapping(data, "provider")
        _only_keys(provider, {"kind", "options"}, "provider")

        audit = _mapping(data, "audit")
        _only_keys(audit, {"path"}, "audit")
        audit_path = Path(audit.get("path", "audit.jsonl"))
        if not audit_path.is_absolute():
            audit_path = (base_dir / audit_path).resolve()

        return cls(
            tools=tools,
            callers=callers,
            readable_labels=readable,
            writes_enabled=writes_enabled,
            provider_kind=str(provider.get("kind", "fixture")),
            provider_options=dict(provider.get("options") or {}),
            audit_path=audit_path,
            base_dir=base_dir,
        )

    def tools_for(self, caller: str) -> list[str]:
        """Tools this caller can actually use right now, in catalog order."""
        scope = self.callers.get(caller)
        if scope is None:
            return []
        return [
            name
            for name in TOOL_CATALOG
            if name in self.tools
            and name in scope.tools
            and (TOOL_CATALOG[name] == "read" or self.writes_enabled)
        ]


def load_policy(path: str | Path) -> Policy:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise PolicyError(f"{path}: not valid YAML ({exc})") from exc
    if not isinstance(data, dict):
        raise PolicyError(f"{path}: the top level must be a mapping.")
    return Policy.from_dict(data, base_dir=path.parent)


def _tool_policy(name: str, raw: Any) -> ToolPolicy:
    if name == INFO_TOOL:
        raise PolicyError(f"{INFO_TOOL} is always on; don't list it under `tools`.")
    if name not in TOOL_CATALOG:
        known = ", ".join(TOOL_CATALOG)
        raise PolicyError(f"Unknown tool {name!r} in `tools`. Known tools: {known}.")
    raw = raw or {}
    if not isinstance(raw, dict):
        raise PolicyError(f"tools.{name} must be a mapping (use {{}} for no limits).")
    _only_keys(
        raw,
        {"max_results", "max_items", "max_body_chars", "allowed_recipient_domains"},
        f"tools.{name}",
    )
    for key in ("max_results", "max_items", "max_body_chars"):
        value = raw.get(key)
        if value is not None and (
            not isinstance(value, int) or isinstance(value, bool) or value < 1
        ):
            raise PolicyError(f"tools.{name}.{key} must be a positive integer.")
    domains = raw.get("allowed_recipient_domains")
    return ToolPolicy(
        max_results=raw.get("max_results"),
        max_items=raw.get("max_items"),
        max_body_chars=raw.get("max_body_chars"),
        allowed_recipient_domains=(
            tuple(d.lower().lstrip("@") for d in domains) if domains is not None else None
        ),
    )


def _mapping(data: dict[str, Any], key: str) -> dict[str, Any]:
    value = data.get(key) or {}
    if not isinstance(value, dict):
        raise PolicyError(f"`{key}` must be a mapping.")
    return value


def _only_keys(data: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = set(data) - allowed
    if extra:
        prefix = f"{where}: " if where else ""
        raise PolicyError(f"{prefix}unknown keys {sorted(extra)}. Allowed: {sorted(allowed)}.")

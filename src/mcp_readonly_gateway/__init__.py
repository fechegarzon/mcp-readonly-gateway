"""Put a third-party API behind a narrow, audited, least-privilege MCP surface."""

from ._version import __version__
from .audit import AuditLog, redact
from .gateway import Denied, Gateway
from .policy import Policy, PolicyError, load_policy
from .server import build_server

__all__ = [
    "AuditLog",
    "Denied",
    "Gateway",
    "Policy",
    "PolicyError",
    "__version__",
    "build_server",
    "load_policy",
    "redact",
]

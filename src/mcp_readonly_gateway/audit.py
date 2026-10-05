"""Append-only JSONL audit log, one line per tool call.

Each record says who called what, whether the gateway allowed it, why not if
it didn't, how many items came back, and how long it took. Arguments are
stored redacted (emails and phone numbers masked, long strings cut) next to a
hash of the raw arguments, so you can match repeated calls without keeping
the PII. Results are never logged.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
# Candidate phone numbers: a digit run with common separators. We only mask
# candidates with 9+ digits so that dates (2026-10-04) and short ids survive.
# It errs toward masking: two numbers separated by a space can merge into one
# candidate and get masked together. That is the cheaper mistake.
PHONE_CANDIDATE_RE = re.compile(r"(?<![\w+(])[+(]?\d[\d\s().-]{6,}\d(?!\w)")
MIN_PHONE_DIGITS = 9
MAX_LOGGED_CHARS = 200

#: Set this to key the argument hash. Without a key, the hash of a short value
#: like an email address can be brute-forced, so use one outside of demos.
HASH_KEY_ENV = "GATEWAY_AUDIT_HASH_KEY"


def redact_text(text: str) -> str:
    text = EMAIL_RE.sub("[email]", text)

    def _phone(match: re.Match[str]) -> str:
        digits = sum(ch.isdigit() for ch in match.group())
        return "[phone]" if digits >= MIN_PHONE_DIGITS else match.group()

    text = PHONE_CANDIDATE_RE.sub(_phone, text)
    if len(text) > MAX_LOGGED_CHARS:
        text = f"{text[:MAX_LOGGED_CHARS]}...[+{len(text) - MAX_LOGGED_CHARS} chars]"
    return text


def redact(value: Any) -> Any:
    """Recursively redact every string inside dicts, lists, and tuples."""
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {str(k): redact(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [redact(v) for v in value]
    return value


def args_hash(args: dict[str, Any], key: bytes | None = None) -> str:
    canonical = json.dumps(args, sort_keys=True, separators=(",", ":"), default=str).encode()
    if key:
        return "hmac-sha256:" + hmac.new(key, canonical, hashlib.sha256).hexdigest()
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path, hash_key: bytes | None = None) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        env_key = os.environ.get(HASH_KEY_ENV)
        self._hash_key = (
            hash_key if hash_key is not None else (env_key.encode() if env_key else None)
        )
        self._lock = threading.Lock()

    def record(
        self,
        *,
        caller: str,
        tool: str,
        args: dict[str, Any],
        decision: str,
        latency_ms: float,
        code: str | None = None,
        reason: str | None = None,
        items: int | None = None,
    ) -> dict[str, Any]:
        entry: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "caller": caller,
            "tool": tool,
            "args_hash": args_hash(args, self._hash_key),
            "args": redact(args),
            "decision": decision,
            "latency_ms": round(latency_ms, 2),
        }
        if code is not None:
            entry["code"] = code
        if reason is not None:
            entry["reason"] = redact_text(reason)
        if items is not None:
            entry["items"] = items

        line = json.dumps(entry, ensure_ascii=False) + "\n"
        with self._lock:
            # O_APPEND: every write lands at the end, even with several writers.
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            try:
                os.write(fd, line.encode("utf-8"))
            finally:
                os.close(fd)
        return entry

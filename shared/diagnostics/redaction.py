"""JSON-safe redaction helpers for diagnostic event details."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


SENSITIVE_KEYWORDS = (
    "token",
    "secret",
    "password",
    "api_key",
    "authorization",
    "bearer",
    "private_key",
    "credential",
)
REDACTION_MARKER = "[REDACTED]"


def _is_sensitive_key(key: object) -> bool:
    lowered = str(key).lower()
    return any(keyword in lowered for keyword in SENSITIVE_KEYWORDS)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    return repr(value)


def redact_value(value: Any) -> Any:
    return _json_safe(value)


def redact_mapping(data: Mapping[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in data.items():
        key_string = str(key)
        if _is_sensitive_key(key_string):
            redacted[key_string] = REDACTION_MARKER
        else:
            redacted[key_string] = redact_value(value)
    return redacted

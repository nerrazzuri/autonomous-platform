from __future__ import annotations

"""Provisioning role registry with backward-compatible defaults."""

_REGISTERED_ROLES: set[str] = {"logistics", "patrol"}


def _normalize_role(role: str) -> str:
    if not isinstance(role, str) or not role.strip():
        raise ValueError("role must be a non-empty string")
    return role.strip()


def register_role(role: str) -> None:
    """Future apps should register roles instead of editing shared provisioning code."""
    _REGISTERED_ROLES.add(_normalize_role(role))


def unregister_role(role: str) -> None:
    _REGISTERED_ROLES.discard(_normalize_role(role))


def get_registered_roles() -> set[str]:
    return set(_REGISTERED_ROLES)


def validate_role(role: object) -> str:
    normalized_role = _normalize_role(role) if isinstance(role, str) else ""
    if normalized_role not in _REGISTERED_ROLES:
        allowed = "', '".join(sorted(_REGISTERED_ROLES))
        raise ValueError(f"role must be one of '{allowed}'")
    return normalized_role

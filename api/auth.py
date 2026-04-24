from __future__ import annotations

"""Static bearer-token authentication helpers for the internal REST API."""

import secrets
from dataclasses import dataclass
from enum import Enum
from typing import Annotated

from fastapi import Header, HTTPException, status

from core.config import get_config
from core.logger import get_logger


logger = get_logger(__name__)

AuthorizationHeader = Annotated[str | None, Header(alias="Authorization")]  # pragma: no mutate


class AuthError(Exception):
    """Raised when a request cannot be authenticated or authorized."""

    def __init__(
        self,
        *,
        status_code: int,
        reason: str,
        token_name: str | None = None,
        role: "Role | None" = None,
        allowed_roles: tuple["Role", ...] = (),
    ) -> None:
        super().__init__(reason)
        self.status_code = status_code
        self.reason = reason
        self.token_name = token_name
        self.role = role
        self.allowed_roles = allowed_roles


class Role(str, Enum):
    OPERATOR = "operator"
    QA = "qa"
    SUPERVISOR = "supervisor"


@dataclass(frozen=True)
class AuthContext:
    role: Role
    token_name: str


def _configured_tokens() -> tuple[tuple[str, Role, str], ...]:
    auth_config = get_config().auth
    return (
        ("operator_token", Role.OPERATOR, auth_config.operator_token),
        ("qa_token", Role.QA, auth_config.qa_token),
        ("supervisor_token", Role.SUPERVISOR, auth_config.supervisor_token),
    )


def _extract_bearer_token(authorization: str | None) -> str:
    if authorization is None or not authorization.strip():
        raise AuthError(status_code=status.HTTP_401_UNAUTHORIZED, reason="missing_authorization")

    parts = authorization.strip().split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise AuthError(status_code=status.HTTP_401_UNAUTHORIZED, reason="invalid_scheme")
    return parts[1]


def _resolve_auth_context(authorization: str | None) -> AuthContext:
    token = _extract_bearer_token(authorization)
    for token_name, role, configured_token in _configured_tokens():
        if secrets.compare_digest(token, configured_token):
            return AuthContext(role=role, token_name=token_name)
    raise AuthError(status_code=status.HTTP_403_FORBIDDEN, reason="unknown_token")


def _validate_role(context: AuthContext, allowed_roles: tuple[Role, ...]) -> AuthContext:
    if context.role in allowed_roles:
        return context
    raise AuthError(
        status_code=status.HTTP_403_FORBIDDEN,
        reason="insufficient_role",
        token_name=context.token_name,
        role=context.role,
        allowed_roles=allowed_roles,
    )


def _log_auth_failure(exc: AuthError) -> None:
    extra: dict[str, object] = {"reason": exc.reason}
    if exc.token_name is not None:
        extra["token_name"] = exc.token_name
    if exc.role is not None:
        extra["role"] = exc.role.value
    if exc.allowed_roles:
        extra["allowed_roles"] = [role.value for role in exc.allowed_roles]
    logger.warning("Authentication failed", extra=extra)


def _to_http_exception(exc: AuthError) -> HTTPException:
    if exc.status_code == status.HTTP_401_UNAUTHORIZED:
        return HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Forbidden")


def get_auth_context(authorization: str | None) -> AuthContext:
    try:
        return _resolve_auth_context(authorization)
    except AuthError as exc:
        _log_auth_failure(exc)
        raise _to_http_exception(exc) from exc


def require_role(*allowed_roles: Role):
    normalized_roles = tuple(dict.fromkeys(allowed_roles))
    if not normalized_roles:
        raise ValueError("At least one allowed role is required")

    def dependency(authorization: AuthorizationHeader = None) -> AuthContext:
        try:
            context = _resolve_auth_context(authorization)
            return _validate_role(context, normalized_roles)
        except AuthError as exc:
            _log_auth_failure(exc)
            raise _to_http_exception(exc) from exc

    dependency.__name__ = "require_" + "_or_".join(role.value for role in normalized_roles)
    return dependency


_require_operator_dependency = require_role(Role.OPERATOR, Role.QA, Role.SUPERVISOR)
_require_qa_dependency = require_role(Role.QA, Role.SUPERVISOR)
_require_supervisor_dependency = require_role(Role.SUPERVISOR)


def require_operator(authorization: AuthorizationHeader = None) -> AuthContext:
    return _require_operator_dependency(authorization)


def require_qa(authorization: AuthorizationHeader = None) -> AuthContext:
    return _require_qa_dependency(authorization)


def require_supervisor(authorization: AuthorizationHeader = None) -> AuthContext:
    return _require_supervisor_dependency(authorization)


__all__ = [
    "AuthContext",
    "AuthError",
    "Role",
    "get_auth_context",
    "require_operator",
    "require_qa",
    "require_role",
    "require_supervisor",
]

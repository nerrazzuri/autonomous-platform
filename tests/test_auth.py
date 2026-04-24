from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest
from fastapi import HTTPException


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


TEST_OPERATOR_TOKEN = "test-operator-token"
TEST_QA_TOKEN = "test-qa-token"
TEST_SUPERVISOR_TOKEN = "test-supervisor-token"


@pytest.fixture
def auth_module(monkeypatch: pytest.MonkeyPatch):
    from core.config import AppConfig, AuthSection

    import api.auth as module

    config = AppConfig(
        auth=AuthSection(
            operator_token=TEST_OPERATOR_TOKEN,
            qa_token=TEST_QA_TOKEN,
            supervisor_token=TEST_SUPERVISOR_TOKEN,
        )
    )
    monkeypatch.setattr(module, "get_config", lambda: config)
    return module


def test_get_auth_context_operator_token(auth_module) -> None:
    context = auth_module.get_auth_context(f"Bearer {TEST_OPERATOR_TOKEN}")

    assert context.role is auth_module.Role.OPERATOR
    assert context.token_name == "operator_token"


def test_get_auth_context_qa_token(auth_module) -> None:
    context = auth_module.get_auth_context(f"Bearer {TEST_QA_TOKEN}")

    assert context.role is auth_module.Role.QA
    assert context.token_name == "qa_token"


def test_get_auth_context_supervisor_token(auth_module) -> None:
    context = auth_module.get_auth_context(f"Bearer {TEST_SUPERVISOR_TOKEN}")

    assert context.role is auth_module.Role.SUPERVISOR
    assert context.token_name == "supervisor_token"


def test_missing_authorization_raises_401(auth_module) -> None:
    with pytest.raises(HTTPException) as exc_info:
        auth_module.get_auth_context(None)

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_wrong_scheme_raises_401(auth_module) -> None:
    with pytest.raises(HTTPException) as exc_info:
        auth_module.get_auth_context(f"Token {TEST_OPERATOR_TOKEN}")

    assert exc_info.value.status_code == 401
    assert exc_info.value.detail == "Authentication required"
    assert exc_info.value.headers == {"WWW-Authenticate": "Bearer"}


def test_unknown_token_raises_403(auth_module) -> None:
    with pytest.raises(HTTPException) as exc_info:
        auth_module.get_auth_context("Bearer unknown-token")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"
    assert exc_info.value.headers is None


def test_require_operator_allows_operator_qa_supervisor(auth_module) -> None:
    operator_context = auth_module.require_operator(f"Bearer {TEST_OPERATOR_TOKEN}")
    qa_context = auth_module.require_operator(f"Bearer {TEST_QA_TOKEN}")
    supervisor_context = auth_module.require_operator(f"Bearer {TEST_SUPERVISOR_TOKEN}")

    assert operator_context.role is auth_module.Role.OPERATOR
    assert qa_context.role is auth_module.Role.QA
    assert supervisor_context.role is auth_module.Role.SUPERVISOR


def test_require_qa_allows_qa_supervisor(auth_module) -> None:
    qa_context = auth_module.require_qa(f"Bearer {TEST_QA_TOKEN}")
    supervisor_context = auth_module.require_qa(f"Bearer {TEST_SUPERVISOR_TOKEN}")

    assert qa_context.role is auth_module.Role.QA
    assert supervisor_context.role is auth_module.Role.SUPERVISOR


def test_require_qa_rejects_operator(auth_module) -> None:
    with pytest.raises(HTTPException) as exc_info:
        auth_module.require_qa(f"Bearer {TEST_OPERATOR_TOKEN}")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


def test_require_supervisor_allows_supervisor_only(auth_module) -> None:
    context = auth_module.require_supervisor(f"Bearer {TEST_SUPERVISOR_TOKEN}")

    assert context.role is auth_module.Role.SUPERVISOR

    with pytest.raises(HTTPException) as exc_info:
        auth_module.require_supervisor(f"Bearer {TEST_QA_TOKEN}")

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Forbidden"


def test_tokens_compared_without_plain_logging(
    auth_module, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    compared: list[tuple[str, str]] = []

    def fake_compare_digest(left: str, right: str) -> bool:
        compared.append((left, right))
        return left == right

    monkeypatch.setattr(auth_module.secrets, "compare_digest", fake_compare_digest)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(HTTPException) as exc_info:
            auth_module.get_auth_context("Bearer unknown-token")

    assert exc_info.value.status_code == 403
    assert compared == [
        ("unknown-token", TEST_OPERATOR_TOKEN),
        ("unknown-token", TEST_QA_TOKEN),
        ("unknown-token", TEST_SUPERVISOR_TOKEN),
    ]
    assert "unknown-token" not in caplog.text
    assert TEST_OPERATOR_TOKEN not in caplog.text
    assert TEST_QA_TOKEN not in caplog.text
    assert TEST_SUPERVISOR_TOKEN not in caplog.text


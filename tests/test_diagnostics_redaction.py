from __future__ import annotations

from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.diagnostics.redaction import REDACTION_MARKER, redact_mapping


class NonSerializableValue:
    def __repr__(self) -> str:
        return "<NonSerializableValue>"


def test_token_password_secret_redacted() -> None:
    redacted = redact_mapping(
        {
            "access_token": "not-a-real-token",
            "smtp_password": "not-a-real-password",
            "client_secret": "not-a-real-secret",
        }
    )

    assert redacted["access_token"] == REDACTION_MARKER
    assert redacted["smtp_password"] == REDACTION_MARKER
    assert redacted["client_secret"] == REDACTION_MARKER


def test_nested_dict_redacted() -> None:
    redacted = redact_mapping({"outer": {"Authorization": "placeholder-authorization"}})

    assert redacted["outer"]["Authorization"] == REDACTION_MARKER


def test_list_values_redacted_recursively() -> None:
    redacted = redact_mapping({"items": [{"private_key": "fake"}, {"plain": "ok"}]})

    assert redacted["items"][0]["private_key"] == REDACTION_MARKER
    assert redacted["items"][1]["plain"] == "ok"


def test_non_sensitive_fields_preserved() -> None:
    redacted = redact_mapping({"module": "navigator", "count": 3, "enabled": True})

    assert redacted == {"module": "navigator", "count": 3, "enabled": True}


def test_ip_and_robot_id_not_redacted() -> None:
    redacted = redact_mapping({"robot_id": "robot_01", "robot_ip": "192.0.2.10", "route_id": "route_a"})

    assert redacted["robot_id"] == "robot_01"
    assert redacted["robot_ip"] == "192.0.2.10"
    assert redacted["route_id"] == "route_a"


def test_non_serializable_values_are_repr_strings() -> None:
    redacted = redact_mapping({"value": NonSerializableValue()})

    assert redacted["value"] == "<NonSerializableValue>"

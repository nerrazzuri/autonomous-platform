from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.diagnostics import error_codes
from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity, normalize_severity


class NonSerializableDetail:
    def __repr__(self) -> str:
        return "<NonSerializableDetail>"


def test_create_event_with_required_fields() -> None:
    event = DiagnosticEvent.create(
        severity=DiagnosticSeverity.INFO,
        module="shared.navigation",
        event="navigation.ready",
        message="Navigation module ready",
    )

    assert event.severity is DiagnosticSeverity.INFO
    assert event.module == "shared.navigation"
    assert event.event == "navigation.ready"
    assert event.message == "Navigation module ready"


@pytest.mark.parametrize("field_name", ["module", "event", "message"])
def test_empty_required_text_rejected(field_name: str) -> None:
    kwargs = {
        "severity": "info",
        "module": "module",
        "event": "event",
        "message": "message",
    }
    kwargs[field_name] = " "

    with pytest.raises(ValueError, match=field_name):
        DiagnosticEvent.create(**kwargs)


def test_severity_accepts_enum_and_case_insensitive_string() -> None:
    assert normalize_severity(DiagnosticSeverity.ERROR) is DiagnosticSeverity.ERROR
    assert normalize_severity("WARNING") is DiagnosticSeverity.WARNING
    assert normalize_severity(" critical ") is DiagnosticSeverity.CRITICAL


def test_unknown_severity_rejected() -> None:
    with pytest.raises(ValueError, match="severity"):
        normalize_severity("urgent")


def test_event_id_generated() -> None:
    event = DiagnosticEvent.create(severity="info", module="m", event="e", message="msg")

    assert isinstance(event.event_id, str)
    assert event.event_id


def test_timestamp_is_timezone_aware_utc_like_iso_string() -> None:
    event = DiagnosticEvent.create(severity="info", module="m", event="e", message="msg")
    parsed = datetime.fromisoformat(event.ts)

    assert parsed.tzinfo is not None
    assert event.ts.endswith("+00:00")


def test_to_dict_is_json_serializable() -> None:
    event = DiagnosticEvent.create(
        severity="info",
        module="m",
        event="e",
        message="msg",
        details={"plain": "ok"},
    )

    payload = event.to_dict()

    assert payload["severity"] == "info"
    json.dumps(payload)


def test_to_json_and_from_json_round_trip() -> None:
    event = DiagnosticEvent.create(
        severity="warning",
        module="m",
        event="e",
        message="msg",
        robot_id="robot_01",
    )

    restored = DiagnosticEvent.from_json(event.to_json())

    assert restored == event


def test_from_dict_round_trip() -> None:
    event = DiagnosticEvent.create(
        severity="error",
        module="m",
        event="e",
        message="msg",
        task_id="task-1",
    )

    restored = DiagnosticEvent.from_dict(event.to_dict())

    assert restored == event


def test_suggested_action_auto_filled_by_error_code() -> None:
    event = DiagnosticEvent.create(
        severity="error",
        module="sdk",
        event="connect_failed",
        message="SDK connect failed",
        error_code=error_codes.SDK_CONNECT_FAILED,
    )

    assert event.suggested_action == error_codes.get_suggested_action(error_codes.SDK_CONNECT_FAILED)


def test_explicit_suggested_action_overrides_default() -> None:
    event = DiagnosticEvent.create(
        severity="error",
        module="sdk",
        event="connect_failed",
        message="SDK connect failed",
        error_code=error_codes.SDK_CONNECT_FAILED,
        suggested_action="Call the demo lead.",
    )

    assert event.suggested_action == "Call the demo lead."


def test_non_serializable_details_are_stringified() -> None:
    event = DiagnosticEvent.create(
        severity="info",
        module="m",
        event="e",
        message="msg",
        details={"object": NonSerializableDetail()},
    )

    assert event.details["object"] == "<NonSerializableDetail>"
    json.dumps(event.to_dict())


def test_sensitive_details_are_redacted() -> None:
    event = DiagnosticEvent.create(
        severity="info",
        module="m",
        event="e",
        message="msg",
        details={"api_token": "not-a-real-token", "nested": {"password": "not-a-real-password"}},
    )

    assert event.details["api_token"] == "[REDACTED]"
    assert event.details["nested"]["password"] == "[REDACTED]"


def test_get_suggested_action_known_unknown_and_constant_types() -> None:
    assert isinstance(error_codes.SDK_CONNECT_FAILED, str)
    assert error_codes.get_suggested_action(error_codes.LIDAR_SCAN_TIMEOUT)
    assert error_codes.get_suggested_action("app.custom") is None
    assert error_codes.get_suggested_action(None) is None

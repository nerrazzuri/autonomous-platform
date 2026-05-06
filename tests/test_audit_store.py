from __future__ import annotations

import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_audit_event_requires_non_empty_event_type() -> None:
    from shared.audit.audit_models import AuditEvent

    with pytest.raises(ValueError):
        AuditEvent(event_type="  ")


def test_shared_audit_has_no_apps_dependency() -> None:
    audit_dir = Path(__file__).resolve().parents[1] / "shared" / "audit"
    content = "\n".join(path.read_text(encoding="utf-8") for path in audit_dir.glob("*.py"))

    assert "from apps" not in content
    assert "import apps" not in content


def test_audit_store_appends_and_lists_events(tmp_path: Path) -> None:
    from shared.audit.audit_models import AuditEvent
    from shared.audit.audit_store import AuditStore

    store = AuditStore(tmp_path / "audit.jsonl")
    appended = store.append(AuditEvent(event_type="robot_started", robot_id="robot_01"))

    events = store.list_events()

    assert appended.event_id
    assert len(events) == 1
    assert events[0].event_type == "robot_started"
    assert events[0].robot_id == "robot_01"


def test_audit_store_filters_and_limit(tmp_path: Path) -> None:
    from shared.audit.audit_models import AuditEvent
    from shared.audit.audit_store import AuditStore

    store = AuditStore(tmp_path / "audit.jsonl")
    store.append(AuditEvent(event_type="task_dispatched", robot_id="robot_01", severity="info"))
    store.append(AuditEvent(event_type="task_failed", robot_id="robot_01", severity="error"))
    store.append(AuditEvent(event_type="task_dispatched", robot_id="robot_02", severity="warning"))

    filtered = store.list_events(robot_id="robot_01", event_type="task_failed", severity="error", limit=10)
    limited = store.list_events(limit=2)

    assert len(filtered) == 1
    assert filtered[0].robot_id == "robot_01"
    assert filtered[0].event_type == "task_failed"
    assert filtered[0].severity == "error"
    assert len(limited) == 2


def test_audit_store_redacts_sensitive_metadata(tmp_path: Path) -> None:
    from shared.audit.audit_models import AuditEvent
    from shared.audit.audit_store import AuditStore
    from shared.core.logger import MASKED_VALUE

    store = AuditStore(tmp_path / "audit.jsonl")
    store.append(
        AuditEvent(
            event_type="provisioning_failed",
            metadata={
                "target_wifi_password": "super-secret",
                "authorization": "Bearer abc123",
                "nested": {"ssh_password": "robot-pass"},
            },
        )
    )

    event = store.list_events(limit=1)[0]

    assert event.metadata["target_wifi_password"] == MASKED_VALUE
    assert event.metadata["authorization"] == MASKED_VALUE
    assert event.metadata["nested"]["ssh_password"] == MASKED_VALUE


def test_audit_event_context_serializes_and_round_trips() -> None:
    from shared.audit.audit_models import AuditEvent

    event = AuditEvent(
        event_type="navigation_blocked",
        robot_id="robot_01",
        context={"route_id": "route-1", "retryable": True},
    )

    payload = event.to_dict()
    restored = AuditEvent.from_dict(payload)

    assert payload["context"] == {"route_id": "route-1", "retryable": True}
    assert restored == event


def test_legacy_audit_fields_merge_into_context() -> None:
    from shared.audit.audit_models import AuditEvent

    event = AuditEvent(
        event_type="legacy_event",
        task_id="task-1",
        cycle_id="cycle-1",
        route_id="route-1",
        job_id="job-1",
    )

    assert event.context == {
        "task_id": "task-1",
        "cycle_id": "cycle-1",
        "route_id": "route-1",
        "job_id": "job-1",
    }


def test_explicit_audit_context_wins_over_legacy_fields() -> None:
    from shared.audit.audit_models import AuditEvent

    event = AuditEvent(event_type="legacy_event", route_id="legacy-route", context={"route_id": "context-route"})

    assert event.context["route_id"] == "context-route"
    assert event.route_id == "legacy-route"


def test_audit_context_redacts_sensitive_values() -> None:
    from shared.audit.audit_models import AuditEvent
    from shared.core.logger import MASKED_VALUE

    event = AuditEvent(
        event_type="provisioning_failed",
        context={"target_wifi_password": "super-secret", "robot_id": "robot_01"},
    )

    assert event.context["target_wifi_password"] == MASKED_VALUE
    assert event.context["robot_id"] == "robot_01"


def test_audit_event_helper_catches_store_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.audit.audit_store as audit_store_module

    class BrokenStore:
        def append(self, _event):
            raise RuntimeError("disk full")

    monkeypatch.setattr(audit_store_module, "get_audit_store", lambda: BrokenStore())

    event = audit_store_module.audit_event(event_type="audit_write_failed")

    assert event is None

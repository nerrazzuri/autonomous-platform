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


def test_audit_event_helper_catches_store_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.audit.audit_store as audit_store_module

    class BrokenStore:
        def append(self, _event):
            raise RuntimeError("disk full")

    monkeypatch.setattr(audit_store_module, "get_audit_store", lambda: BrokenStore())

    event = audit_store_module.audit_event(event_type="audit_write_failed")

    assert event is None

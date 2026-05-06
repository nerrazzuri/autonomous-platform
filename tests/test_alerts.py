from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def alerts_module():
    import shared.observability.alerts as module

    module.clear_alert_rules()
    module.register_platform_alert_rules()
    yield module
    module.clear_alert_rules()
    module.register_platform_alert_rules()


def make_alert(alerts_module, **overrides):
    payload = {
        "alert_id": "alert-1",
        "timestamp": "2026-04-28T08:00:00Z",
        "severity": "warning",
        "source": "watchdog",
        "event_type": "watchdog_stale_robot",
        "message": "Robot telemetry is stale",
        "robot_id": "robot-01",
        "metadata": {"component": "watchdog"},
    }
    payload.update(overrides)
    return alerts_module.Alert(**payload)


def test_alert_validates_required_fields(alerts_module) -> None:
    with pytest.raises(ValueError, match="severity"):
        make_alert(alerts_module, severity="emergency")

    with pytest.raises(ValueError, match="source"):
        make_alert(alerts_module, source="   ")

    with pytest.raises(ValueError, match="event_type"):
        make_alert(alerts_module, event_type="")

    with pytest.raises(ValueError, match="message"):
        make_alert(alerts_module, message="  ")


def test_alert_redacts_sensitive_metadata(alerts_module) -> None:
    alert = make_alert(
        alerts_module,
        metadata={
            "password": "super-secret",
            "nested": {"api_key": "abc123"},
            "labels": {"a", "b"},
        },
    )

    assert alert.metadata["password"] == alerts_module.MASKED_VALUE
    assert alert.metadata["nested"]["api_key"] == alerts_module.MASKED_VALUE
    assert sorted(alert.metadata["labels"]) == ["a", "b"]


def test_alert_router_emits_and_lists_alerts(alerts_module) -> None:
    router = alerts_module.AlertRouter(max_alerts=10)
    first = router.emit(make_alert(alerts_module, alert_id="alert-1", message="first"))
    second = router.emit(make_alert(alerts_module, alert_id="alert-2", message="second"))

    assert router.get("alert-1") == first
    assert [alert.alert_id for alert in router.list_alerts()] == ["alert-2", "alert-1"]
    assert router.list_alerts(limit=1)[0].alert_id == "alert-2"
    assert second.message == "second"


def test_alert_router_filters(alerts_module) -> None:
    router = alerts_module.AlertRouter(max_alerts=10)
    info_alert = router.emit(
        make_alert(
            alerts_module,
            alert_id="alert-info",
            severity="info",
            robot_id="robot-01",
            event_type="watchdog_restored",
        )
    )
    warning_alert = router.emit(
        make_alert(
            alerts_module,
            alert_id="alert-warning",
            severity="warning",
            robot_id="robot-02",
            event_type="battery_critical",
            source="battery",
        )
    )
    router.acknowledge(info_alert.alert_id, actor_id="operator-1")

    assert [alert.alert_id for alert in router.list_alerts(severity="warning")] == [warning_alert.alert_id]
    assert [alert.alert_id for alert in router.list_alerts(robot_id="robot-01")] == [info_alert.alert_id]
    assert [alert.alert_id for alert in router.list_alerts(acknowledged=True)] == [info_alert.alert_id]
    assert [alert.alert_id for alert in router.list_alerts(acknowledged=False)] == [warning_alert.alert_id]


def test_alert_router_drops_oldest_when_capacity_exceeded(alerts_module) -> None:
    router = alerts_module.AlertRouter(max_alerts=2)
    router.emit(make_alert(alerts_module, alert_id="alert-1"))
    router.emit(make_alert(alerts_module, alert_id="alert-2"))
    router.emit(make_alert(alerts_module, alert_id="alert-3"))

    assert router.get("alert-1") is None
    assert [alert.alert_id for alert in router.list_alerts()] == ["alert-3", "alert-2"]


def test_acknowledge_updates_alert_fields(alerts_module) -> None:
    router = alerts_module.AlertRouter(max_alerts=2)
    router.emit(make_alert(alerts_module, alert_id="alert-1"))

    acknowledged = router.acknowledge("alert-1", actor_id="operator-7")

    assert acknowledged.acknowledged is True
    assert acknowledged.acknowledged_by == "operator-7"
    assert acknowledged.acknowledged_at is not None


def test_emit_alert_helper_catches_router_failure(alerts_module, monkeypatch: pytest.MonkeyPatch) -> None:
    class BrokenRouter:
        def emit(self, _alert):
            raise RuntimeError("router exploded")

    monkeypatch.setattr(alerts_module, "_alert_router", BrokenRouter())

    emitted = alerts_module.emit_alert(
        severity="critical",
        source="system",
        event_type="broken",
        message="This should not raise",
    )

    assert emitted is None


def test_warning_error_and_critical_alerts_create_audit_events(alerts_module, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, object]] = []

    def fake_audit_event(**kwargs):
        calls.append(kwargs)
        return object()

    monkeypatch.setattr(alerts_module, "audit_event", fake_audit_event)

    router = alerts_module.AlertRouter(max_alerts=10)
    router.emit(make_alert(alerts_module, alert_id="alert-warning", severity="warning"))
    router.emit(make_alert(alerts_module, alert_id="alert-error", severity="error"))
    router.emit(make_alert(alerts_module, alert_id="alert-critical", severity="critical"))
    router.emit(make_alert(alerts_module, alert_id="alert-info", severity="info"))

    assert [call["severity"] for call in calls] == ["warning", "error", "critical"]
    assert all(call["event_type"] == "alert_emitted" for call in calls)


def test_shared_alert_rules_exclude_app_specific_events(alerts_module) -> None:
    from shared.core.event_bus import EventName

    alerts_module.clear_alert_rules()
    alerts_module.register_platform_alert_rules()

    registered_events = {rule.event_name for rule in alerts_module.get_registered_alert_rules()}

    assert EventName.BATTERY_CRITICAL in registered_events
    assert EventName.ESTOP_TRIGGERED in registered_events
    assert EventName.ESTOP_RELEASED in registered_events
    assert EventName.TASK_FAILED not in registered_events
    assert EventName.PATROL_CYCLE_FAILED not in registered_events


@pytest.mark.asyncio
async def test_registered_app_alert_rule_emits_alert(alerts_module) -> None:
    from shared.core.event_bus import EventBus, EventName

    event_bus = EventBus()
    router = alerts_module.AlertRouter(max_alerts=10, event_bus=event_bus)
    alerts_module.clear_alert_rules()
    alerts_module.register_alert_rule(
        event_name=EventName.TASK_FAILED,
        alert_type="custom_task_failed",
        default_message="Custom task failed",
        severity="error",
        source="custom_app",
    )
    await event_bus.start()
    await router.start()

    await event_bus.publish(EventName.TASK_FAILED, {"task_id": "task-1", "robot_id": "robot-01"}, task_id="task-1")
    await event_bus.wait_until_idle(timeout=1.0)

    emitted = router.list_alerts()
    assert len(emitted) == 1
    assert emitted[0].event_type == "custom_task_failed"
    assert emitted[0].message == "Custom task failed"
    assert emitted[0].source == "custom_app"
    assert emitted[0].task_id == "task-1"
    assert emitted[0].robot_id == "robot-01"

    await router.stop()
    await event_bus.stop()
    alerts_module.clear_alert_rules()
    alerts_module.register_platform_alert_rules()


def test_logistics_alert_registration_lives_in_app_layer(alerts_module) -> None:
    from apps.logistics.observability.alerts import register_logistics_alert_rules
    from shared.core.event_bus import EventName

    alerts_module.clear_alert_rules()
    register_logistics_alert_rules()

    registered_events = {rule.event_name for rule in alerts_module.get_registered_alert_rules()}

    assert EventName.TASK_FAILED in registered_events
    assert EventName.PATROL_CYCLE_FAILED not in registered_events

    alerts_module.clear_alert_rules()
    alerts_module.register_platform_alert_rules()


def test_patrol_alert_registration_lives_in_app_layer(alerts_module) -> None:
    from apps.patrol.observability.alerts import register_patrol_alert_rules
    from shared.core.event_bus import EventName

    alerts_module.clear_alert_rules()
    register_patrol_alert_rules()

    registered_events = {rule.event_name for rule in alerts_module.get_registered_alert_rules()}

    assert EventName.PATROL_CYCLE_FAILED in registered_events
    assert EventName.TASK_FAILED not in registered_events

    alerts_module.clear_alert_rules()
    alerts_module.register_platform_alert_rules()


@pytest.mark.asyncio
async def test_watchdog_system_alert_event_emits_alert(alerts_module) -> None:
    from shared.core.event_bus import EventBus, EventName

    event_bus = EventBus()
    router = alerts_module.AlertRouter(max_alerts=10, event_bus=event_bus)
    await event_bus.start()
    await router.start()

    await event_bus.publish(
        EventName.SYSTEM_ALERT,
        {
            "severity": "critical",
            "reason": "watchdog_stale_robot",
            "message": "Telemetry stopped updating",
            "module": "watchdog",
            "robot_id": "robot-01",
        },
        source="apps.logistics.tasks.watchdog",
        task_id="task-55",
    )
    await event_bus.wait_until_idle(timeout=1.0)

    alerts = router.list_alerts()
    assert len(alerts) == 1
    assert alerts[0].event_type == "watchdog_stale_robot"
    assert alerts[0].robot_id == "robot-01"
    assert alerts[0].task_id == "task-55"

    await router.stop()
    await event_bus.stop()


@pytest.mark.asyncio
async def test_battery_critical_event_emits_alert(alerts_module) -> None:
    from shared.core.event_bus import EventBus, EventName

    event_bus = EventBus()
    router = alerts_module.AlertRouter(max_alerts=10, event_bus=event_bus)
    await event_bus.start()
    await router.start()

    await event_bus.publish(
        EventName.BATTERY_CRITICAL,
        {"robot_id": "robot-02", "battery_pct": 9},
        source="shared.quadruped.state_monitor",
    )
    await event_bus.wait_until_idle(timeout=1.0)

    alerts = router.list_alerts()
    assert len(alerts) == 1
    assert alerts[0].source == "battery"
    assert alerts[0].event_type == "battery_critical"
    assert alerts[0].metadata["battery_pct"] == 9

    await router.stop()
    await event_bus.stop()

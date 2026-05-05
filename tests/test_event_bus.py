from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import AppConfig
from core.logger import setup_logging


def make_logger_config(tmp_path: Path) -> AppConfig:
    config = AppConfig()
    config.logging.log_dir = str(tmp_path / "logs")
    config.logging.rotating_file_enabled = False
    config.logging.json_output = True
    config.logging.level = "INFO"
    return config


def reset_owned_handlers() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_platform_logger_handler", False):
            root_logger.removeHandler(handler)
            handler.close()


@pytest_asyncio.fixture
async def bus():
    from core.event_bus import EventBus

    event_bus = EventBus()
    yield event_bus
    await event_bus.stop()


@pytest.fixture(autouse=True)
def reset_logging(tmp_path: Path):
    reset_owned_handlers()
    setup_logging(make_logger_config(tmp_path))
    yield
    reset_owned_handlers()


@pytest.mark.asyncio
async def test_subscribe_and_publish_async_callback(bus) -> None:
    from core.event_bus import EventName

    received = []

    async def callback(event):
        received.append(event.payload["value"])

    await bus.start()
    bus.subscribe(EventName.SYSTEM_STARTED, callback)
    await bus.publish(EventName.SYSTEM_STARTED, {"value": 1})
    await bus.wait_until_idle(timeout=0.5)

    assert received == [1]


@pytest.mark.asyncio
async def test_subscribe_and_publish_sync_callback(bus) -> None:
    from core.event_bus import EventName

    received = []

    def callback(event):
        received.append(event.payload["value"])

    await bus.start()
    bus.subscribe(EventName.SYSTEM_STARTED, callback)
    await bus.publish(EventName.SYSTEM_STARTED, {"value": 2})
    await bus.wait_until_idle(timeout=0.5)

    assert received == [2]


@pytest.mark.asyncio
async def test_unsubscribe_stops_receiving_events(bus) -> None:
    from core.event_bus import EventName

    received = []

    async def callback(event):
        received.append(event.payload["value"])

    await bus.start()
    subscription_id = bus.subscribe(EventName.SYSTEM_STARTED, callback)
    assert bus.unsubscribe(subscription_id) is True

    await bus.publish(EventName.SYSTEM_STARTED, {"value": 3})
    await bus.wait_until_idle(timeout=0.5)

    assert received == []


@pytest.mark.asyncio
async def test_unknown_event_name_rejected(bus) -> None:
    def callback(event):
        return None

    with pytest.raises(ValueError):
        bus.subscribe("not.real", callback)

    with pytest.raises(ValueError):
        await bus.publish("not.real", {})


@pytest.mark.asyncio
async def test_publish_nowait_rejects_when_queue_full() -> None:
    from core.event_bus import EventBus, EventName

    small_bus = EventBus(max_queue_size=1)
    try:
        small_bus.publish_nowait(EventName.SYSTEM_STARTED, {"value": 1})
        with pytest.raises(asyncio.QueueFull):
            small_bus.publish_nowait(EventName.SYSTEM_STOPPING, {"value": 2})
    finally:
        await small_bus.stop()


@pytest.mark.asyncio
async def test_wildcard_subscriber_receives_all_events(bus) -> None:
    from core.event_bus import EventName

    received = []

    async def callback(event):
        received.append(event.name)

    await bus.start()
    bus.subscribe("*", callback)
    await bus.publish(EventName.SYSTEM_STARTED)
    await bus.publish(EventName.TASK_SUBMITTED)
    await bus.wait_until_idle(timeout=0.5)

    assert received == [EventName.SYSTEM_STARTED, EventName.TASK_SUBMITTED]


@pytest.mark.asyncio
async def test_callback_exception_does_not_stop_other_callbacks(bus) -> None:
    from core.event_bus import EventName

    received = []

    def failing_callback(event):
        raise RuntimeError("boom")

    async def succeeding_callback(event):
        received.append(event.payload["value"])

    await bus.start()
    bus.subscribe(EventName.SYSTEM_ALERT, failing_callback, subscriber_name="bad")
    bus.subscribe(EventName.SYSTEM_ALERT, succeeding_callback, subscriber_name="good")
    await bus.publish(EventName.SYSTEM_ALERT, {"value": 9})
    await bus.wait_until_idle(timeout=0.5)

    assert received == [9]


@pytest.mark.asyncio
async def test_subscriber_count(bus) -> None:
    from core.event_bus import EventName

    async def callback(event):
        return None

    bus.subscribe(EventName.SYSTEM_STARTED, callback)
    bus.subscribe("*", callback)

    assert bus.subscriber_count() == 2
    assert bus.subscriber_count(EventName.SYSTEM_STARTED) == 2
    assert bus.subscriber_count(EventName.TASK_SUBMITTED) == 1


@pytest.mark.asyncio
async def test_publish_before_start_dispatches_after_start(bus) -> None:
    from core.event_bus import EventName

    received = []

    async def callback(event):
        received.append(event.payload["value"])

    bus.subscribe(EventName.SYSTEM_STARTED, callback)
    await bus.publish(EventName.SYSTEM_STARTED, {"value": 5})

    assert received == []

    await bus.start()
    await bus.wait_until_idle(timeout=0.5)

    assert received == [5]


@pytest.mark.asyncio
async def test_wait_until_idle_processes_queue(bus) -> None:
    from core.event_bus import EventName

    received = []

    async def callback(event):
        await asyncio.sleep(0.01)
        received.append(event.payload["value"])

    await bus.start()
    bus.subscribe(EventName.TASK_SUBMITTED, callback)
    await bus.publish(EventName.TASK_SUBMITTED, {"value": 1})
    await bus.publish(EventName.TASK_SUBMITTED, {"value": 2})
    await bus.wait_until_idle(timeout=0.5)

    assert received == [1, 2]


@pytest.mark.asyncio
async def test_stop_is_idempotent(bus) -> None:
    await bus.start()
    await bus.stop()
    await bus.stop()


@pytest.mark.asyncio
async def test_start_is_idempotent(bus) -> None:
    await bus.start()
    first_task = bus._dispatcher_task
    await bus.start()

    assert bus._dispatcher_task is first_task


@pytest.mark.asyncio
async def test_event_contains_event_id_timestamp_payload_source_task_id_correlation_id(bus) -> None:
    from core.event_bus import EventName

    event = await bus.publish(
        EventName.TASK_SUBMITTED,
        {"station_id": "A"},
        source="tests",
        task_id="task-123",
        correlation_id="corr-7",
    )

    assert isinstance(event.event_id, str)
    assert event.timestamp.tzinfo is not None
    assert event.payload == {"station_id": "A"}
    assert event.source == "tests"
    assert event.task_id == "task-123"
    assert event.correlation_id == "corr-7"


def test_patrol_event_names_are_available() -> None:
    from core.event_bus import EventName

    assert EventName.PATROL_CYCLE_STARTED.value == "patrol.cycle.started"
    assert EventName.PATROL_CYCLE_COMPLETED.value == "patrol.cycle.completed"
    assert EventName.PATROL_CYCLE_FAILED.value == "patrol.cycle.failed"
    assert EventName.PATROL_WAYPOINT_OBSERVED.value == "patrol.waypoint.observed"
    assert EventName.PATROL_ANOMALY_DETECTED.value == "patrol.anomaly.detected"
    assert EventName.PATROL_ANOMALY_CLEARED.value == "patrol.anomaly.cleared"
    assert EventName.PATROL_SUSPENDED.value == "patrol.suspended"
    assert EventName.PATROL_RESUMED.value == "patrol.resumed"


@pytest.mark.asyncio
async def test_runtime_context_is_set_during_callback(
    bus
) -> None:
    from core.event_bus import EventName
    from core.logger import _get_runtime_context

    observed_context = {}

    def callback(event):
        observed_context.update(_get_runtime_context())

    await bus.start()
    bus.subscribe(EventName.NAVIGATION_STARTED, callback)
    await bus.publish(
        EventName.NAVIGATION_STARTED,
        {"quadruped_state": "walking"},
        task_id="task-77",
    )
    await bus.wait_until_idle(timeout=0.5)

    assert observed_context["task_id"] == "task-77"
    assert observed_context["quadruped_state"] == "walking"


@pytest.mark.asyncio
async def test_payload_defaults_to_empty_dict(bus) -> None:
    from core.event_bus import EventName

    event = await bus.publish(EventName.SYSTEM_STARTED)

    assert event.payload == {}


@pytest.mark.asyncio
async def test_publish_rejects_wildcard_event_name(bus) -> None:
    with pytest.raises(ValueError):
        await bus.publish("*", {})

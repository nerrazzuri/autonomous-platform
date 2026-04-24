from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeTaskQueue:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_with: Exception | None = None

    async def submit_task(self, **kwargs):
        if self.fail_with is not None:
            raise self.fail_with
        self.calls.append(dict(kwargs))
        return {"id": kwargs.get("task_id", "task-1")}


@pytest.fixture
def mes_module():
    sys.modules.pop("hardware.mes_bridge", None)
    module = importlib.import_module("hardware.mes_bridge")
    return module


def test_mes_event_to_dict(mes_module) -> None:
    event = mes_module.MESEvent(
        event_id="MES-001",
        station_id="A",
        destination_id="QA",
        batch_id="BATCH-123",
        priority=1,
        timestamp=datetime(2026, 4, 24, 16, 0, tzinfo=timezone.utc),
        raw_payload={"station_id": "A"},
    )

    assert event.to_dict() == {
        "event_id": "MES-001",
        "station_id": "A",
        "destination_id": "QA",
        "batch_id": "BATCH-123",
        "priority": 1,
        "timestamp": "2026-04-24T16:00:00+00:00",
        "raw_payload": {"station_id": "A"},
    }


def test_mes_event_rejects_invalid_values(mes_module) -> None:
    with pytest.raises(mes_module.MESBridgeError, match="event_id"):
        mes_module.MESEvent(
            event_id=" ",
            station_id="A",
            destination_id="QA",
            batch_id=None,
            priority=0,
            timestamp=datetime.now(timezone.utc),
            raw_payload={},
        )

    with pytest.raises(mes_module.MESBridgeError, match="station_id"):
        mes_module.MESEvent(
            event_id="MES-001",
            station_id=" ",
            destination_id="QA",
            batch_id=None,
            priority=0,
            timestamp=datetime.now(timezone.utc),
            raw_payload={},
        )

    with pytest.raises(mes_module.MESBridgeError, match="priority"):
        mes_module.MESEvent(
            event_id="MES-001",
            station_id="A",
            destination_id="QA",
            batch_id=None,
            priority=-1,
            timestamp=datetime.now(timezone.utc),
            raw_payload={},
        )


@pytest.mark.asyncio
async def test_start_stop_listener_idempotent(mes_module) -> None:
    bridge = mes_module.MESBridge(task_queue=FakeTaskQueue())

    await bridge.start_listener()
    await bridge.start_listener()
    assert bridge.is_running() is True

    await bridge.stop_listener()
    await bridge.stop_listener()
    assert bridge.is_running() is False


@pytest.mark.asyncio
async def test_start_listener_does_not_bind_network(mes_module, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = mes_module.MESBridge(task_queue=FakeTaskQueue())

    await bridge.start_listener()

    assert bridge.is_running() is True
    assert "socket" not in mes_module.__dict__


@pytest.mark.asyncio
async def test_submit_mes_event_submits_task(mes_module) -> None:
    queue = FakeTaskQueue()
    bridge = mes_module.MESBridge(task_queue=queue)

    event = await bridge.submit_mes_event(
        {
            "event_id": "MES-001",
            "station_id": "A",
            "destination_id": "QA",
            "batch_id": "BATCH-123",
            "priority": 1,
        }
    )

    assert event.event_id == "MES-001"
    assert queue.calls == [
        {
            "station_id": "A",
            "destination_id": "QA",
            "batch_id": "BATCH-123",
            "priority": 1,
            "notes": "Submitted by MES bridge",
        }
    ]


@pytest.mark.asyncio
async def test_submit_mes_event_generates_event_id_if_missing(mes_module) -> None:
    queue = FakeTaskQueue()
    bridge = mes_module.MESBridge(task_queue=queue)

    event = await bridge.submit_mes_event({"station_id": "A"})

    assert isinstance(event.event_id, str)
    assert event.event_id != ""


@pytest.mark.asyncio
async def test_submit_mes_event_defaults_destination_and_priority(mes_module) -> None:
    queue = FakeTaskQueue()
    bridge = mes_module.MESBridge(task_queue=queue)

    event = await bridge.submit_mes_event({"event_id": "MES-002", "station_id": "B"})

    assert event.destination_id == "QA"
    assert event.priority == 0
    assert queue.calls[0]["destination_id"] == "QA"
    assert queue.calls[0]["priority"] == 0


@pytest.mark.asyncio
async def test_submit_mes_event_invalid_payload_raises(mes_module) -> None:
    bridge = mes_module.MESBridge(task_queue=FakeTaskQueue())

    with pytest.raises(mes_module.MESBridgeError, match="payload"):
        await bridge.submit_mes_event("not-a-dict")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_task_queue_failure_raises_mes_bridge_error(mes_module) -> None:
    queue = FakeTaskQueue()
    queue.fail_with = RuntimeError("queue unavailable")
    bridge = mes_module.MESBridge(task_queue=queue)

    with pytest.raises(mes_module.MESBridgeError, match="queue unavailable"):
        await bridge.submit_mes_event({"event_id": "MES-003", "station_id": "A"})

    assert bridge.last_error() == "queue unavailable"


@pytest.mark.asyncio
async def test_submitted_count(mes_module) -> None:
    queue = FakeTaskQueue()
    bridge = mes_module.MESBridge(task_queue=queue)

    await bridge.submit_mes_event({"event_id": "MES-001", "station_id": "A"})
    await bridge.submit_mes_event({"event_id": "MES-002", "station_id": "B"})

    assert bridge.submitted_count() == 2


@pytest.mark.asyncio
async def test_enabled_true_still_safe_stub(mes_module, monkeypatch: pytest.MonkeyPatch) -> None:
    bridge = mes_module.MESBridge(task_queue=FakeTaskQueue(), enabled=True)

    await bridge.start_listener()

    assert bridge.is_enabled() is True
    assert bridge.is_running() is True
    assert bridge.last_error() is None
    assert "socket" not in mes_module.__dict__


def test_global_get_mes_bridge_returns_bridge(mes_module) -> None:
    assert mes_module.get_mes_bridge() is mes_module.mes_bridge

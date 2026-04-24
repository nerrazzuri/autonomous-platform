from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def gpio_module(monkeypatch: pytest.MonkeyPatch):
    sys.modules.pop("hardware.gpio_relay", None)
    sys.modules.pop("hardware", None)
    module = importlib.import_module("hardware.gpio_relay")
    return module


@pytest.mark.asyncio
async def test_trigger_alert_records_event(gpio_module) -> None:
    relay = gpio_module.GPIORelay()

    event = await relay.trigger_alert("A", level="critical", metadata={"source": "test"})

    assert event.station_id == "A"
    assert event.level == "critical"
    assert event.action == "trigger"
    assert event.metadata == {"source": "test"}
    assert (await relay.list_events()) == [event]


@pytest.mark.asyncio
async def test_clear_alert_records_event(gpio_module) -> None:
    relay = gpio_module.GPIORelay()

    event = await relay.clear_alert("A", metadata={"reason": "reset"})

    assert event.station_id == "A"
    assert event.level == "info"
    assert event.action == "clear"
    assert event.metadata == {"reason": "reset"}


@pytest.mark.asyncio
async def test_invalid_station_rejected(gpio_module) -> None:
    relay = gpio_module.GPIORelay()

    with pytest.raises(gpio_module.GPIORelayError, match="station_id"):
        await relay.trigger_alert("   ")


@pytest.mark.asyncio
async def test_invalid_level_rejected(gpio_module) -> None:
    relay = gpio_module.GPIORelay()

    with pytest.raises(gpio_module.GPIORelayError, match="level"):
        await relay.trigger_alert("A", level="alarm")


def test_invalid_action_rejected(gpio_module) -> None:
    with pytest.raises(gpio_module.GPIORelayError, match="action"):
        gpio_module.RelayEvent(
            station_id="A",
            level="warning",
            action="blink",
            timestamp=datetime.now(timezone.utc),
            metadata={},
        )


@pytest.mark.asyncio
async def test_get_last_event(gpio_module) -> None:
    relay = gpio_module.GPIORelay()

    first = await relay.trigger_alert("A")
    second = await relay.clear_alert("A")

    assert await relay.get_last_event("A") == second
    assert first != second


@pytest.mark.asyncio
async def test_list_events(gpio_module) -> None:
    relay = gpio_module.GPIORelay()

    first = await relay.trigger_alert("A", metadata={"index": 1})
    second = await relay.trigger_alert("B", level="critical", metadata={"index": 2})

    assert await relay.list_events() == [first, second]


def test_is_enabled(gpio_module, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        gpio_module,
        "get_config",
        lambda: SimpleNamespace(alerts=SimpleNamespace(gpio_alert_enabled=True)),
    )

    relay = gpio_module.GPIORelay()

    assert relay.is_enabled() is True


def test_global_get_gpio_relay_returns_relay(gpio_module) -> None:
    assert gpio_module.get_gpio_relay() is gpio_module.gpio_relay

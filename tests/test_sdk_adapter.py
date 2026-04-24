from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeSDK:
    def __init__(self):
        self.calls = []
        self.connected = True
        self.position = (1.0, 2.0, 3.0)
        self.rpy = (0.1, 0.2, 0.3)
        self.battery = 88
        self.control_mode = 7
        self.raise_position = False
        self.raise_rpy = False
        self.raise_battery = False

    def initRobot(self, local_ip, port, quadruped_ip):
        self.calls.append(("initRobot", local_ip, port, quadruped_ip))
        return self.connected

    def passive(self):
        self.calls.append(("passive",))
        return True

    def standUp(self):
        self.calls.append(("standUp",))
        return True

    def lieDown(self):
        self.calls.append(("lieDown",))
        return True

    def move(self, vx, vy, yaw_rate):
        self.calls.append(("move", vx, vy, yaw_rate))
        return True

    def getPosition(self):
        self.calls.append(("getPosition",))
        if self.raise_position:
            raise RuntimeError("position failed")
        return self.position

    def getRPY(self):
        self.calls.append(("getRPY",))
        if self.raise_rpy:
            raise RuntimeError("rpy failed")
        return self.rpy

    def getBattery(self):
        self.calls.append(("getBattery",))
        if self.raise_battery:
            raise RuntimeError("battery failed")
        return self.battery

    def getControlMode(self):
        self.calls.append(("getControlMode",))
        return self.control_mode

    def checkConnect(self):
        self.calls.append(("checkConnect",))
        return self.connected


@pytest.mark.asyncio
async def test_adapter_uses_injected_sdk_client() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)

    assert adapter._sdk_client is fake_sdk


@pytest.mark.asyncio
async def test_connect_success_sets_connected_or_passive_mode() -> None:
    from quadruped.sdk_adapter import QuadrupedMode, SDKAdapter

    adapter = SDKAdapter(sdk_client=FakeSDK())

    connected = await adapter.connect()

    assert connected is True
    assert adapter.current_mode() in {QuadrupedMode.CONNECTED, QuadrupedMode.PASSIVE}


@pytest.mark.asyncio
async def test_connect_failure_returns_false_and_sets_error() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    fake_sdk.connected = False
    adapter = SDKAdapter(sdk_client=fake_sdk)

    connected = await adapter.connect()

    assert connected is False
    assert adapter.last_error() is not None


@pytest.mark.asyncio
async def test_stand_up_requires_connection_or_passive() -> None:
    from quadruped.sdk_adapter import QuadrupedMode, SDKAdapter

    adapter = SDKAdapter(sdk_client=FakeSDK())

    assert await adapter.stand_up() is False

    await adapter.connect()
    stood_up = await adapter.stand_up()

    assert stood_up is True
    assert adapter.current_mode() == QuadrupedMode.STANDING


@pytest.mark.asyncio
async def test_passive_sets_passive_mode() -> None:
    from quadruped.sdk_adapter import QuadrupedMode, SDKAdapter

    adapter = SDKAdapter(sdk_client=FakeSDK())
    await adapter.connect()
    await adapter.stand_up()

    result = await adapter.passive()

    assert result is True
    assert adapter.current_mode() == QuadrupedMode.PASSIVE


@pytest.mark.asyncio
async def test_move_rejected_when_disconnected() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)

    moved = await adapter.move(0.1, 0.0, 0.0)

    assert moved is False
    assert all(call[0] != "move" for call in fake_sdk.calls)


@pytest.mark.asyncio
async def test_move_allowed_when_standing() -> None:
    from quadruped.sdk_adapter import QuadrupedMode, SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)
    await adapter.connect()
    await adapter.stand_up()

    moved = await adapter.move(0.1, 0.0, 0.2)

    assert moved is True
    assert adapter.current_mode() == QuadrupedMode.MOVING
    assert ("move", 0.1, 0.0, 0.2) in fake_sdk.calls


@pytest.mark.asyncio
async def test_move_clamps_velocity() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)
    await adapter.connect()
    await adapter.stand_up()

    moved = await adapter.move(99.0, -99.0, 99.0)

    assert moved is True
    assert ("move", 0.35, -0.35, 0.6) in fake_sdk.calls


@pytest.mark.asyncio
async def test_move_rejects_nan_or_infinite() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)
    await adapter.connect()
    await adapter.stand_up()

    assert await adapter.move(math.nan, 0.0, 0.0) is False
    assert await adapter.move(0.0, math.inf, 0.0) is False
    assert await adapter.move(0.0, 0.0, -math.inf) is False
    assert [call for call in fake_sdk.calls if call[0] == "move"] == []


@pytest.mark.asyncio
async def test_stop_motion_sends_zero_velocity() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)
    await adapter.connect()
    await adapter.stand_up()

    stopped = await adapter.stop_motion()

    assert stopped is True
    assert fake_sdk.calls[-1] == ("move", 0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_get_position_safe_default_on_failure() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    fake_sdk.raise_position = True
    adapter = SDKAdapter(sdk_client=fake_sdk)

    position = await adapter.get_position()

    assert position == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_get_rpy_safe_default_on_failure() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    fake_sdk.raise_rpy = True
    adapter = SDKAdapter(sdk_client=fake_sdk)

    rpy = await adapter.get_rpy()

    assert rpy == (0.0, 0.0, 0.0)


@pytest.mark.asyncio
async def test_get_battery_safe_default_on_failure() -> None:
    from quadruped.sdk_adapter import SDKAdapter

    fake_sdk = FakeSDK()
    fake_sdk.raise_battery = True
    adapter = SDKAdapter(sdk_client=fake_sdk)

    battery = await adapter.get_battery()

    assert battery == 0


@pytest.mark.asyncio
async def test_check_connection_false_sets_error_mode() -> None:
    from quadruped.sdk_adapter import QuadrupedMode, SDKAdapter

    fake_sdk = FakeSDK()
    adapter = SDKAdapter(sdk_client=fake_sdk)
    await adapter.connect()
    fake_sdk.connected = False

    connection_ok = await adapter.check_connection()

    assert connection_ok is False
    assert adapter.current_mode() == QuadrupedMode.ERROR


@pytest.mark.asyncio
async def test_get_telemetry_snapshot_returns_dataclass() -> None:
    from quadruped.sdk_adapter import QuadrupedMode, QuadrupedTelemetrySnapshot, SDKAdapter

    adapter = SDKAdapter(sdk_client=FakeSDK())
    await adapter.connect()
    await adapter.stand_up()

    snapshot = await adapter.get_telemetry_snapshot()

    assert isinstance(snapshot, QuadrupedTelemetrySnapshot)
    assert snapshot.battery_pct == 88
    assert snapshot.position == (1.0, 2.0, 3.0)
    assert snapshot.rpy == (0.1, 0.2, 0.3)
    assert snapshot.control_mode == 7
    assert snapshot.connection_ok is True
    assert snapshot.mode == QuadrupedMode.STANDING


def test_null_sdk_client_available_when_vendor_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import quadruped.sdk_adapter as sdk_module

    def fake_import(name: str):
        raise ImportError("missing sdk")

    monkeypatch.setattr(sdk_module.importlib, "import_module", fake_import)

    adapter = sdk_module.SDKAdapter(sdk_client=None, allow_mock=True)

    assert adapter._sdk_client.__class__.__name__ == "_NullSDKClient"


def test_allow_mock_false_raises_when_sdk_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import quadruped.sdk_adapter as sdk_module

    def fake_import(name: str):
        raise ImportError("missing sdk")

    monkeypatch.setattr(sdk_module.importlib, "import_module", fake_import)

    with pytest.raises(sdk_module.SDKUnavailableError):
        sdk_module.SDKAdapter(sdk_client=None, allow_mock=False)


def test_global_get_sdk_adapter_returns_adapter() -> None:
    from quadruped.sdk_adapter import SDKAdapter, get_sdk_adapter

    assert isinstance(get_sdk_adapter(), SDKAdapter)

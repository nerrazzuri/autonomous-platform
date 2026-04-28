from __future__ import annotations

import logging
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class _AsyncNoOp:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _DatabaseStub:
    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _RouteStoreStub:
    async def load(self) -> None:
        return None


class _EventBusStub:
    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _SDKInstance:
    def __init__(self, quadruped_ip: str) -> None:
        self.quadruped_ip = quadruped_ip

    async def connect(self) -> bool:
        return True

    async def stand_up(self) -> bool:
        return True

    async def disconnect(self) -> None:
        return None


class _SDKFactory:
    def __call__(self, *, quadruped_ip: str, local_ip: str, sdk_port: int, sdk_lib_path: str | None = None) -> _SDKInstance:
        return _SDKInstance(quadruped_ip)


class _HeartbeatInstance:
    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _HeartbeatFactory:
    def __call__(self, *, sdk_adapter, robot_id: str) -> _HeartbeatInstance:
        return _HeartbeatInstance(robot_id)


class _StateMonitorInstance:
    def __init__(self, robot_id: str) -> None:
        self.robot_id = robot_id

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None


class _StateMonitorFactory:
    def __call__(self, *, sdk_adapter, database, robot_id: str) -> _StateMonitorInstance:
        return _StateMonitorInstance(robot_id)


class _NavigatorFactory:
    def __call__(self, *, sdk_adapter, robot_id: str, route_store, state_monitor, heartbeat):
        return SimpleNamespace(robot_id=robot_id)


class _EmptyRegistry:
    def get(self, robot_id: str):
        from shared.quadruped.robot_registry import RobotNotFoundError

        raise RobotNotFoundError(robot_id)

    def all(self):
        return []


def test_redact_sensitive_masks_sensitive_keys() -> None:
    from shared.core.logger import MASKED_VALUE, redact_sensitive

    payload = {
        "password": "secret-password",
        "nested": {
            "api_token": "token-value",
            "ssh_secret": "ssh-secret",
            "plain": "ok",
        },
        "Authorization": "Bearer abc123",
        "robot_id": "logistics_01",
    }

    redacted = redact_sensitive(payload)

    assert redacted["password"] == MASKED_VALUE
    assert redacted["nested"]["api_token"] == MASKED_VALUE
    assert redacted["nested"]["ssh_secret"] == MASKED_VALUE
    assert redacted["nested"]["plain"] == "ok"
    assert redacted["Authorization"] == MASKED_VALUE
    assert redact_sensitive("Bearer abc123") == MASKED_VALUE


def test_get_logger_returns_usable_logger() -> None:
    from shared.core.logger import get_logger

    logger = get_logger("tests.logging")

    assert isinstance(logger, logging.Logger)
    assert logger.name == "tests.logging"


@pytest.mark.asyncio
async def test_startup_logs_include_robot_id_for_robot_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from shared.core.config import AppConfig
    from shared.runtime import base_startup as startup_module

    robots_yaml_path = tmp_path / "robots.yaml"
    robots_yaml_path.write_text(
        """
robots:
  - robot_id: logistics_01
    quadruped_ip: 192.168.1.51
    role: logistics
    enabled: true
""".strip()
        + "\n",
        encoding="utf-8",
    )

    config = AppConfig()
    config.quadruped.auto_stand_on_startup = False

    monkeypatch.setattr(startup_module, "setup_logging", lambda: None)
    monkeypatch.setattr(startup_module, "get_config", lambda: config)
    monkeypatch.setattr(startup_module, "_resolve_robot_config_path", lambda _config: robots_yaml_path)
    monkeypatch.setattr(startup_module, "get_database", lambda: _DatabaseStub())
    monkeypatch.setattr(startup_module, "get_route_store", lambda: _RouteStoreStub())
    monkeypatch.setattr(startup_module, "get_event_bus", lambda: _EventBusStub())
    monkeypatch.setattr(startup_module, "get_obstacle_detector", lambda: _AsyncNoOp())
    monkeypatch.setattr(startup_module, "SDKAdapter", _SDKFactory())
    monkeypatch.setattr(startup_module, "HeartbeatController", _HeartbeatFactory())
    monkeypatch.setattr(startup_module, "StateMonitor", _StateMonitorFactory())
    monkeypatch.setattr(startup_module, "Navigator", _NavigatorFactory())

    caplog.set_level(logging.INFO)
    await startup_module.startup_system()
    await startup_module.shutdown_system()

    assert any(
        record.__dict__.get("robot_id") == "logistics_01"
        and "platform created" in record.getMessage().lower()
        for record in caplog.records
    )
    assert any(
        record.__dict__.get("robot_id") == "logistics_01"
        and "heartbeat" in record.getMessage().lower()
        for record in caplog.records
    )


def test_provisioning_logs_do_not_include_wifi_password(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from shared.provisioning import provision_backend
    from shared.provisioning.provision_models import ProvisionRequest

    class FakeClient:
        def close(self) -> None:
            return None

    monkeypatch.setattr(provision_backend, "ssh_connect", lambda *args, **kwargs: FakeClient())
    monkeypatch.setattr(provision_backend, "ensure_remote_dog_script", lambda client: None)
    monkeypatch.setattr(
        provision_backend,
        "_safe_remote_read",
        lambda client, path: "aa:bb:cc:dd:ee:01" if path.endswith("dog_mac") else None,
    )
    monkeypatch.setattr(provision_backend, "find_ip_by_mac", lambda *args, **kwargs: "192.168.1.50")
    monkeypatch.setattr(provision_backend, "get_pc_ip_for_target", lambda target_ip: "192.168.1.10")
    monkeypatch.setattr(provision_backend, "patch_sdk_config", lambda *args, **kwargs: None)

    caplog.set_level(logging.INFO)
    result = provision_backend.provision_dog(
        ProvisionRequest(
            dog_ap_ssid="D1-Ultra:aa:bb:cc:dd:ee",
            target_wifi_ssid="FACTORY_WIFI",
            target_wifi_password="super-secret-password",
            role="logistics",
            robot_id="logistics_01",
            pc_wifi_iface="wlan0",
        )
    )

    assert result.success is True
    assert "super-secret-password" not in caplog.text
    assert any("provision" in record.getMessage().lower() for record in caplog.records)


def test_dispatcher_unknown_robot_log_includes_robot_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from apps.logistics.tasks.dispatcher import Dispatcher

    dispatcher = Dispatcher(
        task_queue=SimpleNamespace(),
        navigator=SimpleNamespace(),
        state_monitor=SimpleNamespace(),
        robot_registry=_EmptyRegistry(),
    )

    caplog.set_level(logging.WARNING)
    result = dispatcher._resolve_registered_dispatch_target("robot_999")

    assert result is None
    assert any(
        record.__dict__.get("robot_id") == "robot_999"
        and "unknown robot" in record.getMessage().lower()
        for record in caplog.records
    )

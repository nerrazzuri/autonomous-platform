from __future__ import annotations

"""Shared platform startup wiring with no app-specific dependencies."""

import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import yaml

from shared.core.config import get_config
from shared.core.database import get_database
from shared.core.event_bus import get_event_bus
from shared.core.logger import get_logger, setup_logging
from shared.core.robot_config import RobotConfig, RobotConfigError, RobotConfigLoader
from shared.navigation.obstacle import get_obstacle_detector
from shared.navigation.navigator import Navigator
from shared.navigation.route_store import get_route_store
from shared.quadruped.heartbeat import HeartbeatController, get_heartbeat_controller
from shared.quadruped.robot_platform import RobotPlatform
from shared.quadruped.robot_registry import get_robot_registry
from shared.quadruped.sdk_adapter import SDKAdapter, get_sdk_adapter
from shared.quadruped.state_monitor import StateMonitor, get_state_monitor

import shared.navigation.navigator as navigator_module
import shared.quadruped.heartbeat as heartbeat_module
import shared.quadruped.sdk_adapter as sdk_adapter_module
import shared.quadruped.state_monitor as state_monitor_module


logger = get_logger(__name__)

_DEFAULT_SINGLETONS = {
    "sdk_adapter": sdk_adapter_module.sdk_adapter,
    "heartbeat_controller": heartbeat_module.heartbeat_controller,
    "state_monitor": state_monitor_module.state_monitor,
    "navigator": navigator_module.navigator,
}


async def _run_shutdown_steps(steps: list[tuple[str, Callable[[], Awaitable[None]]]]) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    for name, stop_callable in steps:
        try:
            await stop_callable()
        except Exception as exc:
            errors.append((name, str(exc)))
            logger.exception("Shutdown step failed", extra={"component": name})
    return errors


def _resolve_robot_config_path(config: Any) -> Path:
    configured_path = (
        getattr(config, "robots_file", None)
        or getattr(getattr(config, "quadruped", None), "robots_file", None)
        or getattr(getattr(config, "app", None), "robots_file", None)
    )
    return Path(configured_path) if configured_path else Path("data/robots.yaml")


def _entry_is_enabled(entry: dict[str, Any]) -> bool:
    enabled = entry.get("enabled", True)
    return bool(enabled)


def _load_enabled_robot_configs(config: Any) -> list[RobotConfig]:
    config_path = _resolve_robot_config_path(config)
    if not config_path.exists():
        return []

    try:
        with config_path.open("r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise RobotConfigError(f"Failed to parse robot config file '{config_path}': {exc}") from exc
    except OSError as exc:
        raise RobotConfigError(f"Failed to read robot config file '{config_path}': {exc}") from exc

    if raw is None:
        return []
    if isinstance(raw, dict):
        entries = raw.get("robots")
        if entries is None:
            raise RobotConfigError(f"Robot config file '{config_path}' must contain a top-level list or 'robots' list")
    elif isinstance(raw, list):
        entries = raw
    else:
        raise RobotConfigError(f"Robot config file '{config_path}' must contain a top-level list or 'robots' list")

    if not isinstance(entries, list):
        raise RobotConfigError(f"Robot config file '{config_path}' must contain a 'robots' list")

    enabled_entries = [entry for entry in entries if isinstance(entry, dict) and _entry_is_enabled(entry)]
    if not enabled_entries:
        return []

    loader = RobotConfigLoader(config_path)
    robot_configs: list[RobotConfig] = []
    seen_robot_ids: set[str] = set()
    for index, entry in enumerate(enabled_entries, start=1):
        robot_configs.append(loader._build_robot_config(entry, index, seen_robot_ids))
    return robot_configs


def _activate_primary_platform(platform: RobotPlatform) -> None:
    sdk_adapter_module.sdk_adapter = platform.sdk_adapter
    heartbeat_module.heartbeat_controller = platform.heartbeat
    state_monitor_module.state_monitor = platform.state_monitor
    navigator_module.navigator = platform.navigator
    _retarget_existing_singleton_consumers(platform.navigator, platform.state_monitor)


def _restore_default_component_singletons() -> None:
    sdk_adapter_module.sdk_adapter = _DEFAULT_SINGLETONS["sdk_adapter"]
    heartbeat_module.heartbeat_controller = _DEFAULT_SINGLETONS["heartbeat_controller"]
    state_monitor_module.state_monitor = _DEFAULT_SINGLETONS["state_monitor"]
    navigator_module.navigator = _DEFAULT_SINGLETONS["navigator"]
    _retarget_existing_singleton_consumers(
        _DEFAULT_SINGLETONS["navigator"],
        _DEFAULT_SINGLETONS["state_monitor"],
    )


def _retarget_existing_singleton_consumers(navigator: Any, state_monitor: Any) -> None:
    dispatcher_module = sys.modules.get("apps.logistics.tasks.dispatcher")
    if dispatcher_module is not None and hasattr(dispatcher_module, "dispatcher"):
        dispatcher = dispatcher_module.dispatcher
        if hasattr(dispatcher, "_navigator"):
            dispatcher._navigator = navigator
        if hasattr(dispatcher, "_state_monitor"):
            dispatcher._state_monitor = state_monitor

    battery_manager_module = sys.modules.get("apps.logistics.tasks.battery_manager")
    if battery_manager_module is not None and hasattr(battery_manager_module, "battery_manager"):
        battery_manager = battery_manager_module.battery_manager
        if hasattr(battery_manager, "_state_monitor"):
            battery_manager._state_monitor = state_monitor
        if hasattr(battery_manager, "_dispatcher") and dispatcher_module is not None and hasattr(dispatcher_module, "dispatcher"):
            battery_manager._dispatcher = dispatcher_module.dispatcher

    watchdog_module = sys.modules.get("apps.logistics.tasks.watchdog")
    if watchdog_module is not None and hasattr(watchdog_module, "watchdog"):
        watchdog = watchdog_module.watchdog
        if hasattr(watchdog, "_state_monitor"):
            watchdog._state_monitor = state_monitor
        if hasattr(watchdog, "_dispatcher") and dispatcher_module is not None and hasattr(dispatcher_module, "dispatcher"):
            watchdog._dispatcher = dispatcher_module.dispatcher

    patrol_dispatcher_module = sys.modules.get("apps.patrol.tasks.patrol_dispatcher")
    if patrol_dispatcher_module is not None and hasattr(patrol_dispatcher_module, "patrol_dispatcher"):
        patrol_dispatcher = patrol_dispatcher_module.patrol_dispatcher
        if hasattr(patrol_dispatcher, "_navigator"):
            patrol_dispatcher._navigator = navigator


async def _disconnect_sdk_adapter(adapter: Any) -> None:
    disconnect = getattr(adapter, "disconnect", None)
    if callable(disconnect):
        await disconnect()
        return

    passive = getattr(adapter, "passive", None)
    if callable(passive):
        await passive(reason="shutdown")


async def _shutdown_registered_platforms(
    platforms: list[RobotPlatform],
    *,
    clear_registry: bool,
    restore_defaults: bool,
) -> None:
    for platform in platforms:
        try:
            logger.info("Robot heartbeat stopping", extra={"robot_id": platform.robot_id, "component": "heartbeat"})
            await platform.heartbeat.stop()
            logger.info("Robot heartbeat stopped", extra={"robot_id": platform.robot_id, "component": "heartbeat"})
        except Exception:
            logger.exception("Robot heartbeat shutdown failed", extra={"robot_id": platform.robot_id})
        try:
            logger.info("Robot state monitor stopping", extra={"robot_id": platform.robot_id, "component": "state_monitor"})
            await platform.state_monitor.stop()
            logger.info("Robot state monitor stopped", extra={"robot_id": platform.robot_id, "component": "state_monitor"})
        except Exception:
            logger.exception("Robot state monitor shutdown failed", extra={"robot_id": platform.robot_id})
        try:
            logger.info("Robot SDK disconnecting", extra={"robot_id": platform.robot_id, "component": "sdk_adapter"})
            await _disconnect_sdk_adapter(platform.sdk_adapter)
            logger.info("Robot SDK disconnected", extra={"robot_id": platform.robot_id, "component": "sdk_adapter"})
        except Exception:
            logger.exception("Robot SDK shutdown failed", extra={"robot_id": platform.robot_id})

    registry = get_robot_registry()
    if clear_registry:
        registry.clear()
    if restore_defaults:
        _restore_default_component_singletons()


def _build_robot_platform(config: Any, robot_config: RobotConfig, route_store: Any, database: Any) -> RobotPlatform:
    robot_id = robot_config.connection.robot_id
    sdk_adapter = SDKAdapter(
        quadruped_ip=robot_config.connection.robot_ip,
        local_ip=robot_config.connection.local_ip,
        sdk_port=robot_config.connection.sdk_port,
        sdk_lib_path=getattr(config.quadruped, "sdk_lib_path", None),
    )
    heartbeat = HeartbeatController(sdk_adapter=sdk_adapter, robot_id=robot_id)
    state_monitor = StateMonitor(sdk_adapter=sdk_adapter, database=database, robot_id=robot_id)
    navigator = Navigator(
        sdk_adapter=sdk_adapter,
        robot_id=robot_id,
        route_store=route_store,
        state_monitor=state_monitor,
        heartbeat=heartbeat,
    )
    platform = RobotPlatform(
        robot_id=robot_id,
        config=robot_config,
        sdk_adapter=sdk_adapter,
        heartbeat=heartbeat,
        state_monitor=state_monitor,
        navigator=navigator,
    )
    logger.info(
        "Robot platform created",
        extra={
            "robot_id": robot_id,
            "role": robot_config.role,
            "component": "robot_platform",
            "status": "created",
        },
    )
    return platform


async def _startup_single_robot_system(config: Any, database: Any, route_store: Any, event_bus: Any, obstacle_detector: Any) -> None:
    rollback_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    connected = False
    sdk_adapter = get_sdk_adapter()
    heartbeat_controller = get_heartbeat_controller()
    state_monitor = get_state_monitor()

    try:
        await database.initialize()
        rollback_steps.append(("database", database.close))

        await route_store.load()

        await event_bus.start()
        rollback_steps.append(("event_bus", event_bus.stop))

        logger.info("Single-robot SDK connect attempt", extra={"component": "sdk_adapter", "robot_id": "default"})
        connected = await sdk_adapter.connect()
        if not connected:
            logger.warning("Quadruped startup SDK connect failed")
        else:
            logger.info("Single-robot SDK connect succeeded", extra={"component": "sdk_adapter", "robot_id": "default"})

        logger.info("Single-robot heartbeat starting", extra={"component": "heartbeat", "robot_id": "default"})
        await heartbeat_controller.start()
        logger.info("Single-robot heartbeat started", extra={"component": "heartbeat", "robot_id": "default"})
        rollback_steps.append(("heartbeat_controller", heartbeat_controller.stop))

        logger.info("Single-robot state monitor starting", extra={"component": "state_monitor", "robot_id": "default"})
        await state_monitor.start()
        logger.info("Single-robot state monitor started", extra={"component": "state_monitor", "robot_id": "default"})
        rollback_steps.append(("state_monitor", state_monitor.stop))

        await obstacle_detector.start()
        rollback_steps.append(("obstacle_detector", obstacle_detector.stop))
    except Exception:
        logger.warning("Startup rollback begin", extra={"component": "startup", "status": "rollback_begin"})
        await _run_shutdown_steps(list(reversed(rollback_steps)))
        logger.warning("Startup rollback end", extra={"component": "startup", "status": "rollback_end"})
        raise

    if config.quadruped.auto_stand_on_startup:
        if not connected:
            logger.warning("Quadruped auto-stand startup connect failed")
        else:
            stood_up = await sdk_adapter.stand_up()
            if not stood_up:
                logger.warning("Quadruped auto-stand startup stand_up failed")


async def _startup_multi_robot_system(
    config: Any,
    robot_configs: list[RobotConfig],
    database: Any,
    route_store: Any,
    event_bus: Any,
    obstacle_detector: Any,
) -> None:
    rollback_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    registry = get_robot_registry()
    platforms: list[RobotPlatform] = []
    connected_by_robot_id: dict[str, bool] = {}

    try:
        await database.initialize()
        rollback_steps.append(("database", database.close))

        await route_store.load()

        await event_bus.start()
        rollback_steps.append(("event_bus", event_bus.stop))

        registry.clear()
        _restore_default_component_singletons()
        for robot_config in robot_configs:
            platform = _build_robot_platform(config, robot_config, route_store, database)
            registry.register(platform)
            platforms.append(platform)

        _activate_primary_platform(platforms[0])
        rollback_steps.append(
            (
                "robot_platforms",
                lambda: _shutdown_registered_platforms(list(registry.all()), clear_registry=True, restore_defaults=True),
            )
        )

        for platform in platforms:
            logger.info(
                "Robot SDK connect attempt",
                extra={"robot_id": platform.robot_id, "component": "sdk_adapter", "status": "connect_attempt"},
            )
            connected = await platform.sdk_adapter.connect()
            connected_by_robot_id[platform.robot_id] = connected
            if not connected:
                logger.warning("Quadruped startup SDK connect failed", extra={"robot_id": platform.robot_id})
            else:
                logger.info(
                    "Robot SDK connect succeeded",
                    extra={"robot_id": platform.robot_id, "component": "sdk_adapter", "status": "connected"},
                )

            logger.info("Robot heartbeat starting", extra={"robot_id": platform.robot_id, "component": "heartbeat"})
            await platform.heartbeat.start()
            logger.info("Robot heartbeat started", extra={"robot_id": platform.robot_id, "component": "heartbeat"})
            logger.info("Robot state monitor starting", extra={"robot_id": platform.robot_id, "component": "state_monitor"})
            await platform.state_monitor.start()
            logger.info("Robot state monitor started", extra={"robot_id": platform.robot_id, "component": "state_monitor"})

        await obstacle_detector.start()
        rollback_steps.append(("obstacle_detector", obstacle_detector.stop))
    except Exception:
        logger.warning("Startup rollback begin", extra={"component": "startup", "status": "rollback_begin"})
        await _run_shutdown_steps(list(reversed(rollback_steps)))
        logger.warning("Startup rollback end", extra={"component": "startup", "status": "rollback_end"})
        raise

    if config.quadruped.auto_stand_on_startup:
        for platform in platforms:
            if not connected_by_robot_id.get(platform.robot_id, False):
                logger.warning("Quadruped auto-stand startup connect failed", extra={"robot_id": platform.robot_id})
                continue
            stood_up = await platform.sdk_adapter.stand_up()
            if not stood_up:
                logger.warning("Quadruped auto-stand startup stand_up failed", extra={"robot_id": platform.robot_id})


async def startup_system() -> None:
    setup_logging()

    config = get_config()
    config_path = _resolve_robot_config_path(config)
    logger.info("Shared platform startup begin", extra={"component": "startup", "robots_yaml_path": str(config_path)})
    database = get_database()
    route_store = get_route_store()
    event_bus = get_event_bus()
    obstacle_detector = get_obstacle_detector()
    robot_configs = _load_enabled_robot_configs(config)
    logger.info(
        "Robot config load complete",
        extra={
            "component": "startup",
            "robots_yaml_path": str(config_path),
            "enabled_robot_count": len(robot_configs),
        },
    )

    if robot_configs:
        await _startup_multi_robot_system(config, robot_configs, database, route_store, event_bus, obstacle_detector)
    else:
        await _startup_single_robot_system(config, database, route_store, event_bus, obstacle_detector)

    logger.info("Shared platform startup complete")


async def shutdown_system() -> None:
    logger.info("Shared platform shutdown begin", extra={"component": "shutdown"})
    registry = get_robot_registry()
    platforms = registry.all()
    if platforms:
        shutdown_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
            ("obstacle_detector", get_obstacle_detector().stop),
            (
                "robot_platforms",
                lambda: _shutdown_registered_platforms(platforms, clear_registry=True, restore_defaults=True),
            ),
            ("event_bus", get_event_bus().stop),
            ("database", get_database().close),
        ]
    else:
        shutdown_steps = [
            ("obstacle_detector", get_obstacle_detector().stop),
            ("state_monitor", get_state_monitor().stop),
            ("heartbeat_controller", get_heartbeat_controller().stop),
            ("event_bus", get_event_bus().stop),
            ("database", get_database().close),
        ]

    errors = await _run_shutdown_steps(shutdown_steps)
    logger.info("Shared platform shutdown complete", extra={"error_count": len(errors)})


__all__ = ["shutdown_system", "startup_system"]

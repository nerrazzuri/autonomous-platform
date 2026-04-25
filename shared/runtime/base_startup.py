from __future__ import annotations

"""Shared platform startup wiring with no app-specific dependencies."""

from collections.abc import Awaitable, Callable

from shared.core.config import get_config
from shared.core.database import get_database
from shared.core.event_bus import get_event_bus
from shared.core.logger import get_logger, setup_logging
from shared.navigation.obstacle import get_obstacle_detector
from shared.navigation.route_store import get_route_store
from shared.quadruped.heartbeat import get_heartbeat_controller
from shared.quadruped.sdk_adapter import get_sdk_adapter
from shared.quadruped.state_monitor import get_state_monitor


logger = get_logger(__name__)


async def _run_shutdown_steps(steps: list[tuple[str, Callable[[], Awaitable[None]]]]) -> list[tuple[str, str]]:
    errors: list[tuple[str, str]] = []
    for name, stop_callable in steps:
        try:
            await stop_callable()
        except Exception as exc:
            errors.append((name, str(exc)))
            logger.exception("Shutdown step failed", extra={"component": name})
    return errors


async def startup_system() -> None:
    setup_logging()

    config = get_config()
    database = get_database()
    route_store = get_route_store()
    event_bus = get_event_bus()
    sdk_adapter = get_sdk_adapter()
    heartbeat_controller = get_heartbeat_controller()
    state_monitor = get_state_monitor()
    obstacle_detector = get_obstacle_detector()

    rollback_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    connected = False

    try:
        await database.initialize()
        rollback_steps.append(("database", database.close))

        await route_store.load()

        await event_bus.start()
        rollback_steps.append(("event_bus", event_bus.stop))

        connected = await sdk_adapter.connect()
        if not connected:
            logger.warning("Quadruped startup SDK connect failed")

        await heartbeat_controller.start()
        rollback_steps.append(("heartbeat_controller", heartbeat_controller.stop))

        await state_monitor.start()
        rollback_steps.append(("state_monitor", state_monitor.stop))

        await obstacle_detector.start()
        rollback_steps.append(("obstacle_detector", obstacle_detector.stop))
    except Exception:
        await _run_shutdown_steps(list(reversed(rollback_steps)))
        raise

    if config.quadruped.auto_stand_on_startup:
        if not connected:
            logger.warning("Quadruped auto-stand startup connect failed")
        else:
            stood_up = await sdk_adapter.stand_up()
            if not stood_up:
                logger.warning("Quadruped auto-stand startup stand_up failed")

    logger.info("Shared platform startup complete")


async def shutdown_system() -> None:
    shutdown_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("obstacle_detector", get_obstacle_detector().stop),
        ("state_monitor", get_state_monitor().stop),
        ("heartbeat_controller", get_heartbeat_controller().stop),
        ("event_bus", get_event_bus().stop),
        ("database", get_database().close),
    ]

    errors = await _run_shutdown_steps(shutdown_steps)
    logger.info("Shared platform shutdown complete", extra={"error_count": len(errors)})


__all__ = ["shutdown_system", "startup_system"]

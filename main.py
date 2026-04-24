from __future__ import annotations

"""Application entrypoint for starting the quadruped logistics backend."""

from collections.abc import Awaitable, Callable

from core.config import get_config
from core.database import get_database
from core.event_bus import get_event_bus
from core.logger import get_logger, setup_logging
from navigation.obstacle import get_obstacle_detector
from navigation.route_store import get_route_store
from quadruped.heartbeat import get_heartbeat_controller
from quadruped.sdk_adapter import get_sdk_adapter
from quadruped.state_monitor import get_state_monitor
from tasks.battery_manager import get_battery_manager
from tasks.dispatcher import get_dispatcher
from tasks.watchdog import get_watchdog


logger = get_logger(__name__)


async def startup_system() -> None:
    setup_logging()

    config = get_config()
    database = get_database()
    route_store = get_route_store()
    event_bus = get_event_bus()
    heartbeat_controller = get_heartbeat_controller()
    state_monitor = get_state_monitor()
    obstacle_detector = get_obstacle_detector()
    dispatcher = get_dispatcher()
    battery_manager = get_battery_manager()
    watchdog = get_watchdog()

    await database.initialize()
    await route_store.load()
    await event_bus.start()
    await heartbeat_controller.start()
    await state_monitor.start()
    await obstacle_detector.start()
    await dispatcher.start()
    await battery_manager.start()
    await watchdog.start()

    if config.quadruped.auto_stand_on_startup:
        sdk_adapter = get_sdk_adapter()
        connected = await sdk_adapter.connect()
        if not connected:
            logger.warning("Quadruped auto-stand startup connect failed")
        else:
            stood_up = await sdk_adapter.stand_up()
            if not stood_up:
                logger.warning("Quadruped auto-stand startup stand_up failed")

    logger.info("Backend system startup complete")


async def shutdown_system() -> None:
    shutdown_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("watchdog", get_watchdog().stop),
        ("battery_manager", get_battery_manager().stop),
        ("dispatcher", get_dispatcher().stop),
        ("obstacle_detector", get_obstacle_detector().stop),
        ("state_monitor", get_state_monitor().stop),
        ("heartbeat_controller", get_heartbeat_controller().stop),
        ("event_bus", get_event_bus().stop),
        ("database", get_database().close),
    ]

    errors: list[tuple[str, str]] = []
    for name, stop_callable in shutdown_steps:
        try:
            await stop_callable()
        except Exception as exc:
            errors.append((name, str(exc)))
            logger.exception("Shutdown step failed", extra={"component": name})

    logger.info("Backend system shutdown complete", extra={"error_count": len(errors)})


def create_uvicorn_config() -> dict:
    config = get_config()
    return {
        "app": "api.rest:app",
        "host": config.api.host,
        "port": config.api.port,
        "reload": False,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(**create_uvicorn_config())


if __name__ == "__main__":
    main()


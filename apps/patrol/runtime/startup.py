from __future__ import annotations

"""Patrol runtime startup wiring built on top of the shared platform runtime."""

import importlib
from collections.abc import Awaitable, Callable

from apps.patrol.observation.zone_config import get_zone_config
from apps.patrol.tasks.patrol_dispatcher import get_patrol_dispatcher
from apps.patrol.tasks.patrol_scheduler import get_patrol_scheduler
from apps.patrol.tasks.patrol_watchdog import get_patrol_watchdog
from shared.core.config import get_config
from shared.core.logger import get_logger, setup_logging
from shared.provisioning.roles import register_role
from shared.runtime import base_startup


logger = get_logger(__name__)


def get_patrol_queue():
    queue_module = importlib.import_module("apps.patrol.tasks.patrol_queue")
    getter = getattr(queue_module, "get_patrol_queue", None)
    if callable(getter):
        return getter()
    return queue_module.PatrolQueue()


def _retarget_patrol_singletons(navigator, state_monitor) -> None:
    patrol_dispatcher = get_patrol_dispatcher()
    if hasattr(patrol_dispatcher, "_navigator"):
        patrol_dispatcher._navigator = navigator


base_startup.register_singleton_retarget_hook(_retarget_patrol_singletons)
register_role("patrol")


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

    zone_config = get_zone_config()
    patrol_queue = get_patrol_queue()
    patrol_scheduler = get_patrol_scheduler()
    patrol_dispatcher = get_patrol_dispatcher()
    patrol_watchdog = get_patrol_watchdog()

    await base_startup.startup_system()

    rollback_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    try:
        await zone_config.load()
        await patrol_queue.initialize()

        await patrol_scheduler.start()
        rollback_steps.append(("patrol_scheduler", patrol_scheduler.stop))

        await patrol_dispatcher.start()
        rollback_steps.append(("patrol_dispatcher", patrol_dispatcher.stop))

        await patrol_watchdog.start()
        rollback_steps.append(("patrol_watchdog", patrol_watchdog.stop))
    except Exception:
        await _run_shutdown_steps(list(reversed(rollback_steps)))
        try:
            await base_startup.shutdown_system()
        except Exception:
            logger.exception("Patrol runtime rollback base shutdown failed")
        raise

    logger.info("Patrol runtime startup complete")


async def shutdown_system() -> None:
    shutdown_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("patrol_watchdog", get_patrol_watchdog().stop),
        ("patrol_dispatcher", get_patrol_dispatcher().stop),
        ("patrol_scheduler", get_patrol_scheduler().stop),
    ]

    errors = await _run_shutdown_steps(shutdown_steps)
    try:
        await base_startup.shutdown_system()
    except Exception as exc:
        errors.append(("base_startup", str(exc)))
        logger.exception("Patrol runtime base shutdown failed")

    logger.info("Patrol runtime shutdown complete", extra={"error_count": len(errors)})


def create_uvicorn_config() -> dict:
    config = get_config()
    return {
        "app": "apps.patrol.api.rest:app",
        "host": config.api.host,
        "port": getattr(config.api, "patrol_port", 8081),
        "reload": False,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(**create_uvicorn_config())


__all__ = ["create_uvicorn_config", "main", "shutdown_system", "startup_system"]

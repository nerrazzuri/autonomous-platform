from __future__ import annotations

"""Logistics runtime startup wiring built on top of the shared platform runtime."""

from collections.abc import Awaitable, Callable

from apps.logistics import events as logistics_events
from apps.logistics.observability import (
    register_logistics_alert_rules,
    register_logistics_status_provider,
    register_logistics_websocket_events,
)
from apps.logistics.tasks.battery_manager import get_battery_manager
from apps.logistics.tasks.dispatcher import get_dispatcher
from apps.logistics.tasks.watchdog import get_watchdog
from shared.core.config import get_config
from shared.core.event_bus import get_event_bus
from shared.core.logger import get_logger
from shared.hardware.speaker import SpeakerAlert
from shared.provisioning.roles import register_role
from shared.runtime import base_startup


logger = get_logger(__name__)


def _retarget_logistics_singletons(navigator, state_monitor) -> None:
    dispatcher = get_dispatcher()
    battery_manager = get_battery_manager()
    watchdog = get_watchdog()

    if hasattr(dispatcher, "_navigator"):
        dispatcher._navigator = navigator
    if hasattr(dispatcher, "_state_monitor"):
        dispatcher._state_monitor = state_monitor

    if hasattr(battery_manager, "_state_monitor"):
        battery_manager._state_monitor = state_monitor
    if hasattr(battery_manager, "_dispatcher"):
        battery_manager._dispatcher = dispatcher

    if hasattr(watchdog, "_state_monitor"):
        watchdog._state_monitor = state_monitor

    configure_hold_release_events = getattr(navigator, "configure_hold_release_events", None)
    if callable(configure_hold_release_events):
        configure_hold_release_events(
            (
                logistics_events.HUMAN_CONFIRMED_LOAD,
                logistics_events.HUMAN_CONFIRMED_UNLOAD,
            )
        )
    if hasattr(watchdog, "_dispatcher"):
        watchdog._dispatcher = dispatcher


base_startup.register_singleton_retarget_hook(_retarget_logistics_singletons)
register_role("logistics")
register_logistics_alert_rules()
register_logistics_status_provider()
register_logistics_websocket_events()


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
    cfg = get_config()
    dispatcher = get_dispatcher()
    battery_manager = get_battery_manager()
    watchdog = get_watchdog()
    register_logistics_alert_rules()
    register_logistics_status_provider()
    register_logistics_websocket_events()

    await base_startup.startup_system()

    speaker = SpeakerAlert(
        enabled=cfg.speaker.enabled,
        arrival_sound=cfg.speaker.arrival_sound,
        volume_pct=cfg.speaker.volume_pct,
        player_cmd=cfg.speaker.player_cmd,
    )
    speaker.start(get_event_bus())

    rollback_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = []
    try:
        await dispatcher.start()
        rollback_steps.append(("dispatcher", dispatcher.stop))

        await battery_manager.start()
        rollback_steps.append(("battery_manager", battery_manager.stop))

        await watchdog.start()
        rollback_steps.append(("watchdog", watchdog.stop))
    except Exception:
        await _run_shutdown_steps(list(reversed(rollback_steps)))
        await base_startup.shutdown_system()
        raise

    logger.info("Logistics runtime startup complete")


async def shutdown_system() -> None:
    shutdown_steps: list[tuple[str, Callable[[], Awaitable[None]]]] = [
        ("watchdog", get_watchdog().stop),
        ("battery_manager", get_battery_manager().stop),
        ("dispatcher", get_dispatcher().stop),
    ]

    errors = await _run_shutdown_steps(shutdown_steps)
    await base_startup.shutdown_system()
    logger.info("Logistics runtime shutdown complete", extra={"error_count": len(errors)})


def create_uvicorn_config() -> dict:
    config = get_config()
    return {
        "app": "apps.logistics.api.rest:app",
        "host": config.api.host,
        "port": config.api.port,
        "reload": False,
    }


def main() -> None:
    import uvicorn

    uvicorn.run(**create_uvicorn_config())


__all__ = ["create_uvicorn_config", "main", "shutdown_system", "startup_system"]

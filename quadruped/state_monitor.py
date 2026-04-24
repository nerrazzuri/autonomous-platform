from __future__ import annotations

"""Quadruped telemetry polling and in-memory state monitoring."""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from core.config import get_config
from core.event_bus import EventName, get_event_bus
from core.logger import get_logger
from core.database import Database, get_database
from quadruped.sdk_adapter import (
    QuadrupedMode,
    QuadrupedTelemetrySnapshot,
    SDKAdapter,
    get_sdk_adapter,
)


logger = get_logger(__name__)


class StateMonitorError(Exception):
    """Raised when the state monitor is configured with invalid values."""


@dataclass(frozen=True)
class QuadrupedState:
    timestamp: datetime
    battery_pct: int
    position: tuple[float, float, float]
    rpy: tuple[float, float, float]
    control_mode: int
    connection_ok: bool
    mode: QuadrupedMode

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp.isoformat(),
            "battery_pct": self.battery_pct,
            "position": list(self.position),
            "rpy": list(self.rpy),
            "control_mode": self.control_mode,
            "connection_ok": self.connection_ok,
            "mode": self.mode.value,
        }

    def is_battery_warn(self, warn_pct: int) -> bool:
        return self.battery_pct <= warn_pct

    def is_battery_critical(self, critical_pct: int) -> bool:
        return self.battery_pct <= critical_pct


class StateMonitor:
    def __init__(
        self,
        sdk_adapter: SDKAdapter | None = None,
        database: Database | None = None,
        poll_interval_seconds: float | None = None,
        persist_telemetry: bool = True,
    ):
        config = get_config()
        resolved_interval = poll_interval_seconds if poll_interval_seconds is not None else config.heartbeat.interval_seconds
        if resolved_interval <= 0:
            raise StateMonitorError("poll_interval_seconds must be > 0")

        self._sdk_adapter = sdk_adapter or get_sdk_adapter()
        self._database = database or get_database()
        self._poll_interval_seconds = resolved_interval
        self._persist_telemetry = persist_telemetry
        self._current_state: QuadrupedState | None = None
        self._state_lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._poll_count = 0
        self._last_error: str | None = None
        self._previous_connection_ok: bool | None = None
        self._battery_warn_emitted = False
        self._battery_critical_emitted = False
        self._database_initialized = False

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        if self._persist_telemetry and not self._database_initialized:
            try:
                await self._database.initialize()
                self._database_initialized = True
            except Exception as exc:
                self._last_error = f"database initialize failed: {exc}"
                logger.warning("State monitor database initialization failed")

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop(), name="sumitomo-state-monitor")
        logger.info("Quadruped state monitor started")

    async def stop(self) -> None:
        if self._task is None:
            return
        if self._task.done():
            self._task = None
            return

        self._stop_event.set()
        try:
            await self._task
        finally:
            self._task = None
            logger.info("Quadruped state monitor stopped")

    async def poll_once(self) -> QuadrupedState:
        snapshot = await self._sdk_adapter.get_telemetry_snapshot()
        state = self._snapshot_to_state(snapshot)
        async with self._state_lock:
            self._current_state = state
        self._poll_count += 1

        self._safe_publish(EventName.QUADRUPED_TELEMETRY, state.to_dict())
        self._handle_connection_transition(state)
        self._handle_battery_thresholds(state)

        if self._persist_telemetry:
            await self._persist_state(state)

        return state

    async def get_current_state(self) -> QuadrupedState | None:
        async with self._state_lock:
            return self._current_state

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def poll_count(self) -> int:
        return self._poll_count

    def last_error(self) -> str | None:
        return self._last_error

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self.poll_once()
            except Exception as exc:
                self._last_error = str(exc)
                logger.exception("State monitor poll failed")
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._poll_interval_seconds)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

    def _snapshot_to_state(self, snapshot: QuadrupedTelemetrySnapshot) -> QuadrupedState:
        return QuadrupedState(
            timestamp=datetime.now(timezone.utc),
            battery_pct=int(snapshot.battery_pct),
            position=self._coerce_vector(snapshot.position),
            rpy=self._coerce_vector(snapshot.rpy),
            control_mode=int(snapshot.control_mode),
            connection_ok=bool(snapshot.connection_ok),
            mode=snapshot.mode,
        )

    def _coerce_vector(self, value: tuple[float, float, float] | list[float] | Any) -> tuple[float, float, float]:
        if isinstance(value, (list, tuple)) and len(value) == 3:
            try:
                return (float(value[0]), float(value[1]), float(value[2]))
            except (TypeError, ValueError):
                return (0.0, 0.0, 0.0)
        return (0.0, 0.0, 0.0)

    def _handle_connection_transition(self, state: QuadrupedState) -> None:
        if self._previous_connection_ok in {None, False} and state.connection_ok:
            self._safe_publish(EventName.QUADRUPED_CONNECTION_RESTORED, state.to_dict())
            logger.info("Quadruped connection restored")
        elif self._previous_connection_ok is True and not state.connection_ok:
            self._safe_publish(EventName.QUADRUPED_CONNECTION_LOST, state.to_dict())
            logger.warning("Quadruped connection lost")
        self._previous_connection_ok = state.connection_ok

    def _handle_battery_thresholds(self, state: QuadrupedState) -> None:
        config = get_config()

        if state.is_battery_warn(config.battery.warn_pct) and not self._battery_warn_emitted:
            self._battery_warn_emitted = True
            self._safe_publish(EventName.BATTERY_WARN, state.to_dict())
            logger.warning("Quadruped battery warning threshold reached")

        if state.is_battery_critical(config.battery.critical_pct) and not self._battery_critical_emitted:
            self._battery_critical_emitted = True
            self._safe_publish(EventName.BATTERY_CRITICAL, state.to_dict())
            logger.warning("Quadruped battery critical threshold reached")

        if (
            state.battery_pct >= config.battery.resume_pct
            and (self._battery_warn_emitted or self._battery_critical_emitted)
        ):
            self._battery_warn_emitted = False
            self._battery_critical_emitted = False
            self._safe_publish(EventName.BATTERY_RECHARGED, state.to_dict())

    async def _persist_state(self, state: QuadrupedState) -> None:
        try:
            if not self._database_initialized:
                await self._database.initialize()
                self._database_initialized = True
            await self._database.log_telemetry(
                battery_pct=state.battery_pct,
                pos_x=state.position[0],
                pos_y=state.position[1],
                pos_z=state.position[2],
                roll=state.rpy[0],
                pitch=state.rpy[1],
                yaw=state.rpy[2],
                control_mode=state.control_mode,
                connection_ok=state.connection_ok,
            )
        except Exception as exc:
            self._last_error = f"telemetry persistence failed: {exc}"
            logger.warning("State monitor telemetry persistence failed")

    def _safe_publish(self, event_name: EventName, payload: dict[str, Any]) -> None:
        try:
            get_event_bus().publish_nowait(event_name, payload=payload, source=__name__)
        except asyncio.QueueFull:
            logger.warning("State monitor event bus queue full", extra={"event_name": event_name.value})
        except Exception:
            logger.exception("State monitor failed to publish event")


state_monitor = StateMonitor()


def get_state_monitor() -> StateMonitor:
    return state_monitor


__all__ = [
    "QuadrupedState",
    "StateMonitor",
    "StateMonitorError",
    "get_state_monitor",
    "state_monitor",
]

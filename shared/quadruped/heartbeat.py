from __future__ import annotations

"""Dedicated keepalive controller for periodic quadruped SDK movement commands."""

import asyncio
import math
from dataclasses import dataclass
from datetime import datetime, timezone

from shared.core.config import get_config
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.quadruped.sdk_adapter import SDKAdapter, get_sdk_adapter


logger = get_logger(__name__)


class HeartbeatError(Exception):
    """Raised when heartbeat configuration or commands are invalid."""


@dataclass(frozen=True)
class VelocityCommand:
    vx: float
    vy: float
    yaw_rate: float
    source: str = "unknown"
    task_id: str | None = None
    timestamp: datetime | None = None

    def __post_init__(self) -> None:
        for field_name, value in (("vx", self.vx), ("vy", self.vy), ("yaw_rate", self.yaw_rate)):
            if not math.isfinite(value):
                raise HeartbeatError(f"{field_name} must be finite")
        if self.timestamp is None:
            object.__setattr__(self, "timestamp", datetime.now(timezone.utc))
        elif self.timestamp.tzinfo is None:
            raise HeartbeatError("timestamp must be timezone-aware")

    @classmethod
    def zero(cls, source: str = "heartbeat", task_id: str | None = None) -> "VelocityCommand":
        return cls(vx=0.0, vy=0.0, yaw_rate=0.0, source=source, task_id=task_id)


class HeartbeatController:
    def __init__(
        self,
        sdk_adapter: SDKAdapter | None = None,
        interval_seconds: float | None = None,
        robot_id: str = "default",
    ):
        config = get_config()
        resolved_interval = interval_seconds if interval_seconds is not None else config.heartbeat.interval_seconds
        if resolved_interval <= 0:
            raise HeartbeatError("interval_seconds must be > 0")
        if not isinstance(robot_id, str) or not robot_id.strip():
            raise HeartbeatError("robot_id must be a non-empty string")

        self._sdk_adapter = sdk_adapter or get_sdk_adapter()
        self._interval_seconds = resolved_interval
        self.robot_id = robot_id
        self._target_lock = asyncio.Lock()
        self._target_velocity = VelocityCommand.zero(source="heartbeat")
        self._task: asyncio.Task[None] | None = None
        self._stop_event = asyncio.Event()
        self._last_send_ok: bool | None = None
        self._last_error: str | None = None
        self._send_count = 0
        self._subscription_id: str | None = None

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._stop_event = asyncio.Event()
        self._subscription_id = get_event_bus().subscribe(
            EventName.ESTOP_TRIGGERED,
            self._handle_estop,
            subscriber_name="heartbeat_estop_handler",
        )
        self._task = asyncio.create_task(self._run_loop(), name="sumitomo-heartbeat")
        self._safe_publish(EventName.SYSTEM_STARTED, {"module": "heartbeat"})
        logger.info("Heartbeat controller started", extra={"module_name": "heartbeat"})

    async def stop(self) -> None:
        if self._task is None:
            if self._subscription_id is not None:
                get_event_bus().unsubscribe(self._subscription_id)
                self._subscription_id = None
            return
        if self._task.done():
            self._task = None
            if self._subscription_id is not None:
                get_event_bus().unsubscribe(self._subscription_id)
                self._subscription_id = None
            return

        try:
            await self.clear_target_velocity(source="heartbeat")
            try:
                await self._sdk_adapter.stop_motion()
            except Exception as exc:
                self._last_error = f"final stop_motion failed: {exc}"
                logger.warning("Heartbeat final stop_motion failed")
            self._stop_event.set()
            await self._task
        finally:
            if self._subscription_id is not None:
                get_event_bus().unsubscribe(self._subscription_id)
                self._subscription_id = None
            self._task = None
            self._safe_publish(EventName.SYSTEM_STOPPING, {"module": "heartbeat"})
            logger.info("Heartbeat controller stopped", extra={"module_name": "heartbeat"})

    async def set_target_velocity(
        self,
        vx: float,
        vy: float,
        yaw_rate: float,
        *,
        source: str = "unknown",
        task_id: str | None = None,
    ) -> VelocityCommand:
        command = VelocityCommand(vx=vx, vy=vy, yaw_rate=yaw_rate, source=source, task_id=task_id)
        async with self._target_lock:
            self._target_velocity = command
        logger.debug(
            "Heartbeat target velocity updated",
            extra={"source": source, "task_id": task_id},
        )
        return command

    async def clear_target_velocity(self, source: str = "heartbeat") -> VelocityCommand:
        command = VelocityCommand.zero(source=source)
        async with self._target_lock:
            self._target_velocity = command
        logger.debug("Heartbeat target velocity cleared", extra={"source": source})
        return command

    async def get_target_velocity(self) -> VelocityCommand:
        async with self._target_lock:
            return self._target_velocity

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def last_send_ok(self) -> bool | None:
        return self._last_send_ok

    def last_error(self) -> str | None:
        return self._last_error

    def send_count(self) -> int:
        return self._send_count

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            await self._send_once()
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("Unexpected heartbeat loop error")
        return

    async def _send_once(self) -> None:
        command = await self.get_target_velocity()
        try:
            ok = await self._sdk_adapter.move(command.vx, command.vy, command.yaw_rate)
            self._last_send_ok = bool(ok)
            if not ok:
                self._last_error = "heartbeat move command failed"
                logger.warning(
                    "Heartbeat send failed",
                    extra={"source": command.source, "task_id": command.task_id},
                )
        except Exception as exc:
            self._last_send_ok = False
            self._last_error = str(exc)
            logger.exception("Heartbeat loop move raised unexpectedly")
        finally:
            self._send_count += 1

    async def _handle_estop(self, event) -> None:
        logger.warning("Heartbeat received ESTOP event")
        await self.clear_target_velocity(source="estop")
        try:
            ok = await self._sdk_adapter.stop_motion()
            if not ok:
                self._last_error = "stop_motion returned False during ESTOP handling"
        except Exception as exc:
            self._last_error = f"ESTOP stop_motion failed: {exc}"
            logger.exception("Heartbeat ESTOP handling failed")

    def _safe_publish(self, event_name: EventName, payload: dict[str, object]) -> None:
        try:
            enriched_payload = dict(payload)
            enriched_payload["robot_id"] = self.robot_id
            get_event_bus().publish_nowait(event_name, payload=enriched_payload, source=__name__)
        except asyncio.QueueFull:
            logger.warning("Heartbeat event bus queue full", extra={"event_name": event_name.value})
        except Exception:
            logger.exception("Heartbeat failed to publish lifecycle event")


heartbeat_controller = HeartbeatController()


def get_heartbeat_controller() -> HeartbeatController:
    return heartbeat_controller


__all__ = [
    "HeartbeatController",
    "HeartbeatError",
    "VelocityCommand",
    "get_heartbeat_controller",
    "heartbeat_controller",
]

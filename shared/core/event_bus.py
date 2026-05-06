from __future__ import annotations

"""Async in-process event bus for platform modules and app-defined events."""

import asyncio
import inspect
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable, Literal
from uuid import uuid4

from shared.core.logger import clear_runtime_context, get_logger, set_runtime_context


logger = get_logger(__name__)

SubscriberCallback = Callable[["Event"], None | Awaitable[None]]
WildcardEventName = Literal["*"]


class EventName(str, Enum):
    SYSTEM_STARTED = "system.started"
    SYSTEM_STOPPING = "system.stopping"
    SYSTEM_ALERT = "system.alert"

    QUADRUPED_TELEMETRY = "quadruped.telemetry"
    QUADRUPED_IDLE = "quadruped.idle"
    QUADRUPED_CONNECTION_LOST = "quadruped.connection_lost"
    QUADRUPED_CONNECTION_RESTORED = "quadruped.connection_restored"
    QUADRUPED_ARRIVED_AT_WAYPOINT = "quadruped.arrived_at_waypoint"

    BATTERY_WARN = "battery.warn"
    BATTERY_CRITICAL = "battery.critical"
    BATTERY_RECHARGED = "battery.recharged"

    NAVIGATION_STARTED = "navigation.started"
    NAVIGATION_BLOCKED = "navigation.blocked"
    NAVIGATION_RESUMED = "navigation.resumed"
    NAVIGATION_COMPLETED = "navigation.completed"
    NAVIGATION_FAILED = "navigation.failed"

    OBSTACLE_DETECTED = "obstacle.detected"
    OBSTACLE_CLEARED = "obstacle.cleared"

    # Deprecated app compatibility alias. New app code should use app-owned
    # string constants.
    HUMAN_CONFIRMED_LOAD = "human.confirmed_load"
    HUMAN_CONFIRMED_UNLOAD = "human.confirmed_unload"

    # Shared task abstraction events retained here for backward compatibility.
    # Future app-specific workflow events should use plain strings in app packages.
    TASK_SUBMITTED = "task.submitted"
    TASK_DISPATCHED = "task.dispatched"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_CANCELLED = "task.cancelled"

    ESTOP_TRIGGERED = "estop.triggered"
    ESTOP_RELEASED = "estop.released"

    # Deprecated app compatibility alias. New app code should use app-owned
    # string constants.
    PATROL_CYCLE_STARTED = "patrol.cycle.started"
    PATROL_CYCLE_COMPLETED = "patrol.cycle.completed"
    PATROL_CYCLE_FAILED = "patrol.cycle.failed"
    PATROL_WAYPOINT_OBSERVED = "patrol.waypoint.observed"
    PATROL_ANOMALY_DETECTED = "patrol.anomaly.detected"
    PATROL_ANOMALY_CLEARED = "patrol.anomaly.cleared"
    PATROL_SUSPENDED = "patrol.suspended"
    PATROL_RESUMED = "patrol.resumed"


@dataclass(frozen=True)
class Event:
    name: EventName | str
    payload: dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    source: str | None = None
    task_id: str | None = None
    correlation_id: str | None = None


@dataclass
class Subscription:
    subscription_id: str
    event_name: EventName | str | WildcardEventName
    callback: SubscriberCallback
    subscriber_name: str
    created_at: datetime


class EventBus:
    """Lightweight sequential asyncio event bus with wildcard subscription support."""

    _STOP_SENTINEL = object()

    def __init__(self, max_queue_size: int = 1000) -> None:
        self._queue: asyncio.Queue[Event | object] = asyncio.Queue(maxsize=max_queue_size)
        self._subscriptions: dict[str, Subscription] = {}
        self._dispatcher_task: asyncio.Task[None] | None = None

    def subscribe(
        self,
        event_name: EventName | str,
        callback: SubscriberCallback,
        *,
        subscriber_name: str | None = None,
    ) -> str:
        if not callable(callback):
            raise ValueError("callback must be callable")

        normalized_name = self._normalize_event_name(event_name, allow_wildcard=True)
        subscription_id = str(uuid4())
        subscription = Subscription(
            subscription_id=subscription_id,
            event_name=normalized_name,
            callback=callback,
            subscriber_name=subscriber_name or getattr(callback, "__name__", "anonymous_subscriber"),
            created_at=datetime.now(timezone.utc),
        )
        self._subscriptions[subscription_id] = subscription
        return subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        subscription = self._subscriptions.pop(subscription_id, None)
        return subscription is not None

    async def publish(
        self,
        event_name: EventName | str,
        payload: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        event = self._build_event(
            event_name,
            payload,
            source=source,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        await self._queue.put(event)
        self._log_publish(event)
        return event

    def publish_nowait(
        self,
        event_name: EventName | str,
        payload: dict[str, Any] | None = None,
        *,
        source: str | None = None,
        task_id: str | None = None,
        correlation_id: str | None = None,
    ) -> Event:
        event = self._build_event(
            event_name,
            payload,
            source=source,
            task_id=task_id,
            correlation_id=correlation_id,
        )
        self._queue.put_nowait(event)
        self._log_publish(event)
        return event

    async def start(self) -> None:
        if self._dispatcher_task and not self._dispatcher_task.done():
            return

        self._dispatcher_task = asyncio.create_task(self._dispatch_loop(), name="platform-event-bus")
        logger.info("Event bus started", extra={"event_name": EventName.SYSTEM_STARTED.value})

    async def stop(self) -> None:
        if self._dispatcher_task is None:
            return
        if self._dispatcher_task.done():
            self._dispatcher_task = None
            return

        logger.info("Event bus stopping", extra={"event_name": EventName.SYSTEM_STOPPING.value})
        await self.wait_until_idle()
        await self._queue.put(self._STOP_SENTINEL)
        await self._dispatcher_task
        self._dispatcher_task = None
        logger.info("Event bus stopped", extra={"event_name": EventName.SYSTEM_STOPPING.value})

    async def wait_until_idle(self, timeout: float | None = None) -> None:
        if self._dispatcher_task is None and self._queue.empty():
            return
        if self._dispatcher_task is None:
            return

        join_awaitable = self._queue.join()
        if timeout is None:
            await join_awaitable
        else:
            await asyncio.wait_for(join_awaitable, timeout=timeout)

    def subscriber_count(self, event_name: EventName | str | None = None) -> int:
        if event_name is None:
            return len(self._subscriptions)

        normalized_name = self._normalize_event_name(event_name, allow_wildcard=True)
        if normalized_name == "*":
            return sum(1 for subscription in self._subscriptions.values() if subscription.event_name == "*")
        return sum(
            1
            for subscription in self._subscriptions.values()
            if subscription.event_name == normalized_name or subscription.event_name == "*"
        )

    def _build_event(
        self,
        event_name: EventName | str,
        payload: dict[str, Any] | None,
        *,
        source: str | None,
        task_id: str | None,
        correlation_id: str | None,
    ) -> Event:
        normalized_name = self._normalize_event_name(event_name, allow_wildcard=False)
        return Event(
            name=normalized_name,
            payload=dict(payload or {}),
            source=source,
            task_id=task_id,
            correlation_id=correlation_id,
        )

    async def _dispatch_loop(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                if item is self._STOP_SENTINEL:
                    return
                await self._dispatch_event(item)
            finally:
                self._queue.task_done()

    async def _dispatch_event(self, event: Event) -> None:
        subscriptions = self._matching_subscriptions(event.name)
        for subscription in subscriptions:
            await self._invoke_callback(subscription, event)

    def _matching_subscriptions(self, event_name: EventName | str) -> list[Subscription]:
        event_value = self._event_name_value(event_name)
        exact_matches = [
            subscription
            for subscription in self._subscriptions.values()
            if self._event_name_value(subscription.event_name) == event_value
        ]
        wildcard_matches = [
            subscription for subscription in self._subscriptions.values() if subscription.event_name == "*"
        ]
        return exact_matches + wildcard_matches

    async def _invoke_callback(self, subscription: Subscription, event: Event) -> None:
        quadruped_state = event.payload.get("quadruped_state")
        try:
            set_runtime_context(task_id=event.task_id, quadruped_state=quadruped_state)
            result = subscription.callback(event)
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.exception(
                "Event callback failed",
                extra={
                    "event_name": self._event_name_value(event.name),
                    "subscription_id": subscription.subscription_id,
                    "subscriber_name": subscription.subscriber_name,
                    "source": event.source,
                },
            )
        finally:
            clear_runtime_context()

    def _log_publish(self, event: Event) -> None:
        logger.debug(
            "Event published",
            extra={
                "event_name": self._event_name_value(event.name),
                "event_id": event.event_id,
                "source": event.source,
                "task_id": event.task_id,
                "correlation_id": event.correlation_id,
            },
        )

    def _normalize_event_name(
        self, event_name: EventName | str, *, allow_wildcard: bool
    ) -> EventName | str | WildcardEventName:
        if isinstance(event_name, EventName):
            return event_name
        if event_name == "*" and allow_wildcard:
            return "*"
        if event_name == "*":
            raise ValueError("Wildcard event name is allowed only for subscription lookup")
        try:
            return EventName(event_name)
        except ValueError:
            if not isinstance(event_name, str) or not event_name.strip():
                raise ValueError("event name must be a non-empty string")
            return event_name.strip()

    @staticmethod
    def _event_name_value(event_name: EventName | str) -> str:
        return event_name.value if isinstance(event_name, EventName) else event_name


event_bus = EventBus()


def get_event_bus() -> EventBus:
    return event_bus


__all__ = [
    "Event",
    "EventBus",
    "EventName",
    "Subscription",
    "event_bus",
    "get_event_bus",
]

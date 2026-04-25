from __future__ import annotations

"""FastAPI REST surface for quadruped logistics operations."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared.api.alerts import get_alert_manager
from shared.api.auth import require_operator, require_supervisor
from shared.api.ws_broker import get_ws_broker, websocket_endpoint
from shared.core.config import get_config
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.navigation.route_store import RouteDefinition, RouteNotFoundError, RouteStore, RouteStoreError, Waypoint, get_route_store
from shared.quadruped.sdk_adapter import SDKAdapter, get_sdk_adapter
from shared.quadruped.state_monitor import QuadrupedState, StateMonitor, get_state_monitor
from apps.logistics.runtime.startup import shutdown_system, startup_system
from apps.logistics.tasks.dispatcher import Dispatcher, get_dispatcher
from apps.logistics.tasks.queue import (
    InvalidTaskTransitionError,
    QueueSummary,
    TaskQueue,
    TaskQueueError,
    get_task_queue,
)


logger = get_logger(__name__)
EVENT_SOURCE = "api.rest"


class APIError(Exception):
    """Raised when the API layer cannot safely map a request to application behavior."""


class HealthResponse(BaseModel):
    status: str
    service: str


class CreateTaskRequest(BaseModel):
    station_id: str
    destination_id: str
    batch_id: str | None = None
    priority: int = 0
    notes: str | None = None


class TaskResponse(BaseModel):
    id: str
    station_id: str
    destination_id: str
    batch_id: str | None
    priority: int
    status: str
    created_at: str
    dispatched_at: str | None
    completed_at: str | None
    notes: str | None


class QueueStatusResponse(BaseModel):
    total: int
    queued: int
    dispatched: int
    awaiting_load: int
    in_transit: int
    awaiting_unload: int
    completed: int
    failed: int
    cancelled: int


class QuadrupedStatusResponse(BaseModel):
    battery_pct: int | None
    position: tuple[float, float, float] | None
    rpy: tuple[float, float, float] | None
    control_mode: int | None
    connection_ok: bool | None
    mode: str | None
    timestamp: str | None
    active_task_id: str | None = None


class RouteWaypointResponse(BaseModel):
    name: str
    x: float
    y: float
    heading_deg: float
    velocity: float
    hold: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class RouteResponse(BaseModel):
    id: str
    name: str
    origin_id: str
    destination_id: str
    active: bool
    metadata: dict[str, Any] = Field(default_factory=dict)
    waypoints: list[RouteWaypointResponse]


class UpdateRouteRequest(BaseModel):
    name: str
    origin_id: str
    destination_id: str
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    waypoints: list[RouteWaypointResponse]


class MessageResponse(BaseModel):
    message: str


def get_task_queue_dep() -> TaskQueue:
    return get_task_queue()


def get_state_monitor_dep() -> StateMonitor:
    return get_state_monitor()


def get_dispatcher_dep() -> Dispatcher:
    return get_dispatcher()


def get_sdk_adapter_dep() -> SDKAdapter:
    return get_sdk_adapter()


def get_route_store_dep() -> RouteStore:
    return get_route_store()


def task_to_response(task: Any) -> TaskResponse:
    return TaskResponse(
        id=task.id,
        station_id=task.station_id,
        destination_id=task.destination_id,
        batch_id=task.batch_id,
        priority=task.priority,
        status=task.status,
        created_at=task.created_at,
        dispatched_at=task.dispatched_at,
        completed_at=task.completed_at,
        notes=task.notes,
    )


def queue_summary_to_response(summary: QueueSummary) -> QueueStatusResponse:
    return QueueStatusResponse(
        total=summary.total,
        queued=summary.queued,
        dispatched=summary.dispatched,
        awaiting_load=summary.awaiting_load,
        in_transit=summary.in_transit,
        awaiting_unload=summary.awaiting_unload,
        completed=summary.completed,
        failed=summary.failed,
        cancelled=summary.cancelled,
    )


def state_to_response(
    state: QuadrupedState | None,
    active_task_id: str | None = None,
) -> QuadrupedStatusResponse:
    if state is None:
        return QuadrupedStatusResponse(
            battery_pct=None,
            position=None,
            rpy=None,
            control_mode=None,
            connection_ok=None,
            mode=None,
            timestamp=None,
            active_task_id=active_task_id,
        )
    return QuadrupedStatusResponse(
        battery_pct=state.battery_pct,
        position=state.position,
        rpy=state.rpy,
        control_mode=state.control_mode,
        connection_ok=state.connection_ok,
        mode=state.mode.value,
        timestamp=state.timestamp.isoformat(),
        active_task_id=active_task_id,
    )


def waypoint_to_response(waypoint: Waypoint) -> RouteWaypointResponse:
    return RouteWaypointResponse(
        name=waypoint.name,
        x=waypoint.x,
        y=waypoint.y,
        heading_deg=waypoint.heading_deg,
        velocity=waypoint.velocity,
        hold=waypoint.hold,
        metadata=dict(waypoint.metadata),
    )


def route_to_response(route: RouteDefinition) -> RouteResponse:
    return RouteResponse(
        id=route.id,
        name=route.name,
        origin_id=route.origin_id,
        destination_id=route.destination_id,
        active=route.active,
        metadata=dict(route.metadata),
        waypoints=[waypoint_to_response(waypoint) for waypoint in route.waypoints],
    )


def _raise_task_queue_http_error(exc: Exception) -> None:
    logger.warning("REST task queue request failed", extra={"error": str(exc)})
    if isinstance(exc, InvalidTaskTransitionError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, TaskQueueError):
        error_text = str(exc)
        if "not found" in error_text.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_text) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_text) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    logger.exception("Unexpected REST task queue error")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


def _raise_route_store_http_error(exc: Exception) -> None:
    logger.warning("REST route request failed", extra={"error": str(exc)})
    if isinstance(exc, RouteNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, (RouteStoreError, ValueError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    logger.exception("Unexpected REST route store error")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


def _build_route_definition(route_id: str, request: UpdateRouteRequest) -> RouteDefinition:
    try:
        waypoints = [
            Waypoint(
                name=waypoint.name,
                x=waypoint.x,
                y=waypoint.y,
                heading_deg=waypoint.heading_deg,
                velocity=waypoint.velocity,
                hold=waypoint.hold,
                metadata=dict(waypoint.metadata),
            )
            for waypoint in request.waypoints
        ]
        return RouteDefinition(
            id=route_id,
            name=request.name,
            origin_id=request.origin_id,
            destination_id=request.destination_id,
            active=request.active,
            metadata=dict(request.metadata),
            waypoints=waypoints,
        )
    except (RouteStoreError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _confirmation_payload(task_id: str, task: Any) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "station_id": task.station_id,
        "destination_id": task.destination_id,
        "status": task.status,
    }


def create_app() -> FastAPI:
    config = get_config()
    ui_directory = Path(__file__).resolve().parents[1] / "ui"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await startup_system()
        await get_ws_broker().start()
        await get_alert_manager().start()
        try:
            yield
        finally:
            await get_alert_manager().stop()
            await get_ws_broker().stop()
            await shutdown_system()

    application = FastAPI(title=config.app.name, lifespan=lifespan)
    application.add_api_websocket_route("/ws", websocket_endpoint)
    application.mount("/ui", StaticFiles(directory=ui_directory), name="ui")

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service=config.app.name)

    @application.post(
        "/tasks",
        response_model=TaskResponse,
        dependencies=[Depends(require_operator)],
    )
    async def create_task(
        request: CreateTaskRequest,
        task_queue: TaskQueue = Depends(get_task_queue_dep),
    ) -> TaskResponse:
        logger.info("REST task submission requested", extra={"station_id": request.station_id})
        try:
            task = await task_queue.submit_task(
                station_id=request.station_id,
                destination_id=request.destination_id,
                batch_id=request.batch_id,
                priority=request.priority,
                notes=request.notes,
            )
        except Exception as exc:
            _raise_task_queue_http_error(exc)
        return task_to_response(task)

    @application.get(
        "/tasks",
        response_model=list[TaskResponse],
        dependencies=[Depends(require_operator)],
    )
    async def list_tasks(
        status_filter: str | None = Query(default=None, alias="status"),
        limit: int = Query(default=100, ge=1),
        offset: int = Query(default=0, ge=0),
        task_queue: TaskQueue = Depends(get_task_queue_dep),
    ) -> list[TaskResponse]:
        try:
            tasks = await task_queue.list_tasks(status=status_filter, limit=limit, offset=offset)
        except Exception as exc:
            _raise_task_queue_http_error(exc)
        return [task_to_response(task) for task in tasks]

    @application.delete(
        "/tasks/{task_id}",
        response_model=TaskResponse,
        dependencies=[Depends(require_operator)],
    )
    async def cancel_task(
        task_id: str,
        task_queue: TaskQueue = Depends(get_task_queue_dep),
    ) -> TaskResponse:
        logger.info("REST task cancellation requested", extra={"task_id": task_id})
        try:
            task = await task_queue.cancel_task(task_id)
        except Exception as exc:
            _raise_task_queue_http_error(exc)
        return task_to_response(task)

    @application.post(
        "/tasks/{task_id}/confirm-load",
        response_model=MessageResponse,
        dependencies=[Depends(require_operator)],
    )
    async def confirm_load(
        task_id: str,
        task_queue: TaskQueue = Depends(get_task_queue_dep),
    ) -> MessageResponse:
        logger.info("REST human load confirmation requested", extra={"task_id": task_id})
        try:
            task = await task_queue.get_task(task_id)
        except Exception as exc:
            _raise_task_queue_http_error(exc)

        try:
            await get_event_bus().publish(
                EventName.HUMAN_CONFIRMED_LOAD,
                payload=_confirmation_payload(task_id, task),
                source=EVENT_SOURCE,
                task_id=task_id,
            )
        except Exception as exc:
            logger.exception("REST human load confirmation publish failed", extra={"task_id": task_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to publish load confirmation: {exc}",
            ) from exc
        return MessageResponse(message="Load confirmed")

    @application.post(
        "/tasks/{task_id}/confirm-unload",
        response_model=MessageResponse,
        dependencies=[Depends(require_operator)],
    )
    async def confirm_unload(
        task_id: str,
        task_queue: TaskQueue = Depends(get_task_queue_dep),
    ) -> MessageResponse:
        logger.info("REST human unload confirmation requested", extra={"task_id": task_id})
        try:
            task = await task_queue.get_task(task_id)
        except Exception as exc:
            _raise_task_queue_http_error(exc)

        try:
            await get_event_bus().publish(
                EventName.HUMAN_CONFIRMED_UNLOAD,
                payload=_confirmation_payload(task_id, task),
                source=EVENT_SOURCE,
                task_id=task_id,
            )
        except Exception as exc:
            logger.exception("REST human unload confirmation publish failed", extra={"task_id": task_id})
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to publish unload confirmation: {exc}",
            ) from exc
        return MessageResponse(message="Unload confirmed")

    @application.get(
        "/queue/status",
        response_model=QueueStatusResponse,
        dependencies=[Depends(require_operator)],
    )
    async def queue_status(
        task_queue: TaskQueue = Depends(get_task_queue_dep),
    ) -> QueueStatusResponse:
        try:
            summary = await task_queue.get_queue_status()
        except Exception as exc:
            _raise_task_queue_http_error(exc)
        return queue_summary_to_response(summary)

    @application.get(
        "/quadruped/status",
        response_model=QuadrupedStatusResponse,
        dependencies=[Depends(require_operator)],
    )
    async def quadruped_status(
        state_monitor: StateMonitor = Depends(get_state_monitor_dep),
        dispatcher: Dispatcher = Depends(get_dispatcher_dep),
    ) -> QuadrupedStatusResponse:
        try:
            state = await state_monitor.get_current_state()
            if state is None:
                state = await state_monitor.poll_once()
        except Exception:
            logger.exception("REST quadruped status fetch failed")
            state = None
        active_task_id: str | None = None
        try:
            dispatch_state = await dispatcher.get_state()
            active_task_id = dispatch_state.active_task_id
        except Exception as exc:
            logger.warning("REST dispatcher status fetch failed", extra={"error": str(exc)})
        return state_to_response(state, active_task_id=active_task_id)

    @application.post(
        "/estop",
        response_model=MessageResponse,
        dependencies=[Depends(require_operator)],
    )
    async def estop(
        sdk_adapter: SDKAdapter = Depends(get_sdk_adapter_dep),
    ) -> MessageResponse:
        logger.warning("REST emergency stop requested")
        try:
            success = await sdk_adapter.passive()
        except Exception as exc:
            logger.exception("REST emergency stop failed")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quadruped emergency stop unavailable",
            )
        return MessageResponse(message="Emergency stop triggered")

    @application.post(
        "/estop/release",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def estop_release(
        sdk_adapter: SDKAdapter = Depends(get_sdk_adapter_dep),
    ) -> MessageResponse:
        logger.info("REST emergency stop release requested")
        try:
            success = await sdk_adapter.stand_up()
        except Exception as exc:
            logger.exception("REST emergency stop release failed")
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quadruped release unavailable",
            )
        return MessageResponse(message="Emergency stop released")

    @application.get(
        "/routes",
        response_model=list[RouteResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_routes(
        active: bool | None = Query(default=None),
        route_store: RouteStore = Depends(get_route_store_dep),
    ) -> list[RouteResponse]:
        try:
            routes = await route_store.list_routes(active=active)
        except Exception as exc:
            _raise_route_store_http_error(exc)
        return [route_to_response(route) for route in routes]

    @application.get(
        "/routes/{route_id}",
        response_model=RouteResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def get_route(
        route_id: str,
        route_store: RouteStore = Depends(get_route_store_dep),
    ) -> RouteResponse:
        try:
            route = await route_store.get_route_definition(route_id)
        except Exception as exc:
            _raise_route_store_http_error(exc)
        return route_to_response(route)

    @application.put(
        "/routes/{route_id}",
        response_model=RouteResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def update_route(
        route_id: str,
        request: UpdateRouteRequest,
        route_store: RouteStore = Depends(get_route_store_dep),
    ) -> RouteResponse:
        logger.info("REST route update requested", extra={"route_id": route_id})
        route = _build_route_definition(route_id, request)
        try:
            updated = await route_store.upsert_route(route, persist=True)
        except Exception as exc:
            _raise_route_store_http_error(exc)
        return route_to_response(updated)

    logger.info("REST API app created", extra={"service": config.app.name})
    return application


app = create_app()


__all__ = [
    "APIError",
    "CreateTaskRequest",
    "HealthResponse",
    "MessageResponse",
    "QuadrupedStatusResponse",
    "QueueStatusResponse",
    "RouteResponse",
    "RouteWaypointResponse",
    "TaskResponse",
    "UpdateRouteRequest",
    "app",
    "create_app",
    "get_dispatcher_dep",
    "get_route_store_dep",
    "get_sdk_adapter_dep",
    "get_state_monitor_dep",
    "get_task_queue_dep",
    "require_operator",
    "require_supervisor",
]

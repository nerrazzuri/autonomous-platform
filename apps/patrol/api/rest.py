from __future__ import annotations

"""FastAPI REST surface for patrol operations."""

import importlib
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apps.patrol.observation.anomaly_log import AnomalyNotFoundError, AnomalyRecord, get_anomaly_log
from apps.patrol.observation.zone_config import ZoneDefinition, ZoneNotFoundError, get_zone_config
from apps.patrol.runtime.startup import shutdown_system, startup_system
from apps.patrol.tasks.patrol_dispatcher import PatrolDispatcher, get_patrol_dispatcher
from apps.patrol.tasks.patrol_record import InvalidCycleTransition, PatrolRecord
from apps.patrol.tasks.patrol_scheduler import PatrolScheduler, get_patrol_scheduler
from apps.patrol.tasks.patrol_watchdog import PatrolWatchdog, get_patrol_watchdog
from shared.api.alerts import get_alert_manager
from shared.api.auth import require_supervisor
from shared.api.ws_broker import get_ws_broker, websocket_endpoint
from shared.core.config import get_config
from apps.patrol import events as patrol_events
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.navigation.route_store import RouteDefinition, RouteNotFoundError, RouteStore, RouteStoreError, Waypoint, get_route_store
from shared.observability import build_status_summary
from shared.quadruped.robot_registry import RobotNotFoundError, get_robot_registry
from shared.quadruped.sdk_adapter import SDKAdapter, get_sdk_adapter
from shared.quadruped.state_monitor import QuadrupedState


logger = get_logger(__name__)
EVENT_SOURCE = "apps.patrol.api.rest"


class PatrolAPIError(Exception):
    """Raised when the patrol API cannot safely map a request to application behavior."""


class HealthResponse(BaseModel):
    status: str
    service: str


class PatrolStatusResponse(BaseModel):
    running: bool
    scheduler_suspended: bool
    dispatcher_suspended: bool
    active_cycle_id: str | None
    active_route_id: str | None
    consecutive_failures: int
    watchdog_suspended: bool
    last_result: str | None


class PatrolCycleResponse(BaseModel):
    cycle_id: str
    route_id: str
    status: str
    triggered_by: str
    created_at: str
    started_at: str | None
    completed_at: str | None
    waypoints_total: int
    waypoints_observed: int
    anomaly_ids: list[str]
    failure_reason: str | None


class TriggerPatrolRequest(BaseModel):
    route_id: str
    triggered_by: str = "manual"


class SuspendRequest(BaseModel):
    reason: str = "manual suspension"


class ResolveAnomalyRequest(BaseModel):
    resolved_by: str


class AnomalyResponse(BaseModel):
    anomaly_id: str
    cycle_id: str
    zone_id: str
    waypoint_name: str
    detected_at: str
    severity: str
    confidence_max: float
    resolved_at: str | None
    resolved_by: str | None
    threat_objects: list[dict[str, Any]]
    metadata: dict[str, Any]


class ZoneResponse(BaseModel):
    zone_id: str
    description: str
    normal_objects: list[str]
    suspicious_objects: list[str]
    threat_objects: list[str]
    time_rules: list[dict[str, Any]]


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


class PatrolRouteUpsertRequest(BaseModel):
    id: str
    name: str
    origin_id: str
    destination_id: str
    active: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    waypoints: list[RouteWaypointResponse]


class MessageResponse(BaseModel):
    message: str


class PositionResponse(BaseModel):
    x: float
    y: float
    z: float | None = None


class PatrolRobotStatusResponse(BaseModel):
    robot_id: str
    display_name: str | None = None
    role: str | None = None
    connected: bool | None = None
    battery_pct: int | None = None
    position: PositionResponse | None = None
    active_cycle_id: str | None = None
    active_route_id: str | None = None
    mode: int | None = None


def get_patrol_queue():
    queue_module = importlib.import_module("apps.patrol.tasks.patrol_queue")
    getter = getattr(queue_module, "get_patrol_queue", None)
    if callable(getter):
        return getter()
    return queue_module.PatrolQueue()


def get_patrol_queue_dep():
    return get_patrol_queue()


def get_patrol_scheduler_dep() -> PatrolScheduler:
    return get_patrol_scheduler()


def get_patrol_dispatcher_dep() -> PatrolDispatcher:
    return get_patrol_dispatcher()


def get_patrol_watchdog_dep() -> PatrolWatchdog:
    return get_patrol_watchdog()


def get_anomaly_log_dep():
    return get_anomaly_log()


def get_route_store_dep() -> RouteStore:
    return get_route_store()


def get_zone_config_dep():
    return get_zone_config()


def get_sdk_adapter_dep() -> SDKAdapter:
    return get_sdk_adapter()


def cycle_to_response(record: PatrolRecord | Any) -> PatrolCycleResponse:
    status_value = record.status.value if hasattr(record.status, "value") else str(record.status)
    return PatrolCycleResponse(
        cycle_id=record.cycle_id,
        route_id=record.route_id,
        status=status_value,
        triggered_by=record.triggered_by,
        created_at=record.created_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        waypoints_total=record.waypoints_total,
        waypoints_observed=record.waypoints_observed,
        anomaly_ids=list(record.anomaly_ids),
        failure_reason=record.failure_reason,
    )


def anomaly_to_response(record: AnomalyRecord) -> AnomalyResponse:
    return AnomalyResponse(
        anomaly_id=record.anomaly_id,
        cycle_id=record.cycle_id,
        zone_id=record.zone_id,
        waypoint_name=record.waypoint_name,
        detected_at=record.detected_at,
        severity=record.severity,
        confidence_max=record.confidence_max,
        resolved_at=record.resolved_at,
        resolved_by=record.resolved_by,
        threat_objects=record.threat_objects(),
        metadata=record.metadata(),
    )


def zone_to_response(zone: ZoneDefinition) -> ZoneResponse:
    return ZoneResponse(
        zone_id=zone.zone_id,
        description=zone.description,
        normal_objects=list(zone.normal_objects),
        suspicious_objects=list(zone.suspicious_objects),
        threat_objects=list(zone.threat_objects),
        time_rules=[
            {
                "after": rule.after,
                "before": rule.before,
                "escalate_suspicious_to": rule.escalate_suspicious_to,
            }
            for rule in zone.time_rules
        ],
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
        waypoints=[waypoint_to_response(item) for item in route.waypoints],
    )


def _raise_patrol_http_error(exc: Exception) -> None:
    logger.warning("Patrol API request failed", extra={"error": str(exc)})
    queue_module = importlib.import_module("apps.patrol.tasks.patrol_queue")
    patrol_cycle_not_found = getattr(queue_module, "PatrolCycleNotFound")
    patrol_queue_error = getattr(queue_module, "PatrolQueueError")

    if isinstance(exc, (patrol_cycle_not_found, AnomalyNotFoundError, ZoneNotFoundError, RouteNotFoundError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, InvalidCycleTransition):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, (PatrolAPIError, patrol_queue_error, RouteStoreError, ValueError)):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    logger.exception("Unexpected patrol API error")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal server error") from exc


def _build_route_definition(request: PatrolRouteUpsertRequest) -> RouteDefinition:
    metadata = dict(request.metadata)
    metadata.setdefault("route_type", "patrol")
    try:
        return RouteDefinition(
            id=request.id,
            name=request.name,
            origin_id=request.origin_id,
            destination_id=request.destination_id,
            active=request.active,
            metadata=metadata,
            waypoints=[
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
            ],
        )
    except (RouteStoreError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


def _platform_role(platform: Any) -> str | None:
    role = getattr(getattr(platform, "config", None), "role", None)
    if role is None:
        role = getattr(getattr(getattr(platform, "config", None), "connection", None), "role", None)
    return role


def _platform_matches_patrol_scope(platform: Any) -> bool:
    role = _platform_role(platform)
    return role in {None, "patrol"}


def _scoped_registry_platforms() -> list[Any]:
    return [platform for platform in get_robot_registry().all() if _platform_matches_patrol_scope(platform)]


def _get_patrol_platform(robot_id: str) -> Any:
    try:
        platform = get_robot_registry().get(robot_id)
    except RobotNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown robot: {robot_id}") from exc
    if not _platform_matches_patrol_scope(platform):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown robot: {robot_id}")
    return platform


async def _load_platform_state(state_monitor: Any) -> QuadrupedState | None:
    try:
        state = await state_monitor.get_current_state()
        if state is None:
            state = await state_monitor.poll_once()
        return state
    except Exception:
        logger.exception("Patrol robot status fetch failed")
        return None


def _position_response(state: QuadrupedState | None) -> PositionResponse | None:
    if state is None or state.position is None:
        return None
    position = state.position
    z_value = float(position[2]) if len(position) > 2 else None
    return PositionResponse(x=float(position[0]), y=float(position[1]), z=z_value)


def _active_cycle_for_robot(dispatcher: PatrolDispatcher, robot_id: str) -> tuple[str | None, str | None]:
    active_cycles = getattr(dispatcher, "_active_cycles", None)
    active_routes = getattr(dispatcher, "_active_routes", None)
    cycle_id = active_cycles.get(robot_id) if isinstance(active_cycles, dict) else None
    route_id = active_routes.get(robot_id) if isinstance(active_routes, dict) else None
    return cycle_id, route_id


def _robot_status_response(
    platform: Any,
    state: QuadrupedState | None,
    *,
    active_cycle_id: str | None,
    active_route_id: str | None,
) -> PatrolRobotStatusResponse:
    return PatrolRobotStatusResponse(
        robot_id=platform.robot_id,
        display_name=getattr(getattr(platform, "config", None), "display_name", None),
        role=_platform_role(platform),
        connected=None if state is None else state.connection_ok,
        battery_pct=None if state is None else state.battery_pct,
        position=_position_response(state),
        active_cycle_id=active_cycle_id,
        active_route_id=active_route_id,
        mode=None if state is None else state.control_mode,
    )


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
    if ui_directory.exists():
        application.mount("/ui", StaticFiles(directory=ui_directory), name="ui")

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok", service="patrol")

    @application.get(
        "/status/summary",
        dependencies=[Depends(require_supervisor)],
    )
    async def status_summary() -> dict[str, Any]:
        return await build_status_summary()

    @application.get(
        "/patrol/status",
        response_model=PatrolStatusResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def patrol_status(
        scheduler: PatrolScheduler = Depends(get_patrol_scheduler_dep),
        dispatcher: PatrolDispatcher = Depends(get_patrol_dispatcher_dep),
        watchdog: PatrolWatchdog = Depends(get_patrol_watchdog_dep),
    ) -> PatrolStatusResponse:
        try:
            scheduler_state = await scheduler.get_state()
            dispatcher_state = await dispatcher.get_state()
            watchdog_state = await watchdog.get_state()
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return PatrolStatusResponse(
            running=bool(getattr(dispatcher_state, "running", False)),
            scheduler_suspended=bool(scheduler_state.suspended),
            dispatcher_suspended=bool(dispatcher_state.suspended),
            active_cycle_id=dispatcher_state.active_cycle_id,
            active_route_id=dispatcher_state.active_route_id,
            consecutive_failures=int(dispatcher_state.consecutive_failures),
            watchdog_suspended=bool(watchdog_state.suspended),
            last_result=dispatcher_state.last_result,
        )

    @application.get(
        "/robots",
        response_model=list[PatrolRobotStatusResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_robots(
        dispatcher: PatrolDispatcher = Depends(get_patrol_dispatcher_dep),
    ) -> list[PatrolRobotStatusResponse]:
        robots: list[PatrolRobotStatusResponse] = []
        for platform in _scoped_registry_platforms():
            state = await _load_platform_state(platform.state_monitor)
            active_cycle_id, active_route_id = _active_cycle_for_robot(dispatcher, platform.robot_id)
            robots.append(
                _robot_status_response(
                    platform,
                    state,
                    active_cycle_id=active_cycle_id,
                    active_route_id=active_route_id,
                )
            )
        return robots

    @application.get(
        "/robots/{robot_id}/status",
        response_model=PatrolRobotStatusResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def robot_status(
        robot_id: str,
        dispatcher: PatrolDispatcher = Depends(get_patrol_dispatcher_dep),
    ) -> PatrolRobotStatusResponse:
        platform = _get_patrol_platform(robot_id)
        state = await _load_platform_state(platform.state_monitor)
        active_cycle_id, active_route_id = _active_cycle_for_robot(dispatcher, robot_id)
        return _robot_status_response(
            platform,
            state,
            active_cycle_id=active_cycle_id,
            active_route_id=active_route_id,
        )

    @application.get(
        "/patrol/cycles",
        response_model=list[PatrolCycleResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_cycles(
        limit: int = Query(default=100, ge=1),
        status_filter: str | None = Query(default=None, alias="status"),
        patrol_queue=Depends(get_patrol_queue_dep),
    ) -> list[PatrolCycleResponse]:
        try:
            records = await patrol_queue.get_cycle_history(limit)
            if status_filter is not None:
                records = [
                    record
                    for record in records
                    if (record.status.value if hasattr(record.status, "value") else str(record.status)) == status_filter
                ]
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return [cycle_to_response(record) for record in records]

    @application.get(
        "/patrol/cycles/{cycle_id}",
        response_model=PatrolCycleResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def get_cycle(
        cycle_id: str,
        patrol_queue=Depends(get_patrol_queue_dep),
    ) -> PatrolCycleResponse:
        try:
            record = await patrol_queue.get_cycle(cycle_id)
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return cycle_to_response(record)

    @application.post(
        "/patrol/trigger",
        response_model=PatrolCycleResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def trigger_cycle(
        request: TriggerPatrolRequest,
        patrol_queue=Depends(get_patrol_queue_dep),
    ) -> PatrolCycleResponse:
        logger.info("Patrol manual trigger requested", extra={"route_id": request.route_id, "triggered_by": request.triggered_by})
        try:
            record = await patrol_queue.submit_cycle(route_id=request.route_id, triggered_by=request.triggered_by)
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return cycle_to_response(record)

    @application.post(
        "/patrol/suspend",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def suspend_patrol(
        request: SuspendRequest,
        scheduler: PatrolScheduler = Depends(get_patrol_scheduler_dep),
        dispatcher: PatrolDispatcher = Depends(get_patrol_dispatcher_dep),
    ) -> MessageResponse:
        logger.warning("Patrol suspend requested", extra={"reason": request.reason})
        try:
            await scheduler.suspend(request.reason)
            await dispatcher.suspend(request.reason)
            await get_event_bus().publish(
                patrol_events.PATROL_SUSPENDED,
                {"reason": request.reason},
                source=EVENT_SOURCE,
            )
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return MessageResponse(message="Patrol suspended")

    @application.post(
        "/patrol/resume",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def resume_patrol(
        scheduler: PatrolScheduler = Depends(get_patrol_scheduler_dep),
        dispatcher: PatrolDispatcher = Depends(get_patrol_dispatcher_dep),
    ) -> MessageResponse:
        logger.info("Patrol resume requested")
        try:
            await scheduler.resume("manual resume")
            await dispatcher.resume("manual resume")
            await get_event_bus().publish(
                patrol_events.PATROL_RESUMED,
                {"reason": "manual resume"},
                source=EVENT_SOURCE,
            )
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return MessageResponse(message="Patrol resumed")

    @application.get(
        "/patrol/anomalies",
        response_model=list[AnomalyResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_anomalies(
        resolved: bool | None = Query(default=None),
        zone_id: str | None = Query(default=None),
        anomaly_log=Depends(get_anomaly_log_dep),
    ) -> list[AnomalyResponse]:
        if resolved is True:
            return []
        try:
            records = await anomaly_log.list_unresolved(zone_id=zone_id)
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return [anomaly_to_response(record) for record in records]

    @application.post(
        "/patrol/anomalies/{anomaly_id}/resolve",
        response_model=AnomalyResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def resolve_anomaly(
        anomaly_id: str,
        request: ResolveAnomalyRequest,
        anomaly_log=Depends(get_anomaly_log_dep),
    ) -> AnomalyResponse:
        logger.info("Patrol anomaly resolve requested", extra={"anomaly_id": anomaly_id, "resolved_by": request.resolved_by})
        try:
            record = await anomaly_log.resolve(anomaly_id, request.resolved_by)
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return anomaly_to_response(record)

    @application.get(
        "/patrol/routes",
        response_model=list[RouteResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_routes(
        route_store: RouteStore = Depends(get_route_store_dep),
    ) -> list[RouteResponse]:
        try:
            routes = await route_store.list_routes(active=True)
            patrol_routes = [route for route in routes if route.metadata.get("route_type") == "patrol"]
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return [route_to_response(route) for route in patrol_routes]

    @application.post(
        "/patrol/routes",
        response_model=RouteResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def upsert_route(
        request: PatrolRouteUpsertRequest,
        route_store: RouteStore = Depends(get_route_store_dep),
    ) -> RouteResponse:
        try:
            route = _build_route_definition(request)
            stored = await route_store.upsert_route(route, persist=True)
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return route_to_response(stored)

    @application.get(
        "/patrol/zones",
        response_model=list[ZoneResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_zones(
        zone_config=Depends(get_zone_config_dep),
    ) -> list[ZoneResponse]:
        try:
            zones = await zone_config.list_zones()
        except Exception as exc:
            _raise_patrol_http_error(exc)
        return [zone_to_response(zone) for zone in zones]

    @application.post(
        "/robots/{robot_id}/estop",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def robot_estop(robot_id: str) -> MessageResponse:
        platform = _get_patrol_platform(robot_id)
        sdk_adapter = getattr(platform, "sdk_adapter", None)
        if sdk_adapter is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot SDK unavailable")
        logger.warning("Patrol robot emergency stop requested", extra={"robot_id": robot_id})
        try:
            stopped = await sdk_adapter.passive()
            if not stopped:
                raise PatrolAPIError("Emergency stop unavailable")
            await get_event_bus().publish(
                EventName.ESTOP_TRIGGERED,
                {"source": "patrol_api", "robot_id": robot_id},
                source=EVENT_SOURCE,
            )
        except HTTPException:
            raise
        except PatrolAPIError as exc:
            logger.warning("Patrol robot estop request failed", extra={"error": str(exc), "robot_id": robot_id})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("Patrol robot estop request failed", extra={"error": str(exc), "robot_id": robot_id})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return MessageResponse(message="Emergency stop triggered")

    @application.post(
        "/robots/{robot_id}/estop/release",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def robot_estop_release(robot_id: str) -> MessageResponse:
        platform = _get_patrol_platform(robot_id)
        sdk_adapter = getattr(platform, "sdk_adapter", None)
        if sdk_adapter is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot SDK unavailable")
        logger.info("Patrol robot emergency stop release requested", extra={"robot_id": robot_id})
        try:
            released = await sdk_adapter.stand_up()
            if not released:
                raise PatrolAPIError("Emergency stop release unavailable")
        except PatrolAPIError as exc:
            logger.warning("Patrol robot estop release failed", extra={"error": str(exc), "robot_id": robot_id})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("Patrol robot estop release failed", extra={"error": str(exc), "robot_id": robot_id})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return MessageResponse(message="Emergency stop released")

    @application.post(
        "/estop",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def estop(
        sdk_adapter: SDKAdapter = Depends(get_sdk_adapter_dep),
    ) -> MessageResponse:
        logger.warning("Patrol emergency stop requested")
        try:
            stopped = await sdk_adapter.passive()
            if not stopped:
                raise PatrolAPIError("Emergency stop unavailable")
            await get_event_bus().publish(EventName.ESTOP_TRIGGERED, {"source": "patrol_api"}, source=EVENT_SOURCE)
        except HTTPException:
            raise
        except PatrolAPIError as exc:
            logger.warning("Patrol estop request failed", extra={"error": str(exc)})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("Patrol estop request failed", extra={"error": str(exc)})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return MessageResponse(message="Emergency stop triggered")

    @application.post(
        "/estop/release",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def estop_release(
        sdk_adapter: SDKAdapter = Depends(get_sdk_adapter_dep),
    ) -> MessageResponse:
        logger.info("Patrol emergency stop release requested")
        try:
            released = await sdk_adapter.stand_up()
            if not released:
                raise PatrolAPIError("Emergency stop release unavailable")
        except PatrolAPIError as exc:
            logger.warning("Patrol estop release failed", extra={"error": str(exc)})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.warning("Patrol estop release failed", extra={"error": str(exc)})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return MessageResponse(message="Emergency stop released")

    return application


app = create_app()


__all__ = ["PatrolAPIError", "app", "create_app"]

from __future__ import annotations

"""FastAPI REST surface for quadruped logistics operations."""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import threading
import uuid
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, status
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from shared.api.alerts import get_alert_manager
from shared.api.auth import require_operator, require_supervisor
from shared.api.ws_broker import get_ws_broker, websocket_endpoint
from shared.audit import AuditEvent, get_audit_store
from shared.core.config import get_config
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from shared.observability import Alert, emit_alert, get_alert_router, get_metrics_snapshot, get_robot_health, get_system_health
from shared.navigation.route_store import RouteDefinition, RouteNotFoundError, RouteStore, RouteStoreError, Waypoint, get_route_store
from shared.provisioning import provision_backend
from shared.provisioning.provision_backend import ProvisioningError
from shared.provisioning.provision_models import ProvisionRequest, WifiNetwork
from shared.quadruped.robot_registry import RobotNotFoundError, get_robot_registry
from shared.quadruped.sdk_adapter import SDKAdapter, get_sdk_adapter
from shared.quadruped.state_monitor import QuadrupedState, StateMonitor, get_state_monitor
from apps.logistics.api.commissioning import create_commissioning_router
from apps.logistics.api.hmi import create_hmi_router
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
_PROVISIONING_JOBS: dict[str, dict[str, Any]] = {}
_PROVISIONING_JOBS_LOCK = threading.Lock()


class APIError(Exception):
    """Raised when the API layer cannot safely map a request to application behavior."""


class HealthResponse(BaseModel):
    status: str
    service: str
    runtime: dict[str, Any] | None = None
    robots: list[dict[str, Any]] | None = None
    audit: dict[str, Any] | None = None
    provisioning: dict[str, Any] | None = None


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


class PositionResponse(BaseModel):
    x: float
    y: float
    z: float | None = None


class RobotStatusResponse(BaseModel):
    robot_id: str
    display_name: str | None = None
    role: str | None = None
    connected: bool | None = None
    battery_pct: int | None = None
    position: PositionResponse | None = None
    active_task_id: str | None = None
    mode: int | None = None


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


class ProvisionScanResponse(BaseModel):
    ssid: str
    signal: int | None = None
    security: str | None = None
    is_robot_ap: bool = False


class ProvisionStartRequest(BaseModel):
    dog_ap_ssid: str
    target_wifi_ssid: str
    target_wifi_password: str
    role: str
    robot_id: str | None = None
    display_name: str | None = None
    pc_wifi_iface: str | None = None
    ssh_user: str = "firefly"
    ssh_password: str | None = None
    sdk_lib_path: str = "sdk/zsl-1"


class ProvisionJobResponse(BaseModel):
    job_id: str
    status: str


class ProvisionStatusResponse(BaseModel):
    job_id: str
    status: str
    message: str | None = None
    robot_id: str | None = None
    dog_mac: str | None = None
    dog_ip: str | None = None


class ProvisionedRobotResponse(BaseModel):
    robot_id: str
    display_name: str | None = None
    mac: str | None = None
    quadruped_ip: str | None = None
    role: str | None = None
    enabled: bool | None = None


class AuditEventResponse(BaseModel):
    event_id: str
    timestamp: str
    event_type: str
    severity: str
    actor_type: str
    actor_id: str | None = None
    robot_id: str | None = None
    task_id: str | None = None
    cycle_id: str | None = None
    route_id: str | None = None
    job_id: str | None = None
    message: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AlertResponse(BaseModel):
    alert_id: str
    timestamp: str
    severity: str
    source: str
    event_type: str
    message: str
    robot_id: str | None = None
    task_id: str | None = None
    cycle_id: str | None = None
    route_id: str | None = None
    job_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    acknowledged: bool = False
    acknowledged_at: str | None = None
    acknowledged_by: str | None = None


class AcknowledgeAlertRequest(BaseModel):
    actor_id: str | None = None


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


def _get_provisioning_robots_yaml_path() -> Path:
    return Path("data/robots.yaml")


def _sanitize_provisioning_message(
    message: str | None,
    *,
    target_wifi_password: str | None,
    ssh_password: str | None,
) -> str | None:
    if message is None:
        return None
    sanitized = message
    for secret in (target_wifi_password, ssh_password):
        if secret:
            sanitized = sanitized.replace(secret, "[redacted]")
    return sanitized


def _wifi_network_to_response(network: WifiNetwork) -> ProvisionScanResponse:
    return ProvisionScanResponse(
        ssid=network.ssid,
        signal=network.signal,
        security=network.security,
        is_robot_ap=network.is_robot_ap,
    )


def _provision_job_snapshot(job_id: str) -> dict[str, Any]:
    with _PROVISIONING_JOBS_LOCK:
        job = _PROVISIONING_JOBS.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown job: {job_id}")
        return dict(job)


def _set_provision_job(job_id: str, **updates: Any) -> None:
    with _PROVISIONING_JOBS_LOCK:
        current = dict(_PROVISIONING_JOBS.get(job_id, {}))
        current.update(updates)
        _PROVISIONING_JOBS[job_id] = current


def _append_audit_event(
    *,
    event_type: str,
    severity: str = "info",
    actor_type: str = "system",
    actor_id: str | None = None,
    robot_id: str | None = None,
    task_id: str | None = None,
    cycle_id: str | None = None,
    route_id: str | None = None,
    job_id: str | None = None,
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent | None:
    try:
        event = AuditEvent(
            event_type=event_type,
            severity=severity,
            actor_type=actor_type,
            actor_id=actor_id,
            robot_id=robot_id,
            task_id=task_id,
            cycle_id=cycle_id,
            route_id=route_id,
            job_id=job_id,
            message=message,
            metadata=metadata or {},
        )
        return get_audit_store().append(event)
    except Exception:
        logger.exception(
            "Audit event append failed",
            extra={"event_type": event_type, "robot_id": robot_id, "task_id": task_id, "job_id": job_id},
        )
        return None


def _audit_event_to_response(event: AuditEvent) -> AuditEventResponse:
    return AuditEventResponse(**event.to_dict())


def _alert_to_response(alert: Alert) -> AlertResponse:
    return AlertResponse(**alert.to_dict())


def _run_provisioning_job(
    job_id: str,
    request: ProvisionRequest,
    *,
    display_name: str | None,
    sdk_lib_path: str,
    robots_yaml_path: Path,
) -> None:
    _set_provision_job(job_id, status="running", message="Provisioning started")
    try:
        result = provision_backend.provision_dog(request)
        safe_message = _sanitize_provisioning_message(
            result.message,
            target_wifi_password=request.target_wifi_password,
            ssh_password=request.ssh_password,
        )
        if not result.success:
            _append_audit_event(
                event_type="provisioning_failed",
                severity="error",
                actor_type="api",
                robot_id=result.robot_id,
                job_id=job_id,
                message=safe_message or "Provisioning failed",
                metadata={"role": request.role, "dog_mac": result.dog_mac, "dog_ip": result.dog_ip},
            )
            emit_alert(
                severity="error",
                source="provisioning",
                event_type="provisioning_failed",
                message=safe_message or "Provisioning failed",
                robot_id=result.robot_id,
                job_id=job_id,
                metadata={"role": request.role, "dog_mac": result.dog_mac, "dog_ip": result.dog_ip},
            )
            _set_provision_job(
                job_id,
                status="failed",
                message=safe_message or "Provisioning failed",
                robot_id=result.robot_id,
                dog_mac=result.dog_mac,
                dog_ip=result.dog_ip,
            )
            return

        entry = provision_backend.write_robot_entry(
            result,
            request.role,
            robots_yaml_path,
            display_name=display_name,
            sdk_lib_path=sdk_lib_path,
        )
        _append_audit_event(
            event_type="provisioning_succeeded",
            severity="info",
            actor_type="api",
            robot_id=entry.get("robot_id"),
            job_id=job_id,
            message=safe_message or "Provisioning complete",
            metadata={
                "role": request.role,
                "display_name": display_name,
                "dog_mac": entry.get("mac"),
                "dog_ip": entry.get("quadruped_ip"),
            },
        )
        emit_alert(
            severity="info",
            source="provisioning",
            event_type="provisioning_succeeded",
            message=safe_message or "Provisioning complete",
            robot_id=entry.get("robot_id"),
            job_id=job_id,
            metadata={
                "role": request.role,
                "display_name": display_name,
                "dog_mac": entry.get("mac"),
                "dog_ip": entry.get("quadruped_ip"),
            },
        )
        _set_provision_job(
            job_id,
            status="succeeded",
            message=safe_message or "Provisioning complete",
            robot_id=entry.get("robot_id"),
            dog_mac=entry.get("mac"),
            dog_ip=entry.get("quadruped_ip"),
        )
    except Exception as exc:
        safe_message = _sanitize_provisioning_message(
            str(exc),
            target_wifi_password=request.target_wifi_password,
            ssh_password=request.ssh_password,
        )
        _append_audit_event(
            event_type="provisioning_failed",
            severity="error",
            actor_type="api",
            robot_id=request.robot_id,
            job_id=job_id,
            message=safe_message or "Provisioning failed",
            metadata={"role": request.role, "dog_ap_ssid": request.dog_ap_ssid},
        )
        emit_alert(
            severity="error",
            source="provisioning",
            event_type="provisioning_failed",
            message=safe_message or "Provisioning failed",
            robot_id=request.robot_id,
            job_id=job_id,
            metadata={"role": request.role, "dog_ap_ssid": request.dog_ap_ssid},
        )
        _set_provision_job(job_id, status="failed", message=safe_message or "Provisioning failed")


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


def _platform_role(platform: Any) -> str | None:
    role = getattr(getattr(platform, "config", None), "role", None)
    if role is None:
        role = getattr(getattr(getattr(platform, "config", None), "connection", None), "role", None)
    return role


def _platform_matches_logistics_scope(platform: Any) -> bool:
    role = _platform_role(platform)
    return role in {None, "logistics"}


def _scoped_registry_platforms() -> list[Any]:
    return [platform for platform in get_robot_registry().all() if _platform_matches_logistics_scope(platform)]


def _get_logistics_platform(robot_id: str) -> Any:
    try:
        platform = get_robot_registry().get(robot_id)
    except RobotNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown robot: {robot_id}") from exc
    if not _platform_matches_logistics_scope(platform):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown robot: {robot_id}")
    return platform


async def _load_platform_state(state_monitor: Any) -> QuadrupedState | None:
    try:
        state = await state_monitor.get_current_state()
        if state is None:
            state = await state_monitor.poll_once()
        return state
    except Exception:
        logger.exception("REST platform status fetch failed")
        return None


async def _get_active_task_id_for_robot(dispatcher: Dispatcher, robot_id: str) -> str | None:
    active_tasks = getattr(dispatcher, "_active_tasks", None)
    if isinstance(active_tasks, dict):
        if robot_id in active_tasks:
            return active_tasks.get(robot_id)
    try:
        dispatch_state = await dispatcher.get_state()
    except Exception as exc:
        logger.warning("REST dispatcher status fetch failed", extra={"error": str(exc)})
        return None
    return getattr(dispatch_state, "active_task_id", None)


def _position_response(state: QuadrupedState | None) -> PositionResponse | None:
    if state is None or state.position is None:
        return None
    position = state.position
    z_value = float(position[2]) if len(position) > 2 else None
    return PositionResponse(x=float(position[0]), y=float(position[1]), z=z_value)


def _robot_status_response(platform: Any, state: QuadrupedState | None, active_task_id: str | None) -> RobotStatusResponse:
    return RobotStatusResponse(
        robot_id=platform.robot_id,
        display_name=getattr(getattr(platform, "config", None), "display_name", None),
        role=_platform_role(platform),
        connected=None if state is None else state.connection_ok,
        battery_pct=None if state is None else state.battery_pct,
        position=_position_response(state),
        active_task_id=active_task_id,
        mode=None if state is None else state.control_mode,
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
        await get_alert_router().start()
        await get_alert_manager().start()
        try:
            yield
        finally:
            await get_alert_manager().stop()
            await get_alert_router().stop()
            await get_ws_broker().stop()
            await shutdown_system()

    application = FastAPI(title=config.app.name, lifespan=lifespan)
    application.add_api_websocket_route("/ws", websocket_endpoint)
    application.mount("/ui", StaticFiles(directory=ui_directory), name="ui")
    application.include_router(create_hmi_router())
    application.include_router(create_commissioning_router())

    @application.get("/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        snapshot = await get_system_health()
        return HealthResponse(**snapshot)

    @application.get(
        "/health/robots",
        dependencies=[Depends(require_supervisor)],
    )
    async def health_robots() -> list[dict[str, Any]]:
        return await get_robot_health()

    @application.get(
        "/metrics",
        dependencies=[Depends(require_supervisor)],
    )
    async def metrics() -> dict[str, Any]:
        return await get_metrics_snapshot()

    @application.get(
        "/provision/scan",
        response_model=list[ProvisionScanResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def provision_scan() -> list[ProvisionScanResponse]:
        try:
            networks = provision_backend.scan_wifi_networks()
        except ProvisioningError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Provisioning WiFi scan failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Provisioning WiFi scan failed: {exc}",
            ) from exc
        return [_wifi_network_to_response(network) for network in networks]

    @application.post(
        "/provision/start",
        response_model=ProvisionJobResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def provision_start(request: ProvisionStartRequest) -> ProvisionJobResponse:
        try:
            provision_request = ProvisionRequest(
                dog_ap_ssid=request.dog_ap_ssid,
                target_wifi_ssid=request.target_wifi_ssid,
                target_wifi_password=request.target_wifi_password,
                role=request.role,
                pc_wifi_iface=request.pc_wifi_iface,
                robot_id=request.robot_id,
                ssh_user=request.ssh_user,
                ssh_password=request.ssh_password,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

        job_id = str(uuid.uuid4())
        _set_provision_job(job_id, status="queued", message="Provisioning queued")
        _append_audit_event(
            event_type="provisioning_started",
            severity="info",
            actor_type="api",
            robot_id=request.robot_id,
            job_id=job_id,
            message="Provisioning queued",
            metadata={
                "role": request.role,
                "dog_ap_ssid": request.dog_ap_ssid,
                "target_wifi_ssid": request.target_wifi_ssid,
                "display_name": request.display_name,
                "pc_wifi_iface": request.pc_wifi_iface,
                "ssh_user": request.ssh_user,
                "sdk_lib_path": request.sdk_lib_path,
            },
        )
        asyncio.create_task(
            asyncio.to_thread(
                _run_provisioning_job,
                job_id,
                provision_request,
                display_name=request.display_name,
                sdk_lib_path=request.sdk_lib_path,
                robots_yaml_path=_get_provisioning_robots_yaml_path(),
            )
        )
        return ProvisionJobResponse(job_id=job_id, status="queued")

    @application.get(
        "/provision/status/{job_id}",
        response_model=ProvisionStatusResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def provision_status(job_id: str) -> ProvisionStatusResponse:
        job = _provision_job_snapshot(job_id)
        return ProvisionStatusResponse(
            job_id=job_id,
            status=job.get("status", "queued"),
            message=job.get("message"),
            robot_id=job.get("robot_id"),
            dog_mac=job.get("dog_mac"),
            dog_ip=job.get("dog_ip"),
        )

    @application.get(
        "/provision/robots",
        response_model=list[ProvisionedRobotResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def provision_robots() -> list[ProvisionedRobotResponse]:
        try:
            robots = provision_backend.list_robot_entries(_get_provisioning_robots_yaml_path())
        except ProvisioningError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return [
            ProvisionedRobotResponse(
                robot_id=str(robot.get("robot_id")),
                display_name=robot.get("display_name"),
                mac=robot.get("mac"),
                quadruped_ip=robot.get("quadruped_ip"),
                role=robot.get("role"),
                enabled=robot.get("enabled"),
            )
            for robot in robots
            if robot.get("robot_id") is not None
        ]

    @application.delete(
        "/provision/robots/{robot_id}",
        response_model=ProvisionedRobotResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def delete_provisioned_robot(robot_id: str) -> ProvisionedRobotResponse:
        try:
            removed = provision_backend.remove_robot_entry(robot_id, _get_provisioning_robots_yaml_path())
        except ProvisioningError as exc:
            error_text = str(exc)
            if "Unknown robot_id" in error_text:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error_text) from exc
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error_text) from exc
        _append_audit_event(
            event_type="robot_record_removed",
            severity="warning",
            actor_type="api",
            robot_id=robot_id,
            message="Provisioned robot record removed",
            metadata={"role": removed.get("role"), "dog_mac": removed.get("mac")},
        )
        return ProvisionedRobotResponse(
            robot_id=str(removed.get("robot_id")),
            display_name=removed.get("display_name"),
            mac=removed.get("mac"),
            quadruped_ip=removed.get("quadruped_ip"),
            role=removed.get("role"),
            enabled=removed.get("enabled"),
        )

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

        if task.status != "awaiting_load":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task {task_id!r} is in status {task.status!r}, expected 'awaiting_load'",
            )

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

        if task.status != "awaiting_unload":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Task {task_id!r} is in status {task.status!r}, expected 'awaiting_unload'",
            )

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
        scoped_platforms = _scoped_registry_platforms()
        if scoped_platforms:
            first_platform = scoped_platforms[0]
            state = await _load_platform_state(first_platform.state_monitor)
            active_task_id = await _get_active_task_id_for_robot(dispatcher, first_platform.robot_id)
            return state_to_response(state, active_task_id=active_task_id)

        state = await _load_platform_state(state_monitor)
        active_task_id = await _get_active_task_id_for_robot(dispatcher, "default")
        return state_to_response(state, active_task_id=active_task_id)

    @application.get(
        "/robots",
        response_model=list[RobotStatusResponse],
        dependencies=[Depends(require_operator)],
    )
    async def list_robots(
        dispatcher: Dispatcher = Depends(get_dispatcher_dep),
    ) -> list[RobotStatusResponse]:
        robots: list[RobotStatusResponse] = []
        for platform in _scoped_registry_platforms():
            state = await _load_platform_state(platform.state_monitor)
            active_task_id = await _get_active_task_id_for_robot(dispatcher, platform.robot_id)
            robots.append(_robot_status_response(platform, state, active_task_id))
        return robots

    @application.get(
        "/robots/{robot_id}/status",
        response_model=RobotStatusResponse,
        dependencies=[Depends(require_operator)],
    )
    async def robot_status(
        robot_id: str,
        dispatcher: Dispatcher = Depends(get_dispatcher_dep),
    ) -> RobotStatusResponse:
        platform = _get_logistics_platform(robot_id)
        state = await _load_platform_state(platform.state_monitor)
        active_task_id = await _get_active_task_id_for_robot(dispatcher, robot_id)
        return _robot_status_response(platform, state, active_task_id)

    @application.post(
        "/robots/{robot_id}/estop",
        response_model=MessageResponse,
        dependencies=[Depends(require_operator)],
    )
    async def robot_estop(robot_id: str) -> MessageResponse:
        platform = _get_logistics_platform(robot_id)
        sdk_adapter = getattr(platform, "sdk_adapter", None)
        if sdk_adapter is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot SDK unavailable")
        logger.warning("REST robot emergency stop requested", extra={"robot_id": robot_id})
        try:
            success = await sdk_adapter.passive()
        except Exception as exc:
            logger.exception("REST robot emergency stop failed", extra={"robot_id": robot_id})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quadruped emergency stop unavailable",
            )
        _append_audit_event(
            event_type="estop_triggered",
            severity="warning",
            actor_type="api",
            robot_id=robot_id,
            message="Emergency stop triggered",
        )
        emit_alert(
            severity="warning",
            source="system",
            event_type="estop_triggered",
            message="Emergency stop triggered",
            robot_id=robot_id,
        )
        return MessageResponse(message="Emergency stop triggered")

    @application.post(
        "/robots/{robot_id}/estop/release",
        response_model=MessageResponse,
        dependencies=[Depends(require_supervisor)],
    )
    async def robot_estop_release(robot_id: str) -> MessageResponse:
        platform = _get_logistics_platform(robot_id)
        sdk_adapter = getattr(platform, "sdk_adapter", None)
        if sdk_adapter is None:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Robot SDK unavailable")
        logger.info("REST robot emergency stop release requested", extra={"robot_id": robot_id})
        try:
            success = await sdk_adapter.stand_up()
        except Exception as exc:
            logger.exception("REST robot emergency stop release failed", extra={"robot_id": robot_id})
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        if not success:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Quadruped release unavailable",
            )
        _append_audit_event(
            event_type="estop_released",
            severity="info",
            actor_type="api",
            robot_id=robot_id,
            message="Emergency stop released",
        )
        emit_alert(
            severity="info",
            source="system",
            event_type="estop_released",
            message="Emergency stop released",
            robot_id=robot_id,
        )
        return MessageResponse(message="Emergency stop released")

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
        _append_audit_event(
            event_type="estop_triggered",
            severity="warning",
            actor_type="api",
            message="Emergency stop triggered",
            metadata={"scope": "legacy"},
        )
        emit_alert(
            severity="warning",
            source="system",
            event_type="estop_triggered",
            message="Emergency stop triggered",
            metadata={"scope": "legacy"},
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
        _append_audit_event(
            event_type="estop_released",
            severity="info",
            actor_type="api",
            message="Emergency stop released",
            metadata={"scope": "legacy"},
        )
        emit_alert(
            severity="info",
            source="system",
            event_type="estop_released",
            message="Emergency stop released",
            metadata={"scope": "legacy"},
        )
        return MessageResponse(message="Emergency stop released")

    @application.get(
        "/alerts",
        response_model=list[AlertResponse],
        dependencies=[Depends(require_operator)],
    )
    async def list_alerts(
        severity: str | None = Query(default=None),
        robot_id: str | None = Query(default=None),
        acknowledged: bool | None = Query(default=None),
        limit: int = Query(default=100, ge=1),
    ) -> list[AlertResponse]:
        alerts = get_alert_router().list_alerts(
            severity=severity,
            robot_id=robot_id,
            acknowledged=acknowledged,
            limit=limit,
        )
        return [_alert_to_response(alert) for alert in alerts]

    @application.get(
        "/alerts/{alert_id}",
        response_model=AlertResponse,
        dependencies=[Depends(require_operator)],
    )
    async def get_alert(alert_id: str) -> AlertResponse:
        alert = get_alert_router().get(alert_id)
        if alert is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown alert: {alert_id}")
        return _alert_to_response(alert)

    @application.post(
        "/alerts/{alert_id}/acknowledge",
        response_model=AlertResponse,
        dependencies=[Depends(require_operator)],
    )
    async def acknowledge_alert(alert_id: str, request: AcknowledgeAlertRequest | None = None) -> AlertResponse:
        actor_id = request.actor_id if request is not None else None
        try:
            alert = get_alert_router().acknowledge(alert_id, actor_id=actor_id or "operator")
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return _alert_to_response(alert)

    @application.get(
        "/audit/events",
        response_model=list[AuditEventResponse],
        dependencies=[Depends(require_supervisor)],
    )
    async def list_audit_events(
        robot_id: str | None = Query(default=None),
        event_type: str | None = Query(default=None),
        severity: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1),
    ) -> list[AuditEventResponse]:
        events = get_audit_store().list_events(
            robot_id=robot_id,
            event_type=event_type,
            severity=severity,
            limit=limit,
        )
        return [_audit_event_to_response(event) for event in events]

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
    "create_commissioning_router",
    "create_hmi_router",
    "get_dispatcher_dep",
    "get_route_store_dep",
    "get_sdk_adapter_dep",
    "get_state_monitor_dep",
    "get_task_queue_dep",
    "require_operator",
    "require_supervisor",
]

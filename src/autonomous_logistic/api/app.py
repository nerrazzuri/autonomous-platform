from fastapi import FastAPI
from fastapi.responses import JSONResponse

from autonomous_logistic.adapters.agibot_d1 import AgibotD1Adapter
from autonomous_logistic.adapters.robot import RobotAdapter
from autonomous_logistic.api.schemas import CreateTaskRequest, StationResponse, TaskResponse
from autonomous_logistic.config.settings import AppSettings
from autonomous_logistic.core.errors import DomainError, RobotAdapterUnavailable, TaskNotFound
from autonomous_logistic.core.models import Station, TransportTask
from autonomous_logistic.logging.audit import AuditLogger
from autonomous_logistic.services.system_service import SystemService
from autonomous_logistic.services.task_service import TaskService
from autonomous_logistic.simulation_or_mock.mock_robot import MockRobotAdapter
from autonomous_logistic.state.repositories import RepositoryRegistry


def build_robot_adapter(settings: AppSettings) -> RobotAdapter:
    if settings.robot.adapter == "mock":
        robot = MockRobotAdapter()
        robot.connect()
        return robot
    if settings.robot.adapter == "agibot_d1":
        robot = AgibotD1Adapter(
            sdk_module_name=settings.robot.sdk_module_name,
            robot_ip=settings.robot.robot_ip,
            client_ip=settings.robot.client_ip,
            sdk_port=settings.robot.sdk_port,
            control_level=settings.robot.control_level,
        )
        robot.connect()
        return robot
    raise RobotAdapterUnavailable(f"Unsupported robot adapter: {settings.robot.adapter}")


def create_app(settings: AppSettings | None = None) -> FastAPI:
    app_settings = settings or AppSettings.from_sources()
    registry = RepositoryRegistry(app_settings.db_path)
    registry.initialize()
    seed_stations(registry, app_settings.stations)

    robot = build_robot_adapter(app_settings)
    audit = AuditLogger(registry.events)
    task_service = TaskService(registry.tasks, audit, robot)
    system_service = SystemService(app_settings, registry.stations, robot)

    app = FastAPI(title=app_settings.app_name)
    app.state.settings = app_settings
    app.state.registry = registry

    @app.exception_handler(TaskNotFound)
    def handle_task_not_found(_, exc: TaskNotFound):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(DomainError)
    def handle_domain_error(_, exc: DomainError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.post("/tasks", response_model=TaskResponse, status_code=201)
    def create_task(request: CreateTaskRequest) -> TaskResponse:
        task = task_service.create_task(
            source_point=request.source_point,
            destination_point=request.destination_point,
            requested_by=request.requested_by,
            request_source=request.request_source,
            notes=request.notes,
        )
        return task_to_response(task)

    @app.get("/tasks", response_model=list[TaskResponse])
    def list_tasks() -> list[TaskResponse]:
        return [task_to_response(task) for task in task_service.list_tasks()]

    @app.get("/tasks/{task_id}", response_model=TaskResponse)
    def get_task(task_id: str) -> TaskResponse:
        return task_to_response(task_service.get_task(task_id))

    @app.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
    def cancel_task(task_id: str) -> TaskResponse:
        return task_to_response(task_service.cancel_task(task_id))

    @app.post("/tasks/{task_id}/pause", response_model=TaskResponse)
    def pause_task(task_id: str) -> TaskResponse:
        return task_to_response(task_service.pause_task(task_id))

    @app.post("/tasks/{task_id}/resume", response_model=TaskResponse)
    def resume_task(task_id: str) -> TaskResponse:
        return task_to_response(task_service.resume_task(task_id))

    @app.get("/stations", response_model=list[StationResponse])
    def list_stations() -> list[StationResponse]:
        return [station_to_response(station) for station in system_service.list_stations()]

    @app.get("/health")
    def health() -> dict:
        return system_service.get_health()

    @app.get("/capabilities")
    def capabilities() -> dict:
        return system_service.get_capabilities()

    return app


def seed_stations(registry: RepositoryRegistry, stations: list[Station]) -> None:
    for station in stations:
        registry.stations.upsert(station)


def task_to_response(task: TransportTask) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        source_point=task.source_point,
        destination_point=task.destination_point,
        requested_by=task.requested_by,
        request_source=task.request_source,
        created_at=task.created_at,
        started_at=task.started_at,
        completed_at=task.completed_at,
        status=task.status.value,
        error_code=task.error_code,
        notes=task.notes,
    )


def station_to_response(station: Station) -> StationResponse:
    return StationResponse(
        station_id=station.station_id,
        name=station.name,
        position=station.position,
        metadata=station.metadata,
    )

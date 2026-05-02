from __future__ import annotations

"""HMI action endpoint for TJC serial touchscreen integration."""

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shared.api.auth import require_operator
from shared.core.event_bus import EventName, get_event_bus
from shared.core.logger import get_logger
from apps.logistics.tasks.dispatcher import Dispatcher, get_dispatcher
from apps.logistics.tasks.queue import TaskQueue, TaskQueueError, get_task_queue

logger = get_logger(__name__)
EVENT_SOURCE = "api.hmi"


class HmiActionRequest(BaseModel):
    robot_id: str
    screen_id: str
    action: str
    task_id: str | None = None
    station_id: str | None = None
    destination_id: str | None = None


class HmiDisplayCommand(BaseModel):
    page: str | None = None
    text: str | None = None


class HmiActionResponse(BaseModel):
    success: bool
    message: str
    robot_id: str
    screen_id: str
    task_id: str | None = None
    display: HmiDisplayCommand | None = None


def get_task_queue_dep() -> TaskQueue:
    return get_task_queue()


def get_dispatcher_dep() -> Dispatcher:
    return get_dispatcher()


def _require_task_id(task_id: str | None, action: str) -> str:
    if not task_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"task_id is required for action {action!r}",
        )
    return task_id


def _require_station_destination(
    station_id: str | None,
    destination_id: str | None,
    action: str,
) -> tuple[str, str]:
    if not station_id or not destination_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"station_id and destination_id are required for action {action!r}",
        )
    return station_id, destination_id


def create_hmi_router() -> APIRouter:
    router = APIRouter(prefix="/hmi", tags=["hmi"])

    @router.post(
        "/action",
        response_model=HmiActionResponse,
        dependencies=[Depends(require_operator)],
    )
    async def hmi_action(
        request: HmiActionRequest,
        task_queue: TaskQueue = Depends(get_task_queue_dep),
        dispatcher: Dispatcher = Depends(get_dispatcher_dep),
    ) -> HmiActionResponse:
        action = request.action.upper()
        logger.info("HMI action received", extra={"action": action})

        if action == "CONFIRM_LOAD":
            tid = _require_task_id(request.task_id, action)
            try:
                task = await task_queue.get_task(tid)
            except TaskQueueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            if task.status != "awaiting_load":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Task {tid!r} is in status {task.status!r}, expected 'awaiting_load'",
                )
            await get_event_bus().publish(
                EventName.HUMAN_CONFIRMED_LOAD,
                payload={
                    "task_id": tid,
                    "robot_id": request.robot_id,
                    "screen_id": request.screen_id,
                    "station_id": task.station_id,
                    "destination_id": task.destination_id,
                    "status": task.status,
                },
                source=EVENT_SOURCE,
                task_id=tid,
            )
            return HmiActionResponse(
                success=True,
                message="Load confirmed",
                robot_id=request.robot_id,
                screen_id=request.screen_id,
                task_id=tid,
                display=HmiDisplayCommand(page="in_transit", text="Delivering..."),
            )

        if action == "CONFIRM_UNLOAD":
            tid = _require_task_id(request.task_id, action)
            try:
                task = await task_queue.get_task(tid)
            except TaskQueueError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            if task.status != "awaiting_unload":
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"Task {tid!r} is in status {task.status!r}, expected 'awaiting_unload'",
                )
            await get_event_bus().publish(
                EventName.HUMAN_CONFIRMED_UNLOAD,
                payload={
                    "task_id": tid,
                    "robot_id": request.robot_id,
                    "screen_id": request.screen_id,
                    "station_id": task.station_id,
                    "destination_id": task.destination_id,
                    "status": task.status,
                },
                source=EVENT_SOURCE,
                task_id=tid,
            )
            return HmiActionResponse(
                success=True,
                message="Unload confirmed",
                robot_id=request.robot_id,
                screen_id=request.screen_id,
                task_id=tid,
                display=HmiDisplayCommand(page="idle", text="Task complete"),
            )

        if action == "PAUSE_DISPATCHER":
            await dispatcher.pause(reason="hmi")
            return HmiActionResponse(
                success=True,
                message="Dispatcher paused",
                robot_id=request.robot_id,
                screen_id=request.screen_id,
                display=HmiDisplayCommand(page="paused", text="System paused"),
            )

        if action == "RESUME_DISPATCHER":
            await dispatcher.resume()
            return HmiActionResponse(
                success=True,
                message="Dispatcher resumed",
                robot_id=request.robot_id,
                screen_id=request.screen_id,
                display=HmiDisplayCommand(page="idle", text="System active"),
            )

        if action == "CONFIRM_OBSTACLE_CLEARED":
            await get_event_bus().publish(
                EventName.OBSTACLE_CLEARED,
                payload={"robot_id": request.robot_id, "screen_id": request.screen_id},
                source=EVENT_SOURCE,
            )
            return HmiActionResponse(
                success=True,
                message="Obstacle cleared signal sent",
                robot_id=request.robot_id,
                screen_id=request.screen_id,
                display=HmiDisplayCommand(page="idle", text="Resuming..."),
            )

        if action in ("REQUEST_TASK", "RETURN_TO_DOCK"):
            station, destination = _require_station_destination(
                request.station_id, request.destination_id, action
            )
            try:
                task = await task_queue.submit_task(
                    station_id=station,
                    destination_id=destination,
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
            return HmiActionResponse(
                success=True,
                message=f"Task {task.id} queued",
                robot_id=request.robot_id,
                screen_id=request.screen_id,
                task_id=task.id,
                display=HmiDisplayCommand(page="queued", text=f"Task queued: {station} → {destination}"),
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unknown HMI action: {request.action!r}",
        )

    return router


__all__ = [
    "HmiActionRequest",
    "HmiActionResponse",
    "HmiDisplayCommand",
    "create_hmi_router",
    "get_dispatcher_dep",
    "get_task_queue_dep",
]

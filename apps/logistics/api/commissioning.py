from __future__ import annotations

"""Supervisor-only commissioning endpoints for station and route capture."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from shared.api.auth import require_supervisor
from apps.logistics.commissioning.service import (
    CommissioningError,
    CommissioningStore,
    CurrentPose,
    PoseUnavailableError,
    get_commissioning_store,
    get_current_commissioning_pose,
)


class PoseResponse(BaseModel):
    x: float
    y: float
    yaw: float
    source: str
    confidence: float | None = None


class CurrentPoseResponse(BaseModel):
    available: bool
    pose: PoseResponse


class MarkStationRequest(BaseModel):
    label: str | None = None


class MarkStationResponse(BaseModel):
    station: dict[str, Any]


class AddWaypointRequest(BaseModel):
    waypoint_id: str | None = None
    hold: bool = False
    hold_reason: str | None = None


class RouteSummaryResponse(BaseModel):
    route: dict[str, Any]


class PlaceholderRequest(BaseModel):
    placeholder: bool


def get_commissioning_store_dep() -> CommissioningStore:
    return get_commissioning_store()


async def get_current_pose_dep() -> CurrentPose:
    return await get_current_commissioning_pose()


def create_commissioning_router() -> APIRouter:
    router = APIRouter(
        prefix="/commissioning",
        tags=["commissioning"],
        dependencies=[Depends(require_supervisor)],
    )

    @router.get("/pose", response_model=CurrentPoseResponse)
    async def get_pose() -> CurrentPoseResponse:
        pose = await _get_pose_or_409()
        return CurrentPoseResponse(available=True, pose=_pose_response(pose))

    @router.post("/stations/{station_id}/mark-current", response_model=MarkStationResponse)
    async def mark_station_current(
        station_id: str,
        request: MarkStationRequest,
        store: CommissioningStore = Depends(get_commissioning_store_dep),
    ) -> MarkStationResponse:
        pose = await _get_pose_or_409()
        try:
            station = store.mark_station(station_id, pose, label=request.label)
        except CommissioningError as exc:
            raise _commissioning_http_error(exc) from exc
        return MarkStationResponse(station=station)

    @router.post("/routes/{route_id}/waypoints/add-current", response_model=RouteSummaryResponse)
    async def add_current_waypoint(
        route_id: str,
        request: AddWaypointRequest,
        store: CommissioningStore = Depends(get_commissioning_store_dep),
    ) -> RouteSummaryResponse:
        pose = await _get_pose_or_409()
        try:
            route = store.append_waypoint(
                route_id,
                pose,
                waypoint_id=request.waypoint_id,
                hold=request.hold,
                hold_reason=request.hold_reason,
            )
        except CommissioningError as exc:
            raise _commissioning_http_error(exc) from exc
        return RouteSummaryResponse(route=_route_summary(route))

    @router.post("/routes/{route_id}/placeholder", response_model=RouteSummaryResponse)
    async def set_route_placeholder(
        route_id: str,
        request: PlaceholderRequest,
        store: CommissioningStore = Depends(get_commissioning_store_dep),
    ) -> RouteSummaryResponse:
        try:
            route = store.set_route_placeholder(route_id, request.placeholder)
        except CommissioningError as exc:
            raise _commissioning_http_error(exc) from exc
        return RouteSummaryResponse(route=_route_summary(route))

    return router


async def _get_pose_or_409() -> CurrentPose:
    try:
        return await get_current_pose_dep()
    except PoseUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Current pose unavailable") from exc


def _pose_response(pose: CurrentPose) -> PoseResponse:
    return PoseResponse(
        x=pose.x,
        y=pose.y,
        yaw=pose.yaw,
        source=pose.source,
        confidence=pose.confidence,
    )


def _route_summary(route: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": route.get("id"),
        "origin_id": route.get("origin_id"),
        "destination_id": route.get("destination_id"),
        "active": bool(route.get("active", True)),
        "placeholder": bool(route.get("placeholder", False)),
        "waypoint_count": len(route.get("waypoints", [])) if isinstance(route.get("waypoints"), list) else 0,
        "waypoints": list(route.get("waypoints", [])) if isinstance(route.get("waypoints"), list) else [],
    }


def _commissioning_http_error(exc: CommissioningError) -> HTTPException:
    message = str(exc)
    status_code = status.HTTP_400_BAD_REQUEST
    if "not found" in message.lower():
        status_code = status.HTTP_404_NOT_FOUND
    if "at least one waypoint" in message.lower():
        status_code = status.HTTP_409_CONFLICT
    return HTTPException(status_code=status_code, detail=message)


__all__ = [
    "create_commissioning_router",
    "get_commissioning_store_dep",
    "get_current_pose_dep",
]

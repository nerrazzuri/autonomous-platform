from __future__ import annotations

"""File-backed commissioning writes for station and route capture."""

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from shared.core.config import get_config
from shared.core.logger import get_logger


logger = get_logger(__name__)


class CommissioningError(Exception):
    """Raised when commissioning files cannot be safely updated."""


class PoseUnavailableError(CommissioningError):
    """Raised when no real current pose is available to capture."""


@dataclass(frozen=True)
class CurrentPose:
    x: float
    y: float
    yaw: float
    source: str
    confidence: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite("x", self.x))
        object.__setattr__(self, "y", _finite("y", self.y))
        object.__setattr__(self, "yaw", _finite("yaw", self.yaw))
        if not isinstance(self.source, str) or not self.source.strip():
            raise CommissioningError("pose source must not be empty")
        object.__setattr__(self, "source", self.source.strip())
        if self.confidence is not None:
            confidence = _finite("confidence", self.confidence)
            if confidence < 0.0 or confidence > 1.0:
                raise CommissioningError("confidence must be between 0.0 and 1.0")
            object.__setattr__(self, "confidence", confidence)

    def to_capture_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "x": self.x,
            "y": self.y,
            "yaw": self.yaw,
            "source": self.source,
            "confidence": self.confidence,
            "captured_at": datetime.now(timezone.utc).isoformat(),
        }
        return payload


class CommissioningStore:
    def __init__(self, stations_path: Path, routes_path: Path) -> None:
        self.stations_path = Path(stations_path)
        self.routes_path = Path(routes_path)

    def mark_station(self, station_id: str, pose: CurrentPose, *, label: str | None = None) -> dict[str, Any]:
        normalized_station_id = _non_empty("station_id", station_id)
        stations_payload = _read_json_object(self.stations_path)
        stations = _list_field(stations_payload, "stations")
        station = _find_by_id(stations, normalized_station_id, "Station")

        capture = pose.to_capture_dict()
        station["x"] = capture["x"]
        station["y"] = capture["y"]
        station["yaw"] = capture["yaw"]
        station["pose"] = capture
        if label is not None and label.strip():
            station["name"] = label.strip()
        metadata = station.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        metadata["commissioning_pose"] = dict(capture)
        station["metadata"] = metadata

        _atomic_write_json(self.stations_path, stations_payload)
        logger.info("Commissioning station marked", extra={"station_id": normalized_station_id, "source": pose.source})
        return dict(station)

    def append_waypoint(
        self,
        route_id: str,
        pose: CurrentPose,
        *,
        waypoint_id: str | None = None,
        hold: bool = False,
        hold_reason: str | None = None,
    ) -> dict[str, Any]:
        normalized_route_id = _non_empty("route_id", route_id)
        routes_payload = _read_json_object(self.routes_path)
        routes = _list_field(routes_payload, "routes")
        route = _find_by_id(routes, normalized_route_id, "Route")

        waypoints = route.get("waypoints")
        if not isinstance(waypoints, list):
            raise CommissioningError(f"Route {normalized_route_id} waypoints must be a list")

        resolved_waypoint_id = _resolve_waypoint_id(waypoints, waypoint_id)
        capture = pose.to_capture_dict()
        waypoint = {
            "id": resolved_waypoint_id,
            "name": resolved_waypoint_id,
            "x": capture["x"],
            "y": capture["y"],
            "yaw": capture["yaw"],
            "heading_deg": math.degrees(capture["yaw"]),
            "velocity": 0.25,
            "hold": bool(hold),
            "hold_reason": hold_reason,
            "source": capture["source"],
            "confidence": capture["confidence"],
            "captured_at": capture["captured_at"],
        }
        waypoints.append(waypoint)
        route["waypoints"] = waypoints

        _atomic_write_json(self.routes_path, routes_payload)
        logger.info(
            "Commissioning waypoint appended",
            extra={"route_id": normalized_route_id, "waypoint_id": resolved_waypoint_id},
        )
        return dict(route)

    def set_route_placeholder(self, route_id: str, placeholder: bool) -> dict[str, Any]:
        normalized_route_id = _non_empty("route_id", route_id)
        routes_payload = _read_json_object(self.routes_path)
        routes = _list_field(routes_payload, "routes")
        route = _find_by_id(routes, normalized_route_id, "Route")
        waypoints = route.get("waypoints")
        if not isinstance(waypoints, list):
            raise CommissioningError(f"Route {normalized_route_id} waypoints must be a list")
        if placeholder is False and not waypoints:
            raise CommissioningError(f"Route {normalized_route_id} needs at least one waypoint before placeholder=false")

        route["placeholder"] = bool(placeholder)
        _atomic_write_json(self.routes_path, routes_payload)
        logger.info(
            "Commissioning route placeholder updated",
            extra={"route_id": normalized_route_id, "placeholder": bool(placeholder)},
        )
        return dict(route)


async def get_current_commissioning_pose() -> CurrentPose:
    config = get_config()

    if getattr(config.navigation, "position_source", "odometry") == "slam":
        try:
            from shared.navigation.slam import SLAMProvider
            from shared.quadruped.state_monitor import get_state_monitor

            provider = SLAMProvider(state_monitor=get_state_monitor(), enabled=True)
            corrected_position = await provider._compute_corrected_position()
            if corrected_position is not None:
                return CurrentPose(
                    x=corrected_position.x,
                    y=corrected_position.y,
                    yaw=corrected_position.heading_rad,
                    source=corrected_position.source,
                    confidence=corrected_position.confidence,
                )
        except Exception as exc:
            logger.warning("Commissioning SLAM pose unavailable", extra={"error": str(exc)})

    try:
        from shared.quadruped.state_monitor import get_state_monitor

        state = await get_state_monitor().get_current_state()
    except Exception as exc:
        raise PoseUnavailableError("Current pose unavailable") from exc

    if state is None:
        raise PoseUnavailableError("Current pose unavailable")

    return CurrentPose(
        x=state.position[0],
        y=state.position[1],
        yaw=state.rpy[2],
        source="odometry",
        confidence=None,
    )


def get_commissioning_store() -> CommissioningStore:
    config = get_config()
    return CommissioningStore(stations_path=config.stations_path(), routes_path=config.routes_path())


def _finite(field_name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise CommissioningError(f"{field_name} must be a finite number")
    return float(value)


def _non_empty(field_name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CommissioningError(f"{field_name} must not be empty")
    return value.strip()


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CommissioningError(f"Commissioning file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise CommissioningError(f"Malformed JSON in {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise CommissioningError(f"{path} must contain a JSON object")
    return payload


def _list_field(payload: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    value = payload.get(field_name)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise CommissioningError(f"{field_name} must be a list of objects")
    return value


def _find_by_id(items: list[dict[str, Any]], item_id: str, label: str) -> dict[str, Any]:
    for item in items:
        if item.get("id") == item_id:
            return item
    raise CommissioningError(f"{label} not found: {item_id}")


def _resolve_waypoint_id(waypoints: list[dict[str, Any]], waypoint_id: str | None) -> str:
    if waypoint_id is not None and waypoint_id.strip():
        return waypoint_id.strip()
    return f"wp_{len(waypoints) + 1:03d}"


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


__all__ = [
    "CommissioningError",
    "CommissioningStore",
    "CurrentPose",
    "PoseUnavailableError",
    "get_commissioning_store",
    "get_current_commissioning_pose",
]

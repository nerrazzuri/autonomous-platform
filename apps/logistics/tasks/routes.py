from __future__ import annotations

"""Placeholder logistics station/route validation for backend task intake."""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from shared.core.config import get_config


class RouteValidationError(Exception):
    """Raised when a requested logistics station pair is not allowed."""


@dataclass(frozen=True)
class Station:
    id: str
    name: str
    type: str
    enabled: bool
    pose: dict | None
    placeholder: bool


@dataclass(frozen=True)
class LogisticsRoute:
    id: str
    origin_id: str
    destination_id: str
    enabled: bool
    placeholder: bool
    waypoints: list


class LogisticsRouteStore:
    def __init__(self, stations: list[Station], routes: list[LogisticsRoute]) -> None:
        self._stations = {station.id: station for station in stations}
        self._routes = {route.id: route for route in routes}

    @classmethod
    def load(cls, path: str | Path | None = None) -> "LogisticsRouteStore":
        resolved_path = Path(path) if path is not None else get_config().logistics_routes_path()
        try:
            payload = json.loads(resolved_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise RouteValidationError(f"Logistics route file not found: {resolved_path}") from exc
        except json.JSONDecodeError as exc:
            raise RouteValidationError(f"Malformed logistics route file: {resolved_path}: {exc}") from exc

        if not isinstance(payload, dict):
            raise RouteValidationError("Logistics route file must contain a JSON object")

        stations = [_station_from_dict(item) for item in _list_field(payload, "stations")]
        routes = [_route_from_dict(item) for item in _list_field(payload, "routes")]
        return cls(stations=stations, routes=routes)

    def get_station(self, station_id: str) -> Station | None:
        return self._stations.get(station_id)

    def find_route(self, origin_id: str, destination_id: str) -> LogisticsRoute | None:
        for route in self._routes.values():
            if route.origin_id == origin_id and route.destination_id == destination_id:
                return route
        for route in self._routes.values():
            if route.origin_id == "*" and route.destination_id == destination_id:
                return route
        return None

    def validate_task_request(
        self,
        origin_id: str,
        destination_id: str,
        *,
        allow_placeholder: bool = True,
    ) -> LogisticsRoute:
        origin = _normalize_station_id("station_id", origin_id)
        destination = _normalize_station_id("destination_id", destination_id)
        if origin == destination:
            raise RouteValidationError("Station and destination must differ")

        origin_station = self.get_station(origin)
        if origin_station is None:
            raise RouteValidationError(f"Unknown station: {origin}")
        if not origin_station.enabled:
            raise RouteValidationError(f"Station disabled: {origin}")

        destination_station = self.get_station(destination)
        if destination_station is None:
            raise RouteValidationError(f"Unknown destination: {destination}")
        if not destination_station.enabled:
            raise RouteValidationError(f"Destination disabled: {destination}")

        route = self.find_route(origin, destination)
        if route is None:
            raise RouteValidationError(f"Route not configured: {origin} -> {destination}")
        if not route.enabled:
            raise RouteValidationError(f"Route disabled: {route.id}")
        if not allow_placeholder and (route.placeholder or not route.waypoints):
            raise RouteValidationError(f"Route {route.id} is placeholder-only")
        return route


def get_logistics_route_store() -> LogisticsRouteStore:
    return LogisticsRouteStore.load()


def _list_field(payload: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    value = payload.get(field_name, [])
    if not isinstance(value, list):
        raise RouteValidationError(f"{field_name} must be a list")
    if not all(isinstance(item, dict) for item in value):
        raise RouteValidationError(f"{field_name} must contain objects")
    return value


def _station_from_dict(data: dict[str, Any]) -> Station:
    pose = data.get("pose")
    if pose is not None and not isinstance(pose, dict):
        raise RouteValidationError("station.pose must be an object or null")
    return Station(
        id=_normalize_station_id("station.id", data.get("id")),
        name=_required_str("station.name", data.get("name")),
        type=_required_str("station.type", data.get("type")),
        enabled=bool(data.get("enabled", True)),
        pose=pose,
        placeholder=bool(data.get("placeholder", False)),
    )


def _route_from_dict(data: dict[str, Any]) -> LogisticsRoute:
    waypoints = data.get("waypoints", [])
    if not isinstance(waypoints, list):
        raise RouteValidationError("route.waypoints must be a list")
    return LogisticsRoute(
        id=_required_str("route.id", data.get("id")),
        origin_id=_required_str("route.origin_id", data.get("origin_id")),
        destination_id=_normalize_station_id("route.destination_id", data.get("destination_id")),
        enabled=bool(data.get("enabled", True)),
        placeholder=bool(data.get("placeholder", False)),
        waypoints=list(waypoints),
    )


def _required_str(field_name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RouteValidationError(f"{field_name} must not be empty")
    return value.strip()


def _normalize_station_id(field_name: str, value: object) -> str:
    return _required_str(field_name, value).upper()


__all__ = [
    "LogisticsRoute",
    "LogisticsRouteStore",
    "RouteValidationError",
    "Station",
    "get_logistics_route_store",
]

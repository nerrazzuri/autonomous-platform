from __future__ import annotations

"""File-backed registry for quadruped routes and stations."""

import asyncio
import json
import math
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from core.config import get_config
from core.database import Database, get_database
from core.event_bus import EventName, get_event_bus
from core.logger import get_logger


logger = get_logger(__name__)


class RouteStoreError(Exception):
    """Raised when route or station definitions cannot be loaded or validated."""


class RouteNotFoundError(RouteStoreError):
    """Raised when a requested route definition does not exist."""


class StationNotFoundError(RouteStoreError):
    """Raised when a requested station definition does not exist."""


def _validate_non_empty(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RouteStoreError(f"{field_name} must not be empty")
    return value.strip()


def _validate_finite_number(field_name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise RouteStoreError(f"{field_name} must be a finite number")
    return float(value)


def _validate_optional_finite_number(field_name: str, value: Any) -> float | None:
    if value is None:
        return None
    return _validate_finite_number(field_name, value)


def _validate_metadata(metadata: Any) -> dict[str, Any]:
    if metadata is None:
        return {}
    if not isinstance(metadata, dict):
        raise RouteStoreError("metadata must be a dictionary")
    return dict(metadata)


def _clone_waypoint(waypoint: "Waypoint") -> "Waypoint":
    return Waypoint.from_dict(waypoint.to_dict())


def _clone_route(route: "RouteDefinition") -> "RouteDefinition":
    return RouteDefinition.from_dict(route.to_dict())


def _clone_station(station: "StationDefinition") -> "StationDefinition":
    return StationDefinition.from_dict(station.to_dict())


@dataclass(frozen=True)
class Waypoint:
    name: str
    x: float
    y: float
    heading_deg: float
    velocity: float = 0.25
    hold: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _validate_non_empty("waypoint.name", self.name))
        object.__setattr__(self, "x", _validate_finite_number("waypoint.x", self.x))
        object.__setattr__(self, "y", _validate_finite_number("waypoint.y", self.y))
        object.__setattr__(self, "heading_deg", _validate_finite_number("waypoint.heading_deg", self.heading_deg))
        velocity = _validate_finite_number("waypoint.velocity", self.velocity)
        if velocity <= 0:
            raise RouteStoreError("waypoint.velocity must be greater than 0")
        object.__setattr__(self, "velocity", velocity)
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "heading_deg": self.heading_deg,
            "velocity": self.velocity,
            "hold": self.hold,
        }
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Waypoint":
        if not isinstance(data, dict):
            raise RouteStoreError("waypoint definition must be a dictionary")
        return cls(
            name=data.get("name", ""),
            x=data.get("x"),
            y=data.get("y"),
            heading_deg=data.get("heading_deg"),
            velocity=data.get("velocity", 0.25),
            hold=bool(data.get("hold", False)),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class RouteDefinition:
    id: str
    name: str
    origin_id: str
    destination_id: str
    waypoints: list[Waypoint]
    active: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_non_empty("route.id", self.id))
        object.__setattr__(self, "name", _validate_non_empty("route.name", self.name))
        object.__setattr__(self, "origin_id", _validate_non_empty("route.origin_id", self.origin_id))
        object.__setattr__(self, "destination_id", _validate_non_empty("route.destination_id", self.destination_id))
        if not isinstance(self.waypoints, list) or not self.waypoints:
            raise RouteStoreError("route.waypoints must contain at least one waypoint")
        normalized_waypoints: list[Waypoint] = []
        for waypoint in self.waypoints:
            if isinstance(waypoint, Waypoint):
                normalized_waypoints.append(_clone_waypoint(waypoint))
            elif isinstance(waypoint, dict):
                normalized_waypoints.append(Waypoint.from_dict(waypoint))
            else:
                raise RouteStoreError("route.waypoints must contain waypoint definitions")
        object.__setattr__(self, "waypoints", normalized_waypoints)
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "origin_id": self.origin_id,
            "destination_id": self.destination_id,
            "active": self.active,
            "waypoints": [waypoint.to_dict() for waypoint in self.waypoints],
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteDefinition":
        if not isinstance(data, dict):
            raise RouteStoreError("route definition must be a dictionary")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            origin_id=data.get("origin_id", ""),
            destination_id=data.get("destination_id", ""),
            active=bool(data.get("active", True)),
            waypoints=list(data.get("waypoints", [])),
            metadata=data.get("metadata", {}),
        )


@dataclass(frozen=True)
class StationDefinition:
    id: str
    name: str
    station_type: str
    x: float | None = None
    y: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "id", _validate_non_empty("station.id", self.id))
        object.__setattr__(self, "name", _validate_non_empty("station.name", self.name))
        object.__setattr__(self, "station_type", _validate_non_empty("station.station_type", self.station_type))
        object.__setattr__(self, "x", _validate_optional_finite_number("station.x", self.x))
        object.__setattr__(self, "y", _validate_optional_finite_number("station.y", self.y))
        object.__setattr__(self, "metadata", _validate_metadata(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "station_type": self.station_type,
            "metadata": dict(self.metadata),
        }
        if self.x is not None:
            payload["x"] = self.x
        if self.y is not None:
            payload["y"] = self.y
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StationDefinition":
        if not isinstance(data, dict):
            raise RouteStoreError("station definition must be a dictionary")
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            station_type=data.get("station_type", ""),
            x=data.get("x"),
            y=data.get("y"),
            metadata=data.get("metadata", {}),
        )


class RouteStore:
    """In-memory route and station registry backed by JSON files."""

    def __init__(
        self,
        routes_file: str | Path | None = None,
        stations_file: str | Path | None = None,
        database: Database | None = None,
        hot_reload_enabled: bool | None = None,
    ) -> None:
        config = get_config()
        self._routes_file = Path(routes_file) if routes_file is not None else config.routes_path()
        self._stations_file = Path(stations_file) if stations_file is not None else config.stations_path()
        self._database = database if database is not None else get_database()
        self._hot_reload_enabled = (
            hot_reload_enabled if hot_reload_enabled is not None else config.routes.hot_reload_enabled
        )
        self._routes: dict[str, RouteDefinition] = {}
        self._stations: dict[str, StationDefinition] = {}
        self._routes_mtime: float | None = None
        self._stations_mtime: float | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        routes = self._load_routes_from_file()
        stations = self._load_stations_from_file()

        async with self._lock:
            self._routes = {route.id: route for route in routes}
            self._stations = {station.id: station for station in stations}
            self._routes_mtime = self._file_mtime(self._routes_file)
            self._stations_mtime = self._file_mtime(self._stations_file)

        await self._persist_loaded_routes(routes)
        self._publish_event(EventName.SYSTEM_STARTED, {"module": "route_store", "action": "loaded"})
        logger.info(
            "Route store loaded",
            extra={
                "routes_file": str(self._routes_file),
                "stations_file": str(self._stations_file),
                "route_count": len(routes),
                "station_count": len(stations),
            },
        )

    async def reload_if_changed(self) -> bool:
        if not self._hot_reload_enabled:
            return False

        current_routes_mtime = self._file_mtime(self._routes_file)
        current_stations_mtime = self._file_mtime(self._stations_file)
        if current_routes_mtime == self._routes_mtime and current_stations_mtime == self._stations_mtime:
            return False

        await self.load()
        self._publish_event(
            EventName.SYSTEM_ALERT,
            {"module": "route_store", "severity": "info", "message": "Route store hot reload completed"},
        )
        logger.info("Route store hot reload completed")
        return True

    async def save_routes(self) -> None:
        async with self._lock:
            payload = {"routes": [route.to_dict() for route in sorted(self._routes.values(), key=lambda item: item.id)]}

        self._routes_file.parent.mkdir(parents=True, exist_ok=True)
        self._routes_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        async with self._lock:
            self._routes_mtime = self._file_mtime(self._routes_file)
        logger.info("Route definitions saved", extra={"routes_file": str(self._routes_file)})

    async def save_stations(self) -> None:
        async with self._lock:
            payload = {
                "stations": [station.to_dict() for station in sorted(self._stations.values(), key=lambda item: item.id)]
            }

        self._stations_file.parent.mkdir(parents=True, exist_ok=True)
        self._stations_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        async with self._lock:
            self._stations_mtime = self._file_mtime(self._stations_file)
        logger.info("Station definitions saved", extra={"stations_file": str(self._stations_file)})

    async def list_routes(self, active: bool | None = None) -> list[RouteDefinition]:
        async with self._lock:
            routes = list(self._routes.values())
        if active is not None:
            routes = [route for route in routes if route.active is active]
        return [_clone_route(route) for route in sorted(routes, key=lambda item: item.id)]

    async def get_route(self, origin_id: str, destination_id: str) -> list[Waypoint]:
        normalized_origin = _validate_non_empty("origin_id", origin_id)
        normalized_destination = _validate_non_empty("destination_id", destination_id)
        async with self._lock:
            matches = [
                route
                for route in self._routes.values()
                if route.active and route.origin_id == normalized_origin and route.destination_id == normalized_destination
            ]
        if not matches:
            raise RouteNotFoundError(f"Active route not found for {normalized_origin} -> {normalized_destination}")
        selected = sorted(matches, key=lambda item: item.id)[0]
        return [_clone_waypoint(waypoint) for waypoint in selected.waypoints]

    async def get_route_definition(self, route_id: str) -> RouteDefinition:
        normalized_route_id = _validate_non_empty("route_id", route_id)
        async with self._lock:
            route = self._routes.get(normalized_route_id)
        if route is None:
            raise RouteNotFoundError(f"Route not found: {normalized_route_id}")
        return _clone_route(route)

    async def upsert_route(self, route: RouteDefinition, *, persist: bool = True) -> RouteDefinition:
        if not isinstance(route, RouteDefinition):
            raise RouteStoreError("route must be a RouteDefinition")
        stored_route = _clone_route(route)
        async with self._lock:
            self._routes[stored_route.id] = stored_route
        if persist:
            await self.save_routes()
            await self._persist_route_to_database(stored_route)
        logger.info("Route definition upserted", extra={"route_id": stored_route.id, "active": stored_route.active})
        return _clone_route(stored_route)

    async def set_route_active(self, route_id: str, active: bool, *, persist: bool = True) -> RouteDefinition:
        normalized_route_id = _validate_non_empty("route_id", route_id)
        async with self._lock:
            route = self._routes.get(normalized_route_id)
            if route is None:
                raise RouteNotFoundError(f"Route not found: {normalized_route_id}")
            updated_route = replace(route, active=active)
            self._routes[normalized_route_id] = updated_route
        if persist:
            await self.save_routes()
            await self._persist_route_to_database(updated_route)
        logger.info("Route active state updated", extra={"route_id": normalized_route_id, "active": active})
        return _clone_route(updated_route)

    async def list_stations(self, station_type: str | None = None) -> list[StationDefinition]:
        async with self._lock:
            stations = list(self._stations.values())
        if station_type is not None:
            normalized_type = _validate_non_empty("station_type", station_type)
            stations = [station for station in stations if station.station_type == normalized_type]
        return [_clone_station(station) for station in sorted(stations, key=lambda item: item.id)]

    async def get_station(self, station_id: str) -> StationDefinition:
        normalized_station_id = _validate_non_empty("station_id", station_id)
        async with self._lock:
            station = self._stations.get(normalized_station_id)
        if station is None:
            raise StationNotFoundError(f"Station not found: {normalized_station_id}")
        return _clone_station(station)

    async def upsert_station(self, station: StationDefinition, *, persist: bool = True) -> StationDefinition:
        if not isinstance(station, StationDefinition):
            raise RouteStoreError("station must be a StationDefinition")
        stored_station = _clone_station(station)
        async with self._lock:
            self._stations[stored_station.id] = stored_station
        if persist:
            await self.save_stations()
        logger.info("Station definition upserted", extra={"station_id": stored_station.id})
        return _clone_station(stored_station)

    def route_count(self) -> int:
        return len(self._routes)

    def station_count(self) -> int:
        return len(self._stations)

    def _load_routes_from_file(self) -> list[RouteDefinition]:
        payload = self._read_collection_file(self._routes_file, "routes")
        routes: list[RouteDefinition] = []
        for item in payload:
            try:
                routes.append(RouteDefinition.from_dict(item))
            except RouteStoreError:
                raise
            except Exception as exc:
                raise RouteStoreError(f"Invalid route definition in {self._routes_file}: {exc}") from exc
        return routes

    def _load_stations_from_file(self) -> list[StationDefinition]:
        payload = self._read_collection_file(self._stations_file, "stations")
        stations: list[StationDefinition] = []
        for item in payload:
            try:
                stations.append(StationDefinition.from_dict(item))
            except RouteStoreError:
                raise
            except Exception as exc:
                raise RouteStoreError(f"Invalid station definition in {self._stations_file}: {exc}") from exc
        return stations

    def _read_collection_file(self, path: Path, top_level_key: str) -> list[dict[str, Any]]:
        if not path.exists():
            logger.warning("%s file missing; continuing with empty registry", top_level_key[:-1].capitalize())
            self._publish_event(
                EventName.SYSTEM_ALERT,
                {
                    "module": "route_store",
                    "severity": "warning",
                    "message": f"{path} is missing",
                },
            )
            return []

        try:
            raw_payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            logger.error("Malformed JSON in %s", path)
            raise RouteStoreError(f"Malformed JSON in {path}: {exc}") from exc
        except OSError as exc:
            logger.error("Failed to read %s", path)
            raise RouteStoreError(f"Failed to read {path}: {exc}") from exc

        if not isinstance(raw_payload, dict):
            raise RouteStoreError(f"{path} must contain a JSON object")

        items = raw_payload.get(top_level_key, [])
        if not isinstance(items, list):
            raise RouteStoreError(f"{path} field '{top_level_key}' must be a list")
        return items

    async def _persist_loaded_routes(self, routes: list[RouteDefinition]) -> None:
        for route in routes:
            await self._persist_route_to_database(route)

    async def _persist_route_to_database(self, route: RouteDefinition) -> None:
        try:
            await self._database.upsert_route(
                route_id=route.id,
                name=route.name,
                origin_id=route.origin_id,
                destination_id=route.destination_id,
                waypoints=[waypoint.to_dict() for waypoint in route.waypoints],
                active=route.active,
            )
        except Exception as exc:
            logger.warning(
                "Route database persistence failed",
                extra={"route_id": route.id, "error": str(exc)},
            )

    def _publish_event(self, event_name: EventName, payload: dict[str, Any]) -> None:
        try:
            get_event_bus().publish_nowait(event_name, payload, source="navigation.route_store")
        except Exception:
            logger.debug("Route store event publish skipped", extra={"event_name": event_name.value})

    @staticmethod
    def _file_mtime(path: Path) -> float | None:
        if not path.exists():
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None


route_store = RouteStore()


def get_route_store() -> RouteStore:
    return route_store


__all__ = [
    "RouteDefinition",
    "RouteNotFoundError",
    "RouteStore",
    "RouteStoreError",
    "StationDefinition",
    "StationNotFoundError",
    "Waypoint",
    "get_route_store",
    "route_store",
]

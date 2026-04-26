from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, time
from pathlib import Path
from typing import Any

import yaml

from shared.core.config import get_config
from shared.core.logger import get_logger


logger = get_logger(__name__)

_HHMM_PATTERN = re.compile(r"^\d{2}:\d{2}$")


class ZoneConfigError(Exception):
    """Raised when patrol zone configuration cannot be loaded or validated."""


class ZoneNotFoundError(ZoneConfigError):
    """Raised when a requested patrol zone does not exist."""


def _validate_non_empty(field_name: str, value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ZoneConfigError(f"{field_name} must not be empty")
    return value.strip()


def _validate_object_list(field_name: str, value: Any) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ZoneConfigError(f"{field_name} must be a list[str]")
    return [item.strip() for item in value]


def _parse_hhmm(field_name: str, value: str) -> time:
    if not isinstance(value, str) or not _HHMM_PATTERN.match(value):
        raise ZoneConfigError(f"{field_name} must use HH:MM format")
    hours, minutes = value.split(":", maxsplit=1)
    hour = int(hours)
    minute = int(minutes)
    if hour > 23 or minute > 59:
        raise ZoneConfigError(f"{field_name} must use HH:MM format")
    return time(hour=hour, minute=minute)


@dataclass(frozen=True)
class TimeRule:
    after: str
    before: str
    escalate_suspicious_to: str = "THREAT"

    def __post_init__(self) -> None:
        _parse_hhmm("after", self.after)
        _parse_hhmm("before", self.before)
        if self.escalate_suspicious_to != "THREAT":
            raise ZoneConfigError("escalate_suspicious_to must be THREAT")

    def matches(self, dt: datetime) -> bool:
        current = dt.timetz().replace(tzinfo=None)
        after_time = _parse_hhmm("after", self.after)
        before_time = _parse_hhmm("before", self.before)
        if after_time <= before_time:
            return after_time <= current <= before_time
        return current >= after_time or current <= before_time


@dataclass(frozen=True)
class ZoneDefinition:
    zone_id: str
    description: str
    normal_objects: list[str]
    suspicious_objects: list[str]
    threat_objects: list[str]
    time_rules: list[TimeRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        object.__setattr__(self, "zone_id", _validate_non_empty("zone_id", self.zone_id))
        object.__setattr__(self, "description", _validate_non_empty("description", self.description))
        object.__setattr__(self, "normal_objects", _validate_object_list("normal_objects", self.normal_objects))
        object.__setattr__(self, "suspicious_objects", _validate_object_list("suspicious_objects", self.suspicious_objects))
        object.__setattr__(self, "threat_objects", _validate_object_list("threat_objects", self.threat_objects))
        if not isinstance(self.time_rules, list) or any(not isinstance(rule, TimeRule) for rule in self.time_rules):
            raise ZoneConfigError("time_rules must be a list[TimeRule]")
        object.__setattr__(self, "time_rules", list(self.time_rules))
        if not any((self.normal_objects, self.suspicious_objects, self.threat_objects)):
            raise ZoneConfigError("at least one zone object list must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return {
            "zone_id": self.zone_id,
            "description": self.description,
            "normal_objects": list(self.normal_objects),
            "suspicious_objects": list(self.suspicious_objects),
            "threat_objects": list(self.threat_objects),
            "time_rules": [
                {
                    "after": rule.after,
                    "before": rule.before,
                    "escalate_suspicious_to": rule.escalate_suspicious_to,
                }
                for rule in self.time_rules
            ],
        }

    @classmethod
    def from_dict(cls, zone_id: str, data: dict[str, Any]) -> ZoneDefinition:
        if not isinstance(data, dict):
            raise ZoneConfigError(f"zone '{zone_id}' must be a mapping")
        raw_time_rules = data.get("time_rules", [])
        if raw_time_rules is None:
            raw_time_rules = []
        if not isinstance(raw_time_rules, list):
            raise ZoneConfigError(f"zone '{zone_id}' time_rules must be a list")
        return cls(
            zone_id=zone_id,
            description=data.get("description", ""),
            normal_objects=data.get("normal_objects", []),
            suspicious_objects=data.get("suspicious_objects", []),
            threat_objects=data.get("threat_objects", []),
            time_rules=[
                TimeRule(
                    after=rule.get("after", ""),
                    before=rule.get("before", ""),
                    escalate_suspicious_to=rule.get("escalate_suspicious_to", "THREAT"),
                )
                for rule in raw_time_rules
                if isinstance(rule, dict)
            ],
        )

    def to_prompt_fragment(self) -> str:
        lines = [
            f"Zone ID: {self.zone_id}",
            f"Description: {self.description}",
            f"Normal Objects: {', '.join(self.normal_objects) if self.normal_objects else 'None'}",
            f"Suspicious Objects: {', '.join(self.suspicious_objects) if self.suspicious_objects else 'None'}",
            f"Threat Objects: {', '.join(self.threat_objects) if self.threat_objects else 'None'}",
        ]
        if self.time_rules:
            lines.append("Time Rules:")
            lines.extend(
                [
                    (
                        f"- After {rule.after}, before {rule.before}, "
                        f"escalate suspicious to {rule.escalate_suspicious_to}"
                    )
                    for rule in self.time_rules
                ]
            )
        return "\n".join(lines)


class ZoneConfig:
    def __init__(
        self,
        zones_file: str | Path | None = None,
        patrol_routes_file: str | Path | None = None,
    ) -> None:
        config = get_config()
        self._zones_file = Path(zones_file) if zones_file is not None else Path(config.vision.zones_file)
        self._patrol_routes_file = (
            Path(patrol_routes_file) if patrol_routes_file is not None else Path("data/patrol_routes.json")
        )
        self._zones: dict[str, ZoneDefinition] = {}
        self._zones_mtime: float | None = None
        self._lock = asyncio.Lock()

    async def load(self) -> None:
        zones = self._load_zones_from_file()
        self._validate_patrol_route_references(zones)

        async with self._lock:
            self._zones = zones
            self._zones_mtime = self._file_mtime(self._zones_file)

        logger.info(
            "Patrol zone config loaded",
            extra={
                "zones_file": str(self._zones_file),
                "patrol_routes_file": str(self._patrol_routes_file),
                "zone_count": len(zones),
            },
        )

    async def reload_if_changed(self) -> bool:
        current_mtime = self._file_mtime(self._zones_file)
        if current_mtime is None:
            raise ZoneConfigError(f"Zones file is missing: {self._zones_file}")
        if current_mtime == self._zones_mtime:
            return False
        await self.load()
        return True

    async def get_zone(self, zone_id: str) -> ZoneDefinition | None:
        normalized_zone_id = _validate_non_empty("zone_id", zone_id)
        async with self._lock:
            return self._zones.get(normalized_zone_id)

    async def require_zone(self, zone_id: str) -> ZoneDefinition:
        zone = await self.get_zone(zone_id)
        if zone is None:
            raise ZoneNotFoundError(f"Zone not found: {zone_id}")
        return zone

    async def list_zones(self) -> list[ZoneDefinition]:
        async with self._lock:
            return [self._zones[zone_id] for zone_id in sorted(self._zones)]

    def zone_count(self) -> int:
        return len(self._zones)

    def _load_zones_from_file(self) -> dict[str, ZoneDefinition]:
        if not self._zones_file.exists():
            raise ZoneConfigError(f"Zones file is missing: {self._zones_file}")

        try:
            with self._zones_file.open("r", encoding="utf-8") as handle:
                payload = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise ZoneConfigError(f"Malformed YAML in {self._zones_file}: {exc}") from exc
        except OSError as exc:
            raise ZoneConfigError(f"Failed to read zones file {self._zones_file}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ZoneConfigError(f"{self._zones_file} must contain a top-level mapping")
        if "zones" not in payload:
            raise ZoneConfigError(f"{self._zones_file} must contain a top-level 'zones' mapping")

        raw_zones = payload["zones"]
        if not isinstance(raw_zones, dict):
            raise ZoneConfigError(f"{self._zones_file} field 'zones' must be a mapping")

        zones: dict[str, ZoneDefinition] = {}
        for zone_id, zone_data in raw_zones.items():
            if not isinstance(zone_id, str):
                raise ZoneConfigError("zone ids must be strings")
            zones[zone_id] = ZoneDefinition.from_dict(zone_id, zone_data)
        return zones

    def _validate_patrol_route_references(self, zones: dict[str, ZoneDefinition]) -> None:
        if not self._patrol_routes_file.exists():
            return

        try:
            with self._patrol_routes_file.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except json.JSONDecodeError as exc:
            raise ZoneConfigError(f"Malformed JSON in {self._patrol_routes_file}: {exc}") from exc
        except OSError as exc:
            raise ZoneConfigError(f"Failed to read patrol routes file {self._patrol_routes_file}: {exc}") from exc

        if not isinstance(payload, dict):
            raise ZoneConfigError(f"{self._patrol_routes_file} must contain a JSON object")
        routes = payload.get("routes", [])
        if not isinstance(routes, list):
            raise ZoneConfigError(f"{self._patrol_routes_file} field 'routes' must be a list")

        missing_zone_ids: set[str] = set()
        missing_zone_id_locations: list[str] = []

        for route in routes:
            if not isinstance(route, dict):
                continue
            route_id = route.get("id", "<unknown-route>")
            waypoints = route.get("waypoints", [])
            if not isinstance(waypoints, list):
                continue
            for index, waypoint in enumerate(waypoints):
                if not isinstance(waypoint, dict):
                    continue
                waypoint_name = waypoint.get("name", f"waypoint-{index}")
                metadata = waypoint.get("metadata")
                if not isinstance(metadata, dict) or metadata.get("observe") is not True:
                    continue
                zone_id = metadata.get("zone_id")
                if not isinstance(zone_id, str) or not zone_id.strip():
                    missing_zone_id_locations.append(f"{route_id}:{waypoint_name}")
                    continue
                if zone_id not in zones:
                    missing_zone_ids.add(zone_id)

        if missing_zone_id_locations:
            locations = ", ".join(sorted(missing_zone_id_locations))
            raise ZoneConfigError(f"Observed patrol waypoints missing metadata.zone_id: {locations}")
        if missing_zone_ids:
            zone_list = ", ".join(sorted(missing_zone_ids))
            raise ZoneConfigError(f"Patrol routes reference unknown zone ids: {zone_list}")

    @staticmethod
    def _file_mtime(path: Path) -> float | None:
        if not path.exists():
            return None
        try:
            return path.stat().st_mtime
        except OSError:
            return None


zone_config = ZoneConfig()


def get_zone_config() -> ZoneConfig:
    return zone_config


__all__ = [
    "TimeRule",
    "ZoneConfig",
    "ZoneConfigError",
    "ZoneDefinition",
    "ZoneNotFoundError",
    "get_zone_config",
    "zone_config",
]

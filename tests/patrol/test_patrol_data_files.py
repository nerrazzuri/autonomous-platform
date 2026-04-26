from __future__ import annotations

import json
from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]
ZONES_PATH = ROOT / "data" / "zones.yaml"
PATROL_ROUTES_PATH = ROOT / "data" / "patrol_routes.json"


def load_zones() -> dict:
    with ZONES_PATH.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)
    assert isinstance(data, dict)
    return data


def load_patrol_routes() -> dict:
    with PATROL_ROUTES_PATH.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    assert isinstance(data, dict)
    return data


def test_patrol_data_files_exist() -> None:
    assert ZONES_PATH.exists()
    assert PATROL_ROUTES_PATH.exists()


def test_zones_yaml_has_expected_structure() -> None:
    data = load_zones()

    assert "zones" in data
    assert isinstance(data["zones"], dict)
    assert len(data["zones"]) >= 2

    for zone_id, zone in data["zones"].items():
        assert isinstance(zone_id, str)
        assert isinstance(zone, dict)
        assert isinstance(zone.get("description"), str)
        assert isinstance(zone.get("normal_objects"), list)
        assert isinstance(zone.get("suspicious_objects"), list)
        assert isinstance(zone.get("threat_objects"), list)

        for time_rule in zone.get("time_rules", []):
            assert isinstance(time_rule, dict)
            assert "after" in time_rule
            assert "before" in time_rule
            assert "escalate_suspicious_to" in time_rule


def test_patrol_routes_json_has_expected_structure() -> None:
    zones = load_zones()["zones"]
    data = load_patrol_routes()

    assert "routes" in data
    assert isinstance(data["routes"], list)

    active_routes = [route for route in data["routes"] if route.get("active") is True]
    assert active_routes

    for route in active_routes:
        metadata = route.get("metadata")
        assert isinstance(metadata, dict)
        assert metadata.get("route_type") == "patrol"

        for waypoint in route.get("waypoints", []):
            waypoint_metadata = waypoint.get("metadata")
            assert isinstance(waypoint_metadata, dict)
            assert isinstance(waypoint_metadata.get("observe"), bool)
            if waypoint_metadata["observe"] is True:
                zone_id = waypoint_metadata.get("zone_id")
                assert isinstance(zone_id, str)
                assert zone_id in zones

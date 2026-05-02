from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.logistics.tasks.routes import LogisticsRouteStore, RouteValidationError


ROOT = Path(__file__).resolve().parents[1]


def test_loads_default_placeholder_route_file() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    assert store.get_station("LINE_A") is not None
    assert store.get_station("LINE_B") is not None
    assert store.get_station("LINE_C") is not None
    assert store.get_station("QA") is not None
    assert store.get_station("DOCK") is not None
    assert store.find_route("LINE_A", "QA").id == "LINE_A_TO_QA"
    assert store.find_route("QA", "LINE_A").id == "QA_TO_LINE_A"


def test_unknown_station_is_rejected() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    with pytest.raises(RouteValidationError, match="Unknown station"):
        store.validate_task_request("MYSTERY", "QA")


def test_unknown_destination_is_rejected() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    with pytest.raises(RouteValidationError, match="Unknown destination"):
        store.validate_task_request("LINE_A", "WAREHOUSE")


def test_disabled_station_is_rejected(tmp_path: Path) -> None:
    path = _write_routes(
        tmp_path,
        stations=[
            _station("LINE_A", enabled=False),
            _station("QA"),
        ],
        routes=[_route("LINE_A_TO_QA", "LINE_A", "QA")],
    )
    store = LogisticsRouteStore.load(path)

    with pytest.raises(RouteValidationError, match="Station disabled"):
        store.validate_task_request("LINE_A", "QA")


def test_disabled_route_is_rejected(tmp_path: Path) -> None:
    path = _write_routes(
        tmp_path,
        stations=[_station("LINE_A"), _station("QA")],
        routes=[_route("LINE_A_TO_QA", "LINE_A", "QA", enabled=False)],
    )
    store = LogisticsRouteStore.load(path)

    with pytest.raises(RouteValidationError, match="Route disabled"):
        store.validate_task_request("LINE_A", "QA")


def test_placeholder_route_allowed_for_current_poc() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    route = store.validate_task_request("LINE_A", "QA", allow_placeholder=True)

    assert route.id == "LINE_A_TO_QA"
    assert route.placeholder is True
    assert route.waypoints == []


def test_placeholder_route_rejected_when_real_waypoints_required() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    with pytest.raises(RouteValidationError, match="placeholder"):
        store.validate_task_request("LINE_A", "QA", allow_placeholder=False)


def test_unsupported_pair_is_rejected() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    with pytest.raises(RouteValidationError, match="Route not configured"):
        store.validate_task_request("LINE_A", "LINE_B")


def test_same_origin_destination_is_rejected() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    with pytest.raises(RouteValidationError, match="must differ"):
        store.validate_task_request("LINE_A", "LINE_A")


def test_return_to_dock_wildcard_route_validates() -> None:
    store = LogisticsRouteStore.load(ROOT / "data" / "logistics_routes.json")

    route = store.validate_task_request("LINE_A", "DOCK")

    assert route.id == "RETURN_TO_DOCK"
    assert route.origin_id == "*"
    assert route.destination_id == "DOCK"


def _station(station_id: str, *, enabled: bool = True) -> dict:
    return {
        "id": station_id,
        "name": station_id,
        "type": "test",
        "enabled": enabled,
        "pose": None,
        "placeholder": True,
    }


def _route(route_id: str, origin_id: str, destination_id: str, *, enabled: bool = True) -> dict:
    return {
        "id": route_id,
        "origin_id": origin_id,
        "destination_id": destination_id,
        "enabled": enabled,
        "placeholder": True,
        "waypoints": [],
    }


def _write_routes(tmp_path: Path, *, stations: list[dict], routes: list[dict]) -> Path:
    path = tmp_path / "logistics_routes.json"
    path.write_text(json.dumps({"version": 1, "stations": stations, "routes": routes}), encoding="utf-8")
    return path

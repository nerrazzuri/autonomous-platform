from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class FakeDatabase:
    def __init__(self):
        self.routes = []
        self.fail = False

    async def upsert_route(self, **kwargs):
        if self.fail:
            raise RuntimeError("database unavailable")
        self.routes.append(kwargs)


def sample_route_payload() -> dict:
    return {
        "id": "A_TO_QA",
        "name": "Line A to QA Lab",
        "origin_id": "A",
        "destination_id": "QA",
        "active": True,
        "waypoints": [
            {
                "name": "A_exit",
                "x": 0.0,
                "y": 0.0,
                "heading_deg": 0.0,
                "velocity": 0.25,
                "hold": True,
            },
            {
                "name": "QA_approach",
                "x": 5.0,
                "y": 2.0,
                "heading_deg": 90.0,
                "velocity": 0.2,
                "hold": True,
            },
        ],
        "metadata": {"notes": "sample"},
    }


def sample_station_payload() -> dict:
    return {
        "id": "A",
        "name": "Production Line A",
        "station_type": "production_line",
        "x": 0.0,
        "y": 0.0,
        "metadata": {"zone": "north"},
    }


@pytest_asyncio.fixture
async def route_store_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from core.event_bus import EventBus
    import navigation.route_store as route_store_module

    event_bus = EventBus()
    await event_bus.start()
    monkeypatch.setattr(route_store_module, "get_event_bus", lambda: event_bus)

    routes_file = tmp_path / "data" / "routes.json"
    stations_file = tmp_path / "data" / "stations.json"
    database = FakeDatabase()
    store = route_store_module.RouteStore(
        routes_file=routes_file,
        stations_file=stations_file,
        database=database,
        hot_reload_enabled=True,
    )
    yield store, routes_file, stations_file, database, event_bus, route_store_module
    await event_bus.stop()


def test_waypoint_from_dict_and_to_dict() -> None:
    from navigation.route_store import Waypoint

    data = {
        "name": "wp1",
        "x": 1.0,
        "y": 2.0,
        "heading_deg": 90.0,
        "velocity": 0.3,
        "hold": True,
        "metadata": {"note": "test"},
    }

    waypoint = Waypoint.from_dict(data)

    assert waypoint.to_dict() == data


@pytest.mark.parametrize(
    "payload",
    [
        {"name": "", "x": 1.0, "y": 2.0, "heading_deg": 0.0},
        {"name": "wp", "x": float("inf"), "y": 2.0, "heading_deg": 0.0},
        {"name": "wp", "x": 1.0, "y": 2.0, "heading_deg": 0.0, "velocity": 0.0},
    ],
)
def test_waypoint_rejects_invalid_values(payload: dict) -> None:
    from navigation.route_store import RouteStoreError, Waypoint

    with pytest.raises(RouteStoreError):
        Waypoint.from_dict(payload)


def test_route_definition_from_dict_and_to_dict() -> None:
    from navigation.route_store import RouteDefinition

    payload = sample_route_payload()

    route = RouteDefinition.from_dict(payload)

    assert route.to_dict() == payload


def test_route_definition_requires_waypoints() -> None:
    from navigation.route_store import RouteDefinition, RouteStoreError

    payload = sample_route_payload()
    payload["waypoints"] = []

    with pytest.raises(RouteStoreError):
        RouteDefinition.from_dict(payload)


def test_station_definition_from_dict_and_to_dict() -> None:
    from navigation.route_store import StationDefinition

    payload = sample_station_payload()

    station = StationDefinition.from_dict(payload)

    assert station.to_dict() == payload


@pytest.mark.asyncio
async def test_load_missing_files_does_not_crash(route_store_env) -> None:
    store, _, _, _, _, _ = route_store_env

    await store.load()

    assert store.route_count() == 0
    assert store.station_count() == 0


@pytest.mark.asyncio
async def test_load_valid_routes_and_stations(route_store_env) -> None:
    store, routes_file, stations_file, _, _, _ = route_store_env
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text(json.dumps({"routes": [sample_route_payload()]}), encoding="utf-8")
    stations_file.write_text(json.dumps({"stations": [sample_station_payload()]}), encoding="utf-8")

    await store.load()

    assert store.route_count() == 1
    assert store.station_count() == 1


@pytest.mark.asyncio
async def test_load_malformed_json_raises(route_store_env) -> None:
    store, routes_file, _, _, _, route_store_module = route_store_env
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text("{bad json", encoding="utf-8")

    with pytest.raises(route_store_module.RouteStoreError):
        await store.load()


@pytest.mark.asyncio
async def test_get_route_returns_active_matching_route(route_store_env) -> None:
    store, routes_file, stations_file, _, _, _ = route_store_env
    payload = sample_route_payload()
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text(json.dumps({"routes": [payload]}), encoding="utf-8")
    stations_file.write_text(json.dumps({"stations": [sample_station_payload()]}), encoding="utf-8")
    await store.load()

    route = await store.get_route("A", "QA")

    assert [waypoint.name for waypoint in route] == ["A_exit", "QA_approach"]


@pytest.mark.asyncio
async def test_get_route_ignores_inactive_route(route_store_env) -> None:
    store, routes_file, stations_file, _, _, route_store_module = route_store_env
    payload = sample_route_payload()
    payload["active"] = False
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text(json.dumps({"routes": [payload]}), encoding="utf-8")
    stations_file.write_text(json.dumps({"stations": [sample_station_payload()]}), encoding="utf-8")
    await store.load()

    with pytest.raises(route_store_module.RouteNotFoundError):
        await store.get_route("A", "QA")


@pytest.mark.asyncio
async def test_get_route_not_found_raises(route_store_env) -> None:
    store, _, _, _, _, route_store_module = route_store_env

    with pytest.raises(route_store_module.RouteNotFoundError):
        await store.get_route("A", "QA")


@pytest.mark.asyncio
async def test_list_routes_active_filter(route_store_env) -> None:
    store, _, _, _, _, route_store_module = route_store_env
    active_route = route_store_module.RouteDefinition.from_dict(sample_route_payload())
    inactive_payload = sample_route_payload()
    inactive_payload["id"] = "QA_TO_A"
    inactive_payload["name"] = "QA Lab to Line A"
    inactive_payload["origin_id"] = "QA"
    inactive_payload["destination_id"] = "A"
    inactive_payload["active"] = False
    inactive_route = route_store_module.RouteDefinition.from_dict(inactive_payload)

    await store.upsert_route(active_route, persist=False)
    await store.upsert_route(inactive_route, persist=False)

    active = await store.list_routes(active=True)
    inactive = await store.list_routes(active=False)

    assert [route.id for route in active] == ["A_TO_QA"]
    assert [route.id for route in inactive] == ["QA_TO_A"]


@pytest.mark.asyncio
async def test_get_route_definition(route_store_env) -> None:
    store, _, _, _, _, route_store_module = route_store_env
    route = route_store_module.RouteDefinition.from_dict(sample_route_payload())
    await store.upsert_route(route, persist=False)

    loaded = await store.get_route_definition(route.id)

    assert loaded.id == route.id


@pytest.mark.asyncio
async def test_set_route_active(route_store_env) -> None:
    store, routes_file, _, _, _, route_store_module = route_store_env
    route = route_store_module.RouteDefinition.from_dict(sample_route_payload())
    await store.upsert_route(route, persist=True)

    updated = await store.set_route_active(route.id, False, persist=True)

    assert updated.active is False
    on_disk = json.loads(routes_file.read_text(encoding="utf-8"))
    assert on_disk["routes"][0]["active"] is False


@pytest.mark.asyncio
async def test_upsert_route_persists_to_file(route_store_env) -> None:
    store, routes_file, _, database, _, route_store_module = route_store_env
    route = route_store_module.RouteDefinition.from_dict(sample_route_payload())

    await store.upsert_route(route, persist=True)

    assert routes_file.exists()
    payload = json.loads(routes_file.read_text(encoding="utf-8"))
    assert payload["routes"][0]["id"] == route.id
    assert database.routes


@pytest.mark.asyncio
async def test_list_stations_type_filter(route_store_env) -> None:
    store, _, _, _, _, route_store_module = route_store_env
    station = route_store_module.StationDefinition.from_dict(sample_station_payload())
    other_station = route_store_module.StationDefinition.from_dict(
        {
            "id": "QA",
            "name": "QA Laboratory",
            "station_type": "qa_lab",
            "x": 5.0,
            "y": 2.0,
        }
    )

    await store.upsert_station(station, persist=False)
    await store.upsert_station(other_station, persist=False)

    qa_stations = await store.list_stations("qa_lab")

    assert [item.id for item in qa_stations] == ["QA"]


@pytest.mark.asyncio
async def test_get_station(route_store_env) -> None:
    store, _, _, _, _, route_store_module = route_store_env
    station = route_store_module.StationDefinition.from_dict(sample_station_payload())
    await store.upsert_station(station, persist=False)

    loaded = await store.get_station("A")

    assert loaded.id == "A"


@pytest.mark.asyncio
async def test_upsert_station_persists_to_file(route_store_env) -> None:
    store, _, stations_file, _, _, route_store_module = route_store_env
    station = route_store_module.StationDefinition.from_dict(sample_station_payload())

    await store.upsert_station(station, persist=True)

    payload = json.loads(stations_file.read_text(encoding="utf-8"))
    assert payload["stations"][0]["id"] == "A"


@pytest.mark.asyncio
async def test_reload_if_changed_returns_false_when_disabled(route_store_env) -> None:
    store, _, _, _, _, _ = route_store_env
    store._hot_reload_enabled = False

    changed = await store.reload_if_changed()

    assert changed is False


@pytest.mark.asyncio
async def test_reload_if_changed_detects_file_change(route_store_env) -> None:
    store, routes_file, stations_file, _, _, _ = route_store_env
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text(json.dumps({"routes": [sample_route_payload()]}), encoding="utf-8")
    stations_file.write_text(json.dumps({"stations": [sample_station_payload()]}), encoding="utf-8")
    await store.load()

    payload = sample_route_payload()
    payload["id"] = "B_TO_QA"
    payload["name"] = "Line B to QA Lab"
    payload["origin_id"] = "B"
    routes_file.write_text(json.dumps({"routes": [payload]}), encoding="utf-8")
    os.utime(routes_file, None)
    await asyncio.sleep(0.02)

    changed = await store.reload_if_changed()

    assert changed is True
    assert store.route_count() == 1
    route = await store.get_route_definition("B_TO_QA")
    assert route.origin_id == "B"


@pytest.mark.asyncio
async def test_database_failure_does_not_block_load(route_store_env) -> None:
    store, routes_file, stations_file, database, _, _ = route_store_env
    database.fail = True
    routes_file.parent.mkdir(parents=True, exist_ok=True)
    routes_file.write_text(json.dumps({"routes": [sample_route_payload()]}), encoding="utf-8")
    stations_file.write_text(json.dumps({"stations": [sample_station_payload()]}), encoding="utf-8")

    await store.load()

    assert store.route_count() == 1


def test_global_get_route_store_returns_route_store() -> None:
    from navigation.route_store import RouteStore, get_route_store

    assert isinstance(get_route_store(), RouteStore)

from __future__ import annotations

from apps.hmi_agent.mapper import ButtonActionMapper, HmiMappedAction
from apps.hmi_agent.protocol import TjcTouchEvent


def test_maps_known_button_to_simple_action() -> None:
    mapper = ButtonActionMapper({"3:1:press": "PAUSE_DISPATCHER"})

    assert mapper.map_touch(TjcTouchEvent(3, 1, "press")) == HmiMappedAction(action="PAUSE_DISPATCHER")


def test_maps_object_action_with_station_and_destination() -> None:
    mapper = ButtonActionMapper(
        {
            "1:1:press": {
                "action": "REQUEST_TASK",
                "station_id": "LINE_A",
                "destination": "QA",
            }
        }
    )

    assert mapper.map_touch(TjcTouchEvent(1, 1, "press")) == HmiMappedAction(
        action="REQUEST_TASK",
        station_id="LINE_A",
        destination="QA",
    )


def test_maps_object_action_with_task_and_route() -> None:
    mapper = ButtonActionMapper(
        {
            "2:1:press": {
                "action": "CONFIRM_LOAD",
                "task_id": "task-1",
                "route_id": "route-a",
            }
        }
    )

    assert mapper.map_touch(TjcTouchEvent(2, 1, "press")) == HmiMappedAction(
        action="CONFIRM_LOAD",
        task_id="task-1",
        route_id="route-a",
    )


def test_unknown_button_returns_none() -> None:
    mapper = ButtonActionMapper({"3:1:press": "PAUSE_DISPATCHER"})

    assert mapper.map_touch(TjcTouchEvent(9, 9, "press")) is None


def test_release_event_is_ignored_when_not_mapped() -> None:
    mapper = ButtonActionMapper({"3:1:press": "PAUSE_DISPATCHER"})

    assert mapper.map_touch(TjcTouchEvent(3, 1, "release")) is None

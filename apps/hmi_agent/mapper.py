from __future__ import annotations

"""Map TJC touch events to backend HMI actions."""

from dataclasses import dataclass
from typing import Any

from apps.hmi_agent.protocol import TjcTouchEvent


@dataclass(frozen=True)
class HmiMappedAction:
    action: str
    station_id: str | None = None
    destination: str | None = None
    task_id: str | None = None
    route_id: str | None = None


class ButtonActionMapper:
    def __init__(self, mapping: dict[str, str | dict[str, Any]]) -> None:
        self._mapping = dict(mapping)

    def map_touch(self, event: TjcTouchEvent) -> HmiMappedAction | None:
        key = f"{event.page_id}:{event.component_id}:{event.touch_event}"
        value = self._mapping.get(key)
        if value is None:
            return None

        if isinstance(value, str):
            return HmiMappedAction(action=value)

        if isinstance(value, dict):
            action = value.get("action")
            if not isinstance(action, str) or not action:
                return None
            destination = value.get("destination", value.get("destination_id"))
            return HmiMappedAction(
                action=action,
                station_id=_optional_str(value.get("station_id")),
                destination=_optional_str(destination),
                task_id=_optional_str(value.get("task_id")),
                route_id=_optional_str(value.get("route_id")),
            )

        return None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


DEFAULT_BUTTON_MAPPING: dict[str, str | dict[str, str]] = {
    "1:1:press": {"action": "REQUEST_TASK", "station_id": "LINE_A", "destination": "QA"},
    "1:2:press": {"action": "REQUEST_TASK", "station_id": "LINE_B", "destination": "QA"},
    "1:3:press": {"action": "REQUEST_TASK", "station_id": "LINE_C", "destination": "QA"},
    "1:4:press": {"action": "RETURN_TO_DOCK", "station_id": "QA", "destination": "DOCK"},
    "2:1:press": "CONFIRM_LOAD",
    "2:2:press": "CONFIRM_UNLOAD",
    "3:1:press": "PAUSE_DISPATCHER",
    "3:2:press": "RESUME_DISPATCHER",
    "4:1:press": "CONFIRM_OBSTACLE_CLEARED",
}


__all__ = [
    "ButtonActionMapper",
    "DEFAULT_BUTTON_MAPPING",
    "HmiMappedAction",
]

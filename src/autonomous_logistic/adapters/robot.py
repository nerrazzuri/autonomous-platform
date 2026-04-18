from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class NavigationResult:
    accepted: bool
    target: str
    message: str


class RobotAdapter(Protocol):
    def connect(self) -> None:
        ...

    def move(self, direction: str, speed: float) -> NavigationResult:
        ...

    def stop(self) -> NavigationResult:
        ...

    def pause(self) -> NavigationResult:
        ...

    def resume(self) -> NavigationResult:
        ...

    def navigate_to(self, target: str) -> NavigationResult:
        ...

    def get_sensor_status(self) -> dict:
        ...

    def get_health_status(self) -> dict:
        ...

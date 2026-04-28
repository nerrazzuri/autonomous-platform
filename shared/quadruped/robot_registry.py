from __future__ import annotations

from shared.quadruped.robot_platform import RobotPlatform


class RobotRegistryError(Exception):
    """Base error for robot registry operations."""


class RobotNotFoundError(RobotRegistryError):
    """Raised when a requested robot is not registered."""


class RobotAlreadyRegisteredError(RobotRegistryError):
    """Raised when attempting to register a duplicate robot."""


class RobotRegistry:
    def __init__(self) -> None:
        self._platforms: dict[str, RobotPlatform] = {}

    def register(self, platform: RobotPlatform) -> None:
        if not isinstance(platform, RobotPlatform):
            raise RobotRegistryError("platform must be a RobotPlatform")
        if platform.robot_id in self._platforms:
            raise RobotAlreadyRegisteredError(f"Robot '{platform.robot_id}' is already registered")
        self._platforms[platform.robot_id] = platform

    def get(self, robot_id: str) -> RobotPlatform:
        if not isinstance(robot_id, str) or not robot_id:
            raise RobotNotFoundError("Robot '' is not registered")
        try:
            return self._platforms[robot_id]
        except KeyError as exc:
            raise RobotNotFoundError(f"Robot '{robot_id}' is not registered") from exc

    def get_by_role(self, role: str) -> list[RobotPlatform]:
        if not isinstance(role, str) or not role:
            return []

        matching: list[RobotPlatform] = []
        for platform in self._platforms.values():
            platform_role = getattr(platform.config, "role", None)
            if platform_role is None:
                platform_role = getattr(platform.config.connection, "role", None)
            if platform_role == role:
                matching.append(platform)
        return matching

    def all(self) -> list[RobotPlatform]:
        return list(self._platforms.values())

    def remove(self, robot_id: str) -> None:
        if not isinstance(robot_id, str) or not robot_id or robot_id not in self._platforms:
            raise RobotNotFoundError(f"Robot '{robot_id}' is not registered")
        del self._platforms[robot_id]

    def clear(self) -> None:
        self._platforms.clear()

    def is_registered(self, robot_id: str) -> bool:
        if not isinstance(robot_id, str) or not robot_id:
            return False
        return robot_id in self._platforms

    def count(self) -> int:
        return len(self._platforms)


robot_registry = RobotRegistry()


def get_robot_registry() -> RobotRegistry:
    return robot_registry

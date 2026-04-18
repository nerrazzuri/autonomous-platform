from autonomous_logistic.adapters.robot import NavigationResult


class MockRobotAdapter:
    def __init__(self) -> None:
        self.connected = False
        self.paused = False
        self.last_target: str | None = None
        self.last_direction: str | None = None
        self.last_speed = 0.0

    def connect(self) -> None:
        self.connected = True

    def move(self, direction: str, speed: float) -> NavigationResult:
        self.connected = True
        self.paused = False
        self.last_direction = direction
        self.last_speed = speed
        return NavigationResult(True, direction, f"Mock movement accepted: {direction} at {speed}")

    def stop(self) -> NavigationResult:
        self.last_speed = 0.0
        return NavigationResult(True, self.last_target or "current", "Mock robot stopped")

    def pause(self) -> NavigationResult:
        self.paused = True
        return NavigationResult(True, self.last_target or "current", "Mock robot paused")

    def resume(self) -> NavigationResult:
        self.paused = False
        return NavigationResult(True, self.last_target or "current", "Mock robot resumed")

    def navigate_to(self, target: str) -> NavigationResult:
        self.connected = True
        self.paused = False
        self.last_target = target
        return NavigationResult(True, target, f"Mock navigation accepted for {target}")

    def get_sensor_status(self) -> dict:
        return {
            "adapter": "mock",
            "obstacle_detected": False,
            "lidar_available": False,
            "last_target": self.last_target,
        }

    def get_health_status(self) -> dict:
        return {
            "mode": "mock",
            "connected": self.connected,
            "paused": self.paused,
            "last_target": self.last_target,
            "last_direction": self.last_direction,
        }

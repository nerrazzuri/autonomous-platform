from __future__ import annotations

import atexit
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.ros2.bridge_node import Ros2BridgeNode

_bridge: "Ros2BridgeNode | None" = None
_atexit_registered = False


def init_bridge(cfg) -> "Ros2BridgeNode":
    global _bridge, _atexit_registered
    if _bridge is not None:
        shutdown_bridge()
    from shared.ros2.thread_runner import start_ros2_bridge
    _bridge = start_ros2_bridge(cfg)
    if not _atexit_registered:
        atexit.register(shutdown_bridge)
        _atexit_registered = True
    return _bridge


def get_bridge() -> "Ros2BridgeNode | None":
    return _bridge


def shutdown_bridge() -> None:
    global _bridge
    if _bridge is None:
        return
    try:
        executor = getattr(_bridge, "_bridge_executor", None)
        if executor is not None:
            executor.shutdown()
        import rclpy
        _bridge.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()
        thread = getattr(_bridge, "_bridge_thread", None)
        if thread is not None and thread.is_alive():
            thread.join(timeout=1.0)
    except Exception:
        pass
    _bridge = None


__all__ = ["init_bridge", "get_bridge", "shutdown_bridge"]

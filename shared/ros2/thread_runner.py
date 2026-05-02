from __future__ import annotations

import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from shared.ros2.bridge_node import Ros2BridgeNode


def start_ros2_bridge(cfg) -> "Ros2BridgeNode":
    try:
        import rclpy
        from rclpy.executors import SingleThreadedExecutor
    except ImportError as exc:
        raise RuntimeError(
            "rclpy not found — source ROS2 before starting the platform: "
            ". /opt/ros/humble/setup.bash"
        ) from exc

    rclpy.init()
    from shared.ros2.bridge_node import Ros2BridgeNode
    node = Ros2BridgeNode(cfg)
    executor = SingleThreadedExecutor()
    executor.add_node(node)
    thread = threading.Thread(target=executor.spin, daemon=True, name="ros2-bridge")
    thread.start()
    node._bridge_executor = executor
    node._bridge_thread = thread
    return node

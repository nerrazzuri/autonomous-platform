from __future__ import annotations

import math
import threading
from typing import TYPE_CHECKING

from rclpy.node import Node
from geometry_msgs.msg import PoseWithCovarianceStamped, TransformStamped
from nav_msgs.msg import Odometry
from sensor_msgs.msg import LaserScan
from tf2_ros import TransformBroadcaster

if TYPE_CHECKING:
    from shared.core.config import Ros2Section


class Ros2BridgeNode(Node):
    def __init__(self, cfg: "Ros2Section") -> None:
        super().__init__("autonomous_platform_bridge")
        self._lock = threading.Lock()
        self._latest_scan: LaserScan | None = None
        self._latest_pose: PoseWithCovarianceStamped | None = None
        self._odom_state: dict = {"x": 0.0, "y": 0.0, "yaw": 0.0, "vx": 0.0, "vyaw": 0.0}
        self._cfg = cfg

        self.create_subscription(LaserScan, cfg.scan_topic, self._scan_cb, 10)
        self.create_subscription(PoseWithCovarianceStamped, cfg.pose_topic, self._pose_cb, 10)

        self._odom_pub = self.create_publisher(Odometry, cfg.odom_topic, 10)
        self._tf_broadcaster = TransformBroadcaster(self)
        self.create_timer(1.0 / cfg.odom_publish_hz, self._publish_odom)

    # --- callbacks (rclpy thread) ---

    def _scan_cb(self, msg: LaserScan) -> None:
        with self._lock:
            self._latest_scan = msg

    def _pose_cb(self, msg: PoseWithCovarianceStamped) -> None:
        with self._lock:
            self._latest_pose = msg

    # --- reads (asyncio thread) ---

    def get_latest_scan(self) -> LaserScan | None:
        with self._lock:
            return self._latest_scan

    def get_latest_pose(self) -> PoseWithCovarianceStamped | None:
        with self._lock:
            return self._latest_pose

    def set_odometry_state(self, x: float, y: float, yaw: float,
                           vx: float = 0.0, vyaw: float = 0.0) -> None:
        with self._lock:
            self._odom_state = {"x": x, "y": y, "yaw": yaw, "vx": vx, "vyaw": vyaw}

    # --- timer (rclpy thread) ---

    def _publish_odom(self) -> None:
        with self._lock:
            state = dict(self._odom_state)

        now = self.get_clock().now().to_msg()
        qx, qy, qz, qw = _yaw_to_quat(state["yaw"])

        odom = Odometry()
        odom.header.stamp = now
        odom.header.frame_id = self._cfg.odom_frame
        odom.child_frame_id = self._cfg.base_frame
        odom.pose.pose.position.x = state["x"]
        odom.pose.pose.position.y = state["y"]
        odom.pose.pose.position.z = 0.0
        odom.pose.pose.orientation.x = qx
        odom.pose.pose.orientation.y = qy
        odom.pose.pose.orientation.z = qz
        odom.pose.pose.orientation.w = qw
        odom.twist.twist.linear.x = state["vx"]
        odom.twist.twist.angular.z = state["vyaw"]
        self._odom_pub.publish(odom)

        t = TransformStamped()
        t.header.stamp = now
        t.header.frame_id = self._cfg.odom_frame
        t.child_frame_id = self._cfg.base_frame
        t.transform.translation.x = state["x"]
        t.transform.translation.y = state["y"]
        t.transform.translation.z = 0.0
        t.transform.rotation.x = qx
        t.transform.rotation.y = qy
        t.transform.rotation.z = qz
        t.transform.rotation.w = qw
        self._tf_broadcaster.sendTransform(t)


def _yaw_to_quat(yaw: float) -> tuple[float, float, float, float]:
    half = yaw * 0.5
    return (0.0, 0.0, math.sin(half), math.cos(half))

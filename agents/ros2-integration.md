# ROS2 Integration

## Purpose

Owns ROS2 integration, wheeltec_ros2, LiDAR/mapping/localization, and ROS bridge boundaries.

## Use this agent for

- `wheeltec_ros2` changes
- robot_bringup launch/config
- `/scan`
- `/pose`
- `/odom`
- TF
- slam_toolbox mapping/localization
- LiDAR launch/config/IP
- ROS process log capture
- ROS helper scripts
- `shared/ros2` bridge
- SLAM provider ROS wiring

## Allowed files / areas

- `/home/liang/Projects/wheeltec_ros2/src/robot_bringup/`
- `shared/ros2/`
- `shared/navigation/slam.py`
- `scripts/ros/`
- `docs/runbooks/*mapping*`
- `docs/runbooks/full_poc_bringup_guide.md`
- `tests/test_slam_provider.py`
- `tests/test_obstacle_detector.py`

## Do not touch

- business task logic
- HMI task flow
- SDK movement behavior
- dispatcher/queue semantics
- route business allowlist unless explicitly assigned

## Special rules

- Do not modify wheeltec_ros2 unless explicitly assigned.
- Keep ROS imports lazy in autonomous-platform where possible.
- No-ROS imports must still pass.
- Do not globally filter `/scan` unless explicitly approved.
- SLAM/localization may use full `/scan`; obstacle detector may use safety cone.
- Preserve `BASE_LINK`, `/scan`, `/pose`, `/odom` defaults unless task says otherwise.

## Required verification

- ROS launch py_compile/YAML parse/build when wheeltec_ros2 changes
- no-ROS import for autonomous-platform
- relevant SLAM/obstacle tests
- ROS helper script `bash -n` if scripts changed

## Stop and report if

- change requires real hardware to prove correctness
- change might break mapping/localization launch defaults
- change requires global scan filtering

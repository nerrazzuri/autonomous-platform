# Factory Mapping and Route Commissioning Runbook

## Purpose

This runbook is for site setup after hardware is available. It helps a site engineer verify ROS2, create a factory map, start localization, and collect the station and route data needed for real navigation.

This workflow does not prove production readiness by itself. The route files currently contain placeholder commissioning data until a hardware mapping session records real station poses and route waypoints.

## Prerequisites

- ROS2 Humble installed at `/opt/ros/humble`.
- `wheeltec_ros2` built and sourced from `/home/liang/Projects/wheeltec_ros2`.
- M10 LiDAR connected, powered, and publishing `LaserScan` on `/scan`.
- `autonomous-platform-main` backend available when checking `/odom`, TF, and `/pose`.
- `slam_toolbox`, `tf2_ros`, and `nav2_map_server` installed.

Override the ROS workspace path when needed:

```bash
WHEELTEC_WS=/path/to/wheeltec_ros2 ./scripts/ros/check_ros_env.sh
```

## Environment Check

From `/home/liang/Projects/autonomous-platform-main`:

```bash
./scripts/ros/check_ros_env.sh
```

This checks ROS2 Humble, the wheeltec install overlay, and package visibility for `robot_bringup`, `lslidar_driver`, `slam_toolbox`, and `nav2_map_server`.

## Check Scan

```bash
./scripts/ros/check_scan.sh
```

Useful overrides:

```bash
SCAN_TOPIC=/scan ONCE_TIMEOUT=10 HZ_TIMEOUT=10 ./scripts/ros/check_scan.sh
SCAN_TOPIC=/x10/scan ./scripts/ros/check_scan.sh
```

Use `/x10/scan` only after `ros2 topic list` confirms that is the live LaserScan topic.

## Start Mapping

Default mapping startup does not start the LiDAR driver:

```bash
./scripts/ros/start_mapping.sh
```

If the site wants the launch file to start the M10 driver:

```bash
START_LIDAR=true ./scripts/ros/start_mapping.sh
```

Dry-run without launching:

```bash
DRY_RUN=1 ./scripts/ros/start_mapping.sh
```

## Save Map

After mapping has a stable map:

```bash
./scripts/ros/save_map.sh facility_map
```

The default output directory is:

```text
/home/liang/Projects/wheeltec_ros2/src/robot_bringup/maps
```

Override it when needed:

```bash
MAP_OUTPUT_DIR=/tmp/maps ./scripts/ros/save_map.sh facility_map
```

Commit the generated `.yaml` and `.pgm` only after confirming this is the correct factory map.

## Start Localization

Using a map base name:

```bash
./scripts/ros/start_localization.sh facility_map
```

Using an absolute map YAML path:

```bash
./scripts/ros/start_localization.sh /home/liang/Projects/wheeltec_ros2/src/robot_bringup/maps/facility_map.yaml
```

Useful overrides:

```bash
SCAN_TOPIC=/scan START_LIDAR=false USE_SIM_TIME=false ./scripts/ros/start_localization.sh facility_map
```

Dry-run without launching:

```bash
DRY_RUN=1 ./scripts/ros/start_localization.sh facility_map
```

## Check TF And Pose

Check `odom -> BASE_LINK`:

```bash
./scripts/ros/check_tf.sh
```

Check slam_toolbox pose output:

```bash
./scripts/ros/check_pose.sh
```

Full localization is not proven until `/scan`, `/odom`, TF `odom -> BASE_LINK`, a valid map file, and `/pose` are all live.

## Marking Stations

For now, manually move the robot to each station and record `/pose`:

- `LINE_A`
- `LINE_B`
- `LINE_C`
- `QA`
- `DOCK`

Future Module 12B can add a backend API to mark the current pose. Until then, record pose values carefully and review them before updating route files.

## Updating Route Files

- `data/stations.json`: station poses used by route/navigation tooling.
- `data/routes.json`: navigation route waypoints.
- `data/logistics_routes.json`: HMI/task allowlist.

Keep station IDs consistent across all three:

```text
LINE_A, LINE_B, LINE_C, QA, DOCK
```

`data/logistics_routes.json` is an allowlist, not a navigation waypoint source.

## Safety Notes

- These scripts do not command robot movement.
- Move the robot manually and slowly during commissioning.
- Keep emergency stop ready.
- Verify the map visually before route testing.
- Placeholder routes must not be used for real navigation.
- Test route segments slowly and with a clear aisle.

## Troubleshooting

No `/scan`:

- Check M10 power and Ethernet.
- Check LiDAR IP and workstation network settings.
- Check `lslidar_driver` launch and config.
- Check `SCAN_TOPIC`; use `/x10/scan` only if ROS confirms it exists.

No `/odom`:

- Check autonomous-platform backend startup.
- Check `ros2.enabled=true` in the active config.
- Check the ROS2 bridge logs.

No TF `odom -> BASE_LINK`:

- Check the autonomous-platform odometry publisher.
- Check frame names: `odom` and `BASE_LINK`.
- Check ROS domain and sourced overlays.

No `/pose`:

- Check slam_toolbox localization is running.
- Check `map_file` points to a real saved map.
- Check `/scan`, `/odom`, and TF are live.

ROS package not visible:

- Rebuild and source `wheeltec_ros2`.
- Confirm `source /opt/ros/humble/setup.bash`.
- Confirm `source /home/liang/Projects/wheeltec_ros2/install/setup.bash`.

ROS_DOMAIN_ID mismatch:

- Make sure terminals and machines use the same `ROS_DOMAIN_ID` when multiple hosts are involved.

# Local Logistics Demo Startup

This runbook is for local development only. It is not a production systemd setup, it does not prove hardware runtime, and it does not auto-start robot movement.

## Prerequisites

- Python 3.10. The Agibot SDK requires Python 3.10.
- Optional ROS2 Humble if you want the ROS bridge or localization stack.
- Optional built `wheeltec_ros2` workspace at `/home/liang/Projects/wheeltec_ros2`.
- Optional `data/audio/arrival.wav` and `aplay` if speaker alerts are enabled later.
- Optional TJC screen connected through USB-to-TTL as `/dev/tjc_hmi` or `/dev/ttyUSBx`.

## Check The Environment

From `/home/liang/Projects/autonomous-platform-main`:

```bash
./scripts/check_runtime_env.sh
```

The script fails only for required project files or missing Python 3.10. Missing ROS2, audio, TJC, or wheeltec hardware paths are warnings.

## Start The Backend

Dry-run first:

```bash
DRY_RUN=1 ./scripts/start_logistics_dev.sh
```

Start the local backend:

```bash
./scripts/start_logistics_dev.sh
```

By default the script uses:

```bash
APP_CONFIG=apps/logistics/config/logistics_demo_config.yaml
```

To use another config:

```bash
APP_CONFIG=config.yaml ./scripts/start_logistics_dev.sh
```

The project’s real config selector is `QUADRUPED_CONFIG_PATH`; the script exports it from `APP_CONFIG` unless it is already set.

## Optional ROS Localization Stack

Manual startup remains the clearest path while hardware and maps are still being commissioned:

```bash
cd /home/liang/Projects/wheeltec_ros2
source /opt/ros/humble/setup.bash
source install/setup.bash
ros2 launch robot_bringup localization.launch.py start_lidar:=false
```

The backend startup script can start this in the background only when explicitly requested:

```bash
START_ROS_STACK=1 ./scripts/start_logistics_dev.sh
```

Pass extra launch arguments with:

```bash
ROS_LAUNCH_ARGS="start_lidar:=false map_file:=/path/to/map.yaml" START_ROS_STACK=1 ./scripts/start_logistics_dev.sh
```

## HMI Smoke Examples

REST action example using placeholder tokens only:

```bash
curl -sS -X POST http://localhost:8080/hmi/action \
  -H "Authorization: Bearer change-me-operator" \
  -H "Content-Type: application/json" \
  -d '{
    "robot_id": "robot-1",
    "screen_id": "screen-front",
    "action": "REQUEST_TASK",
    "station_id": "LINE_A",
    "destination_id": "QA"
  }'
```

WebSocket backend endpoint:

```text
ws://localhost:8080/hmi/ws?token=<operator-token>
```

Do not put real tokens in committed files or shared logs.

## Safety Notes

- HMI requests do not directly command motion.
- The TJC screen and HMI agent send high-level action JSON only.
- The workstation backend validates actions and route allowlists before queueing tasks.
- `data/logistics_routes.json` contains placeholder station/route metadata, not real map coordinates.
- Real navigation still requires a mapped facility, real station poses, real waypoints, live `/scan`, live `/odom`, and validated TF.
- Speaker output is disabled by default.

## Later Production Step

Add systemd services only after hardware paths, TJC device names, real map files, real route coordinates, and startup ordering are stable.

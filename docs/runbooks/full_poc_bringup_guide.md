# Full POC Bring-Up Guide — Quadruped Factory Logistics

## 1. Purpose and Scope

This guide brings up the full supervised quadruped logistics POC from a fresh Ubuntu workstation to a first supervised factory route test.

It covers workstation setup, ROS2 setup, backend setup, LiDAR validation, SDK validation, mapping, localization, station and route commissioning, obstacle testing, HMI testing, optional speaker testing, and a first `LINE_A -> QA` logistics task.

This guide is for a supervised POC only. It does not cover production systemd or Docker deployment, multi-robot fleet operation, Nav2 dynamic rerouting, MES integration, cloud deployment, or commercial safety certification.

Nothing in this guide should be interpreted as approval for unsupervised operation. Keep the robot in a controlled area, move slowly, and keep an emergency stop or safe-stop plan ready before any motion test.

## 2. System Overview

The POC uses two independent repositories.

`autonomous-platform-main`:

- Path: `/home/liang/Projects/autonomous-platform-main`
- Backend and control platform.
- Task queue and dispatcher.
- HMI REST and WebSocket APIs.
- Commissioning API for station and route capture.
- Route validation and station allowlist.
- Navigator and obstacle handling.
- Speaker arrival alert.
- Agibot SDK adapter.
- Local setup, runtime, ROS helper, and commissioning scripts.

`wheeltec_ros2`:

- Path: `/home/liang/Projects/wheeltec_ros2`
- ROS2 workspace for LiDAR, mapping, and localization.
- `robot_bringup` launch/config package.
- `edu_description` robot description.
- `lslidar_driver` M10 LiDAR driver.
- `slam_toolbox` mapping/localization launch files.
- Saved map files under the ROS workspace.

Runtime flow:

```text
Worker/operator request
-> backend validates route and permissions
-> task queued
-> load confirmation
-> navigator executes route
-> obstacle detector can stop and resume navigation
-> arrival at destination
-> unload confirmation
-> task complete
```

The TJC screen must never directly command robot movement. TJC/HMI actions are high-level requests only. The backend validates and routes those requests through the existing task, dispatcher, and safety logic.

## 3. Hardware Required

- Ubuntu 22.04 workstation.
- Agibot D1 EDU quadruped.
- M10 LiDAR.
- Ethernet cable for the LiDAR.
- USB-to-TTL adapter for the TJC screen.
- TJC screen.
- USB speaker, if arrival audio is part of the POC.
- USB hub, if needed.
- Cargo box or upper structure, if testing loaded logistics behavior.
- Factory Wi-Fi or LAN access.
- Power supplies and chargers.
- Emergency stop or clear safe-stop plan.

Ubuntu 22.04 Jammy is required/recommended for this POC workstation. Ubuntu 24.04 Noble is not supported for this POC because ROS2 Humble is the target ROS distribution and the Agibot SDK requires Python 3.10.

## 4. Network Plan

Before installing or moving hardware, write down:

- Workstation IP address.
- Quadruped IP address.
- M10 LiDAR IP address. The default is commonly `192.168.1.200`, but confirm on-site.
- Factory Wi-Fi/LAN SSID and subnet.
- ROS domain ID, if the site uses one.
- Which interface is used for LiDAR Ethernet and which interface is used for Wi-Fi/LAN.

Basic checks:

```bash
ip addr
ip route
ping <quadruped_ip>
ping 192.168.1.200
ros2 doctor
```

`ros2 doctor` is optional and only works after ROS2 is installed and sourced.

The workstation and quadruped must be reachable on the expected network. The LiDAR Ethernet link must be on a subnet that can reach the M10. If the LiDAR IP differs from the expected config, update the LiDAR config or launch arguments according to the `wheeltec_ros2` LiDAR documentation.

DDS/ROS2 network interface selection may need tuning if `/scan` does not appear even though the driver is running.

## 5. Fresh Ubuntu Workstation Setup

Start from the backend repo:

```bash
cd /home/liang/Projects/autonomous-platform-main
```

Run the workstation preflight:

```bash
./scripts/setup/check_ubuntu_workstation.sh
```

Dry-run dependency installation:

```bash
DRY_RUN=1 ./scripts/setup/install_ubuntu_workstation_deps.sh
```

Install OS dependencies:

```bash
sudo bash ./scripts/setup/install_ubuntu_workstation_deps.sh
```

Install ROS2 Humble:

```bash
sudo bash ./scripts/setup/install_ros2_humble.sh
```

Set up Python 3.10 environment:

```bash
./scripts/setup/setup_python_env.sh
```

Build the ROS2 workspace:

```bash
./scripts/setup/build_wheeltec_ros2.sh
```

Verify both runtime environments:

```bash
./scripts/check_runtime_env.sh
./scripts/ros/check_ros_env.sh
```

If setup scripts fail because the OS is Ubuntu 24.04, use Ubuntu 22.04 for the POC. Do not bypass the OS check for a supervised factory POC.

If the user cannot access `/dev/ttyUSB*`, add the user to `dialout` and log out/back in:

```bash
sudo usermod -aG dialout "$USER"
```

## 6. Clone / Prepare Repos

Expected paths:

```text
/home/liang/Projects/autonomous-platform-main
/home/liang/Projects/wheeltec_ros2
```

Check and update the backend repo:

```bash
cd /home/liang/Projects/autonomous-platform-main
git status
git pull
```

Check and update the ROS workspace:

```bash
cd /home/liang/Projects/wheeltec_ros2
git status
git pull
```

Rules:

- Do not edit SDK binary files.
- Do not commit real tokens.
- Do not commit `.env`, `.venv`, logs, runtime databases, or generated test files.
- Do not commit route backups or generated map files unless they are intentionally reviewed POC assets.
- Commit final map files only after confirming they are the correct factory map.

## 7. Create Local POC Config

Do not put real secrets into committed config files. Generate a local uncommitted config with generated tokens:

```bash
cd /home/liang/Projects/autonomous-platform-main
python3.10 scripts/setup/create_poc_local_config.py \
  --output config.local.yaml \
  --workstation-ip <workstation_ip> \
  --quadruped-ip <quadruped_ip> \
  --sdk-lib-path sdk/zsl-1
```

The script refuses to overwrite an existing config unless `--force` is provided. It generates operator, QA, and supervisor tokens, writes them into `config.local.yaml`, and sets POC defaults such as `ros2.enabled=true` and `navigation.position_source=slam`.

If you need to display the generated tokens once in a private terminal:

```bash
python3.10 scripts/setup/create_poc_local_config.py \
  --output config.local.yaml \
  --workstation-ip <workstation_ip> \
  --quadruped-ip <quadruped_ip> \
  --sdk-lib-path sdk/zsl-1 \
  --print-tokens
```

Do not use `--print-tokens` in screenshots, shared terminals, or logs.

Review the generated file:

```bash
nano config.local.yaml
```

Confirm these values:

- `auth.operator_token`: generated operator token.
- `auth.qa_token`: generated QA token.
- `auth.supervisor_token`: generated supervisor token.
- `workstation.local_ip`: real workstation LAN IP.
- `workstation.lan_ip`: real workstation LAN IP.
- `quadruped.quadruped_ip`: real quadruped IP.
- `quadruped.sdk_lib_path`: `sdk/zsl-1` or `sdk/zsl-1w`, whichever matches the installed SDK package.
- `ros2.enabled`: `true` for integrated demo.
- `ros2.scan_topic`: `/scan`.
- `ros2.pose_topic`: `/pose`.
- `ros2.odom_topic`: `/odom`.
- `ros2.odom_frame`: `odom`.
- `ros2.base_frame`: `BASE_LINK`.
- `navigation.position_source`: `slam` for real localization-backed navigation.
- `logistics.allow_placeholder_routes`: `true` during commissioning, then `false` before real route demo.
- `speaker.enabled`: `true` only when testing speaker output.

During initial commissioning, `logistics.allow_placeholder_routes` may remain `true` while station and route data are being captured. Before a real task demo, set it to `false` after routes are captured and route `placeholder` flags are cleared.

Dry-run startup with the generated config:

```bash
APP_CONFIG=config.local.yaml DRY_RUN=1 ./scripts/start_logistics_dev.sh
```

## 8. Start Backend

From the backend repo:

```bash
cd /home/liang/Projects/autonomous-platform-main
```

Dry-run first:

```bash
APP_CONFIG=config.local.yaml DRY_RUN=1 ./scripts/start_logistics_dev.sh
```

Start the backend:

```bash
APP_CONFIG=config.local.yaml ./scripts/start_logistics_dev.sh
```

`scripts/start_logistics_dev.sh` reads `APP_CONFIG` and exports it as `QUADRUPED_CONFIG_PATH` unless `QUADRUPED_CONFIG_PATH` is already set.

In another terminal:

```bash
curl http://127.0.0.1:8080/health
```

Set placeholder shell variables for later examples:

```bash
export API_BASE_URL=http://127.0.0.1:8080
export SUPERVISOR_TOKEN=<your-supervisor-token>
export OPERATOR_TOKEN=<your-operator-token>
```

Never paste real tokens into committed files, shared tickets, screenshots, or logs.

## 9. SDK / Quadruped Connection Validation

SDK files can exist locally while hardware runtime is still unproven. Runtime validation must happen with the quadruped powered on and reachable.

Use Python 3.10:

```bash
python3.10 --version
```

Run a safe SDK path/config preflight. This prints config wiring only and does not touch hardware:

```bash
APP_CONFIG=config.local.yaml python3.10 scripts/sdk_preflight_check.py
```

Safe validation sequence:

1. Put the robot in a safe open area.
2. Keep emergency stop or safe-stop control ready.
3. Power on the quadruped.
4. Confirm workstation and quadruped are on the same LAN.
5. Verify `ping <quadruped_ip>`.
6. Start the backend.
7. Use the existing REST robot connect/status endpoints from the API documentation or operator runbook.
8. Confirm the SDK reports connected/passive state.
9. Confirm telemetry such as position/RPY/battery changes or updates.
10. Confirm `/odom` and `odom -> BASE_LINK` only after bridge/telemetry is alive.

Do not run motion before connection, passive/safe-stop, and emergency procedures are validated.

## 10. LiDAR `/scan` Validation

From the backend repo:

```bash
cd /home/liang/Projects/autonomous-platform-main
./scripts/ros/check_ros_env.sh
```

Power the M10 LiDAR and connect Ethernet.

Check LiDAR IP:

```bash
ping 192.168.1.200
```

If ping fails:

- Check cable and power.
- Check host Ethernet IP/subnet.
- Confirm actual LiDAR IP.
- Update LiDAR config if necessary.

Start the LiDAR driver using the mapping/localization launch with `START_LIDAR=true`, or start the vendor driver launch if the site procedure requires it.

Check scan:

```bash
./scripts/ros/check_scan.sh
```

Expected:

- `/scan` exists.
- `/scan` rate is stable.
- `frame_id` matches `laser` or the expected LiDAR frame.

Troubleshooting:

- Wrong LiDAR IP.
- Wrong network interface.
- Firewall or DDS discovery issue.
- `lslidar_driver` not built or not sourced.
- Scan topic is `/x10/scan` instead of `/scan`.

Use `/x10/scan` only if ROS confirms it:

```bash
ros2 topic list | grep scan
SCAN_TOPIC=/x10/scan ./scripts/ros/check_scan.sh
```

## 11. Mapping

Start mapping:

```bash
./scripts/ros/start_mapping.sh
```

Default mapping startup uses `START_LIDAR=false`. If the launch should start the M10 driver:

```bash
START_LIDAR=true ./scripts/ros/start_mapping.sh
```

During mapping, manually guide, push, or slowly walk the robot through:

- `DOCK`
- `LINE_A`
- `LINE_B`
- `LINE_C`
- `QA`
- Corridors between stations.
- Turns, narrow areas, and loading/unloading zones.

Safety:

- No fast movement.
- Keep emergency stop ready.
- Keep the aisle clear.
- Do not carry payload during the first mapping pass unless required.

Save the map:

```bash
./scripts/ros/save_map.sh factory_poc_map
```

Expected files:

```text
/home/liang/Projects/wheeltec_ros2/src/robot_bringup/maps/factory_poc_map.yaml
/home/liang/Projects/wheeltec_ros2/src/robot_bringup/maps/factory_poc_map.pgm
```

Commit the map only after verifying it is good.

## 12. Localization

Start localization:

```bash
./scripts/ros/start_localization.sh factory_poc_map
```

Verify TF and pose:

```bash
./scripts/ros/check_tf.sh
./scripts/ros/check_pose.sh
```

Expected:

- `odom -> BASE_LINK` TF exists.
- `/pose` exists.
- Pose changes reasonably when the robot is manually moved.
- Localization remains stable.

If no pose:

- Verify `map_file`.
- Verify `/scan`.
- Verify `/odom`.
- Verify TF.
- Verify `ros2.enabled=true` in the active backend config.
- Verify the backend bridge is running.

## 13. Commissioning Stations and Routes

Set API environment:

```bash
export API_BASE_URL=http://127.0.0.1:8080
export SUPERVISOR_TOKEN=<your-supervisor-token>
```

Check current pose:

```bash
./scripts/commissioning/check_pose.sh
```

Back up route files before capture:

```bash
./scripts/commissioning/backup_routes.sh before-commissioning
```

Move the robot physically to each station, then mark it:

```bash
./scripts/commissioning/mark_station.sh LINE_A
./scripts/commissioning/mark_station.sh LINE_B
./scripts/commissioning/mark_station.sh LINE_C
./scripts/commissioning/mark_station.sh QA
./scripts/commissioning/mark_station.sh DOCK
```

For routes, manually move the robot to each waypoint location and add that current pose.

Example `LINE_A_TO_QA`:

```bash
./scripts/commissioning/add_waypoint.sh LINE_A_TO_QA line_a_pickup --hold awaiting_load
./scripts/commissioning/add_waypoint.sh LINE_A_TO_QA corridor_1
./scripts/commissioning/add_waypoint.sh LINE_A_TO_QA corridor_2
./scripts/commissioning/add_waypoint.sh LINE_A_TO_QA qa_dropoff --hold awaiting_unload
```

Mark the route ready:

```bash
./scripts/commissioning/set_route_ready.sh LINE_A_TO_QA
```

Repeat for:

- `LINE_B_TO_QA`
- `LINE_C_TO_QA`
- `QA_TO_LINE_A`
- `QA_TO_LINE_B`
- `QA_TO_LINE_C`
- `RETURN_TO_DOCK`, if supported in the current route model.

File roles:

- `data/stations.json`: station poses.
- `data/routes.json`: navigation waypoints.
- `data/logistics_routes.json`: HMI/task allowlist.

After route capture, ensure any logistics route placeholder status required by backend validation is updated. Before real task demo, set `logistics.allow_placeholder_routes=false` in the local config.

## 14. Speaker Test

Speaker output is optional but useful for arrival alerts.

Requirements:

- `aplay` installed.
- `data/audio/arrival.wav` exists.
- `speaker.enabled=true` in local config if testing backend-triggered audio.

Manual audio check:

```bash
aplay data/audio/arrival.wav
```

Then trigger a successful navigation completion, or run the existing speaker tests if doing software-only validation.

Troubleshooting:

- No USB audio device.
- Wrong default ALSA output.
- Missing `arrival.wav`.
- `speaker.enabled=false`.

## 15. TJC Screen Test

### Option A: POC Without Physical TJC

Use REST/curl or a WebSocket client to send HMI actions. This is acceptable for early POC if the physical TJC screen is not required.

### Option B: Physical TJC

Required checks:

- USB-to-TTL visible as `/dev/ttyUSBx`.
- User is in the `dialout` group.
- `pyserial` installed if the serial loop is used.
- TJC baudrate is correct.
- TJC screen is powered.
- TX/RX wiring is correct.
- Raw serial frames are received.
- TJC commands end with `FF FF FF`.
- Backend HMI WebSocket is reachable.

If the serial daemon is not implemented or not running yet, the physical TJC cannot be used as the final operator interface.

The TJC screen must never directly command robot motion. All actions go through the backend HMI API or WebSocket.

## 16. Obstacle Detection and Auto-Resume Test

Expected behavior:

- Obstacle detected -> robot stops.
- Path clear for stable period -> robot resumes slowly.
- Repeated unstable obstacles -> manual confirmation required.

Before testing:

1. Verify `/scan`.
2. Verify scan angle zero points forward.
3. Place an object in front of the robot.
4. Confirm obstacle detector triggers.
5. Remove the object.
6. Confirm auto-resume after stable-clear delay.
7. Repeat obstacles multiple times and confirm manual fallback behavior.

Cargo box / upper structure:

- Verify any box behind the LiDAR does not trigger the forward obstacle arc.
- If false positives occur, tune `obstacle_forward_arc_deg`.
- If needed in a future module, add angle-center or ignore-zone support.
- Do not globally filter `/scan` for SLAM unless necessary and reviewed.

Current config fields:

- `obstacle_stable_clear_seconds`
- `obstacle_min_hold_seconds`
- `obstacle_resume_ramp_seconds`
- `obstacle_repeat_fallback_count`
- `obstacle_stop_distance_m`
- `obstacle_forward_arc_deg`

## 17. E-Stop Test

This must be tested before a route demo.

Requirements:

- Robot in a safe open area.
- Backend running.
- SDK connected.
- Emergency stop or passive/safe-stop mechanism ready.
- Operator knows recovery procedure.

Use the existing `/estop` endpoint or site-approved E-stop mechanism. Confirm the robot physically stops or enters passive/safe state. Only release E-stop after the operator confirms the area is safe.

Do not perform route testing until E-stop behavior has been validated.

## 18. First Route Test: `LINE_A -> QA`

Prerequisites:

- Backend running.
- ROS localization running.
- `/scan` live.
- `/pose` live.
- SDK connected.
- Map loaded.
- `LINE_A` and `QA` marked.
- `LINE_A_TO_QA` route has real waypoints.
- Route `placeholder=false`.
- `logistics.allow_placeholder_routes=false`.
- E-stop tested.
- Obstacle stop/resume tested.

Set operator token:

```bash
export OPERATOR_TOKEN=<operator-token>
```

Request a task through HMI REST:

```bash
curl -sS -X POST "$API_BASE_URL/hmi/action" \
  -H "Authorization: Bearer $OPERATOR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "robot_id": "robot-1",
    "screen_id": "screen-front",
    "action": "REQUEST_TASK",
    "station_id": "LINE_A",
    "destination_id": "QA"
  }'
```

Expected sequence:

1. Backend validates route.
2. Task is queued.
3. Robot waits for load confirmation if the route includes an `awaiting_load` hold.
4. Operator confirms load with HMI `CONFIRM_LOAD`.
5. Robot moves slowly through waypoints.
6. At QA hold point, operator confirms unload with HMI `CONFIRM_UNLOAD`.
7. Task completes.
8. Speaker alert plays if enabled and configured.

If a route holds for load/unload, do not bypass those confirmations.

## 19. Troubleshooting

### Backend Will Not Start

- Placeholder tokens are still in active config.
- Wrong `APP_CONFIG`.
- Python dependencies missing.
- Port `8080` already in use.
- Python is not 3.10.

### No `/scan`

- LiDAR power/IP issue.
- Driver not launched.
- Wrong scan topic.
- Network interface mismatch.
- ROS2 workspace not sourced.

### No `/pose`

- No map loaded.
- No `/scan`.
- No `/odom`.
- TF missing.
- Localization not running.

### No `odom -> BASE_LINK`

- SDK not connected.
- ROS2 bridge disabled.
- Wrong base frame.
- Robot telemetry missing.

### Commissioning API Says Pose Unavailable

- Backend is not receiving pose.
- `navigation.position_source` config is wrong.
- Localization is not running.
- `/pose` is stale or absent.

### Route Request Rejected

- `data/logistics_routes.json` allowlist missing route.
- Placeholder route not allowed.
- Station/destination typo.
- Route marked inactive or not ready.

### Robot Does Not Move

- SDK not connected.
- Task waiting for confirm-load.
- Obstacle blocked.
- E-stop/passive state active.
- Route has no waypoints.

### Obstacle False Positives

- Scan angle orientation wrong.
- Cargo box or mount visible inside forward arc.
- Arc too wide.
- Stop distance too long.

### TJC Not Working

- Serial port permission issue.
- Wrong baudrate.
- TX/RX reversed.
- TJC daemon not implemented or not running.
- Token/WebSocket issue.

## 20. Recovery and Backup

Back up before commissioning:

```bash
./scripts/commissioning/backup_routes.sh before-session
```

Restore latest backup:

```bash
FORCE=1 ./scripts/commissioning/restore_routes.sh latest
```

Do not overwrite good routes without a backup. Keep copies of map files before replacing them.

## 21. POC Acceptance Checklist

Workstation:

- [ ] Ubuntu 22.04.
- [ ] Python 3.10.
- [ ] ROS2 Humble.
- [ ] Backend starts.
- [ ] Real tokens configured in local uncommitted config.

Quadruped:

- [ ] Reachable over network.
- [ ] SDK connects.
- [ ] E-stop/passive behavior tested.

LiDAR:

- [ ] `/scan` live.
- [ ] Angle zero confirmed forward.
- [ ] Obstacle detection works.

Map/localization:

- [ ] Map saved.
- [ ] Localization starts.
- [ ] `/pose` live.

Routes:

- [ ] Stations marked.
- [ ] Waypoints added.
- [ ] Route `placeholder=false`.
- [ ] `allow_placeholder_routes=false`.

Task:

- [ ] `REQUEST_TASK` works.
- [ ] `CONFIRM_LOAD` works.
- [ ] Robot navigates under supervision.
- [ ] Obstacle stop/resume works.
- [ ] `CONFIRM_UNLOAD` works.
- [ ] Task completes.

HMI/speaker:

- [ ] HMI REST/WebSocket works.
- [ ] TJC physical screen works, if required.
- [ ] Speaker works, if required.

## 22. What Is Not Required for This POC

- Polished UI.
- Full production systemd.
- Docker deployment.
- Multi-robot support.
- Nav2 dynamic rerouting.
- MES integration.
- Commercial safety certification.
- Cloud deployment.

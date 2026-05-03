# Workstation Setup Runbook

This runbook walks through setting up a fresh Ubuntu workstation to run the
autonomous platform backend and build the wheeltec_ros2 workspace.

**Supported OS:** Ubuntu 22.04 LTS (Jammy). Ubuntu 24.04 Noble is not
supported for ROS2 Humble apt install.

---

## Overview

| Script | Purpose | Needs sudo |
|--------|---------|-----------|
| `check_ubuntu_workstation.sh` | Pre-flight report — read-only | No |
| `install_ubuntu_workstation_deps.sh` | OS packages, Python 3.10, serial/audio tools | Yes |
| `setup_python_env.sh` | Project `.venv`, `pip install requirements.txt` | No |
| `install_ros2_humble.sh` | ROS2 Humble via official apt, rosdep | Yes |
| `build_wheeltec_ros2.sh` | `colcon build` of `robot_bringup` | No (rosdep step may sudo) |

All scripts support `DRY_RUN=1` to print planned commands without executing
them.

---

## Step 1 — Pre-flight check

Run this first on any machine to see what is already installed:

```bash
bash scripts/setup/check_ubuntu_workstation.sh
```

The script prints `PASS`, `WARN`, or `FAIL` for each item and exits with code
1 if any required item is missing. Items marked `WARN` are optional
(audio, serial devices, ROS2) and will not block the backend if the
corresponding hardware is absent.

---

## Step 2 — Install OS dependencies

```bash
sudo bash scripts/setup/install_ubuntu_workstation_deps.sh
```

Installs: `git`, `curl`, `wget`, build tools, Python 3.10 + venv + dev,
colcon/rosdep/vcstool, `alsa-utils`, `usbutils`, `minicom`, network tools.

Also adds the current non-root user to the `dialout` group for `/dev/ttyUSB*`
access. **Log out and back in** (or run `newgrp dialout`) for the group change
to take effect.

Dry run:

```bash
DRY_RUN=1 bash scripts/setup/install_ubuntu_workstation_deps.sh
```

---

## Step 3 — Create the Python virtual environment

Run as the project user (not root):

```bash
bash scripts/setup/setup_python_env.sh
```

Creates `.venv/` in the project root using `python3.10 -m venv`, then runs
`pip install -r requirements.txt`.

To force recreation of an existing `.venv`:

```bash
FORCE_RECREATE_VENV=1 bash scripts/setup/setup_python_env.sh
```

Activate the environment before running any backend code:

```bash
source .venv/bin/activate
```

---

## Step 4 — Install ROS2 Humble

**Ubuntu 22.04 Jammy only.** The script will exit with an error on any other
OS version.

```bash
sudo bash scripts/setup/install_ros2_humble.sh
```

To also add `source /opt/ros/humble/setup.bash` to `~/.bashrc`:

```bash
ADD_TO_BASHRC=1 sudo bash scripts/setup/install_ros2_humble.sh
```

If `ADD_TO_BASHRC` is not set, activate manually in each shell session:

```bash
source /opt/ros/humble/setup.bash
```

---

## Step 5 — Build wheeltec_ros2

Ensure the wheeltec_ros2 repository is cloned before running this step.
The default workspace path is `/home/liang/Projects/wheeltec_ros2`. Override
with the `WHEELTEC_WS` environment variable if needed.

```bash
bash scripts/setup/build_wheeltec_ros2.sh
```

or with a custom path:

```bash
WHEELTEC_WS=/path/to/wheeltec_ros2 bash scripts/setup/build_wheeltec_ros2.sh
```

After a successful build, activate the overlay:

```bash
source /home/liang/Projects/wheeltec_ros2/install/setup.bash
```

---

## Step 6 — Post-setup verification

Re-run the pre-flight check after all steps to confirm everything is in place:

```bash
bash scripts/setup/check_ubuntu_workstation.sh
```

Expected outcome: `FAIL: 0`. Warnings for audio, serial devices, and
wheeltec_ros2 are expected when the hardware is not connected.

---

## Step 7 — Generate Local POC Config

The committed demo/example configs contain placeholder auth tokens. Do not
edit them with real secrets. Generate an uncommitted local config instead:

```bash
python3.10 scripts/setup/create_poc_local_config.py \
  --output config.local.yaml \
  --workstation-ip <workstation_ip> \
  --quadruped-ip <quadruped_ip> \
  --sdk-lib-path sdk/zsl-1
```

The script:

- Creates `config.local.yaml` from `apps/logistics/config/logistics_demo_config.yaml`.
- Generates operator, QA, and supervisor tokens.
- Refuses to overwrite an existing config unless `--force` is used.
- Sets POC defaults such as `ros2.enabled=true` and `navigation.position_source=slam`.
- Leaves `logistics.allow_placeholder_routes=true` for commissioning.
- Writes the file with restrictive permissions where supported.

Use `--print-tokens` only in a private terminal if you need to see the generated
tokens once. Do not include token output in screenshots or shared logs.

Review site-specific values:

```bash
nano config.local.yaml
```

Then verify startup without launching the backend:

```bash
APP_CONFIG=config.local.yaml DRY_RUN=1 ./scripts/start_logistics_dev.sh
```

After real station and route capture, switch `allow_placeholder_routes` to
`false` before a supervised route demo.

---

## Common Issues

### `python3.10` not found after install

On Ubuntu 24.04 Noble, `python3.10` is not in the default apt repos. The
`install_ubuntu_workstation_deps.sh` script will attempt to add the
deadsnakes PPA, but the recommended path is to use Ubuntu 22.04.

### `/dev/ttyUSB*` permission denied

The user must be in the `dialout` group. After running
`install_ubuntu_workstation_deps.sh`, log out and back in, or run:

```bash
newgrp dialout
```

Verify:

```bash
id | grep dialout
```

### `rosdep install` fails with "cannot locate rosdep definition"

Run `rosdep update` to refresh the index:

```bash
rosdep update
```

If rosdep has never been initialized:

```bash
sudo rosdep init
rosdep update
```

### colcon build fails — missing package

Check that ROS2 Humble is sourced before building:

```bash
source /opt/ros/humble/setup.bash
bash scripts/setup/build_wheeltec_ros2.sh
```

### ROS2 command not available after install

Source the setup file in the current shell:

```bash
source /opt/ros/humble/setup.bash
```

To make this permanent, add to `~/.bashrc` (or rerun with
`ADD_TO_BASHRC=1`).

---

## Environment Variables Summary

| Variable | Default | Script(s) | Effect |
|----------|---------|-----------|--------|
| `DRY_RUN` | `0` | all | Print commands without executing |
| `FORCE_RECREATE_VENV` | `0` | `setup_python_env.sh` | Delete and recreate `.venv` |
| `ADD_TO_BASHRC` | `0` | `install_ros2_humble.sh` | Append ROS2 source to `~/.bashrc` |
| `WHEELTEC_WS` | `/home/liang/Projects/wheeltec_ros2` | `check_ubuntu_workstation.sh`, `build_wheeltec_ros2.sh` | Override workspace path |

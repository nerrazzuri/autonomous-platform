#!/usr/bin/env bash
# Build the wheeltec_ros2 workspace using colcon.
# Runs rosdep install, then colcon build with robot_bringup as the target.
# Safe to re-run: colcon --merge-install is idempotent.
#
# Usage:
#   bash scripts/setup/build_wheeltec_ros2.sh
#   DRY_RUN=1 bash scripts/setup/build_wheeltec_ros2.sh
#   WHEELTEC_WS=/path/to/ws bash scripts/setup/build_wheeltec_ros2.sh
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
WHEELTEC_WS="${WHEELTEC_WS:-/home/liang/Projects/wheeltec_ros2}"
ROS_SETUP="/opt/ros/humble/setup.bash"

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[DRY_RUN] %s\n' "$*"
    else
        "$@"
    fi
}

section() { printf '\n==> %s\n' "$1"; }

# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------
section "Preflight"
printf 'Workspace: %s\n' "$WHEELTEC_WS"

if [[ ! -f "$ROS_SETUP" ]]; then
    printf 'ERROR: ROS2 Humble not found at %s\n' "$ROS_SETUP" >&2
    printf '       Run install_ros2_humble.sh first.\n' >&2
    exit 1
fi

if [[ ! -d "$WHEELTEC_WS" ]]; then
    printf 'ERROR: wheeltec_ros2 workspace not found: %s\n' "$WHEELTEC_WS" >&2
    printf '       Clone the repo or set WHEELTEC_WS to the correct path.\n' >&2
    exit 1
fi

if [[ ! -d "$WHEELTEC_WS/src" ]]; then
    printf 'ERROR: No src/ directory in %s — workspace must be initialized.\n' "$WHEELTEC_WS" >&2
    exit 1
fi

if [[ "$DRY_RUN" != "1" ]]; then
    if ! command -v colcon >/dev/null 2>&1; then
        printf 'ERROR: colcon not found.\n' >&2
        printf '       Run: sudo apt-get install python3-colcon-common-extensions\n' >&2
        exit 1
    fi

    if ! command -v rosdep >/dev/null 2>&1; then
        printf 'ERROR: rosdep not found.\n' >&2
        printf '       Run: sudo apt-get install python3-rosdep\n' >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Source ROS2
# ---------------------------------------------------------------------------
section "Source ROS2 Humble"
# shellcheck disable=SC1090
if [[ "$DRY_RUN" != "1" ]]; then
    source "$ROS_SETUP"
    printf 'ROS_DISTRO=%s\n' "${ROS_DISTRO:-unset}"
else
    printf '[DRY_RUN] source %s\n' "$ROS_SETUP"
fi

# ---------------------------------------------------------------------------
# rosdep install
# ---------------------------------------------------------------------------
section "rosdep install"
ROSDEP_INITIALIZED=0
if [[ -f /etc/ros/rosdep/sources.list.d/20-default.list ]]; then
    ROSDEP_INITIALIZED=1
fi

if [[ "$ROSDEP_INITIALIZED" -eq 0 ]]; then
    printf 'rosdep not initialized — running rosdep init first.\n'
    run sudo rosdep init
fi
run rosdep update

# Install dependencies for all packages in src/
run rosdep install \
    --from-paths "$WHEELTEC_WS/src" \
    --ignore-src \
    --rosdistro humble \
    -y

# ---------------------------------------------------------------------------
# colcon build
# ---------------------------------------------------------------------------
section "colcon build"
cd "$WHEELTEC_WS"

# Build robot_bringup and its transitive deps.
# --merge-install keeps the install tree flat and idempotent.
run colcon build \
    --merge-install \
    --packages-up-to robot_bringup \
    --cmake-args -DCMAKE_BUILD_TYPE=Release \
    --event-handlers console_cohesion+

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
section "Verify"
if [[ "$DRY_RUN" == "1" ]]; then
    printf '[DRY_RUN] would verify: %s/install/setup.bash\n' "$WHEELTEC_WS"
    printf '[DRY_RUN] would verify: %s/src/robot_bringup\n' "$WHEELTEC_WS"
else
    if [[ -f "$WHEELTEC_WS/install/setup.bash" ]]; then
        printf 'PASS  install overlay exists: %s/install/setup.bash\n' "$WHEELTEC_WS"
    else
        printf 'FAIL  install/setup.bash not found — build may have failed\n' >&2
        exit 1
    fi

    if [[ -d "$WHEELTEC_WS/src/robot_bringup" ]]; then
        printf 'PASS  robot_bringup source present\n'
    else
        printf 'WARN  robot_bringup not found in src/ — check vcs import step\n'
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n============================\n'
if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY RUN complete — no changes made.\n'
else
    printf 'build_wheeltec_ros2.sh complete.\n'
    printf 'Activate with: source %s/install/setup.bash\n' "$WHEELTEC_WS"
    printf 'Run check: bash scripts/setup/check_ubuntu_workstation.sh\n'
fi
printf '============================\n'

#!/usr/bin/env bash
# Install ROS2 Humble via the official apt repository.
# Ubuntu 22.04 Jammy ONLY — will exit on any other OS.
# Does NOT source setup.bash automatically; see ADD_TO_BASHRC option.
#
# Usage:
#   sudo bash scripts/setup/install_ros2_humble.sh
#   DRY_RUN=1 bash scripts/setup/install_ros2_humble.sh
#   ADD_TO_BASHRC=1 sudo bash scripts/setup/install_ros2_humble.sh
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"
ADD_TO_BASHRC="${ADD_TO_BASHRC:-0}"
ROS_DISTRO="humble"
ROS_SETUP="/opt/ros/${ROS_DISTRO}/setup.bash"

if [[ "$EUID" -ne 0 ]] && [[ "$DRY_RUN" != "1" ]]; then
    printf 'ERROR: This script must be run as root (sudo) unless DRY_RUN=1.\n' >&2
    exit 1
fi

run() {
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[DRY_RUN] %s\n' "$*"
    else
        "$@"
    fi
}

section() { printf '\n==> %s\n' "$1"; }

# ---------------------------------------------------------------------------
# OS check — fail hard on anything that is not Ubuntu 22.04 Jammy
# ---------------------------------------------------------------------------
section "OS check"
if [[ ! -f /etc/os-release ]]; then
    printf 'ERROR: /etc/os-release not found.\n' >&2
    exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
OS_CODENAME="${VERSION_CODENAME:-}"
printf 'Detected: %s %s (%s)\n' "${NAME:-unknown}" "${VERSION_ID:-unknown}" "${OS_CODENAME:-unknown}"

if [[ "$OS_CODENAME" == "noble" ]]; then
    printf 'ERROR: Ubuntu 24.04 Noble is NOT supported for ROS2 Humble apt install.\n' >&2
    printf '       ROS2 Humble requires Ubuntu 22.04 Jammy.\n' >&2
    printf '       Options:\n' >&2
    printf '         - Install Ubuntu 22.04 on this machine.\n' >&2
    printf '         - Use a Docker container with ros:humble.\n' >&2
    exit 1
fi

if [[ "$OS_CODENAME" != "jammy" ]]; then
    printf 'ERROR: OS codename "%s" is not Ubuntu 22.04 Jammy.\n' "$OS_CODENAME" >&2
    printf '       ROS2 Humble apt install is only supported on Ubuntu 22.04.\n' >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Check for existing installation
# ---------------------------------------------------------------------------
section "Existing installation check"
if [[ -f "$ROS_SETUP" ]] && [[ "$DRY_RUN" != "1" ]]; then
    printf 'ROS2 Humble is already installed at %s\n' "$ROS_SETUP"
    printf 'Skipping installation. Re-running is safe but unnecessary.\n'
    SKIP_INSTALL=1
else
    SKIP_INSTALL=0
fi

# ---------------------------------------------------------------------------
# Add ROS2 apt repository
# ---------------------------------------------------------------------------
if [[ "$SKIP_INSTALL" -eq 0 ]]; then
    section "Add ROS2 apt repository"
    run apt-get update -y
    run apt-get install -y curl gnupg lsb-release

    KEYRING="/usr/share/keyrings/ros-archive-keyring.gpg"
    if [[ ! -f "$KEYRING" ]] || [[ "$DRY_RUN" == "1" ]]; then
        run bash -c "curl -sSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.asc | \
            gpg --dearmor -o $KEYRING"
    else
        printf 'ROS keyring already present: %s\n' "$KEYRING"
    fi

    SOURCES_LIST="/etc/apt/sources.list.d/ros2.list"
    if [[ ! -f "$SOURCES_LIST" ]] || [[ "$DRY_RUN" == "1" ]]; then
        run bash -c "printf 'deb [arch=%s signed-by=%s] http://packages.ros.org/ros2/ubuntu %s main\n' \
            \"\$(dpkg --print-architecture)\" \"$KEYRING\" \"\$(lsb_release -cs)\" \
            > $SOURCES_LIST"
    else
        printf 'ROS2 sources list already present: %s\n' "$SOURCES_LIST"
    fi

    # ---------------------------------------------------------------------------
    # Install ROS2 Humble desktop (includes rviz2, rqt tools)
    # ---------------------------------------------------------------------------
    section "Install ros-humble-desktop"
    run apt-get update -y
    run apt-get install -y ros-humble-desktop

    # ---------------------------------------------------------------------------
    # Additional ROS2 packages needed by wheeltec_ros2
    # ---------------------------------------------------------------------------
    section "Additional ROS2 packages"
    EXTRA_PKGS=(
        ros-humble-slam-toolbox
        ros-humble-nav2-bringup
        ros-humble-robot-state-publisher
        ros-humble-joint-state-publisher
        ros-humble-xacro
        ros-humble-tf2-tools
        ros-humble-tf2-ros
        ros-humble-laser-filters
        python3-colcon-common-extensions
        python3-rosdep
        python3-vcstool
        python3-argcomplete
    )
    run apt-get install -y "${EXTRA_PKGS[@]}"

    # ---------------------------------------------------------------------------
    # Initialize rosdep
    # ---------------------------------------------------------------------------
    section "rosdep init"
    if [[ -f /etc/ros/rosdep/sources.list.d/20-default.list ]] && [[ "$DRY_RUN" != "1" ]]; then
        printf 'rosdep already initialized.\n'
    else
        run rosdep init
    fi
    run rosdep update
fi

# ---------------------------------------------------------------------------
# Optional: add ROS2 source to .bashrc
# ---------------------------------------------------------------------------
section "Shell configuration"
TARGET_USER="${SUDO_USER:-${USER:-}}"
if [[ -n "$TARGET_USER" ]] && [[ "$TARGET_USER" != "root" ]]; then
    TARGET_BASHRC="/home/$TARGET_USER/.bashrc"
else
    TARGET_BASHRC="$HOME/.bashrc"
fi

SOURCE_LINE="source $ROS_SETUP"
if [[ "$ADD_TO_BASHRC" == "1" ]]; then
    if [[ "$DRY_RUN" == "1" ]]; then
        printf '[DRY_RUN] would append to %s: %s\n' "$TARGET_BASHRC" "$SOURCE_LINE"
    elif grep -qF "$SOURCE_LINE" "$TARGET_BASHRC" 2>/dev/null; then
        printf 'ROS2 source already in %s — skipping.\n' "$TARGET_BASHRC"
    else
        printf '\n%s\n' "$SOURCE_LINE" >> "$TARGET_BASHRC"
        printf 'Added to %s: %s\n' "$TARGET_BASHRC" "$SOURCE_LINE"
    fi
else
    printf 'ADD_TO_BASHRC not set — not modifying .bashrc.\n'
    printf 'To activate ROS2 manually: source %s\n' "$ROS_SETUP"
fi

# ---------------------------------------------------------------------------
# Verify
# ---------------------------------------------------------------------------
if [[ "$DRY_RUN" != "1" ]]; then
    section "Verify"
    if [[ -f "$ROS_SETUP" ]]; then
        printf 'PASS  %s exists\n' "$ROS_SETUP"
    else
        printf 'FAIL  %s not found after install\n' "$ROS_SETUP" >&2
        exit 1
    fi
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n============================\n'
if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY RUN complete — no changes made.\n'
else
    printf 'install_ros2_humble.sh complete.\n'
    printf 'Activate with: source %s\n' "$ROS_SETUP"
    printf 'Next: run build_wheeltec_ros2.sh to build the robot workspace.\n'
fi
printf '============================\n'

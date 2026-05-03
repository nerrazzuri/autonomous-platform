#!/usr/bin/env bash
# Install common OS dependencies for the autonomous-platform workstation.
# Does NOT install ROS2 — use install_ros2_humble.sh for that.
# Does NOT write config secrets or install systemd services.
#
# Usage:
#   sudo bash scripts/setup/install_ubuntu_workstation_deps.sh
#   DRY_RUN=1 bash scripts/setup/install_ubuntu_workstation_deps.sh
set -euo pipefail

DRY_RUN="${DRY_RUN:-0}"

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
# OS check
# ---------------------------------------------------------------------------
section "OS detection"
if [[ ! -f /etc/os-release ]]; then
    printf 'ERROR: /etc/os-release not found — cannot detect OS.\n' >&2
    exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
OS_CODENAME="${VERSION_CODENAME:-}"
printf 'Detected: %s %s (%s)\n' "${NAME:-unknown}" "${VERSION_ID:-unknown}" "${OS_CODENAME:-unknown}"

if [[ "$OS_CODENAME" == "noble" ]]; then
    printf 'WARNING: Ubuntu 24.04 Noble detected.\n'
    printf '  python3.10 packages are not available in the Noble apt repos by default.\n'
    printf '  You must add the deadsnakes PPA or use a container to get python3.10.\n'
    printf '  Proceeding, but python3.10 installation will likely fail.\n'
elif [[ "$OS_CODENAME" != "jammy" ]]; then
    printf 'WARNING: Untested OS "%s". Continuing, but results may vary.\n' "$OS_CODENAME"
fi

# ---------------------------------------------------------------------------
# apt update
# ---------------------------------------------------------------------------
section "apt update"
run apt-get update -y

# ---------------------------------------------------------------------------
# Core utilities
# ---------------------------------------------------------------------------
section "Core utilities"
CORE_PKGS=(
    git
    curl
    wget
    ca-certificates
    gnupg
    lsb-release
    software-properties-common
    apt-transport-https
)
run apt-get install -y "${CORE_PKGS[@]}"

# ---------------------------------------------------------------------------
# Build tools
# ---------------------------------------------------------------------------
section "Build tools"
BUILD_PKGS=(
    build-essential
    pkg-config
    cmake
    ninja-build
)
run apt-get install -y "${BUILD_PKGS[@]}"

# ---------------------------------------------------------------------------
# Python 3.10
# ---------------------------------------------------------------------------
section "Python 3.10"
PYTHON_PKGS=(
    python3.10
    python3.10-venv
    python3.10-dev
    python3-pip
)

if [[ "$OS_CODENAME" == "noble" ]]; then
    printf 'WARN: On Noble, python3.10 is not in the default repos.\n'
    printf '      Adding deadsnakes PPA to attempt python3.10 install.\n'
    if [[ "$DRY_RUN" != "1" ]]; then
        if ! apt-cache show python3.10 >/dev/null 2>&1; then
            add-apt-repository -y ppa:deadsnakes/ppa
            apt-get update -y
        fi
    else
        printf '[DRY_RUN] add-apt-repository -y ppa:deadsnakes/ppa\n'
        printf '[DRY_RUN] apt-get update -y\n'
    fi
fi

run apt-get install -y "${PYTHON_PKGS[@]}"

# ---------------------------------------------------------------------------
# ROS2 build dependencies (colcon, rosdep, vcstool)
# These are needed for building wheeltec_ros2 even before ROS2 is installed.
# ---------------------------------------------------------------------------
section "ROS2 build tools (colcon / rosdep / vcstool)"
ROS_BUILD_PKGS=(
    python3-colcon-common-extensions
    python3-rosdep
    python3-vcstool
    python3-argcomplete
)
run apt-get install -y "${ROS_BUILD_PKGS[@]}"

# ---------------------------------------------------------------------------
# Audio
# ---------------------------------------------------------------------------
section "Audio (alsa-utils)"
run apt-get install -y alsa-utils

# ---------------------------------------------------------------------------
# USB / Serial
# ---------------------------------------------------------------------------
section "USB and serial tools"
SERIAL_PKGS=(
    usbutils
    minicom
    screen
)
run apt-get install -y "${SERIAL_PKGS[@]}"

CURRENT_USER="${SUDO_USER:-${USER:-}}"
if [[ -n "$CURRENT_USER" ]] && [[ "$CURRENT_USER" != "root" ]]; then
    if id -nG "$CURRENT_USER" 2>/dev/null | grep -qw dialout; then
        printf 'User "%s" is already in the dialout group.\n' "$CURRENT_USER"
    else
        printf 'Adding user "%s" to dialout group for ttyUSB access.\n' "$CURRENT_USER"
        run usermod -aG dialout "$CURRENT_USER"
        printf 'NOTE: Log out and back in (or run: newgrp dialout) for group change to take effect.\n'
    fi
else
    printf 'WARN: Could not determine non-root user — dialout group not modified.\n'
    printf '      Run manually: sudo usermod -aG dialout $USER\n'
fi

# ---------------------------------------------------------------------------
# Network tools
# ---------------------------------------------------------------------------
section "Network tools"
NET_PKGS=(
    net-tools
    iproute2
    iputils-ping
    dnsutils
    tcpdump
    nmap
    jq
)
run apt-get install -y "${NET_PKGS[@]}"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
section "apt cleanup"
run apt-get autoremove -y
run apt-get clean

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n============================\n'
if [[ "$DRY_RUN" == "1" ]]; then
    printf 'DRY RUN complete — no changes made.\n'
else
    printf 'install_ubuntu_workstation_deps.sh complete.\n'
    printf 'Next: run setup_python_env.sh to create the project venv.\n'
fi
printf '============================\n'

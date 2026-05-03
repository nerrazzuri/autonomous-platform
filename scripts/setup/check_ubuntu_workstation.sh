#!/usr/bin/env bash
# Read-only workstation pre-flight check.
# Reports PASS/WARN/FAIL for required and optional items.
# DRY_RUN=1 is accepted but has no effect — this script never modifies system state.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
WHEELTEC_WS="${WHEELTEC_WS:-/home/liang/Projects/wheeltec_ros2}"

pass_count=0
warn_count=0
fail_count=0

_pass() { pass_count=$((pass_count + 1)); printf 'PASS  %s\n' "$1"; }
_warn() { warn_count=$((warn_count + 1)); printf 'WARN  %s\n' "$1"; }
_fail() { fail_count=$((fail_count + 1)); printf 'FAIL  %s\n' "$1"; }

section() { printf '\n-- %s --\n' "$1"; }

# ---------------------------------------------------------------------------
# OS detection
# ---------------------------------------------------------------------------
section "OS"
if [[ -f /etc/os-release ]]; then
    # shellcheck disable=SC1091
    source /etc/os-release
    printf 'NAME=%s  VERSION_ID=%s  VERSION_CODENAME=%s\n' \
        "${NAME:-unknown}" "${VERSION_ID:-unknown}" "${VERSION_CODENAME:-unknown}"
    OS_NAME="${NAME:-}"
    OS_VERSION="${VERSION_ID:-}"
    OS_CODENAME="${VERSION_CODENAME:-}"
else
    _fail "/etc/os-release not found — cannot determine OS"
    OS_NAME="" OS_VERSION="" OS_CODENAME=""
fi

ARCH="$(uname -m)"
printf 'Architecture: %s\n' "$ARCH"

# Supported / unsupported platform guidance
if [[ "$OS_CODENAME" == "jammy" ]]; then
    _pass "Ubuntu 22.04 Jammy — ROS2 Humble apt install is supported"
elif [[ "$OS_CODENAME" == "noble" ]]; then
    _fail "Ubuntu 24.04 Noble detected — ROS2 Humble apt install is NOT supported on this release. " \
          "Use Ubuntu 22.04 Jammy for a workstation that requires ROS2 Humble via apt."
elif [[ -n "$OS_CODENAME" ]]; then
    _warn "OS codename '$OS_CODENAME' is not Ubuntu 22.04 Jammy — ROS2 Humble apt support unverified"
fi

# ---------------------------------------------------------------------------
# Required: Python 3.10
# ---------------------------------------------------------------------------
section "Python"
if command -v python3.10 >/dev/null 2>&1; then
    _pass "python3.10 available: $(python3.10 --version 2>&1)"
else
    _fail "python3.10 not found — required for Agibot SDK and backend"
fi

if python3.10 -m venv --help >/dev/null 2>&1; then
    _pass "python3.10-venv module available"
else
    _fail "python3.10-venv not available — install python3.10-venv"
fi

if python3.10 -m pip --version >/dev/null 2>&1; then
    _pass "pip available for python3.10"
else
    _warn "pip not available for python3.10 — run: python3.10 -m ensurepip"
fi

# ---------------------------------------------------------------------------
# Required: project files
# ---------------------------------------------------------------------------
section "Project"
if [[ -d "$ROOT_DIR" ]]; then
    _pass "autonomous-platform-main root: $ROOT_DIR"
else
    _fail "project root not found: $ROOT_DIR"
fi

for f in requirements.txt config.yaml.example data/logistics_routes.json; do
    if [[ -f "$ROOT_DIR/$f" ]]; then
        _pass "file exists: $f"
    else
        _fail "required file missing: $ROOT_DIR/$f"
    fi
done

if [[ -d "$ROOT_DIR/sdk" ]]; then
    _pass "sdk/ directory present"
else
    _warn "sdk/ directory not found — vendor SDK binaries required for real robot"
fi

if [[ -d "$ROOT_DIR/.venv" ]]; then
    _pass ".venv exists"
else
    _warn ".venv not found — run setup_python_env.sh to create it"
fi

# ---------------------------------------------------------------------------
# Required: build tools
# ---------------------------------------------------------------------------
section "Build tools"
for cmd in git curl wget; do
    if command -v "$cmd" >/dev/null 2>&1; then
        _pass "command available: $cmd"
    else
        _fail "required command missing: $cmd"
    fi
done

if dpkg -s ca-certificates >/dev/null 2>&1; then
    _pass "package installed: ca-certificates"
else
    _fail "package missing: ca-certificates — run: sudo apt-get install ca-certificates"
fi

for cmd in make gcc g++; do
    if command -v "$cmd" >/dev/null 2>&1; then
        _pass "build tool available: $cmd"
    else
        _warn "build tool missing: $cmd (install build-essential)"
    fi
done

# ---------------------------------------------------------------------------
# Optional: audio
# ---------------------------------------------------------------------------
section "Audio"
if command -v aplay >/dev/null 2>&1; then
    _pass "aplay available: $(aplay --version 2>&1 | head -1)"
else
    _warn "aplay not found — arrival audio alerts will not work (install alsa-utils)"
fi

if [[ -f "$ROOT_DIR/data/audio/arrival.wav" ]]; then
    _pass "arrival audio file exists"
else
    _warn "arrival audio file missing: data/audio/arrival.wav"
fi

# ---------------------------------------------------------------------------
# Optional: serial / USB
# ---------------------------------------------------------------------------
section "Serial / USB"
if command -v lsusb >/dev/null 2>&1; then
    _pass "lsusb available"
else
    _warn "lsusb not found — install usbutils for USB device diagnostics"
fi

if command -v dmesg >/dev/null 2>&1; then
    _pass "dmesg available"
else
    _warn "dmesg not available"
fi

CURRENT_USER="${USER:-$(id -un 2>/dev/null || echo unknown)}"
if id -nG "$CURRENT_USER" 2>/dev/null | grep -qw dialout; then
    _pass "user '$CURRENT_USER' is in the dialout group — can access ttyUSB devices"
else
    _warn "user '$CURRENT_USER' is NOT in the dialout group — TJC serial will fail; run: sudo usermod -aG dialout $CURRENT_USER"
fi

if [[ -e /dev/tjc_hmi ]]; then
    _pass "TJC device exists: /dev/tjc_hmi"
else
    _warn "TJC device /dev/tjc_hmi not found — expected when TJC screen is connected"
fi

shopt -s nullglob
tty_devices=(/dev/ttyUSB*)
shopt -u nullglob
if ((${#tty_devices[@]} > 0)); then
    _pass "USB serial devices present: ${tty_devices[*]}"
else
    _warn "no /dev/ttyUSB* devices — normal if hardware is not connected"
fi

# ---------------------------------------------------------------------------
# Optional: network tools
# ---------------------------------------------------------------------------
section "Network tools"
for cmd in ping ip netstat; do
    if command -v "$cmd" >/dev/null 2>&1; then
        _pass "network tool available: $cmd"
    else
        _warn "network tool missing: $cmd"
    fi
done

# ---------------------------------------------------------------------------
# Optional: ROS2 Humble
# ---------------------------------------------------------------------------
section "ROS2"
if [[ -f /opt/ros/humble/setup.bash ]]; then
    _pass "ROS2 Humble setup exists: /opt/ros/humble/setup.bash"
else
    _warn "ROS2 Humble not found at /opt/ros/humble — run install_ros2_humble.sh if needed"
fi

if command -v ros2 >/dev/null 2>&1; then
    _pass "ros2 command available: $(ros2 --version 2>/dev/null | head -1)"
else
    _warn "ros2 command not available in current PATH — source /opt/ros/humble/setup.bash first"
fi

for pkg in python3-colcon-common-extensions python3-rosdep python3-vcstool; do
    if dpkg -s "$pkg" >/dev/null 2>&1; then
        _pass "package installed: $pkg"
    else
        _warn "package missing: $pkg (needed for wheeltec_ros2 build)"
    fi
done

# ---------------------------------------------------------------------------
# Optional: wheeltec workspace
# ---------------------------------------------------------------------------
section "wheeltec_ros2"
if [[ -d "$WHEELTEC_WS" ]]; then
    _pass "wheeltec workspace exists: $WHEELTEC_WS"
else
    _warn "wheeltec workspace not found: $WHEELTEC_WS"
fi

if [[ -f "$WHEELTEC_WS/install/setup.bash" ]]; then
    _pass "wheeltec install overlay exists"
else
    _warn "wheeltec not built — run build_wheeltec_ros2.sh"
fi

if [[ -d "$WHEELTEC_WS/src/robot_bringup" ]]; then
    _pass "robot_bringup package source present"
else
    _warn "robot_bringup not found in $WHEELTEC_WS/src"
fi

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
printf '\n============================\n'
printf 'Workstation check summary\n'
printf 'PASS: %d   WARN: %d   FAIL: %d\n' "$pass_count" "$warn_count" "$fail_count"
printf '============================\n'

if ((fail_count > 0)); then
    printf 'Some REQUIRED items are missing. Fix them before starting the backend.\n' >&2
    exit 1
fi
if ((warn_count > 0)); then
    printf 'Optional items are missing — review WARNs above.\n'
fi
printf 'Check complete.\n'

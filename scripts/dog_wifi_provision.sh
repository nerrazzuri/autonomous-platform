#!/usr/bin/env bash
set -euo pipefail

START_MOTION_CONTROL_PATH="/opt/app_launch/start_motion_control.sh"
ROBOT_LAUNCH_SERVICE_PATH="/etc/systemd/system/robot-launch.service"
TMP_DOG_MAC_PATH="/tmp/dog_mac"
TMP_DOG_IP_PATH="/tmp/dog_ip"

START_MOTION_CONTROL_START_MARKER="# DOG_WIFI_PROVISION_START"
START_MOTION_CONTROL_END_MARKER="# DOG_WIFI_PROVISION_END"
ROBOT_LAUNCH_UNIT_START_MARKER="# DOG_WIFI_PROVISION_UNIT_START"
ROBOT_LAUNCH_UNIT_END_MARKER="# DOG_WIFI_PROVISION_UNIT_END"
ROBOT_LAUNCH_SERVICE_START_MARKER="# DOG_WIFI_PROVISION_SERVICE_START"
ROBOT_LAUNCH_SERVICE_END_MARKER="# DOG_WIFI_PROVISION_SERVICE_END"

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

warn() {
  log "WARNING: $*"
}

die() {
  log "ERROR: $*"
  exit 1
}

usage() {
  cat <<'EOF'
Usage: sudo dog_wifi_provision.sh <target_wifi_ssid> <target_wifi_password>

Pass an empty string as <target_wifi_password> to connect to an open network.
EOF
}

require_command() {
  local command_name=$1
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    die "Required command not found: ${command_name}"
  fi
}

backup_file() {
  local file_path=$1
  local timestamp

  timestamp=$(date '+%Y%m%d%H%M%S')
  cp -p "${file_path}" "${file_path}.bak.${timestamp}"
}

get_wlan0_mac() {
  cat /sys/class/net/wlan0/address
}

get_wlan0_ip() {
  ip -4 addr show wlan0 | awk '/inet / {print $2}' | cut -d/ -f1 | head -n1
}

wait_for_wlan0_ip() {
  local attempt
  local ip_address

  for attempt in $(seq 1 30); do
    ip_address=$(get_wlan0_ip || true)
    if [ -n "${ip_address}" ]; then
      printf '%s\n' "${ip_address}"
      return 0
    fi
    sleep 2
  done

  return 1
}

replace_existing_marked_block() {
  local target_file=$1
  local start_marker=$2
  local end_marker=$3
  local block_file=$4
  local temp_file

  temp_file=$(mktemp)
  awk \
    -v start_marker="${start_marker}" \
    -v end_marker="${end_marker}" \
    -v block_file="${block_file}" \
    '
      BEGIN {
        replaced = 0
        skipping = 0
      }
      $0 == start_marker {
        while ((getline line < block_file) > 0) {
          print line
        }
        close(block_file)
        replaced = 1
        skipping = 1
        next
      }
      skipping && $0 == end_marker {
        skipping = 0
        next
      }
      skipping {
        next
      }
      {
        print
      }
      END {
        if (!replaced) {
          exit 1
        }
      }
    ' "${target_file}" > "${temp_file}" || {
    rm -f "${temp_file}"
    return 1
  }

  mv "${temp_file}" "${target_file}"
}

replace_first_matching_line_with_block() {
  local target_file=$1
  local match_pattern=$2
  local block_file=$3
  local temp_file

  temp_file=$(mktemp)
  awk \
    -v match_pattern="${match_pattern}" \
    -v block_file="${block_file}" \
    '
      BEGIN {
        replaced = 0
      }
      $0 ~ match_pattern && !replaced {
        while ((getline line < block_file) > 0) {
          print line
        }
        close(block_file)
        replaced = 1
        next
      }
      {
        print
      }
      END {
        if (!replaced) {
          exit 1
        }
      }
    ' "${target_file}" > "${temp_file}" || {
    rm -f "${temp_file}"
    return 1
  }

  mv "${temp_file}" "${target_file}"
}

insert_block_after_first_line() {
  local target_file=$1
  local block_file=$2
  local temp_file

  temp_file=$(mktemp)
  awk \
    -v block_file="${block_file}" \
    '
      NR == 1 {
        print
        while ((getline line < block_file) > 0) {
          print line
        }
        close(block_file)
        next
      }
      {
        print
      }
    ' "${target_file}" > "${temp_file}"

  mv "${temp_file}" "${target_file}"
}

insert_block_after_section() {
  local target_file=$1
  local section_header=$2
  local block_file=$3
  local temp_file

  temp_file=$(mktemp)
  awk \
    -v section_header="${section_header}" \
    -v block_file="${block_file}" \
    '
      BEGIN {
        inserted = 0
      }
      $0 == section_header && !inserted {
        print
        while ((getline line < block_file) > 0) {
          print line
        }
        close(block_file)
        inserted = 1
        next
      }
      {
        print
      }
      END {
        if (!inserted) {
          exit 1
        }
      }
    ' "${target_file}" > "${temp_file}" || {
    rm -f "${temp_file}"
    return 1
  }

  mv "${temp_file}" "${target_file}"
}

append_section_with_block() {
  local target_file=$1
  local section_header=$2
  local block_file=$3

  {
    printf '\n%s\n' "${section_header}"
    cat "${block_file}"
  } >> "${target_file}"
}

patch_start_motion_control() {
  local block_file

  [ -f "${START_MOTION_CONTROL_PATH}" ] || die "Missing ${START_MOTION_CONTROL_PATH}"

  backup_file "${START_MOTION_CONTROL_PATH}"

  block_file=$(mktemp)
  cat > "${block_file}" <<EOF
${START_MOTION_CONTROL_START_MARKER}
SDK_CLIENT_IP="\$(ip -4 addr show wlan0 | awk '/inet / {print \$2}' | cut -d/ -f1 | head -n1)"
if [ -z "\${SDK_CLIENT_IP}" ]; then
  echo "[DOG_WIFI_PROVISION] Failed to determine wlan0 IPv4 address" >&2
  exit 1
fi
export SDK_CLIENT_IP
${START_MOTION_CONTROL_END_MARKER}
EOF

  if grep -Fq "${START_MOTION_CONTROL_START_MARKER}" "${START_MOTION_CONTROL_PATH}"; then
    replace_existing_marked_block \
      "${START_MOTION_CONTROL_PATH}" \
      "${START_MOTION_CONTROL_START_MARKER}" \
      "${START_MOTION_CONTROL_END_MARKER}" \
      "${block_file}"
  elif grep -Eq '^[[:space:]]*(export[[:space:]]+)?SDK_CLIENT_IP=' "${START_MOTION_CONTROL_PATH}"; then
    replace_first_matching_line_with_block \
      "${START_MOTION_CONTROL_PATH}" \
      '^[[:space:]]*(export[[:space:]]+)?SDK_CLIENT_IP=' \
      "${block_file}"
  else
    insert_block_after_first_line "${START_MOTION_CONTROL_PATH}" "${block_file}"
  fi

  rm -f "${block_file}"
  log "Patched ${START_MOTION_CONTROL_PATH} to derive SDK_CLIENT_IP from wlan0 at boot"
}

patch_robot_launch_service() {
  local unit_block_file
  local service_block_file

  if [ ! -f "${ROBOT_LAUNCH_SERVICE_PATH}" ]; then
    warn "${ROBOT_LAUNCH_SERVICE_PATH} not found; skipping service patch"
    return 0
  fi

  backup_file "${ROBOT_LAUNCH_SERVICE_PATH}"

  unit_block_file=$(mktemp)
  service_block_file=$(mktemp)

  cat > "${unit_block_file}" <<EOF
${ROBOT_LAUNCH_UNIT_START_MARKER}
Wants=network-online.target
After=network-online.target
${ROBOT_LAUNCH_UNIT_END_MARKER}
EOF

  cat > "${service_block_file}" <<EOF
${ROBOT_LAUNCH_SERVICE_START_MARKER}
Environment="ROBOT_NET_INTERFACES=wlan0"
${ROBOT_LAUNCH_SERVICE_END_MARKER}
EOF

  if grep -Fq "${ROBOT_LAUNCH_UNIT_START_MARKER}" "${ROBOT_LAUNCH_SERVICE_PATH}"; then
    replace_existing_marked_block \
      "${ROBOT_LAUNCH_SERVICE_PATH}" \
      "${ROBOT_LAUNCH_UNIT_START_MARKER}" \
      "${ROBOT_LAUNCH_UNIT_END_MARKER}" \
      "${unit_block_file}"
  elif grep -Fxq "[Unit]" "${ROBOT_LAUNCH_SERVICE_PATH}"; then
    insert_block_after_section "${ROBOT_LAUNCH_SERVICE_PATH}" "[Unit]" "${unit_block_file}"
  else
    append_section_with_block "${ROBOT_LAUNCH_SERVICE_PATH}" "[Unit]" "${unit_block_file}"
  fi

  if grep -Fq "${ROBOT_LAUNCH_SERVICE_START_MARKER}" "${ROBOT_LAUNCH_SERVICE_PATH}"; then
    replace_existing_marked_block \
      "${ROBOT_LAUNCH_SERVICE_PATH}" \
      "${ROBOT_LAUNCH_SERVICE_START_MARKER}" \
      "${ROBOT_LAUNCH_SERVICE_END_MARKER}" \
      "${service_block_file}"
  elif grep -Fxq "[Service]" "${ROBOT_LAUNCH_SERVICE_PATH}"; then
    insert_block_after_section "${ROBOT_LAUNCH_SERVICE_PATH}" "[Service]" "${service_block_file}"
  else
    append_section_with_block "${ROBOT_LAUNCH_SERVICE_PATH}" "[Service]" "${service_block_file}"
  fi

  rm -f "${unit_block_file}" "${service_block_file}"

  if command -v systemctl >/dev/null 2>&1; then
    systemctl daemon-reload
    log "Reloaded systemd daemon after patching ${ROBOT_LAUNCH_SERVICE_PATH}"
  else
    warn "systemctl not found; skipped daemon-reload"
  fi
}

connect_wlan0() {
  local ssid=$1
  local password=$2

  if [ -n "${password}" ]; then
    log "Connecting wlan0 to WiFi SSID '${ssid}' using nmcli"
    nmcli --wait 30 device wifi connect "${ssid}" password "${password}" ifname wlan0
  else
    log "Connecting wlan0 to open WiFi SSID '${ssid}' using nmcli"
    nmcli --wait 30 device wifi connect "${ssid}" ifname wlan0
  fi
}

main() {
  local target_wifi_ssid
  local target_wifi_password
  local dog_mac
  local dog_ip

  if [ "${EUID}" -ne 0 ]; then
    die "This script must be run as root"
  fi

  if [ $# -lt 2 ]; then
    usage
    exit 1
  fi

  target_wifi_ssid=$1
  target_wifi_password=$2

  [ -n "${target_wifi_ssid}" ] || die "target WiFi SSID must be non-empty"
  require_command nmcli
  require_command ip
  require_command awk
  require_command cut
  require_command cp
  require_command mktemp

  [ -d /sys/class/net/wlan0 ] || die "wlan0 interface not found"

  dog_mac=$(get_wlan0_mac)
  printf '%s\n' "${dog_mac}" > "${TMP_DOG_MAC_PATH}"
  log "Recorded wlan0 MAC address to ${TMP_DOG_MAC_PATH}: ${dog_mac}"

  connect_wlan0 "${target_wifi_ssid}" "${target_wifi_password}"

  dog_ip=$(wait_for_wlan0_ip) || die "Timed out waiting for wlan0 IPv4 address"
  printf '%s\n' "${dog_ip}" > "${TMP_DOG_IP_PATH}"
  log "Recorded wlan0 IP address to ${TMP_DOG_IP_PATH}: ${dog_ip}"
  log "Detected robot wlan0 IP: ${dog_ip}"

  patch_start_motion_control
  patch_robot_launch_service

  log "Provisioning script completed successfully"
}

main "$@"

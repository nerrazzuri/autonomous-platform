#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT_DIR"

PYTHON_BIN="${PYTHON_BIN:-python3.10}"
VENV_DIR="${VENV_DIR:-$ROOT_DIR/.venv-logistics310}"
CONFIG_TEMPLATE="${CONFIG_TEMPLATE:-$ROOT_DIR/apps/logistics/config/logistics_demo_config.yaml}"
GENERATED_DIR="${GENERATED_DIR:-$ROOT_DIR/.runtime}"
GENERATED_CONFIG="${GENERATED_CONFIG:-$GENERATED_DIR/logistics_demo_config.generated.yaml}"

QUADRUPED_IP="${QUADRUPED_IP:-192.168.234.1}"
OPERATOR_TOKEN="${OPERATOR_TOKEN:-change-me-operator}"
SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-change-me-supervisor}"
QA_TOKEN="${QA_TOKEN:-change-me-qa}"

fail() {
  printf 'ERROR %s\n' "$1" >&2
  exit 1
}

need_command() {
  command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"
}

detect_local_ip() {
  python3 - <<'PY'
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
try:
    sock.connect(("8.8.8.8", 80))
    print(sock.getsockname()[0])
except OSError:
    print("127.0.0.1")
finally:
    sock.close()
PY
}

validate_non_placeholder() {
  local label="$1"
  local value="$2"
  local placeholder="$3"

  if [[ -z "$value" || "$value" == "$placeholder" ]]; then
    fail "${label} must be set to a real value, not ${placeholder}"
  fi
}

need_command "$PYTHON_BIN"
need_command python3

LOCAL_IP="${LOCAL_IP:-$(detect_local_ip)}"

validate_non_placeholder "LOCAL_IP" "$LOCAL_IP" "your-workstation-ip"

mkdir -p "$GENERATED_DIR"

if [[ ! -d "$VENV_DIR" ]]; then
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

python --version | grep -q "3.10" || fail "Virtual environment is not using Python 3.10"

if ! python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
fi

python - <<'PY' "$CONFIG_TEMPLATE" "$GENERATED_CONFIG" "$QUADRUPED_IP" "$LOCAL_IP" "$OPERATOR_TOKEN" "$SUPERVISOR_TOKEN" "$QA_TOKEN"
from pathlib import Path
import sys

template_path = Path(sys.argv[1])
generated_path = Path(sys.argv[2])
quadruped_ip = sys.argv[3]
local_ip = sys.argv[4]
operator_token = sys.argv[5]
supervisor_token = sys.argv[6]
qa_token = sys.argv[7]

content = template_path.read_text(encoding="utf-8")
content = content.replace("__QUADRUPED_IP__", quadruped_ip)
content = content.replace("__LOCAL_IP__", local_ip)
content = content.replace("__OPERATOR_TOKEN__", operator_token)
content = content.replace("__SUPERVISOR_TOKEN__", supervisor_token)
content = content.replace("__QA_TOKEN__", qa_token)
generated_path.write_text(content, encoding="utf-8")
PY

export QUADRUPED_CONFIG_PATH="$GENERATED_CONFIG"

printf 'Logistics config: %s\n' "$QUADRUPED_CONFIG_PATH"
printf 'Local IP: %s\n' "$LOCAL_IP"
printf 'Quadruped IP: %s\n' "$QUADRUPED_IP"
printf 'Supervisor UI: http://localhost:8080/ui/supervisor.html?token=%s\n' "$SUPERVISOR_TOKEN"
printf 'Operator UI: http://localhost:8080/ui/operator.html?station_id=A&token=%s\n' "$OPERATOR_TOKEN"
printf 'Kiosk UI: http://localhost:8080/ui/kiosk.html?station_id=A&token=%s\n' "$OPERATOR_TOKEN"
printf 'Smoke test: BASE_URL=http://localhost:8080 OPERATOR_TOKEN=%s SUPERVISOR_TOKEN=%s apps/logistics/scripts/manual_e2e_smoke.sh\n' "$OPERATOR_TOKEN" "$SUPERVISOR_TOKEN"

python scripts/sdk_preflight_check.py

exec python -c "from apps.logistics.runtime.startup import main; main()"

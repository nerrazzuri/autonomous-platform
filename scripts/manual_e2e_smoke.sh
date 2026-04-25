#!/usr/bin/env bash

set -euo pipefail

exec "$(dirname "$0")/../apps/logistics/scripts/manual_e2e_smoke.sh" "$@"

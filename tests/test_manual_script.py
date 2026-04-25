from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SCRIPT_PATH = ROOT / "apps" / "logistics" / "scripts" / "manual_e2e_smoke.sh"
WRAPPER_SCRIPT_PATH = ROOT / "scripts" / "manual_e2e_smoke.sh"


def test_canonical_manual_script_exists() -> None:
    assert CANONICAL_SCRIPT_PATH.exists()


def test_canonical_manual_script_is_executable() -> None:
    assert os.access(CANONICAL_SCRIPT_PATH, os.X_OK)


def test_canonical_manual_script_references_required_endpoints() -> None:
    content = CANONICAL_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "/health" in content
    assert "/quadruped/status" in content
    assert "/queue/status" in content
    assert "/tasks" in content
    assert "/confirm-load" in content
    assert "/confirm-unload" in content
    assert "/routes" in content
    assert "/estop" in content
    assert "/estop/release" in content


def test_canonical_manual_script_uses_env_vars() -> None:
    content = CANONICAL_SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'BASE_URL="${BASE_URL:-http://localhost:8080}"' in content
    assert 'OPERATOR_TOKEN="${OPERATOR_TOKEN:-change-me-operator}"' in content
    assert 'SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-change-me-supervisor}"' in content


def test_canonical_manual_script_does_not_require_jq() -> None:
    content = CANONICAL_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "jq" not in content


def test_root_manual_script_wrapper_points_to_canonical_script() -> None:
    assert WRAPPER_SCRIPT_PATH.exists()
    assert os.access(WRAPPER_SCRIPT_PATH, os.X_OK)

    content = WRAPPER_SCRIPT_PATH.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash")
    assert 'exec "$(dirname "$0")/../apps/logistics/scripts/manual_e2e_smoke.sh" "$@"' in content

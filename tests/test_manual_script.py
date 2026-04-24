from __future__ import annotations

import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / "scripts" / "manual_e2e_smoke.sh"


def test_manual_script_exists() -> None:
    assert SCRIPT_PATH.exists()


def test_manual_script_is_executable() -> None:
    assert os.access(SCRIPT_PATH, os.X_OK)


def test_manual_script_references_required_endpoints() -> None:
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "/health" in content
    assert "/quadruped/status" in content
    assert "/queue/status" in content
    assert "/tasks" in content
    assert "/confirm-load" in content
    assert "/confirm-unload" in content
    assert "/routes" in content
    assert "/estop" in content
    assert "/estop/release" in content


def test_manual_script_uses_env_vars() -> None:
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert 'BASE_URL="${BASE_URL:-http://localhost:8080}"' in content
    assert 'OPERATOR_TOKEN="${OPERATOR_TOKEN:-change-me-operator}"' in content
    assert 'SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-change-me-supervisor}"' in content


def test_manual_script_does_not_require_jq() -> None:
    content = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "jq" not in content

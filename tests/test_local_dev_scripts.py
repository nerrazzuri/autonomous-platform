from __future__ import annotations

import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_start_logistics_dev_refuses_placeholder_tokens(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "DRY_RUN": "1",
        "VENV_DIR": str(tmp_path / "missing-venv"),
        "APP_CONFIG": "apps/logistics/config/logistics_demo_config.yaml",
    }
    env.pop("ALLOW_PLACEHOLDER_TOKENS", None)

    result = subprocess.run(
        ["bash", "scripts/start_logistics_dev.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert "placeholder" in (result.stdout + result.stderr).lower()


def test_start_logistics_dev_allows_placeholder_tokens_for_explicit_dry_run(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "ALLOW_PLACEHOLDER_TOKENS": "1",
        "DRY_RUN": "1",
        "VENV_DIR": str(tmp_path / "missing-venv"),
        "APP_CONFIG": "apps/logistics/config/logistics_demo_config.yaml",
    }

    result = subprocess.run(
        ["bash", "scripts/start_logistics_dev.sh"],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "DRY_RUN=1 set; backend not started." in result.stdout

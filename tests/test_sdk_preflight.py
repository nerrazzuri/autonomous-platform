from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]


def test_sdk_preflight_script_exists() -> None:
    script_path = ROOT / "scripts" / "sdk_preflight_check.py"

    assert script_path.exists()


def test_sdk_preflight_prints_expected_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    config_path.write_text(
        dedent(
            """
            quadruped:
              quadruped_ip: "192.168.234.1"
              sdk_port: 44999
              sdk_lib_path: "/opt/agibot/sdk"
            workstation:
              local_ip: "0.0.0.0"
            """
        ).strip()
        + "\n",
        encoding="utf-8",
    )

    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "sdk_preflight_check.py")],
        cwd=ROOT,
        env={**os.environ, "QUADRUPED_CONFIG_PATH": str(config_path)},
        capture_output=True,
        text=True,
        check=True,
    )

    assert "quadruped_ip: 192.168.234.1" in result.stdout
    assert "local_ip: 0.0.0.0" in result.stdout
    assert "sdk_port: 44999" in result.stdout
    assert "sdk_lib_path: /opt/agibot/sdk" in result.stdout
    assert "WARNING: local_ip is 0.0.0.0" in result.stdout
    assert "expected_robot_sdk_config.target_ip: 0.0.0.0" in result.stdout
    assert "expected_robot_sdk_config.target_port: 44999" in result.stdout

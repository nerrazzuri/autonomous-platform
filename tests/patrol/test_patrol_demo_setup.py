from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_patrol_demo_config_exists_with_patrol_specific_paths() -> None:
    config_path = ROOT / "apps" / "patrol" / "config" / "patrol_demo_config.yaml"

    assert config_path.exists()

    content = config_path.read_text(encoding="utf-8")
    assert 'sdk_lib_path: "sdk/zsl-1"' in content
    assert 'sqlite_path: "data/patrol_quadruped.db"' in content
    assert 'routes_file: "data/patrol_routes.json"' in content
    assert 'stations_file: "data/patrol_stations.json"' in content
    assert "__LOCAL_IP__" in content
    assert "__SUPERVISOR_TOKEN__" in content


def test_patrol_demo_start_script_exists_and_uses_python310() -> None:
    script_path = ROOT / "apps" / "patrol" / "scripts" / "start_patrol_demo.sh"

    assert script_path.exists()
    assert os.access(script_path, os.X_OK)

    mode = script_path.stat().st_mode
    assert mode & stat.S_IXUSR

    content = script_path.read_text(encoding="utf-8")
    assert "python3.10" in content
    assert "QUADRUPED_CONFIG_PATH" in content
    assert "patrol_demo_config.yaml" in content
    assert "from apps.patrol.runtime.startup import main; main()" in content
    assert "python -m apps.patrol.runtime.startup" not in content
    assert "your-workstation-ip" in content
    assert "change-me-supervisor" in content


def test_patrol_demo_stations_file_exists() -> None:
    stations_path = ROOT / "data" / "patrol_stations.json"

    assert stations_path.exists()

    content = stations_path.read_text(encoding="utf-8")
    assert '"stations"' in content
    assert '"DOCK"' in content


def test_patrol_runtime_source_avoids_datetime_utc_constant() -> None:
    patrol_sources = [
        ROOT / "apps" / "patrol" / "observation" / "anomaly_decider.py",
        ROOT / "apps" / "patrol" / "tasks" / "patrol_watchdog.py",
    ]

    for source_path in patrol_sources:
        content = source_path.read_text(encoding="utf-8")
        assert "from datetime import UTC" not in content
        assert "datetime.now(UTC)" not in content

from __future__ import annotations

import os
import stat
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_logistics_demo_config_exists_with_logistics_specific_paths() -> None:
    config_path = ROOT / "apps" / "logistics" / "config" / "logistics_demo_config.yaml"

    assert config_path.exists()

    content = config_path.read_text(encoding="utf-8")
    assert 'sdk_lib_path: "sdk/zsl-1"' in content
    assert 'sqlite_path: "data/logistics_quadruped.db"' in content
    assert 'routes_file: "data/routes.json"' in content
    assert 'stations_file: "data/stations.json"' in content
    assert 'routes_file: "data/logistics_routes.json"' in content
    assert "__LOCAL_IP__" in content
    assert "__OPERATOR_TOKEN__" in content
    assert "__SUPERVISOR_TOKEN__" in content


def test_logistics_demo_config_enables_ros2_bridge() -> None:
    from shared.core.config import load_config

    config = load_config(ROOT / "apps" / "logistics" / "config" / "logistics_demo_config.yaml")

    assert config.ros2.enabled is True
    assert config.ros2.scan_topic == "/scan"
    assert config.ros2.pose_topic == "/pose"
    assert config.ros2.odom_topic == "/odom"
    assert config.ros2.base_frame == "BASE_LINK"


def test_logistics_demo_start_script_exists_and_uses_python310() -> None:
    script_path = ROOT / "apps" / "logistics" / "scripts" / "start_logistics_demo.sh"

    assert script_path.exists()
    assert os.access(script_path, os.X_OK)

    mode = script_path.stat().st_mode
    assert mode & stat.S_IXUSR

    content = script_path.read_text(encoding="utf-8")
    assert "python3.10" in content
    assert "QUADRUPED_CONFIG_PATH" in content
    assert "logistics_demo_config.yaml" in content
    assert "from apps.logistics.runtime.startup import main; main()" in content
    assert "python -m apps.logistics.runtime.startup" not in content


def test_logistics_demo_data_files_exist() -> None:
    routes_path = ROOT / "data" / "logistics_routes.json"

    assert routes_path.exists()

    routes_content = routes_path.read_text(encoding="utf-8")

    assert '"LINE_A_TO_QA"' in routes_content
    assert '"RETURN_TO_DOCK"' in routes_content
    assert '"LINE_A"' in routes_content
    assert '"DOCK"' in routes_content

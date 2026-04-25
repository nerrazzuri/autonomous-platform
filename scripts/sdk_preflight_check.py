#!/usr/bin/env python3
from __future__ import annotations

"""Print local SDK wiring details for Agibot D1 EDU setup without touching hardware."""

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.core.config import load_config


def main() -> int:
    config = load_config()

    print(f"quadruped_ip: {config.quadruped.quadruped_ip}")
    print(f"local_ip: {config.workstation.local_ip}")
    print(f"sdk_port: {config.quadruped.sdk_port}")
    print(f"sdk_lib_path: {config.quadruped.sdk_lib_path}")

    if config.workstation.local_ip == "0.0.0.0":
        print("WARNING: local_ip is 0.0.0.0; initRobot needs the actual workstation IP.")

    print(f"expected_robot_sdk_config.target_ip: {config.workstation.local_ip}")
    print(f"expected_robot_sdk_config.target_port: {config.quadruped.sdk_port}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

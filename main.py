from __future__ import annotations

"""Platform entry point with app selection."""

import argparse
from collections.abc import Sequence
import sys

from apps.logistics.runtime import startup as _logistics_startup


base_startup = _logistics_startup.base_startup
startup_system = _logistics_startup.startup_system
shutdown_system = _logistics_startup.shutdown_system
create_uvicorn_config = _logistics_startup.create_uvicorn_config
get_dispatcher = _logistics_startup.get_dispatcher
get_battery_manager = _logistics_startup.get_battery_manager
get_watchdog = _logistics_startup.get_watchdog


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agibot D1 EDU Autonomous Platform")
    parser.add_argument(
        "--app",
        default="logistics",
        choices=["logistics", "patrol"],
        help="Mission app to launch (default: logistics)",
    )
    return parser


def _load_app_main(app_name: str):
    if app_name == "logistics":
        from apps.logistics.runtime.startup import main as app_main

        return app_main
    if app_name == "patrol":
        from apps.patrol.runtime.startup import main as app_main

        return app_main
    print(f"Unknown app: {app_name}", file=sys.stderr)
    sys.exit(1)


def main(argv: Sequence[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)
    app_main = _load_app_main(args.app)
    app_main()


if __name__ == "__main__":
    main()

from __future__ import annotations

"""Prototype entrypoint for the quadruped-side TJC HMI agent."""

import argparse
import logging

from apps.hmi_agent.config import TjcHmiAgentConfig


logger = logging.getLogger("apps.hmi_agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Prototype TJC HMI agent")
    parser.add_argument("--serial-port", default=TjcHmiAgentConfig.serial_port)
    parser.add_argument("--baudrate", type=int, default=TjcHmiAgentConfig.baudrate)
    parser.add_argument("--ws-url", default=TjcHmiAgentConfig.ws_url)
    parser.add_argument("--robot-id", default=TjcHmiAgentConfig.robot_id)
    parser.add_argument("--screen-id", default=TjcHmiAgentConfig.screen_id)
    parser.add_argument("--token", required=True)
    return parser


def parse_args(argv: list[str] | None = None) -> TjcHmiAgentConfig:
    args = build_parser().parse_args(argv)
    return TjcHmiAgentConfig(
        serial_port=args.serial_port,
        baudrate=args.baudrate,
        ws_url=args.ws_url,
        robot_id=args.robot_id,
        screen_id=args.screen_id,
        token=args.token,
    )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    config = parse_args(argv)
    logger.info(
        "TJC HMI agent prototype configured",
        extra={
            "serial_port": config.serial_port,
            "baudrate": config.baudrate,
            "ws_url": config.ws_url,
            "robot_id": config.robot_id,
            "screen_id": config.screen_id,
        },
    )
    logger.info("Serial read loop is intentionally not auto-started in this prototype module")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main", "parse_args"]

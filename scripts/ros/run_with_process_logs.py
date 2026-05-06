#!/usr/bin/env python3
"""Run a helper process while capturing stdout/stderr under logs/ros."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.observability.process_logs import ProcessLogCapture, ProcessStartError  # noqa: E402


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture stdout/stderr for ROS or helper processes.")
    parser.add_argument("--name", required=True, help="Logical process name used for log filenames.")
    parser.add_argument("--log-dir", default="logs", help="Base log directory. stdout/stderr go under <log-dir>/ros.")
    parser.add_argument("--cwd", default=None, help="Optional child process working directory.")
    parser.add_argument("--timeout", type=float, default=None, help="Optional wait timeout in seconds.")
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command to run after --.")
    args = parser.parse_args(argv)
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("command is required after --")
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    capture = ProcessLogCapture(
        name=args.name,
        command=args.command,
        log_dir=args.log_dir,
        cwd=args.cwd,
    )
    try:
        status = capture.start()
    except ProcessStartError as exc:
        print(f"process name: {capture.name}")
        print(f"start failed: {exc}", file=sys.stderr)
        print(f"stdout: {capture.status.stdout_path}")
        print(f"stderr: {capture.status.stderr_path}")
        return 127

    print(f"process name: {status.name}")
    print(f"pid: {status.pid}")
    print(f"stdout: {status.stdout_path}")
    print(f"stderr: {status.stderr_path}")
    try:
        status = capture.wait(timeout=args.timeout)
    except subprocess.TimeoutExpired:
        status = capture.terminate()
    except KeyboardInterrupt:
        status = capture.terminate()
    finally:
        capture.close()

    exit_code = status.exit_code if status.exit_code is not None else 1
    print(f"exit code: {exit_code}")
    return int(exit_code)


if __name__ == "__main__":
    raise SystemExit(main())

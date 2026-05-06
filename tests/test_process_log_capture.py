from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import threading

import pytest


class FakeReporter:
    def __init__(self, *, fail: bool = False):
        self.fail = fail
        self.events: list[dict] = []

    def report(self, **kwargs):
        if self.fail:
            raise RuntimeError("diagnostic failed")
        self.events.append(kwargs)
        return kwargs


def _python_command(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_process_log_capture_writes_stdout_and_stderr_logs(tmp_path: Path) -> None:
    from shared.observability.process_logs import ProcessLogCapture

    reporter = FakeReporter()
    capture = ProcessLogCapture(
        name="mapping",
        command=_python_command("import sys; print('hello out'); print('hello err', file=sys.stderr)"),
        log_dir=tmp_path,
        reporter=reporter,
    )

    capture.start()
    status = capture.wait(timeout=5)

    assert status.exit_code == 0
    assert status.stdout_path.read_text(encoding="utf-8").strip() == "hello out"
    assert status.stderr_path.read_text(encoding="utf-8").strip() == "hello err"
    assert status.combined_path is None
    assert [event["event"] for event in reporter.events] == ["process.started", "process.exited"]
    assert reporter.events[0]["details"]["process_name"] == "mapping"
    assert reporter.events[0]["details"]["pid"] == status.pid


def test_nonzero_exit_code_is_captured_and_reported(tmp_path: Path) -> None:
    from shared.diagnostics import error_codes
    from shared.observability.process_logs import ProcessLogCapture

    reporter = FakeReporter()
    capture = ProcessLogCapture(
        name="localization",
        command=_python_command("import sys; print('bad', file=sys.stderr); sys.exit(2)"),
        log_dir=tmp_path,
        reporter=reporter,
    )

    capture.start()
    status = capture.wait(timeout=5)

    assert status.exit_code == 2
    assert "bad" in status.stderr_path.read_text(encoding="utf-8")
    assert reporter.events[-1]["event"] == "process.failed"
    assert reporter.events[-1]["error_code"] == error_codes.PROCESS_EXITED_NONZERO
    assert reporter.events[-1]["details"]["exit_code"] == 2


def test_start_failure_is_reported_and_does_not_leak_handles(tmp_path: Path) -> None:
    from shared.diagnostics import error_codes
    from shared.observability.process_logs import ProcessLogCapture, ProcessStartError

    reporter = FakeReporter()
    capture = ProcessLogCapture(
        name="missing-process",
        command=["/definitely/missing/process"],
        log_dir=tmp_path,
        reporter=reporter,
    )

    with pytest.raises(ProcessStartError):
        capture.start()

    assert reporter.events[-1]["event"] == "process.start_failed"
    assert reporter.events[-1]["error_code"] == error_codes.PROCESS_START_FAILED
    assert capture.status.exit_code is None
    capture.close()
    capture.close()


def test_process_name_sanitization_prevents_path_traversal(tmp_path: Path) -> None:
    from shared.observability.process_logs import ProcessLogCapture, sanitize_process_name

    safe_name = sanitize_process_name("../../bad process/name")
    capture = ProcessLogCapture(
        name="../../bad process/name",
        command=_python_command("print('ok')"),
        log_dir=tmp_path,
    )

    capture.start()
    status = capture.wait(timeout=5)

    assert safe_name
    assert "/" not in safe_name
    assert "\\" not in safe_name
    assert status.stdout_path == tmp_path / "ros" / f"{safe_name}.stdout.log"
    assert not (tmp_path.parent / "bad process").exists()


def test_sensitive_command_details_are_redacted(tmp_path: Path) -> None:
    from shared.diagnostics.redaction import REDACTION_MARKER
    from shared.observability.process_logs import ProcessLogCapture

    reporter = FakeReporter()
    capture = ProcessLogCapture(
        name="ros2_bridge",
        command=_python_command("print('ok')") + ["--token", "super-secret-token", "--mode=demo"],
        log_dir=tmp_path,
        reporter=reporter,
    )

    capture.start()
    capture.wait(timeout=5)

    command = reporter.events[0]["details"]["command"]
    assert "--token" in command
    assert REDACTION_MARKER in command
    assert "super-secret-token" not in json.dumps(reporter.events)


def test_reporter_failure_does_not_break_process_capture(tmp_path: Path) -> None:
    from shared.observability.process_logs import ProcessLogCapture

    capture = ProcessLogCapture(
        name="slam_toolbox",
        command=_python_command("print('ok')"),
        log_dir=tmp_path,
        reporter=FakeReporter(fail=True),
    )

    capture.start()
    status = capture.wait(timeout=5)

    assert status.exit_code == 0
    assert status.stdout_path.read_text(encoding="utf-8").strip() == "ok"


def test_terminate_prevents_leaked_subprocesses(tmp_path: Path) -> None:
    from shared.observability.process_logs import ProcessLogCapture

    before_threads = {thread.name for thread in threading.enumerate()}
    capture = ProcessLogCapture(
        name="long_running",
        command=_python_command("import time; time.sleep(30)"),
        log_dir=tmp_path,
    )

    capture.start()
    status = capture.terminate(timeout=5)

    assert status.exit_code is not None
    assert capture.poll() == status.exit_code
    after_threads = {thread.name for thread in threading.enumerate()}
    assert after_threads == before_threads


def test_context_manager_cleans_up_running_process(tmp_path: Path) -> None:
    from shared.observability.process_logs import ProcessLogCapture

    with ProcessLogCapture(
        name="context_process",
        command=_python_command("import time; time.sleep(30)"),
        log_dir=tmp_path,
    ) as capture:
        assert capture.status.pid is not None

    assert capture.status.exit_code is not None


def test_cli_wrapper_returns_child_exit_code_and_prints_log_paths(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "ros" / "run_with_process_logs.py"
    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--name",
            "cli-test",
            "--log-dir",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        text=True,
        capture_output=True,
    )

    assert result.returncode == 3
    assert "process name: cli-test" in result.stdout
    assert "exit code: 3" in result.stdout
    assert "stdout:" in result.stdout
    assert "stderr:" in result.stdout
    assert (tmp_path / "ros" / "cli-test.stdout.log").read_text(encoding="utf-8").strip() == "out"
    assert (tmp_path / "ros" / "cli-test.stderr.log").read_text(encoding="utf-8").strip() == "err"


def test_cli_wrapper_does_not_require_ros_to_import(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "ros" / "run_with_process_logs.py"
    env = os.environ.copy()
    for key in ("PYTHONPATH", "ROS_DISTRO", "AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH"):
        env.pop(key, None)

    result = subprocess.run(
        [
            sys.executable,
            str(script),
            "--name",
            "no-ros",
            "--log-dir",
            str(tmp_path),
            "--",
            sys.executable,
            "-c",
            "print('no ros needed')",
        ],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0
    assert "no ros needed" in (tmp_path / "ros" / "no-ros.stdout.log").read_text(encoding="utf-8")


def test_shared_process_log_code_has_no_app_or_ros_imports() -> None:
    root = Path(__file__).resolve().parents[1]
    content = (root / "shared" / "observability" / "process_logs.py").read_text(encoding="utf-8")

    forbidden = (
        "from apps",
        "import apps",
        "import " + "rclpy",
        "from " + "rclpy",
        "LINE_A",
        "LINE_B",
        "LINE_C",
        "Sumitomo",
        "HUMAN_CONFIRMED_LOAD",
        "HUMAN_CONFIRMED_UNLOAD",
        "PATROL_",
        "load/unload",
        "patrol cycle",
        "patrol waypoint",
    )
    for term in forbidden:
        assert term not in content

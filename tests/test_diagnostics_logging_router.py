from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def shutdown_logging_router():
    from shared.diagnostics.logging_router import shutdown_diagnostics_logging

    shutdown_diagnostics_logging()
    yield
    shutdown_diagnostics_logging()


def test_configure_creates_log_directories_and_master_files(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging

    configure_diagnostics_logging(log_dir=tmp_path)

    assert tmp_path.is_dir()
    assert (tmp_path / "modules").is_dir()
    assert (tmp_path / "app.log").is_file()
    assert (tmp_path / "app.jsonl").is_file()


def test_module_logger_writes_master_and_module_jsonl(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger

    configure_diagnostics_logging(log_dir=tmp_path)
    logger = get_diagnostic_logger("sdk_adapter")

    logger.info(
        "SDK connection established",
        extra={
            "event": "sdk.connected",
            "robot_id": "robot_01",
            "context": {"route_id": "context-route", "api_token": "fake-token-value"},
            "task_id": "task-1",
            "route_id": "route-a",
            "error_code": "sdk.connect_failed",
            "correlation_id": "corr-1",
            "details": {"attempt": 1},
        },
    )

    app_records = read_jsonl(tmp_path / "app.jsonl")
    module_records = read_jsonl(tmp_path / "modules" / "sdk_adapter.jsonl")

    assert app_records == module_records
    record = app_records[0]
    assert record["level"] == "INFO"
    assert record["module"] == "sdk_adapter"
    assert record["event"] == "sdk.connected"
    assert record["message"] == "SDK connection established"
    assert record["robot_id"] == "robot_01"
    assert record["context"] == {"route_id": "context-route", "api_token": "[REDACTED]"}
    assert record["task_id"] == "task-1"
    assert record["route_id"] == "route-a"
    assert record["error_code"] == "sdk.connect_failed"
    assert record["correlation_id"] == "corr-1"
    assert record["details"] == {"attempt": 1}
    assert isinstance(record["ts"], str)
    assert "SDK connection established" in (tmp_path / "app.log").read_text(encoding="utf-8")


def test_redacts_sensitive_details_and_extra_fields(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger

    configure_diagnostics_logging(log_dir=tmp_path)
    logger = get_diagnostic_logger("hmi")

    logger.warning(
        "Rejected HMI action",
        extra={
            "event": "hmi.rejected",
            "details": {
                "token": "fake-token-value",
                "nested": {"password": "fake-password-value"},
                "header": "Bearer " + "placeholder",
                "robot_id": "robot_01",
            },
            "authorization": "fake-authorization-value",
        },
    )

    record = read_jsonl(tmp_path / "modules" / "hmi.jsonl")[0]

    assert record["details"]["token"] == "[REDACTED]"
    assert record["details"]["nested"]["password"] == "[REDACTED]"
    assert record["details"]["header"] == "[REDACTED]"
    assert record["details"]["robot_id"] == "robot_01"
    assert record["details"]["authorization"] == "[REDACTED]"
    assert "fake-token-value" not in (tmp_path / "app.jsonl").read_text(encoding="utf-8")
    assert "fake-password-value" not in (tmp_path / "modules" / "hmi.jsonl").read_text(encoding="utf-8")


def test_repeated_configure_does_not_duplicate_handlers(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger

    configure_diagnostics_logging(log_dir=tmp_path)
    configure_diagnostics_logging(log_dir=tmp_path)
    logger = get_diagnostic_logger("dispatcher")

    logger.info("Dispatcher ready", extra={"event": "dispatcher.ready"})

    assert len(read_jsonl(tmp_path / "app.jsonl")) == 1
    assert len(read_jsonl(tmp_path / "modules" / "dispatcher.jsonl")) == 1


def test_shutdown_is_idempotent_and_closes_owned_handlers(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import (
        configure_diagnostics_logging,
        get_diagnostic_logger,
        shutdown_diagnostics_logging,
    )

    configure_diagnostics_logging(log_dir=tmp_path)
    get_diagnostic_logger("task_queue").info("Task queued", extra={"event": "task.queued"})

    shutdown_diagnostics_logging()
    shutdown_diagnostics_logging()

    diagnostics_logger = logging.getLogger("diagnostics")
    assert diagnostics_logger.handlers == []


def test_unsafe_module_names_are_sanitized(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger, sanitize_module_name

    configure_diagnostics_logging(log_dir=tmp_path)
    module_name = "../../bad module/name"
    safe_name = sanitize_module_name(module_name)

    get_diagnostic_logger(module_name).info("Unsafe module name routed", extra={"event": "test.event"})

    assert safe_name
    assert "/" not in safe_name
    assert "\\" not in safe_name
    assert (tmp_path / "modules" / f"{safe_name}.jsonl").is_file()
    assert not (tmp_path.parent / "bad module").exists()


def test_unknown_empty_module_routes_to_unknown(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger

    configure_diagnostics_logging(log_dir=tmp_path)
    get_diagnostic_logger("   ").info("Unknown module", extra={"event": "test.event"})

    assert (tmp_path / "modules" / "unknown.jsonl").is_file()


def test_rotation_uses_configured_limits(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger

    configure_diagnostics_logging(log_dir=tmp_path, max_bytes=300, backup_count=1)
    logger = get_diagnostic_logger("system_health")

    for index in range(12):
        logger.info(
            "Health snapshot %s %s",
            index,
            "x" * 120,
            extra={"event": "health.snapshot", "details": {"index": index}},
        )

    assert (tmp_path / "app.jsonl.1").is_file()
    assert (tmp_path / "modules" / "system_health.jsonl.1").is_file()


def test_import_is_safe_without_ros_or_sdk_env() -> None:
    code = "import shared.diagnostics.logging_router; print('logging router import ok')"
    env = os.environ.copy()
    for key in ("PYTHONPATH", "ROS_DISTRO", "AMENT_PREFIX_PATH", "COLCON_PREFIX_PATH"):
        env.pop(key, None)

    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert "logging router import ok" in result.stdout

from __future__ import annotations

import importlib
import json
import logging
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import AppConfig


def load_logger_module():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    sys.modules.pop("core.logger", None)
    return importlib.import_module("core.logger")


def remove_owned_handlers() -> None:
    root_logger = logging.getLogger()
    for handler in list(root_logger.handlers):
        if getattr(handler, "_platform_logger_handler", False):
            root_logger.removeHandler(handler)
            handler.close()


@pytest.fixture(autouse=True)
def reset_logger_state():
    remove_owned_handlers()
    module = load_logger_module()
    module.clear_runtime_context()
    yield
    module.clear_runtime_context()
    remove_owned_handlers()


def make_config(
    tmp_path: Path,
    *,
    json_output: bool = True,
    rotating_file_enabled: bool = False,
    level: str = "INFO",
) -> AppConfig:
    config = AppConfig()
    config.logging.log_dir = str(tmp_path / "logs")
    config.logging.json_output = json_output
    config.logging.rotating_file_enabled = rotating_file_enabled
    config.logging.level = level
    config.logging.max_file_mb = 1
    config.logging.backup_count = 2
    return config


def parse_last_json_line(capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    output = capsys.readouterr().out.strip().splitlines()
    assert output, "expected logger output on stdout"
    return json.loads(output[-1])


def flush_owned_handlers() -> None:
    for handler in logging.getLogger().handlers:
        if getattr(handler, "_platform_logger_handler", False):
            handler.flush()


def test_get_logger_returns_logger(tmp_path: Path) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))

    logger = module.get_logger("tests.logger")

    assert isinstance(logger, logging.Logger)
    assert logger.name == "tests.logger"


def test_setup_logging_idempotent_no_duplicate_handlers(tmp_path: Path) -> None:
    module = load_logger_module()
    config = make_config(tmp_path, rotating_file_enabled=True)

    module.setup_logging(config)
    first_owned_handlers = [
        handler for handler in logging.getLogger().handlers if getattr(handler, "_platform_logger_handler", False)
    ]

    module.setup_logging(config)
    second_owned_handlers = [
        handler for handler in logging.getLogger().handlers if getattr(handler, "_platform_logger_handler", False)
    ]

    assert len(first_owned_handlers) == 2
    assert len(second_owned_handlers) == 2
    assert len({id(handler) for handler in second_owned_handlers}) == 2


def test_json_log_contains_required_fields(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path, json_output=True))

    logger = module.get_logger("tests.logger")
    logger.info("Quadruped logger started")

    payload = parse_last_json_line(capsys)

    assert isinstance(payload["timestamp"], str)
    assert payload["module"] == "tests.logger"
    assert payload["severity"] == "INFO"
    assert payload["message"] == "Quadruped logger started"
    assert payload["task_id"] is None
    assert payload["quadruped_state"] is None
    assert payload["event_name"] is None
    assert payload["extra"] == {}


def test_runtime_context_included_in_log(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))
    module.set_runtime_context(task_id="task-42", quadruped_state="moving")

    logger = module.get_logger("tests.runtime")
    logger.info("Task active")

    payload = parse_last_json_line(capsys)

    assert payload["task_id"] == "task-42"
    assert payload["quadruped_state"] == "moving"


def test_clear_runtime_context_resets_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))
    module.set_runtime_context(task_id="task-99", quadruped_state="charging")
    module.clear_runtime_context()

    logger = module.get_logger("tests.runtime")
    logger.info("Context cleared")

    payload = parse_last_json_line(capsys)

    assert payload["task_id"] is None
    assert payload["quadruped_state"] is None


def test_structured_extra_fields_go_to_extra(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))

    logger = module.get_logger("tests.extra")
    logger.info(
        "Task dispatched",
        extra={
            "event_name": "task.dispatched",
            "station_id": "A",
            "destination_id": "QA",
            "priority": 2,
        },
    )

    payload = parse_last_json_line(capsys)

    assert payload["extra"] == {"station_id": "A", "destination_id": "QA", "priority": 2}


def test_event_name_is_top_level_field(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))

    logger = module.get_logger("tests.event")
    logger.info("Event emitted", extra={"event_name": "task.dispatched", "station_id": "A"})

    payload = parse_last_json_line(capsys)

    assert payload["event_name"] == "task.dispatched"
    assert "event_name" not in payload["extra"]


def test_exception_logging_includes_exception_object(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))

    logger = module.get_logger("tests.exception")
    try:
        raise RuntimeError("quadruped stop")
    except RuntimeError:
        logger.exception("Navigation failed", extra={"event_name": "navigation.failed"})

    payload = parse_last_json_line(capsys)

    assert payload["event_name"] == "navigation.failed"
    assert payload["extra"]["exception"]["type"] == "RuntimeError"
    assert payload["extra"]["exception"]["message"] == "quadruped stop"
    assert "RuntimeError: quadruped stop" in payload["extra"]["exception"]["traceback"]


def test_file_logging_creates_log_file_when_enabled(tmp_path: Path) -> None:
    module = load_logger_module()
    config = make_config(tmp_path, rotating_file_enabled=True)
    module.setup_logging(config)

    logger = module.get_logger("tests.file")
    logger.info("File handler active", extra={"event_name": "logger.file"})
    flush_owned_handlers()

    log_file = Path(config.logging.log_dir) / "app.log"

    assert log_file.exists()
    payload = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert payload["message"] == "File handler active"


def test_plain_text_logging_when_json_disabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path, json_output=False))
    module.set_runtime_context(task_id="task-7", quadruped_state="idle")

    logger = module.get_logger("tests.plain")
    logger.info("Readable output")

    output = capsys.readouterr().out.strip()

    assert "[INFO]" in output
    assert "[tests.plain]" in output
    assert "Readable output" in output
    assert "task_id=task-7" in output
    assert "quadruped_state=idle" in output


def test_secret_like_extra_fields_are_masked(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    module.setup_logging(make_config(tmp_path))

    logger = module.get_logger("tests.masking")
    logger.info(
        "Secrets hidden",
        extra={
            "event_name": "auth.checked",
            "supervisor_token": "raw-token",
            "smtp_password": "hunter2",
            "nested": {"api_key": "abc123", "station_id": "A"},
        },
    )

    payload = parse_last_json_line(capsys)

    assert payload["extra"]["supervisor_token"] == "***MASKED***"
    assert payload["extra"]["smtp_password"] == "***MASKED***"
    assert payload["extra"]["nested"]["api_key"] == "***MASKED***"
    assert payload["extra"]["nested"]["station_id"] == "A"


def test_invalid_log_level_defaults_to_info(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = load_logger_module()
    config = make_config(tmp_path)
    config.logging.level = "NOT-A-LEVEL"
    module.setup_logging(config)

    logger = module.get_logger("tests.level")
    logger.debug("debug should not appear")
    logger.info("info should appear")

    output = capsys.readouterr().out.strip().splitlines()

    assert len(output) == 1
    payload = json.loads(output[0])
    assert payload["message"] == "info should appear"

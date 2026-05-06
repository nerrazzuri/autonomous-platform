from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity
from shared.diagnostics.store import DiagnosticEventStore


class FailingStore:
    def add(self, event: DiagnosticEvent) -> DiagnosticEvent:
        raise RuntimeError("store failed")


class FailingLogger:
    def error(self, *args, **kwargs) -> None:
        raise RuntimeError("log failed")


def read_jsonl(path: Path) -> list[dict[str, object]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture(autouse=True)
def shutdown_router():
    from shared.diagnostics.logging_router import shutdown_diagnostics_logging

    shutdown_diagnostics_logging()
    yield
    shutdown_diagnostics_logging()


def test_reporter_module_has_no_apps_dependency() -> None:
    reporter_path = Path(__file__).resolve().parents[1] / "shared" / "diagnostics" / "reporter.py"
    content = reporter_path.read_text(encoding="utf-8")

    assert "from apps" not in content
    assert "import apps" not in content


def test_reporter_publishes_shared_platform_event() -> None:
    from shared.diagnostics import error_codes
    from shared.diagnostics.reporter import DiagnosticReporter

    store = DiagnosticEventStore()
    reporter = DiagnosticReporter(store=store, default_module="sdk_adapter", default_source="unit-test")

    event = reporter.error(
        event="sdk.connect_failed",
        message="Failed to connect to quadruped SDK.",
        error_code=error_codes.SDK_CONNECT_FAILED,
        robot_id="robot_01",
        details={"robot_ip": "192.168.1.10"},
    )

    assert event is not None
    assert event.severity is DiagnosticSeverity.ERROR
    assert event.module == "sdk_adapter"
    assert event.source == "unit-test"
    assert event.error_code == error_codes.SDK_CONNECT_FAILED
    assert event.suggested_action == error_codes.get_suggested_action(error_codes.SDK_CONNECT_FAILED)
    assert store.recent() == [event]


def test_reporter_accepts_opaque_app_error_code_without_app_import() -> None:
    from shared.diagnostics.reporter import DiagnosticReporter

    store = DiagnosticEventStore()
    reporter = DiagnosticReporter(store=store, default_module="custom_app_module")

    event = reporter.warning(
        event="custom.workflow.warning",
        message="Custom app workflow warning.",
        error_code="custom.workflow_warning",
        suggested_action="Inspect the app-owned workflow state.",
    )

    assert event is not None
    assert event.error_code == "custom.workflow_warning"
    assert event.suggested_action == "Inspect the app-owned workflow state."
    assert store.recent() == [event]


def test_reporter_logs_to_diagnostics_logging_router(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger
    from shared.diagnostics.reporter import DiagnosticReporter

    configure_diagnostics_logging(log_dir=tmp_path)
    store = DiagnosticEventStore()
    reporter = DiagnosticReporter(
        store=store,
        logger=get_diagnostic_logger("navigation"),
        default_module="navigation",
    )

    event = reporter.info(
        event="navigation.started",
        message="Navigation started.",
        robot_id="robot_01",
        task_id="task-1",
        route_id="route-1",
        correlation_id="corr-1",
        details={"speed_limit_mps": 0.2},
    )

    assert event is not None
    app_record = read_jsonl(tmp_path / "app.jsonl")[0]
    module_record = read_jsonl(tmp_path / "modules" / "navigation.jsonl")[0]
    assert app_record == module_record
    assert app_record["event"] == "navigation.started"
    assert app_record["message"] == "Navigation started."
    assert app_record["robot_id"] == "robot_01"
    assert app_record["task_id"] == "task-1"
    assert app_record["route_id"] == "route-1"
    assert app_record["correlation_id"] == "corr-1"
    assert app_record["details"] == {"speed_limit_mps": 0.2}


def test_reporter_redacts_secrets_in_details(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger
    from shared.diagnostics.reporter import DiagnosticReporter

    configure_diagnostics_logging(log_dir=tmp_path)
    reporter = DiagnosticReporter(
        store=DiagnosticEventStore(),
        logger=get_diagnostic_logger("sdk_adapter"),
        default_module="sdk_adapter",
    )

    event = reporter.error(
        event="sdk.connect_failed",
        message="Failed to connect.",
        details={"token": "secret-token", "nested": {"password": "secret-password"}},
    )

    assert event is not None
    assert event.details["token"] == "[REDACTED]"
    assert event.details["nested"]["password"] == "[REDACTED]"
    text = (tmp_path / "modules" / "sdk_adapter.jsonl").read_text(encoding="utf-8")
    assert "secret-token" not in text
    assert "secret-password" not in text


def test_reporter_returns_none_when_store_fails_by_default() -> None:
    from shared.diagnostics.reporter import DiagnosticReporter

    reporter = DiagnosticReporter(store=FailingStore(), default_module="sdk_adapter")

    assert reporter.error(event="sdk.failed", message="SDK failed.") is None


def test_reporter_returns_none_when_logging_fails_by_default() -> None:
    from shared.diagnostics.reporter import DiagnosticReporter

    reporter = DiagnosticReporter(
        store=DiagnosticEventStore(),
        logger=FailingLogger(),
        default_module="sdk_adapter",
    )

    assert reporter.error(event="sdk.failed", message="SDK failed.") is None


def test_reporter_returns_none_when_event_creation_fails_by_default() -> None:
    from shared.diagnostics.reporter import DiagnosticReporter

    reporter = DiagnosticReporter(store=DiagnosticEventStore(), default_module="sdk_adapter")

    assert reporter.error(event="", message="SDK failed.") is None


def test_reporter_raises_when_raise_on_error_enabled() -> None:
    from shared.diagnostics.reporter import DiagnosticReporter

    reporter = DiagnosticReporter(store=FailingStore(), default_module="sdk_adapter", raise_on_error=True)

    with pytest.raises(RuntimeError, match="store failed"):
        reporter.error(event="sdk.failed", message="SDK failed.")


def test_convenience_methods_map_to_severities() -> None:
    from shared.diagnostics.reporter import DiagnosticReporter

    store = DiagnosticEventStore()
    reporter = DiagnosticReporter(store=store, default_module="system_health")

    events = [
        reporter.debug(event="health.debug", message="debug"),
        reporter.info(event="health.info", message="info"),
        reporter.warning(event="health.warning", message="warning"),
        reporter.error(event="health.error", message="error"),
        reporter.critical(event="health.critical", message="critical"),
    ]

    assert [event.severity for event in events if event is not None] == [
        DiagnosticSeverity.DEBUG,
        DiagnosticSeverity.INFO,
        DiagnosticSeverity.WARNING,
        DiagnosticSeverity.ERROR,
        DiagnosticSeverity.CRITICAL,
    ]


def test_repeated_reporter_creation_does_not_duplicate_router_handlers(tmp_path: Path) -> None:
    from shared.diagnostics.logging_router import configure_diagnostics_logging
    from shared.diagnostics.reporter import get_diagnostic_reporter

    configure_diagnostics_logging(log_dir=tmp_path)
    diagnostics_logger = logging.getLogger("diagnostics")
    initial_handler_count = len(diagnostics_logger.handlers)

    get_diagnostic_reporter("sdk_adapter").info(event="sdk.one", message="one")
    get_diagnostic_reporter("sdk_adapter").info(event="sdk.two", message="two")

    assert len(diagnostics_logger.handlers) == initial_handler_count
    assert len(read_jsonl(tmp_path / "modules" / "sdk_adapter.jsonl")) == 2


def test_module_level_reset_replaces_reporter_store() -> None:
    from shared.diagnostics.reporter import get_diagnostic_reporter, reset_diagnostic_reporter

    first_store = DiagnosticEventStore()
    second_store = DiagnosticEventStore()
    first = reset_diagnostic_reporter(module="sdk_adapter", store=first_store, logger=None)
    second = reset_diagnostic_reporter(module="sdk_adapter", store=second_store, logger=None)

    assert first is not second
    assert get_diagnostic_reporter("sdk_adapter") is second

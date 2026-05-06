"""Generic diagnostic event reporter infrastructure."""

from __future__ import annotations

from collections.abc import Mapping
import logging
from threading import RLock
from typing import Any

from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity
from shared.diagnostics.logging_router import get_diagnostic_logger
from shared.diagnostics.store import DiagnosticEventStore, get_diagnostic_store


class DiagnosticReporter:
    """Create, store, and optionally log diagnostic events."""

    def __init__(
        self,
        *,
        store: DiagnosticEventStore | None = None,
        logger: logging.LoggerAdapter | None = None,
        default_module: str | None = None,
        default_source: str | None = None,
        raise_on_error: bool = False,
    ) -> None:
        self._store = store if store is not None else get_diagnostic_store()
        self._logger = logger
        self._default_module = default_module
        self._default_source = default_source
        self._raise_on_error = raise_on_error

    def report(
        self,
        *,
        severity: DiagnosticSeverity | str,
        module: str | None = None,
        event: str,
        message: str,
        error_code: str | None = None,
        subsystem: str | None = None,
        robot_id: str | None = None,
        context: Mapping[str, Any] | None = None,
        # Deprecated compatibility fields. Prefer context for app/domain IDs.
        task_id: str | None = None,
        route_id: str | None = None,
        station_id: str | None = None,
        waypoint_id: str | None = None,
        correlation_id: str | None = None,
        source: str | None = None,
        details: Mapping[str, Any] | None = None,
        suggested_action: str | None = None,
    ) -> DiagnosticEvent | None:
        try:
            diagnostic_event = DiagnosticEvent.create(
                severity=severity,
                module=module or self._default_module or "diagnostics",
                event=event,
                message=message,
                error_code=error_code,
                subsystem=subsystem,
                robot_id=robot_id,
                context=context,
                task_id=task_id,
                route_id=route_id,
                station_id=station_id,
                waypoint_id=waypoint_id,
                correlation_id=correlation_id,
                source=source or self._default_source,
                details=details,
                suggested_action=suggested_action,
            )
            self._store.add(diagnostic_event)
            self._log_event(diagnostic_event)
            return diagnostic_event
        except Exception:
            if self._raise_on_error:
                raise
            return None

    def debug(self, **kwargs: Any) -> DiagnosticEvent | None:
        return self.report(severity=DiagnosticSeverity.DEBUG, **kwargs)

    def info(self, **kwargs: Any) -> DiagnosticEvent | None:
        return self.report(severity=DiagnosticSeverity.INFO, **kwargs)

    def warning(self, **kwargs: Any) -> DiagnosticEvent | None:
        return self.report(severity=DiagnosticSeverity.WARNING, **kwargs)

    def error(self, **kwargs: Any) -> DiagnosticEvent | None:
        return self.report(severity=DiagnosticSeverity.ERROR, **kwargs)

    def critical(self, **kwargs: Any) -> DiagnosticEvent | None:
        return self.report(severity=DiagnosticSeverity.CRITICAL, **kwargs)

    def _log_event(self, event: DiagnosticEvent) -> None:
        if self._logger is None:
            return

        log_method = getattr(self._logger, event.severity.value)
        log_method(
            event.message,
            extra={
                "event": event.event,
                "robot_id": event.robot_id,
                "context": event.context,
                "task_id": event.task_id,
                "route_id": event.route_id,
                "error_code": event.error_code,
                "correlation_id": event.correlation_id,
                "details": event.details,
            },
        )


_REPORTERS: dict[str, DiagnosticReporter] = {}
_REPORTER_LOCK = RLock()


def get_diagnostic_reporter(module: str | None = None) -> DiagnosticReporter:
    """Return a cached reporter for a module."""

    key = _reporter_key(module)
    with _REPORTER_LOCK:
        reporter = _REPORTERS.get(key)
        if reporter is None:
            reporter = DiagnosticReporter(
                default_module=key,
                logger=get_diagnostic_logger(key),
            )
            _REPORTERS[key] = reporter
        return reporter


def reset_diagnostic_reporter(
    *,
    module: str | None = None,
    store: DiagnosticEventStore | None = None,
    logger: logging.LoggerAdapter | None = None,
    default_source: str | None = None,
    raise_on_error: bool = False,
) -> DiagnosticReporter:
    """Replace and return the cached reporter for a module."""

    key = _reporter_key(module)
    with _REPORTER_LOCK:
        reporter = DiagnosticReporter(
            store=store,
            logger=logger,
            default_module=key,
            default_source=default_source,
            raise_on_error=raise_on_error,
        )
        _REPORTERS[key] = reporter
        return reporter


def _reporter_key(module: str | None) -> str:
    normalized = str(module).strip() if module is not None else ""
    return normalized or "diagnostics"


__all__ = [
    "DiagnosticReporter",
    "get_diagnostic_reporter",
    "reset_diagnostic_reporter",
]

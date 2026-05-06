"""Structured diagnostics logging router with module-specific JSONL files."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
from threading import RLock
from typing import Any

from shared.diagnostics.redaction import redact_mapping


DEFAULT_LOG_DIR = "logs"
DEFAULT_MAX_BYTES = 10 * 1024 * 1024
DEFAULT_BACKUP_COUNT = 5
DIAGNOSTICS_LOGGER_NAME = "diagnostics"
_UNKNOWN_MODULE = "unknown"
_STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}
_OWNED_HANDLER_ATTR = "_platform_diagnostics_logging_handler"
_LOCK = RLock()
_CONFIGURED = False


def sanitize_module_name(module: str | None) -> str:
    """Return a filesystem-safe module log stem."""

    if module is None:
        return _UNKNOWN_MODULE
    normalized = str(module).strip().lower()
    if not normalized:
        return _UNKNOWN_MODULE
    normalized = normalized.replace("\\", "_").replace("/", "_")
    normalized = re.sub(r"[^a-z0-9_.-]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")
    if not normalized or normalized in {".", ".."}:
        return _UNKNOWN_MODULE
    return normalized


class DiagnosticLoggerAdapter(logging.LoggerAdapter):
    """Logger adapter that preserves caller extras while adding a diagnostic module."""

    def process(self, msg: Any, kwargs: dict[str, Any]) -> tuple[Any, dict[str, Any]]:
        merged_extra = dict(kwargs.get("extra") or {})
        merged_extra["diagnostic_module"] = self.extra["diagnostic_module"]
        kwargs["extra"] = merged_extra
        return msg, kwargs


class DiagnosticJSONFormatter(logging.Formatter):
    """Format diagnostics log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(self.build_payload(record), default=repr, sort_keys=True)

    def build_payload(self, record: logging.LogRecord) -> dict[str, Any]:
        details = self._extract_details(record)
        return {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "module": _record_module(record),
            "event": getattr(record, "event", None),
            "message": record.getMessage(),
            "robot_id": getattr(record, "robot_id", None),
            "context": self._extract_context(record),
            "task_id": getattr(record, "task_id", None),
            "route_id": getattr(record, "route_id", None),
            "error_code": getattr(record, "error_code", None),
            "correlation_id": getattr(record, "correlation_id", None),
            "details": details,
        }

    def _extract_context(self, record: logging.LogRecord) -> dict[str, Any]:
        raw_context = getattr(record, "context", None)
        if isinstance(raw_context, Mapping):
            return redact_mapping(raw_context)
        if raw_context is not None:
            return {"context": repr(raw_context)}
        return {}

    def _extract_details(self, record: logging.LogRecord) -> dict[str, Any]:
        details: dict[str, Any] = {}
        raw_details = getattr(record, "details", None)
        if isinstance(raw_details, Mapping):
            details.update(raw_details)
        elif raw_details is not None:
            details["details"] = raw_details

        excluded = {
            "diagnostic_module",
            "event",
            "robot_id",
            "context",
            "task_id",
            "route_id",
            "error_code",
            "correlation_id",
            "details",
        }
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS or key in excluded or key.startswith("_"):
                continue
            details[key] = value
        return redact_mapping(details)


class DiagnosticPlainFormatter(logging.Formatter):
    """Human-readable formatter for the master diagnostics log."""

    def format(self, record: logging.LogRecord) -> str:
        payload = DiagnosticJSONFormatter().build_payload(record)
        context = " ".join(
            f"{key}={payload[key]}"
            for key in ("event", "robot_id", "task_id", "route_id", "error_code", "correlation_id")
            if payload.get(key) is not None
        )
        prefix = f"[{payload['ts']}] [{payload['level']}] [{payload['module']}]"
        message = f"{prefix} {payload['message']}"
        if context:
            message = f"{message} {context}"
        if payload["details"]:
            message = f"{message} details={json.dumps(payload['details'], default=repr, sort_keys=True)}"
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return message


class ModuleJSONLRouterHandler(logging.Handler):
    """Route each diagnostics record to its module-specific JSONL file."""

    def __init__(self, module_dir: Path, *, max_bytes: int, backup_count: int) -> None:
        super().__init__()
        self.module_dir = module_dir
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self._handlers: dict[str, RotatingFileHandler] = {}
        self._lock = RLock()
        self.setFormatter(DiagnosticJSONFormatter())

    def emit(self, record: logging.LogRecord) -> None:
        try:
            handler = self._handler_for(_record_module(record))
            handler.emit(record)
        except Exception:
            self.handleError(record)

    def flush(self) -> None:
        with self._lock:
            for handler in self._handlers.values():
                handler.flush()

    def close(self) -> None:
        with self._lock:
            for handler in self._handlers.values():
                handler.close()
            self._handlers.clear()
        super().close()

    def _handler_for(self, module: str) -> RotatingFileHandler:
        safe_module = sanitize_module_name(module)
        with self._lock:
            existing = self._handlers.get(safe_module)
            if existing is not None:
                return existing
            self.module_dir.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(
                self.module_dir / f"{safe_module}.jsonl",
                maxBytes=self.max_bytes,
                backupCount=self.backup_count,
                encoding="utf-8",
            )
            handler.setFormatter(DiagnosticJSONFormatter())
            _mark_owned(handler)
            self._handlers[safe_module] = handler
            return handler


def configure_diagnostics_logging(
    *,
    log_dir: str | Path = DEFAULT_LOG_DIR,
    level: int | str = logging.INFO,
    max_bytes: int = DEFAULT_MAX_BYTES,
    backup_count: int = DEFAULT_BACKUP_COUNT,
) -> None:
    """Configure master and module diagnostics log files."""

    if max_bytes <= 0:
        raise ValueError("max_bytes must be positive")
    if backup_count < 0:
        raise ValueError("backup_count must be non-negative")

    resolved_level = _resolve_level(level)
    base_dir = Path(log_dir)
    module_dir = base_dir / "modules"
    base_dir.mkdir(parents=True, exist_ok=True)
    module_dir.mkdir(parents=True, exist_ok=True)

    with _LOCK:
        shutdown_diagnostics_logging()
        diagnostics_logger = logging.getLogger(DIAGNOSTICS_LOGGER_NAME)
        diagnostics_logger.setLevel(resolved_level)
        diagnostics_logger.propagate = False
        diagnostics_logger.disabled = False

        plain_handler = RotatingFileHandler(
            base_dir / "app.log",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        plain_handler.setFormatter(DiagnosticPlainFormatter())
        plain_handler.setLevel(resolved_level)
        diagnostics_logger.addHandler(_mark_owned(plain_handler))

        json_handler = RotatingFileHandler(
            base_dir / "app.jsonl",
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
        )
        json_handler.setFormatter(DiagnosticJSONFormatter())
        json_handler.setLevel(resolved_level)
        diagnostics_logger.addHandler(_mark_owned(json_handler))

        module_handler = ModuleJSONLRouterHandler(module_dir, max_bytes=max_bytes, backup_count=backup_count)
        module_handler.setLevel(resolved_level)
        diagnostics_logger.addHandler(_mark_owned(module_handler))

        global _CONFIGURED
        _CONFIGURED = True


def get_diagnostic_logger(module: str) -> DiagnosticLoggerAdapter:
    """Return a diagnostics logger adapter for a logical module."""

    if not _CONFIGURED:
        configure_diagnostics_logging()
    safe_module = sanitize_module_name(module)
    logger = logging.getLogger(f"{DIAGNOSTICS_LOGGER_NAME}.{safe_module}")
    logger.disabled = False
    logger.propagate = True
    return DiagnosticLoggerAdapter(logger, {"diagnostic_module": safe_module})


def shutdown_diagnostics_logging() -> None:
    """Close and remove all diagnostics logging handlers."""

    with _LOCK:
        diagnostics_logger = logging.getLogger(DIAGNOSTICS_LOGGER_NAME)
        for handler in list(diagnostics_logger.handlers):
            if getattr(handler, _OWNED_HANDLER_ATTR, False):
                diagnostics_logger.removeHandler(handler)
                handler.close()
        global _CONFIGURED
        _CONFIGURED = False


def _record_module(record: logging.LogRecord) -> str:
    return sanitize_module_name(getattr(record, "diagnostic_module", None) or record.name)


def _resolve_level(level: int | str) -> int:
    if isinstance(level, int):
        return level
    resolved = getattr(logging, str(level).upper(), None)
    if isinstance(resolved, int):
        return resolved
    raise ValueError(f"Unknown logging level: {level}")


def _mark_owned(handler: logging.Handler) -> logging.Handler:
    setattr(handler, _OWNED_HANDLER_ATTR, True)
    return handler


__all__ = [
    "DEFAULT_BACKUP_COUNT",
    "DEFAULT_LOG_DIR",
    "DEFAULT_MAX_BYTES",
    "DiagnosticJSONFormatter",
    "DiagnosticLoggerAdapter",
    "DiagnosticPlainFormatter",
    "ModuleJSONLRouterHandler",
    "configure_diagnostics_logging",
    "get_diagnostic_logger",
    "sanitize_module_name",
    "shutdown_diagnostics_logging",
]

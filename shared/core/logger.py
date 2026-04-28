from __future__ import annotations

"""Centralized structured logging for the quadruped logistics application."""

import json
import logging
import sys
import traceback
from contextvars import ContextVar
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from shared.core.config import AppConfig, get_config


MASKED_VALUE = "***MASKED***"
_SECRET_KEY_PARTS = ("token", "password", "secret", "api_key", "authorization", "bearer", "key")
_STANDARD_LOG_RECORD_FIELDS = set(logging.makeLogRecord({}).__dict__.keys()) | {"message", "asctime"}
_RUNTIME_CONTEXT: ContextVar[dict[str, Any]] = ContextVar(
    "sumitomo_runtime_context",
    default={"task_id": None, "quadruped_state": None},
)
_LOGGING_CONFIGURED = False


def sanitize_log_value(value: Any) -> Any:
    """Sanitize nested log values without mutating the original object."""

    return _sanitize_log_value(value)


def redact_sensitive(value: Any) -> Any:
    """Redact sensitive payload keys and obvious bearer-style strings."""

    return _sanitize_log_value(value)


def _sanitize_log_value(value: Any, *, key: str | None = None) -> Any:
    if key and any(part in key.lower() for part in _SECRET_KEY_PARTS):
        return MASKED_VALUE
    if isinstance(value, str):
        lowered = value.lower()
        if lowered.startswith("bearer ") or lowered == "bearer":
            return MASKED_VALUE
        return value
    if isinstance(value, dict):
        return {item_key: _sanitize_log_value(item_value, key=str(item_key)) for item_key, item_value in value.items()}
    if isinstance(value, list):
        return [_sanitize_log_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_log_value(item) for item in value)
    if isinstance(value, set):
        return {_sanitize_log_value(item) for item in value}
    return value


def _default_runtime_context() -> dict[str, Any]:
    return {"task_id": None, "quadruped_state": None}


def set_runtime_context(task_id: Any = None, quadruped_state: Any = None, **kwargs: Any) -> None:
    """Set runtime log context for the current execution context."""

    updated_context = dict(_RUNTIME_CONTEXT.get())
    updated_context["task_id"] = task_id
    updated_context["quadruped_state"] = quadruped_state
    updated_context.update(kwargs)
    _RUNTIME_CONTEXT.set(updated_context)


def clear_runtime_context() -> None:
    """Reset runtime log context values for the current execution context."""

    _RUNTIME_CONTEXT.set(_default_runtime_context())


def _get_runtime_context() -> dict[str, Any]:
    context = dict(_RUNTIME_CONTEXT.get())
    context.setdefault("task_id", None)
    context.setdefault("quadruped_state", None)
    return context


def _resolve_log_level(level_name: str | int | None) -> int:
    if isinstance(level_name, int):
        return level_name
    if isinstance(level_name, str):
        resolved = getattr(logging, level_name.upper(), None)
        if isinstance(resolved, int):
            return resolved
    return logging.INFO


def _remove_owned_handlers(root_logger: logging.Logger) -> None:
    for handler in list(root_logger.handlers):
        if getattr(handler, "_sumitomo_logger_handler", False):
            root_logger.removeHandler(handler)
            handler.close()


def _mark_owned(handler: logging.Handler) -> logging.Handler:
    handler._sumitomo_logger_handler = True  # type: ignore[attr-defined]
    return handler


def _build_formatter(json_output: bool) -> logging.Formatter:
    return JsonLogFormatter() if json_output else PlainLogFormatter()


def setup_logging(config: AppConfig | None = None) -> None:
    """Configure application logging using MOD-00 config and owned handlers only."""

    global _LOGGING_CONFIGURED

    app_config = config or get_config()
    root_logger = logging.getLogger()
    level = _resolve_log_level(app_config.logging.level)

    _remove_owned_handlers(root_logger)

    formatter = _build_formatter(app_config.logging.json_output)
    stdout_handler = _mark_owned(logging.StreamHandler(sys.stdout))
    stdout_handler.setLevel(level)
    stdout_handler.setFormatter(formatter)
    root_logger.addHandler(stdout_handler)

    if app_config.logging.rotating_file_enabled:
        log_dir = Path(app_config.logging.log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = _mark_owned(
            RotatingFileHandler(
                log_dir / "app.log",
                maxBytes=app_config.logging.max_file_mb * 1024 * 1024,
                backupCount=app_config.logging.backup_count,
                encoding="utf-8",
            )
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

    root_logger.setLevel(level)
    _LOGGING_CONFIGURED = True


def get_logger(module_name: str) -> logging.Logger:
    """Return a logger configured by the shared application logging setup."""

    if not _LOGGING_CONFIGURED:
        setup_logging()
    return logging.getLogger(module_name)


class JsonLogFormatter(logging.Formatter):
    """Format quadruped log records as one JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        runtime_context = _get_runtime_context()
        task_id = sanitize_log_value(runtime_context.get("task_id"))
        quadruped_state = sanitize_log_value(runtime_context.get("quadruped_state"))
        extra_fields = self._extract_extra_fields(record)

        runtime_extra_context = {
            key: sanitize_log_value(value)
            for key, value in runtime_context.items()
            if key not in {"task_id", "quadruped_state"} and value is not None
        }
        if runtime_extra_context:
            extra_fields["runtime_context"] = runtime_extra_context

        if record.exc_info:
            exc_type, exc_value, exc_traceback = record.exc_info
            extra_fields["exception"] = {
                "type": exc_type.__name__ if exc_type else None,
                "message": str(exc_value) if exc_value else "",
                "traceback": "".join(traceback.format_exception(exc_type, exc_value, exc_traceback)),
            }

        payload = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().isoformat(),
            "module": record.name,
            "severity": record.levelname,
            "message": record.getMessage(),
            "task_id": task_id,
            "quadruped_state": quadruped_state,
            "event_name": getattr(record, "event_name", None),
            "extra": extra_fields,
        }
        return json.dumps(payload, default=str)

    def _extract_extra_fields(self, record: logging.LogRecord) -> dict[str, Any]:
        extra_fields: dict[str, Any] = {}
        for key, value in record.__dict__.items():
            if key in _STANDARD_LOG_RECORD_FIELDS:
                continue
            if key in {"event_name", "task_id", "quadruped_state"}:
                continue
            if key.startswith("_sumitomo_"):
                continue
            extra_fields[key] = _sanitize_log_value(value, key=key)
        return extra_fields


class PlainLogFormatter(logging.Formatter):
    """Readable plain-text formatter for quadruped logs."""

    def format(self, record: logging.LogRecord) -> str:
        runtime_context = _get_runtime_context()
        timestamp = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone().isoformat()
        message = f"[{timestamp}] [{record.levelname}] [{record.name}] {record.getMessage()}"

        task_id = sanitize_log_value(runtime_context.get("task_id"))
        quadruped_state = sanitize_log_value(runtime_context.get("quadruped_state"))
        message = f"{message} task_id={task_id} quadruped_state={quadruped_state}"

        event_name = getattr(record, "event_name", None)
        if event_name is not None:
            message = f"{message} event_name={event_name}"

        extra_fields = JsonLogFormatter()._extract_extra_fields(record)
        for key, value in extra_fields.items():
            message = f"{message} {key}={value}"

        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"
        return message


__all__ = [
    "JsonLogFormatter",
    "clear_runtime_context",
    "get_logger",
    "redact_sensitive",
    "sanitize_log_value",
    "set_runtime_context",
    "setup_logging",
]

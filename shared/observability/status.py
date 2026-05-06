from __future__ import annotations

"""App-agnostic platform status summary and extension registry."""

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
import inspect
import re
import threading
import time
from typing import Any

from shared.core.config import get_config
from shared.core.logger import get_logger
from shared.diagnostics import DiagnosticSeverity, get_diagnostic_store
from shared.diagnostics.redaction import redact_mapping
from shared.observability.alerts import AlertRouter, get_alert_router
from shared.observability.health import _safe_get_current_state
from shared.quadruped.robot_registry import get_robot_registry


logger = get_logger(__name__)

StatusProvider = Callable[[], Mapping[str, Any]]

_PROVIDER_NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")
_PROVIDER_LOCK = threading.RLock()
_STATUS_PROVIDERS: dict[str, StatusProvider] = {}
_START_TIME = time.monotonic()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_provider_name(name: object) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("status provider name must be a non-empty string")
    normalized = name.strip()
    if not _PROVIDER_NAME_PATTERN.fullmatch(normalized):
        raise ValueError("status provider name may only contain letters, numbers, dot, underscore, and dash")
    return normalized


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, set):
        return sorted(_json_safe(item) for item in value)
    return repr(value)


def _safe_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    redacted = redact_mapping(value)
    return _json_safe(redacted)


def register_status_provider(name: str, provider: StatusProvider) -> None:
    """Register or replace a status extension provider by name."""

    normalized_name = _require_provider_name(name)
    if not callable(provider):
        raise ValueError("status provider must be callable")
    with _PROVIDER_LOCK:
        _STATUS_PROVIDERS[normalized_name] = provider


def unregister_status_provider(name: str) -> None:
    normalized_name = _require_provider_name(name)
    with _PROVIDER_LOCK:
        _STATUS_PROVIDERS.pop(normalized_name, None)


def clear_status_providers() -> None:
    with _PROVIDER_LOCK:
        _STATUS_PROVIDERS.clear()


def get_registered_status_providers() -> dict[str, StatusProvider]:
    with _PROVIDER_LOCK:
        return dict(_STATUS_PROVIDERS)


def _battery_state(percent: object, *, warn_pct: int, critical_pct: int) -> str:
    if not isinstance(percent, int) or isinstance(percent, bool):
        return "unknown"
    if percent <= critical_pct:
        return "critical"
    if percent <= warn_pct:
        return "low"
    return "ok"


def _robot_status(
    *,
    connected: bool | None,
    battery_state: str,
    heartbeat_ok: bool | None,
    state_available: bool,
) -> str:
    if not state_available:
        return "unknown"
    if connected is False or battery_state == "critical":
        return "error"
    if connected is None or battery_state in {"low", "unknown"} or heartbeat_ok is False:
        return "degraded"
    return "ok"


async def _robot_summaries() -> dict[str, dict[str, Any]]:
    config = get_config()
    robots: dict[str, dict[str, Any]] = {}
    for platform in get_robot_registry().all():
        robot_id = str(getattr(platform, "robot_id", "") or "")
        if not robot_id:
            continue
        state = await _safe_get_current_state(getattr(platform, "state_monitor", None))
        connected = None if state is None else getattr(state, "connection_ok", None)
        battery_pct = None if state is None else getattr(state, "battery_pct", None)
        battery_state = _battery_state(
            battery_pct,
            warn_pct=config.battery.warn_pct,
            critical_pct=config.battery.critical_pct,
        )
        heartbeat = getattr(platform, "heartbeat", None)
        last_send_ok = heartbeat.last_send_ok() if hasattr(heartbeat, "last_send_ok") else None
        heartbeat_running = heartbeat.is_running() if hasattr(heartbeat, "is_running") else None
        heartbeat_error = heartbeat.last_error() if hasattr(heartbeat, "last_error") else None
        timestamp = None if state is None else getattr(state, "timestamp", None)
        last_telemetry_ts = timestamp.isoformat() if hasattr(timestamp, "isoformat") else None
        robot_status = _robot_status(
            connected=connected,
            battery_state=battery_state,
            heartbeat_ok=last_send_ok,
            state_available=state is not None,
        )
        robots[robot_id] = _safe_mapping(
            {
                "status": robot_status,
                "connected": connected,
                "estop": None,
                "battery": {
                    "percent": battery_pct,
                    "state": battery_state,
                },
                "last_telemetry_ts": last_telemetry_ts,
                "details": {
                    "heartbeat_running": heartbeat_running,
                    "heartbeat_last_send_ok": last_send_ok,
                    "heartbeat_last_error": heartbeat_error,
                },
            }
        )
    return robots


def _diagnostics_summary() -> dict[str, Any]:
    store = get_diagnostic_store()
    errors = store.errors(limit=100)
    critical = [event for event in errors if event.severity == DiagnosticSeverity.CRITICAL]
    latest_error = errors[0] if errors else None
    compact_latest = None
    if latest_error is not None:
        compact_latest = _safe_mapping(
            {
                "event_id": latest_error.event_id,
                "ts": latest_error.ts,
                "severity": latest_error.severity.value,
                "module": latest_error.module,
                "event": latest_error.event,
                "message": latest_error.message,
                "error_code": latest_error.error_code,
                "robot_id": latest_error.robot_id,
                "correlation_id": latest_error.correlation_id,
                "context": latest_error.context,
                "details": latest_error.details,
                "suggested_action": latest_error.suggested_action,
            }
        )
    return {
        "recent_count": store.count(),
        "error_count": len(errors),
        "critical_count": len(critical),
        "latest_error": compact_latest,
    }


def _alerts_summary(alert_router: AlertRouter | None = None) -> dict[str, Any]:
    try:
        router = alert_router or get_alert_router()
        recent_alerts = router.list_alerts(limit=100)
    except Exception:
        logger.exception("Status summary failed to read alert router")
        return {
            "active_count": 0,
            "latest": None,
            "status": "degraded",
        }

    active_alerts = [alert for alert in recent_alerts if not alert.acknowledged]
    latest = recent_alerts[0] if recent_alerts else None
    compact_latest = None
    if latest is not None:
        compact_latest = _safe_mapping(
            {
                "alert_id": latest.alert_id,
                "timestamp": latest.timestamp,
                "severity": latest.severity,
                "source": latest.source,
                "event_type": latest.event_type,
                "message": latest.message,
                "robot_id": latest.robot_id,
                "acknowledged": latest.acknowledged,
                "metadata": latest.metadata,
            }
        )
    return {
        "active_count": len(active_alerts),
        "latest": compact_latest,
    }


async def _call_provider(provider: StatusProvider) -> Mapping[str, Any]:
    result = provider()
    if inspect.isawaitable(result):
        result = await result
    if not isinstance(result, Mapping):
        raise ValueError("status provider must return a mapping")
    return result


async def _extension_summaries() -> tuple[dict[str, Any], bool]:
    with _PROVIDER_LOCK:
        providers = dict(_STATUS_PROVIDERS)

    extensions: dict[str, Any] = {}
    failed = False
    for name, provider in providers.items():
        try:
            extensions[name] = _safe_mapping(await _call_provider(provider))
        except Exception as exc:
            failed = True
            logger.exception("Status provider failed", extra={"status_provider": name})
            extensions[name] = _safe_mapping(
                {
                    "status": "error",
                    "error": "provider_failed",
                    "message": str(exc),
                }
            )
    return extensions, failed


def _overall_status(
    *,
    robots: Mapping[str, Mapping[str, Any]],
    diagnostics: Mapping[str, Any],
    alerts: Mapping[str, Any],
    provider_failed: bool,
) -> str:
    robot_statuses = {str(item.get("status")) for item in robots.values()}
    if "error" in robot_statuses or int(diagnostics.get("critical_count") or 0) > 0:
        return "error"
    if (
        provider_failed
        or robot_statuses.intersection({"degraded", "unknown"})
        or int(diagnostics.get("error_count") or 0) > 0
        or int(alerts.get("active_count") or 0) > 0
        or alerts.get("status") == "degraded"
    ):
        return "degraded"
    return "ok"


async def build_status_summary() -> dict[str, Any]:
    """Build a redacted app-agnostic platform status summary."""

    config = get_config()
    robots = await _robot_summaries()
    diagnostics = _diagnostics_summary()
    alerts = _alerts_summary()
    extensions, provider_failed = await _extension_summaries()
    status = _overall_status(
        robots=robots,
        diagnostics=diagnostics,
        alerts=alerts,
        provider_failed=provider_failed,
    )
    return {
        "status": status,
        "ts": _utc_now_iso(),
        "platform": {
            "uptime_seconds": round(time.monotonic() - _START_TIME, 3),
            "version": None,
            "app": config.app.name,
        },
        "robots": robots,
        "diagnostics": diagnostics,
        "alerts": alerts,
        "extensions": extensions,
    }


__all__ = [
    "StatusProvider",
    "build_status_summary",
    "clear_status_providers",
    "get_registered_status_providers",
    "register_status_provider",
    "unregister_status_provider",
]

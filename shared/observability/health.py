from __future__ import annotations

from typing import Any

from shared.audit import get_audit_store
from shared.core.config import get_config
from shared.core.logger import get_logger
from shared.quadruped.robot_registry import get_robot_registry


logger = get_logger(__name__)


def _platform_role(platform: Any) -> str | None:
    role = getattr(getattr(platform, "config", None), "role", None)
    if role is None:
        role = getattr(getattr(getattr(platform, "config", None), "connection", None), "role", None)
    return role


async def _safe_get_current_state(state_monitor: Any) -> Any | None:
    if state_monitor is None:
        return None
    getter = getattr(state_monitor, "get_current_state", None)
    if not callable(getter):
        return None
    try:
        return await getter()
    except Exception:
        logger.exception("Health snapshot failed to read robot state")
        return None


def _robot_status(state: Any, critical_battery_pct: int) -> str:
    if state is None:
        return "unknown"

    battery_pct = getattr(state, "battery_pct", None)
    if isinstance(battery_pct, int) and battery_pct <= critical_battery_pct:
        return "critical"

    connected = getattr(state, "connection_ok", None)
    if connected is True:
        return "ok"
    return "degraded"


async def get_robot_health() -> list[dict[str, Any]]:
    config = get_config()
    robots: list[dict[str, Any]] = []

    for platform in get_robot_registry().all():
        state = await _safe_get_current_state(getattr(platform, "state_monitor", None))
        connected = None if state is None else getattr(state, "connection_ok", None)
        battery_pct = None if state is None else getattr(state, "battery_pct", None)
        robots.append(
            {
                "robot_id": getattr(platform, "robot_id", None),
                "role": _platform_role(platform),
                "connected": connected,
                "battery_pct": battery_pct,
                "status": _robot_status(state, config.battery.critical_pct),
            }
        )

    return robots


async def get_system_health() -> dict[str, Any]:
    robots = await get_robot_health()
    robot_count = len(robots)
    statuses = {robot["status"] for robot in robots}

    if "critical" in statuses:
        overall_status = "critical"
    elif statuses.intersection({"degraded", "unknown"}):
        overall_status = "degraded"
    else:
        overall_status = "ok"

    audit_available = True
    try:
        get_audit_store()
    except Exception:
        audit_available = False

    provisioning_available = True
    try:
        from shared.provisioning import provision_backend as _unused_backend
    except Exception:
        provisioning_available = False

    config = get_config()
    return {
        "status": overall_status,
        "service": config.app.name,
        "runtime": {
            "started": True,
            "registered_robot_count": robot_count,
        },
        "robots": robots,
        "audit": {"available": audit_available},
        "provisioning": {"available": provisioning_available},
    }

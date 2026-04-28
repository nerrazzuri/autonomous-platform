from __future__ import annotations

import json
from typing import Any

from shared.audit import get_audit_store
from shared.audit.audit_models import AuditEvent
from shared.core.logger import get_logger
from shared.observability.health import _platform_role, _safe_get_current_state
from shared.quadruped.robot_registry import get_robot_registry


logger = get_logger(__name__)


def _audit_counts() -> dict[str, int]:
    try:
        store = get_audit_store()
    except Exception:
        return {"audit_event_count": 0, "audit_error_count": 0, "audit_critical_count": 0}

    path = getattr(store, "path", None)
    if path is None or not path.exists():
        return {"audit_event_count": 0, "audit_error_count": 0, "audit_critical_count": 0}

    total = 0
    error_count = 0
    critical_count = 0
    try:
        with path.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    continue
                event = AuditEvent.from_dict(payload)
                total += 1
                if event.severity == "error":
                    error_count += 1
                if event.severity == "critical":
                    critical_count += 1
    except Exception:
        logger.exception("Metrics snapshot failed to read audit store")
        return {"audit_event_count": 0, "audit_error_count": 0, "audit_critical_count": 0}

    return {
        "audit_event_count": total,
        "audit_error_count": error_count,
        "audit_critical_count": critical_count,
    }


async def get_metrics_snapshot() -> dict[str, Any]:
    platforms = get_robot_registry().all()
    robots_by_role: dict[str, int] = {}
    connected_robot_count = 0
    disconnected_robot_count = 0

    for platform in platforms:
        role = _platform_role(platform) or "unknown"
        robots_by_role[role] = robots_by_role.get(role, 0) + 1

        state = await _safe_get_current_state(getattr(platform, "state_monitor", None))
        connected = None if state is None else getattr(state, "connection_ok", None)
        if connected is True:
            connected_robot_count += 1
        else:
            disconnected_robot_count += 1

    snapshot = {
        "registered_robot_count": len(platforms),
        "robots_by_role": robots_by_role,
        "connected_robot_count": connected_robot_count,
        "disconnected_robot_count": disconnected_robot_count,
    }
    snapshot.update(_audit_counts())
    return snapshot

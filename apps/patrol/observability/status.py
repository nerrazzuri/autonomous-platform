from __future__ import annotations

"""Patrol-owned status summary extension registration."""

from typing import Any

from shared.observability.status import register_status_provider


def _patrol_status() -> dict[str, Any]:
    return {
        "status": "unknown",
        "message": "patrol status provider registered",
    }


def register_patrol_status_provider() -> None:
    register_status_provider("patrol", _patrol_status)


__all__ = ["register_patrol_status_provider"]

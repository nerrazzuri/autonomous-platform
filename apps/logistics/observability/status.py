from __future__ import annotations

"""Logistics-owned status summary extension registration."""

from typing import Any

from shared.observability.status import register_status_provider


def _logistics_status() -> dict[str, Any]:
    return {
        "status": "unknown",
        "message": "logistics status provider registered",
    }


def register_logistics_status_provider() -> None:
    register_status_provider("logistics", _logistics_status)


__all__ = ["register_logistics_status_provider"]

from __future__ import annotations

"""Patrol-owned config helpers.

The shared AppConfig still exposes ``config.patrol`` as a deprecated
compatibility section. New patrol code should enter through this module so
future app config registry work can move the source without changing callers.
"""

from typing import Any

from pydantic import BaseModel, Field


class PatrolSection(BaseModel):
    schedule_enabled: bool = True
    patrol_interval_seconds: int = Field(default=1800, gt=0)
    observation_dwell_seconds: float = Field(default=3.0, gt=0)
    anomaly_cooldown_seconds: float = Field(default=300.0, ge=0)
    max_consecutive_failures: int = Field(default=3, gt=0)
    alert_on_anomaly: bool = True
    webhook_url: str | None = None


def get_patrol_config(config: Any) -> PatrolSection:
    """Return patrol config from the current AppConfig compatibility section."""

    existing = getattr(config, "patrol", None)
    if existing is None:
        return PatrolSection()
    if isinstance(existing, PatrolSection):
        return existing
    if hasattr(existing, "model_dump"):
        return PatrolSection.model_validate(existing.model_dump(mode="python"))
    if hasattr(existing, "__dict__"):
        return PatrolSection.model_validate(vars(existing))
    return PatrolSection.model_validate(existing)


__all__ = ["PatrolSection", "get_patrol_config"]

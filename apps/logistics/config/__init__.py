from __future__ import annotations

"""Logistics-owned config helpers.

The shared AppConfig still exposes ``config.logistics`` as a deprecated
compatibility section. New logistics code should enter through this module so
future app config registry work can move the source without changing callers.
"""

from typing import Any

from pydantic import BaseModel


class LogisticsSection(BaseModel):
    routes_file: str = "data/logistics_routes.json"
    allow_placeholder_routes: bool = True


def get_logistics_config(config: Any) -> LogisticsSection:
    """Return logistics config from the current AppConfig compatibility section."""

    existing = getattr(config, "logistics", None)
    if existing is None:
        return LogisticsSection()
    if isinstance(existing, LogisticsSection):
        return existing
    if hasattr(existing, "model_dump"):
        return LogisticsSection.model_validate(existing.model_dump(mode="python"))
    if hasattr(existing, "__dict__"):
        return LogisticsSection.model_validate(vars(existing))
    return LogisticsSection.model_validate(existing)


__all__ = ["LogisticsSection", "get_logistics_config"]

"""Logistics-specific diagnostics vocabulary."""

from __future__ import annotations

from apps.logistics.diagnostics import error_codes
from apps.logistics.diagnostics.error_codes import get_suggested_action

__all__ = ["error_codes", "get_suggested_action"]

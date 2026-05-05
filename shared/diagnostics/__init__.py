"""Lightweight diagnostic event model and in-memory store."""

from __future__ import annotations

from shared.diagnostics import error_codes
from shared.diagnostics.error_codes import get_suggested_action
from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity
from shared.diagnostics.store import DiagnosticEventStore, get_diagnostic_store, reset_diagnostic_store

__all__ = [
    "DiagnosticEvent",
    "DiagnosticEventStore",
    "DiagnosticSeverity",
    "error_codes",
    "get_diagnostic_store",
    "get_suggested_action",
    "reset_diagnostic_store",
]

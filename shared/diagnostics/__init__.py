"""Lightweight diagnostic event model and in-memory store."""

from __future__ import annotations

from shared.diagnostics import error_codes
from shared.diagnostics.error_codes import get_suggested_action
from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity
from shared.diagnostics.logging_router import (
    configure_diagnostics_logging,
    get_diagnostic_logger,
    shutdown_diagnostics_logging,
)
from shared.diagnostics.store import DiagnosticEventStore, get_diagnostic_store, reset_diagnostic_store

__all__ = [
    "DiagnosticEvent",
    "DiagnosticEventStore",
    "DiagnosticSeverity",
    "configure_diagnostics_logging",
    "error_codes",
    "get_diagnostic_logger",
    "get_diagnostic_store",
    "get_suggested_action",
    "reset_diagnostic_store",
    "shutdown_diagnostics_logging",
]

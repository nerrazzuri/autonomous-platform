from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import sys

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from shared.diagnostics import error_codes
from shared.diagnostics.events import DiagnosticEvent, DiagnosticSeverity
from shared.diagnostics.store import DiagnosticEventStore, get_diagnostic_store, reset_diagnostic_store


def make_event(
    index: int,
    *,
    severity: DiagnosticSeverity | str = "info",
    module: str = "module",
    error_code: str | None = None,
    robot_id: str | None = None,
    task_id: str | None = None,
) -> DiagnosticEvent:
    return DiagnosticEvent.create(
        severity=severity,
        module=module,
        event=f"event.{index}",
        message=f"Message {index}",
        error_code=error_code,
        robot_id=robot_id,
        task_id=task_id,
    )


def test_add_event() -> None:
    store = DiagnosticEventStore(max_events=10)
    event = make_event(1)

    returned = store.add(event)

    assert returned is event
    assert store.count() == 1


def test_create_event_helper() -> None:
    store = DiagnosticEventStore(max_events=10)

    event = store.create_event(severity="warning", module="m", event="e", message="msg")

    assert event.severity is DiagnosticSeverity.WARNING
    assert store.count() == 1


def test_ring_buffer_maxlen_drops_old_events() -> None:
    store = DiagnosticEventStore(max_events=2)
    first = store.add(make_event(1))
    second = store.add(make_event(2))
    third = store.add(make_event(3))

    assert store.recent(limit=10) == [third, second]
    assert first not in store.recent(limit=10)


def test_recent_newest_first() -> None:
    store = DiagnosticEventStore(max_events=10)
    first = store.add(make_event(1))
    second = store.add(make_event(2))

    assert store.recent(limit=10) == [second, first]


def test_severity_filter() -> None:
    store = DiagnosticEventStore(max_events=10)
    info = store.add(make_event(1, severity="info"))
    error = store.add(make_event(2, severity="error"))

    assert store.recent(severity="ERROR") == [error]
    assert info not in store.recent(severity=DiagnosticSeverity.ERROR)


def test_module_filter() -> None:
    store = DiagnosticEventStore(max_events=10)
    store.add(make_event(1, module="shared.navigation"))
    target = store.add(make_event(2, module="shared.diagnostics"))

    assert store.recent(module="shared.diagnostics") == [target]


def test_error_code_filter() -> None:
    store = DiagnosticEventStore(max_events=10)
    target = store.add(make_event(1, error_code=error_codes.ROUTE_NOT_FOUND))
    store.add(make_event(2, error_code=error_codes.SDK_CONNECT_FAILED))

    assert store.recent(error_code=error_codes.ROUTE_NOT_FOUND) == [target]


def test_robot_id_and_task_id_filters_use_and_semantics() -> None:
    store = DiagnosticEventStore(max_events=10)
    target = store.add(make_event(1, robot_id="robot_01", task_id="task-1"))
    store.add(make_event(2, robot_id="robot_01", task_id="task-2"))
    store.add(make_event(3, robot_id="robot_02", task_id="task-1"))

    assert store.recent(robot_id="robot_01", task_id="task-1") == [target]


def test_errors_returns_error_and_critical_only() -> None:
    store = DiagnosticEventStore(max_events=10)
    store.add(make_event(1, severity="info"))
    error = store.add(make_event(2, severity="error"))
    critical = store.add(make_event(3, severity="critical"))

    assert store.errors() == [critical, error]


def test_clear_resets_store() -> None:
    store = DiagnosticEventStore(max_events=10)
    store.add(make_event(1))

    store.clear()

    assert store.count() == 0
    assert store.recent() == []


def test_invalid_max_events_rejected() -> None:
    with pytest.raises(ValueError, match="max_events"):
        DiagnosticEventStore(max_events=0)


def test_singleton_reset_works() -> None:
    first = reset_diagnostic_store(max_events=2)
    first.add(make_event(1))

    second = reset_diagnostic_store(max_events=3)

    assert get_diagnostic_store() is second
    assert second is not first
    assert second.count() == 0


def test_to_list_returns_newest_first_dicts() -> None:
    store = DiagnosticEventStore(max_events=10)
    store.add(make_event(1))
    newest = store.add(make_event(2))

    payloads = store.to_list(limit=1)

    assert payloads == [newest.to_dict()]


def test_thread_safety_smoke_test() -> None:
    store = DiagnosticEventStore(max_events=200)

    def add_event(index: int) -> None:
        store.add(make_event(index))

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(add_event, range(100)))

    assert store.count() == 100

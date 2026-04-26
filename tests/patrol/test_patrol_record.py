from __future__ import annotations

import pytest


def make_record_dict(**overrides):
    record = {
        "cycle_id": "cycle-1",
        "route_id": "PATROL_NORTH_LOOP",
        "status": "scheduled",
        "triggered_by": "manual",
        "created_at": "2026-04-26T00:00:00+00:00",
        "started_at": None,
        "completed_at": None,
        "waypoints_total": 2,
        "waypoints_observed": 1,
        "anomaly_ids": ["anom-1"],
        "failure_reason": None,
    }
    record.update(overrides)
    return record


def test_patrol_record_valid() -> None:
    from apps.patrol.tasks.patrol_record import PatrolCycleStatus, PatrolRecord

    record = PatrolRecord.from_dict(make_record_dict())

    assert record.cycle_id == "cycle-1"
    assert record.route_id == "PATROL_NORTH_LOOP"
    assert record.status is PatrolCycleStatus.SCHEDULED
    assert record.triggered_by == "manual"
    assert record.waypoints_total == 2
    assert record.waypoints_observed == 1
    assert record.anomaly_ids == ["anom-1"]


def test_patrol_record_rejects_empty_ids() -> None:
    from apps.patrol.tasks.patrol_record import PatrolTaskError, PatrolRecord

    with pytest.raises(PatrolTaskError, match="cycle_id"):
        PatrolRecord.from_dict(make_record_dict(cycle_id=""))

    with pytest.raises(PatrolTaskError, match="route_id"):
        PatrolRecord.from_dict(make_record_dict(route_id=""))


def test_patrol_record_rejects_invalid_triggered_by() -> None:
    from apps.patrol.tasks.patrol_record import PatrolTaskError, PatrolRecord

    with pytest.raises(PatrolTaskError, match="triggered_by"):
        PatrolRecord.from_dict(make_record_dict(triggered_by="operator"))


def test_patrol_record_rejects_bad_waypoint_counts() -> None:
    from apps.patrol.tasks.patrol_record import PatrolTaskError, PatrolRecord

    with pytest.raises(PatrolTaskError, match="waypoints_total"):
        PatrolRecord.from_dict(make_record_dict(waypoints_total=-1))

    with pytest.raises(PatrolTaskError, match="waypoints_observed"):
        PatrolRecord.from_dict(make_record_dict(waypoints_observed=-1))

    with pytest.raises(PatrolTaskError, match="waypoints_observed"):
        PatrolRecord.from_dict(make_record_dict(waypoints_total=1, waypoints_observed=2))


def test_patrol_record_to_dict_roundtrip() -> None:
    from apps.patrol.tasks.patrol_record import PatrolRecord

    original = PatrolRecord.from_dict(make_record_dict())
    cloned = PatrolRecord.from_dict(original.to_dict())

    assert cloned == original


def test_state_machine_valid_transitions() -> None:
    from apps.patrol.tasks.patrol_record import PatrolCycleStateMachine, PatrolCycleStatus

    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.SCHEDULED, PatrolCycleStatus.ACTIVE) is True
    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.ACTIVE, PatrolCycleStatus.COMPLETED) is True
    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.ACTIVE, PatrolCycleStatus.FAILED) is True
    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.ACTIVE, PatrolCycleStatus.SUSPENDED) is True
    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.SUSPENDED, PatrolCycleStatus.ACTIVE) is True
    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.SUSPENDED, PatrolCycleStatus.FAILED) is True


def test_state_machine_invalid_transitions() -> None:
    from apps.patrol.tasks.patrol_record import (
        InvalidCycleTransition,
        PatrolCycleStateMachine,
        PatrolCycleStatus,
    )

    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.SCHEDULED, PatrolCycleStatus.COMPLETED) is False

    with pytest.raises(InvalidCycleTransition):
        PatrolCycleStateMachine.transition_status(PatrolCycleStatus.SCHEDULED, PatrolCycleStatus.COMPLETED)

    with pytest.raises(InvalidCycleTransition):
        PatrolCycleStateMachine.transition_status(PatrolCycleStatus.ACTIVE, PatrolCycleStatus.SCHEDULED)


def test_terminal_statuses_do_not_transition() -> None:
    from apps.patrol.tasks.patrol_record import (
        InvalidCycleTransition,
        PatrolCycleStateMachine,
        PatrolCycleStatus,
    )

    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.COMPLETED, PatrolCycleStatus.ACTIVE) is False
    assert PatrolCycleStateMachine.can_transition(PatrolCycleStatus.FAILED, PatrolCycleStatus.ACTIVE) is False

    with pytest.raises(InvalidCycleTransition):
        PatrolCycleStateMachine.transition_status(PatrolCycleStatus.COMPLETED, PatrolCycleStatus.ACTIVE)

    with pytest.raises(InvalidCycleTransition):
        PatrolCycleStateMachine.transition_status(PatrolCycleStatus.FAILED, PatrolCycleStatus.ACTIVE)

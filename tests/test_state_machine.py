import pytest

from autonomous_logistic.core.errors import InvalidTaskTransition
from autonomous_logistic.core.models import TaskStatus
from autonomous_logistic.state.task_state_machine import (
    pause_status,
    resume_status,
    transition_status,
)


def test_valid_transport_lifecycle_transitions_to_completed():
    status = TaskStatus.CREATED

    for next_status in (
        TaskStatus.QUEUED,
        TaskStatus.DISPATCHED,
        TaskStatus.MOVING_TO_SOURCE,
        TaskStatus.ARRIVED_SOURCE,
        TaskStatus.LOADING,
        TaskStatus.MOVING_TO_DESTINATION,
        TaskStatus.ARRIVED_DESTINATION,
        TaskStatus.UNLOADING,
        TaskStatus.RETURNING,
        TaskStatus.COMPLETED,
    ):
        status = transition_status(status, next_status)

    assert status is TaskStatus.COMPLETED


def test_invalid_transition_raises_domain_error():
    with pytest.raises(InvalidTaskTransition) as error:
        transition_status(TaskStatus.CREATED, TaskStatus.COMPLETED)

    assert "CREATED -> COMPLETED" in str(error.value)


def test_pause_and_resume_preserve_previous_active_status():
    paused_status, previous_status = pause_status(TaskStatus.MOVING_TO_SOURCE)

    assert paused_status is TaskStatus.PAUSED
    assert previous_status is TaskStatus.MOVING_TO_SOURCE
    assert resume_status(paused_status, previous_status) is TaskStatus.MOVING_TO_SOURCE


def test_completed_task_cannot_be_paused():
    with pytest.raises(InvalidTaskTransition):
        pause_status(TaskStatus.COMPLETED)

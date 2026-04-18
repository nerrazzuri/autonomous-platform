from autonomous_logistic.core.errors import InvalidTaskTransition
from autonomous_logistic.core.models import TaskStatus


TERMINAL_STATUSES = {
    TaskStatus.COMPLETED,
    TaskStatus.FAILED,
    TaskStatus.CANCELLED,
    TaskStatus.EMERGENCY_STOP,
}

PAUSABLE_STATUSES = {
    TaskStatus.CREATED,
    TaskStatus.QUEUED,
    TaskStatus.DISPATCHED,
    TaskStatus.MOVING_TO_SOURCE,
    TaskStatus.ARRIVED_SOURCE,
    TaskStatus.LOADING,
    TaskStatus.MOVING_TO_DESTINATION,
    TaskStatus.ARRIVED_DESTINATION,
    TaskStatus.UNLOADING,
    TaskStatus.RETURNING,
}

ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.CREATED: {TaskStatus.QUEUED, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.QUEUED: {TaskStatus.DISPATCHED, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.DISPATCHED: {TaskStatus.MOVING_TO_SOURCE, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.MOVING_TO_SOURCE: {TaskStatus.ARRIVED_SOURCE, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.ARRIVED_SOURCE: {TaskStatus.LOADING, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.LOADING: {TaskStatus.MOVING_TO_DESTINATION, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.MOVING_TO_DESTINATION: {TaskStatus.ARRIVED_DESTINATION, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.ARRIVED_DESTINATION: {TaskStatus.UNLOADING, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.UNLOADING: {TaskStatus.RETURNING, TaskStatus.COMPLETED, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.RETURNING: {TaskStatus.COMPLETED, TaskStatus.PAUSED, TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.PAUSED: set(PAUSABLE_STATUSES) | {TaskStatus.CANCELLED, TaskStatus.FAILED, TaskStatus.EMERGENCY_STOP},
    TaskStatus.COMPLETED: set(),
    TaskStatus.FAILED: set(),
    TaskStatus.CANCELLED: set(),
    TaskStatus.EMERGENCY_STOP: set(),
}


def transition_status(current_status: TaskStatus, next_status: TaskStatus) -> TaskStatus:
    if next_status not in ALLOWED_TRANSITIONS[current_status]:
        raise InvalidTaskTransition(current_status.value, next_status.value)
    return next_status


def pause_status(current_status: TaskStatus) -> tuple[TaskStatus, TaskStatus]:
    if current_status not in PAUSABLE_STATUSES:
        raise InvalidTaskTransition(current_status.value, TaskStatus.PAUSED.value)
    return TaskStatus.PAUSED, current_status


def resume_status(current_status: TaskStatus, previous_status: TaskStatus | None) -> TaskStatus:
    if current_status is not TaskStatus.PAUSED or previous_status is None:
        raise InvalidTaskTransition(current_status.value, "RESUME")
    return transition_status(current_status, previous_status)

class DomainError(Exception):
    """Base class for expected domain-level failures."""


class InvalidTaskTransition(DomainError):
    def __init__(self, current_status: str, next_status: str) -> None:
        super().__init__(f"Invalid task transition: {current_status} -> {next_status}")
        self.current_status = current_status
        self.next_status = next_status


class TaskNotFound(DomainError):
    def __init__(self, task_id: str) -> None:
        super().__init__(f"Task not found: {task_id}")
        self.task_id = task_id


class RobotAdapterUnavailable(DomainError):
    """Raised when a selected robot adapter cannot be used in this environment."""

from autonomous_logistic.adapters.robot import RobotAdapter
from autonomous_logistic.core.errors import TaskNotFound
from autonomous_logistic.core.models import AuditEventType, TaskStatus, TransportTask
from autonomous_logistic.logging.audit import AuditLogger
from autonomous_logistic.state.repositories import TaskRepository
from autonomous_logistic.state.task_state_machine import pause_status, resume_status, transition_status


class TaskService:
    def __init__(self, tasks: TaskRepository, audit: AuditLogger, robot: RobotAdapter) -> None:
        self.tasks = tasks
        self.audit = audit
        self.robot = robot

    def create_task(
        self,
        source_point: str,
        destination_point: str,
        requested_by: str,
        request_source: str,
        notes: str | None = None,
    ) -> TransportTask:
        task = TransportTask.create(source_point, destination_point, requested_by, request_source, notes)
        self.tasks.create(task)
        self.audit.record(
            AuditEventType.TASK_CREATED,
            task_id=task.task_id,
            metadata={"source_point": source_point, "destination_point": destination_point, "request_source": request_source},
        )
        return task

    def list_tasks(self) -> list[TransportTask]:
        return self.tasks.list_all()

    def get_task(self, task_id: str) -> TransportTask:
        task = self.tasks.get(task_id)
        if task is None:
            raise TaskNotFound(task_id)
        return task

    def cancel_task(self, task_id: str) -> TransportTask:
        task = self.get_task(task_id)
        task.status = transition_status(task.status, TaskStatus.CANCELLED)
        task.previous_status = None
        self.robot.stop()
        self.tasks.save(task)
        self.audit.record(AuditEventType.TASK_CANCELLED, task_id=task.task_id, metadata={"status": task.status.value})
        return task

    def pause_task(self, task_id: str) -> TransportTask:
        task = self.get_task(task_id)
        task.status, task.previous_status = pause_status(task.status)
        self.robot.pause()
        self.tasks.save(task)
        self.audit.record(
            AuditEventType.TASK_PAUSED,
            task_id=task.task_id,
            metadata={"previous_status": task.previous_status.value if task.previous_status else None},
        )
        return task

    def resume_task(self, task_id: str) -> TransportTask:
        task = self.get_task(task_id)
        next_status = resume_status(task.status, task.previous_status)
        task.status = next_status
        task.previous_status = None
        self.robot.resume()
        self.tasks.save(task)
        self.audit.record(AuditEventType.TASK_RESUMED, task_id=task.task_id, metadata={"status": task.status.value})
        return task

from autonomous_logistic.core.models import AuditEvent, AuditEventType
from autonomous_logistic.state.repositories import AuditEventRepository


class AuditLogger:
    def __init__(self, events: AuditEventRepository) -> None:
        self.events = events

    def record(
        self,
        event_type: AuditEventType,
        task_id: str | None = None,
        metadata: dict | None = None,
    ) -> AuditEvent:
        return self.events.create(AuditEvent.create(event_type=event_type, task_id=task_id, metadata=metadata))

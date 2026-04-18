from datetime import datetime
from typing import Any

from pydantic import BaseModel


class CreateTaskRequest(BaseModel):
    source_point: str
    destination_point: str
    requested_by: str
    request_source: str
    notes: str | None = None


class TaskResponse(BaseModel):
    task_id: str
    source_point: str
    destination_point: str
    requested_by: str
    request_source: str
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    status: str
    error_code: str | None
    notes: str | None


class StationResponse(BaseModel):
    station_id: str
    name: str
    position: dict[str, Any]
    metadata: dict[str, Any]

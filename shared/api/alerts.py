from __future__ import annotations

"""Alert manager for normalized system alerts, persistence, broadcast, and email."""

import asyncio
import json
import smtplib
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from typing import Any

from shared.core.config import get_config
from shared.core.database import Database, get_database
from shared.core.event_bus import Event, EventName, get_event_bus
from shared.core.logger import get_logger
from shared.api.ws_broker import WebSocketBroker, get_ws_broker


logger = get_logger(__name__)

_ALLOWED_SEVERITIES = {"info", "warning", "critical"}


class AlertManagerError(Exception):
    """Raised when alert normalization or alert handling cannot proceed safely."""


@dataclass(frozen=True)
class AlertMessage:
    alert_id: str
    severity: str
    reason: str
    module: str
    message: str
    timestamp: datetime
    active_task_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        normalized_severity = self.severity.strip().lower() if isinstance(self.severity, str) else ""
        if normalized_severity not in _ALLOWED_SEVERITIES:
            raise AlertManagerError("severity must be one of: info, warning, critical")
        if not isinstance(self.reason, str) or not self.reason.strip():
            raise AlertManagerError("reason must not be empty")
        if not isinstance(self.module, str) or not self.module.strip():
            raise AlertManagerError("module must not be empty")
        if not isinstance(self.message, str) or not self.message.strip():
            raise AlertManagerError("message must not be empty")
        if not isinstance(self.metadata, dict):
            raise AlertManagerError("metadata must be a dict")

        object.__setattr__(self, "severity", normalized_severity)
        object.__setattr__(self, "reason", self.reason.strip())
        object.__setattr__(self, "module", self.module.strip())
        object.__setattr__(self, "message", self.message.strip())
        object.__setattr__(self, "metadata", dict(self.metadata))

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity,
            "reason": self.reason,
            "module": self.module,
            "message": self.message,
            "timestamp": self.timestamp.isoformat(),
            "active_task_id": self.active_task_id,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_event(cls, event: Event) -> "AlertMessage":
        if event.name is not EventName.SYSTEM_ALERT:
            raise AlertManagerError("AlertMessage.from_event requires EventName.SYSTEM_ALERT")

        payload = dict(event.payload)
        raw_severity = payload.pop("severity", "warning")
        severity = raw_severity.strip().lower() if isinstance(raw_severity, str) else raw_severity

        raw_reason = payload.pop("reason", None)
        reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
        if not reason:
            reason = "unspecified"

        raw_module = payload.pop("module", None)
        module_name = raw_module.strip() if isinstance(raw_module, str) and raw_module.strip() else event.source or "unknown"

        raw_message = payload.pop("message", None)
        message = (
            raw_message.strip()
            if isinstance(raw_message, str) and raw_message.strip()
            else f"{str(severity).upper()}: {reason}"
        )

        active_task_id = payload.pop("active_task_id", None)
        if active_task_id is None:
            active_task_id = event.task_id

        return cls(
            alert_id=event.event_id,
            severity=severity,
            reason=reason,
            module=module_name,
            message=message,
            timestamp=event.timestamp,
            active_task_id=active_task_id,
            metadata=payload,
        )


class AlertManager:
    def __init__(
        self,
        database: Database | None = None,
        ws_broker: WebSocketBroker | None = None,
        email_enabled: bool | None = None,
    ) -> None:
        self._database = database or get_database()
        self._ws_broker = ws_broker or get_ws_broker()
        self._event_bus = get_event_bus()
        self._email_enabled = get_config().alerts.email_enabled if email_enabled is None else email_enabled
        self._running = False
        self._subscription_ids: list[str] = []
        self._last_alert: AlertMessage | None = None
        self._last_error: str | None = None

    async def start(self) -> None:
        if self._running:
            return

        subscription_id = self._event_bus.subscribe(
            EventName.SYSTEM_ALERT,
            self.handle_alert_event,
            subscriber_name="alert_manager",
        )
        self._subscription_ids = [subscription_id]
        self._running = True
        logger.info("Alert manager started", extra={"subscription_count": 1})

    async def stop(self) -> None:
        if not self._subscription_ids and not self._running:
            return

        subscription_ids = list(self._subscription_ids)
        self._subscription_ids.clear()
        for subscription_id in subscription_ids:
            self._event_bus.unsubscribe(subscription_id)

        self._running = False
        logger.info("Alert manager stopped", extra={"subscription_count": len(subscription_ids)})

    async def handle_alert_event(self, event: Event) -> AlertMessage:
        self._last_error = None
        try:
            alert = AlertMessage.from_event(event)
            self._last_alert = alert
            self._log_handled_alert(alert)

            await self._persist_alert(alert)
            await self._broadcast_alert(alert)
            if self._email_enabled:
                await self._send_email_notification(alert)
            return alert
        except AlertManagerError as exc:
            self._remember_error(str(exc))
            raise
        except Exception as exc:
            self._remember_error(str(exc))
            logger.exception(
                "Unexpected alert handling failure",
                extra={
                    "event_name": getattr(event.name, "value", str(event.name)),
                    "source": event.source,
                    "task_id": event.task_id,
                },
            )
            raise AlertManagerError(f"Unexpected alert handling failure: {exc}") from exc

    async def get_last_alert(self) -> AlertMessage | None:
        return self._last_alert

    def is_running(self) -> bool:
        return self._running

    def last_error(self) -> str | None:
        return self._last_error

    async def _persist_alert(self, alert: AlertMessage) -> None:
        try:
            await self._database.initialize()
            await self._database.log_event(
                event_name=EventName.SYSTEM_ALERT.value,
                payload=alert.to_dict(),
                source=alert.module,
                task_id=alert.active_task_id,
                event_id=alert.alert_id,
            )
        except Exception as exc:
            self._remember_error(str(exc))
            logger.warning(
                "Alert persistence failed",
                extra={"alert_id": alert.alert_id, "reason": alert.reason, "alert_module": alert.module},
            )

    async def _broadcast_alert(self, alert: AlertMessage) -> None:
        try:
            await self._ws_broker.broadcast({"type": "alert", "alert": alert.to_dict()})
        except Exception as exc:
            self._remember_error(str(exc))
            logger.warning(
                "Alert broadcast failed",
                extra={"alert_id": alert.alert_id, "reason": alert.reason, "alert_module": alert.module},
            )

    async def _send_email_notification(self, alert: AlertMessage) -> None:
        alerts_config = get_config().alerts
        missing_fields: list[str] = []
        if not alerts_config.smtp_host:
            missing_fields.append("smtp_host")
        if not alerts_config.supervisor_email:
            missing_fields.append("supervisor_email")
        if bool(alerts_config.smtp_username) != bool(alerts_config.smtp_password):
            missing_fields.append("smtp_credentials")

        if missing_fields:
            message = f"Incomplete email alert config: {', '.join(missing_fields)}"
            self._remember_error(message)
            logger.warning("Alert email skipped", extra={"reason": message})
            return

        try:
            await asyncio.to_thread(self._send_email_sync, alert)
        except Exception as exc:
            self._remember_error(str(exc))
            logger.warning(
                "Alert email failed",
                extra={"alert_id": alert.alert_id, "reason": alert.reason, "alert_module": alert.module},
            )

    def _send_email_sync(self, alert: AlertMessage) -> None:
        alerts_config = get_config().alerts
        subject = f"[Sumitomo Quadruped] {alert.severity.upper()} alert: {alert.reason}"
        body_lines = [
            f"severity: {alert.severity}",
            f"reason: {alert.reason}",
            f"module: {alert.module}",
            f"message: {alert.message}",
            f"timestamp: {alert.timestamp.isoformat()}",
        ]
        if alert.active_task_id is not None:
            body_lines.append(f"active_task_id: {alert.active_task_id}")
        body_lines.extend(
            [
                "",
                "metadata:",
                json.dumps(alert.metadata, indent=2, sort_keys=True, default=str),
            ]
        )

        email_message = EmailMessage()
        email_message["Subject"] = subject
        email_message["To"] = alerts_config.supervisor_email
        email_message["From"] = alerts_config.smtp_username or "agibot-quadruped@localhost"
        email_message.set_content("\n".join(body_lines))

        with smtplib.SMTP(alerts_config.smtp_host, alerts_config.smtp_port) as smtp:
            if alerts_config.smtp_username and alerts_config.smtp_password:
                smtp.login(alerts_config.smtp_username, alerts_config.smtp_password)
            smtp.send_message(email_message)

    def _log_handled_alert(self, alert: AlertMessage) -> None:
        log_extra = {
            "alert_id": alert.alert_id,
            "severity": alert.severity,
            "reason": alert.reason,
            "alert_module": alert.module,
            "active_task_id": alert.active_task_id,
        }
        if alert.severity == "info":
            logger.info("Alert handled", extra=log_extra)
        else:
            logger.warning("Alert handled", extra=log_extra)

    def _remember_error(self, message: str) -> None:
        self._last_error = message


alert_manager = AlertManager()


def get_alert_manager() -> AlertManager:
    return alert_manager


__all__ = [
    "AlertManager",
    "AlertManagerError",
    "AlertMessage",
    "alert_manager",
    "get_alert_manager",
]

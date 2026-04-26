from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from apps.patrol.observation.anomaly_log import AnomalyRecord
from shared.core.config import get_config
from shared.core.logger import get_logger


logger = get_logger(__name__)


class AlertNotifierError(Exception):
    """Raised when patrol alert notifier inputs are invalid."""


@dataclass(frozen=True)
class AlertNotificationResult:
    attempted: bool
    delivered: bool
    destination: str | None
    error: str | None = None

    def __post_init__(self) -> None:
        if self.delivered and not self.attempted:
            raise AlertNotifierError("attempted must be True when delivered is True")

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "delivered": self.delivered,
            "destination": self.destination,
            "error": self.error,
        }


def build_payload(anomaly_record: AnomalyRecord) -> dict[str, Any]:
    if not isinstance(anomaly_record, AnomalyRecord):
        raise AlertNotifierError("anomaly_record must be an AnomalyRecord")

    return {
        "anomaly_id": anomaly_record.anomaly_id,
        "cycle_id": anomaly_record.cycle_id,
        "zone_id": anomaly_record.zone_id,
        "waypoint_name": anomaly_record.waypoint_name,
        "detected_at": anomaly_record.detected_at,
        "severity": anomaly_record.severity,
        "confidence_max": anomaly_record.confidence_max,
        "threat_objects": anomaly_record.threat_objects(),
        "metadata": anomaly_record.metadata(),
    }


class AlertNotifier:
    build_payload = staticmethod(build_payload)

    def __init__(
        self,
        webhook_url: str | None = None,
        enabled: bool | None = None,
    ) -> None:
        config_webhook_url = getattr(get_config().patrol, "webhook_url", None) if webhook_url is None else webhook_url
        normalized_webhook_url = config_webhook_url.strip() if isinstance(config_webhook_url, str) else None
        self._webhook_url = normalized_webhook_url or None
        self._enabled = bool(self._webhook_url) if enabled is None else bool(enabled)

    async def notify(self, anomaly_record: AnomalyRecord) -> AlertNotificationResult:
        if not self._enabled:
            logger.debug("Patrol alert notifier disabled")
            return AlertNotificationResult(
                attempted=False,
                delivered=False,
                destination=None,
            )

        if self._webhook_url is None:
            return AlertNotificationResult(
                attempted=False,
                delivered=False,
                destination=None,
                error="webhook_url not configured",
            )

        try:
            await self._send_webhook(anomaly_record)
        except Exception as exc:
            logger.warning(
                "Patrol alert notifier delivery failed",
                extra={"destination": self._webhook_url, "error": str(exc)},
            )
            return AlertNotificationResult(
                attempted=True,
                delivered=False,
                destination=self._webhook_url,
                error=str(exc),
            )

        return AlertNotificationResult(
            attempted=True,
            delivered=True,
            destination=self._webhook_url,
        )

    async def _send_webhook(self, anomaly_record: AnomalyRecord) -> None:
        payload = build_payload(anomaly_record)
        logger.info(
            "Patrol alert notifier stub would send webhook",
            extra={"destination": self._webhook_url, "payload": payload},
        )


alert_notifier = AlertNotifier()


def get_alert_notifier() -> AlertNotifier:
    return alert_notifier


__all__ = [
    "AlertNotificationResult",
    "AlertNotifier",
    "AlertNotifierError",
    "alert_notifier",
    "build_payload",
    "get_alert_notifier",
]

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def make_anomaly_record():
    from apps.patrol.observation.anomaly_log import AnomalyRecord

    return AnomalyRecord(
        anomaly_id="anom-1",
        cycle_id="cycle-1",
        zone_id="ZONE_NORTH",
        waypoint_name="north_observation_1",
        detected_at="2026-04-26T00:00:00+00:00",
        severity="warning",
        threat_objects_json='[{"label":"wild boar","threat_level":"SUSPICIOUS","confidence":0.84}]',
        confidence_max=0.84,
        metadata_json='{"source":"camera-1","task_id":"cycle-1"}',
    )


@pytest.fixture
def notifier_module():
    return importlib.import_module("apps.patrol.hardware.alert_notifier")


def test_build_payload(notifier_module) -> None:
    record = make_anomaly_record()
    notifier = notifier_module.AlertNotifier(enabled=False)

    payload = notifier.build_payload(record)

    assert payload == {
        "anomaly_id": "anom-1",
        "cycle_id": "cycle-1",
        "zone_id": "ZONE_NORTH",
        "waypoint_name": "north_observation_1",
        "detected_at": "2026-04-26T00:00:00+00:00",
        "severity": "warning",
        "confidence_max": 0.84,
        "threat_objects": [{"label": "wild boar", "threat_level": "SUSPICIOUS", "confidence": 0.84}],
        "metadata": {"source": "camera-1", "task_id": "cycle-1"},
    }


@pytest.mark.asyncio
async def test_notify_disabled_returns_not_attempted(notifier_module) -> None:
    notifier = notifier_module.AlertNotifier(webhook_url="https://example.invalid/patrol", enabled=False)

    result = await notifier.notify(make_anomaly_record())

    assert result.attempted is False
    assert result.delivered is False
    assert result.destination is None
    assert result.error is None


@pytest.mark.asyncio
async def test_notify_enabled_missing_webhook_returns_error(notifier_module) -> None:
    notifier = notifier_module.AlertNotifier(webhook_url=None, enabled=True)

    result = await notifier.notify(make_anomaly_record())

    assert result.attempted is False
    assert result.delivered is False
    assert result.destination is None
    assert result.error == "webhook_url not configured"


@pytest.mark.asyncio
async def test_notify_enabled_stub_delivers(notifier_module, monkeypatch: pytest.MonkeyPatch) -> None:
    notifier = notifier_module.AlertNotifier(webhook_url="https://example.invalid/patrol", enabled=True)
    sent = []

    async def fake_send(record):
        sent.append(record.anomaly_id)

    monkeypatch.setattr(notifier, "_send_webhook", fake_send)

    result = await notifier.notify(make_anomaly_record())

    assert sent == ["anom-1"]
    assert result.attempted is True
    assert result.delivered is True
    assert result.destination == "https://example.invalid/patrol"
    assert result.error is None


@pytest.mark.asyncio
async def test_notify_send_failure_returns_error_not_raise(notifier_module, monkeypatch: pytest.MonkeyPatch) -> None:
    notifier = notifier_module.AlertNotifier(webhook_url="https://example.invalid/patrol", enabled=True)

    async def fake_send(_record):
        raise RuntimeError("delivery failed")

    monkeypatch.setattr(notifier, "_send_webhook", fake_send)

    result = await notifier.notify(make_anomaly_record())

    assert result.attempted is True
    assert result.delivered is False
    assert result.destination == "https://example.invalid/patrol"
    assert result.error == "delivery failed"


def test_result_validation(notifier_module) -> None:
    valid = notifier_module.AlertNotificationResult(
        attempted=True,
        delivered=True,
        destination="https://example.invalid/patrol",
    )

    assert valid.to_dict()["delivered"] is True

    with pytest.raises(notifier_module.AlertNotifierError, match="attempted"):
        notifier_module.AlertNotificationResult(
            attempted=False,
            delivered=True,
            destination="https://example.invalid/patrol",
        )


def test_global_get_alert_notifier_returns_notifier(notifier_module) -> None:
    assert notifier_module.get_alert_notifier() is notifier_module.alert_notifier
    assert isinstance(notifier_module.alert_notifier, notifier_module.AlertNotifier)

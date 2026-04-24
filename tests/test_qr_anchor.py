from __future__ import annotations

import importlib
import sys
from datetime import datetime, timezone
from pathlib import Path
from math import inf

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture
def qr_module():
    sys.modules.pop("hardware.qr_anchor", None)
    module = importlib.import_module("hardware.qr_anchor")
    return module


def test_correction_result_to_dict(qr_module) -> None:
    result = qr_module.CorrectionResult(
        marker_id="qr-17",
        x=1.25,
        y=-0.5,
        heading_rad=0.75,
        confidence=0.92,
        timestamp=datetime(2026, 4, 24, 15, 0, tzinfo=timezone.utc),
        metadata={"station_id": "A"},
    )

    assert result.to_dict() == {
        "marker_id": "qr-17",
        "x": 1.25,
        "y": -0.5,
        "heading_rad": 0.75,
        "confidence": 0.92,
        "timestamp": "2026-04-24T15:00:00+00:00",
        "source": "qr_anchor",
        "metadata": {"station_id": "A"},
    }


def test_correction_result_rejects_invalid_marker_id(qr_module) -> None:
    with pytest.raises(qr_module.QRAnchorError, match="marker_id"):
        qr_module.CorrectionResult(
            marker_id=" ",
            x=0.0,
            y=0.0,
            heading_rad=0.0,
            confidence=0.5,
            timestamp=datetime.now(timezone.utc),
        )


def test_correction_result_rejects_invalid_confidence(qr_module) -> None:
    with pytest.raises(qr_module.QRAnchorError, match="confidence"):
        qr_module.CorrectionResult(
            marker_id="qr-1",
            x=0.0,
            y=0.0,
            heading_rad=0.0,
            confidence=1.5,
            timestamp=datetime.now(timezone.utc),
        )


def test_correction_result_rejects_non_finite_values(qr_module) -> None:
    with pytest.raises(qr_module.QRAnchorError, match="x"):
        qr_module.CorrectionResult(
            marker_id="qr-1",
            x=inf,
            y=0.0,
            heading_rad=0.0,
            confidence=0.5,
            timestamp=datetime.now(timezone.utc),
        )

    with pytest.raises(qr_module.QRAnchorError, match="heading_rad"):
        qr_module.CorrectionResult(
            marker_id="qr-1",
            x=0.0,
            y=0.0,
            heading_rad=float("nan"),
            confidence=0.5,
            timestamp=datetime.now(timezone.utc),
        )


@pytest.mark.asyncio
async def test_reader_check_frame_returns_none_in_phase1(qr_module) -> None:
    reader = qr_module.QRAnchorReader()

    assert await reader.check_frame(object()) is None


@pytest.mark.asyncio
async def test_reader_accepts_none_frame(qr_module) -> None:
    reader = qr_module.QRAnchorReader()

    assert await reader.check_frame(None) is None


@pytest.mark.asyncio
async def test_read_count_increments(qr_module) -> None:
    reader = qr_module.QRAnchorReader()

    assert reader.read_count() == 0
    await reader.check_frame(None)
    await reader.check_frame(object())
    assert reader.read_count() == 2


@pytest.mark.asyncio
async def test_last_correction_none_in_phase1(qr_module) -> None:
    reader = qr_module.QRAnchorReader()

    await reader.check_frame(None)
    assert await reader.get_last_correction() is None


@pytest.mark.asyncio
async def test_enabled_true_still_safe_stub(qr_module) -> None:
    reader = qr_module.QRAnchorReader(enabled=True)

    correction = await reader.check_frame(None)

    assert reader.is_enabled() is True
    assert correction is None
    assert reader.last_error() is None


def test_global_get_qr_anchor_reader_returns_reader(qr_module) -> None:
    assert qr_module.get_qr_anchor_reader() is qr_module.qr_anchor_reader

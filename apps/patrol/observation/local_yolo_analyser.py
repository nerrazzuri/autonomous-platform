from __future__ import annotations

import importlib

from apps.patrol.observation.anomaly_decider import DetectedObject, VisionAnalysisResult
from shared.core.config import get_config
from shared.hardware.video_reader import VideoFrame


class LocalYOLOUnavailableError(Exception):
    """Raised when local YOLO analysis cannot be used."""


class LocalYOLOAnalyser:
    def __init__(self, model_path: str | None = None) -> None:
        vision_config = get_config().vision
        self._model_path = model_path or getattr(vision_config, "local_yolo_model_path", "yolov8n.pt")
        self._model = None

    async def analyse(self, frame: VideoFrame) -> VisionAnalysisResult:
        if not isinstance(frame, VideoFrame):
            raise LocalYOLOUnavailableError("frame must be a VideoFrame")

        model = self._load_model()
        raw_results = self._run_model(model, frame)
        objects = self._map_detections(model, raw_results)
        zone_id = frame.metadata.get("zone_id") if isinstance(frame.metadata.get("zone_id"), str) else "local_yolo"
        return VisionAnalysisResult(
            zone_id=zone_id,
            objects_detected=objects,
            analysis_source="local_yolo",
        )

    def _load_model(self):
        if self._model is not None:
            return self._model

        try:
            ultralytics = importlib.import_module("ultralytics")
        except ImportError as exc:
            raise LocalYOLOUnavailableError("ultralytics is not installed") from exc

        if not hasattr(ultralytics, "YOLO"):
            raise LocalYOLOUnavailableError("ultralytics.YOLO is unavailable")

        self._model = ultralytics.YOLO(self._model_path)
        return self._model

    @staticmethod
    def _run_model(model, frame: VideoFrame):
        if hasattr(model, "predict"):
            return model.predict(frame.data if frame.data is not None else frame)
        if callable(model):
            return model(frame.data if frame.data is not None else frame)
        return []

    @staticmethod
    def _map_detections(model, raw_results) -> list[DetectedObject]:
        names = getattr(model, "names", {})
        objects: list[DetectedObject] = []
        for result in raw_results or []:
            boxes = getattr(result, "boxes", [])
            for box in boxes or []:
                class_id = int(getattr(box, "cls", 0))
                confidence = float(getattr(box, "conf", 0.5))
                label = names.get(class_id, str(class_id)) if isinstance(names, dict) else str(class_id)
                objects.append(
                    DetectedObject(
                        label=label,
                        threat_level="SUSPICIOUS",
                        confidence=confidence,
                        reason="Local YOLO detection requires human review",
                    )
                )
        return objects


__all__ = [
    "LocalYOLOAnalyser",
    "LocalYOLOUnavailableError",
]

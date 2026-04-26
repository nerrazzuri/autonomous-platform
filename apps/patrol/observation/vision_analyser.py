from __future__ import annotations

import importlib
import json

from apps.patrol.observation.anomaly_decider import DetectedObject, VisionAnalysisResult
from apps.patrol.observation.local_yolo_analyser import LocalYOLOAnalyser, LocalYOLOUnavailableError
from apps.patrol.observation.zone_config import ZoneDefinition
from shared.core.config import get_config
from shared.core.logger import get_logger
from shared.hardware.video_reader import VideoFrame


logger = get_logger(__name__)

ALLOWED_PROVIDERS = {"claude", "local_yolo", "none"}
ALLOWED_OFFLINE_FALLBACK_MODES = {"conservative", "local_model", "disabled"}


class VisionAnalyserError(Exception):
    """Raised when patrol vision analysis configuration or parsing fails."""


class VisionAnalyser:
    def __init__(
        self,
        enabled: bool | None = None,
        provider: str | None = None,
        claude_model: str | None = None,
        max_tokens: int | None = None,
        api_timeout_seconds: float | None = None,
        offline_fallback_mode: str | None = None,
        local_yolo_analyser: object | None = None,
    ) -> None:
        config = get_config().vision
        self.enabled = config.enabled if enabled is None else bool(enabled)
        self.provider = config.provider if provider is None else provider
        self.claude_model = config.claude_model if claude_model is None else claude_model
        self.max_tokens = config.claude_max_tokens if max_tokens is None else max_tokens
        self.api_timeout_seconds = config.api_timeout_seconds if api_timeout_seconds is None else api_timeout_seconds
        self.offline_fallback_mode = (
            config.offline_fallback_mode if offline_fallback_mode is None else offline_fallback_mode
        )
        self._local_yolo_analyser = local_yolo_analyser

        if self.provider not in ALLOWED_PROVIDERS:
            allowed = ", ".join(sorted(ALLOWED_PROVIDERS))
            raise VisionAnalyserError(f"provider must be one of: {allowed}")
        if self.offline_fallback_mode not in ALLOWED_OFFLINE_FALLBACK_MODES:
            allowed = ", ".join(sorted(ALLOWED_OFFLINE_FALLBACK_MODES))
            raise VisionAnalyserError(f"offline_fallback_mode must be one of: {allowed}")
        if not isinstance(self.max_tokens, int) or self.max_tokens <= 0:
            raise VisionAnalyserError("max_tokens must be > 0")
        if not isinstance(self.api_timeout_seconds, (int, float)) or float(self.api_timeout_seconds) <= 0:
            raise VisionAnalyserError("api_timeout_seconds must be > 0")
        self.api_timeout_seconds = float(self.api_timeout_seconds)

    async def analyse(self, frame: VideoFrame | None, zone: ZoneDefinition) -> VisionAnalysisResult:
        if not isinstance(zone, ZoneDefinition):
            raise VisionAnalyserError("zone must be a ZoneDefinition")

        if self.enabled is False:
            return VisionAnalysisResult(zone_id=zone.zone_id, objects_detected=[], analysis_source="stub")

        if frame is None:
            return VisionAnalysisResult(
                zone_id=zone.zone_id,
                objects_detected=[],
                analysis_source="stub",
                error="no frame",
            )

        if self.provider == "none":
            return VisionAnalysisResult(zone_id=zone.zone_id, objects_detected=[], analysis_source="stub")

        if self.provider == "local_yolo":
            analyser = self._local_yolo_analyser or LocalYOLOAnalyser()
            result = await analyser.analyse(frame)
            return self._with_zone_id(result, zone.zone_id)

        try:
            result = await self._analyse_with_claude(frame, zone)
            return self._with_zone_id(result, zone.zone_id)
        except Exception as exc:
            return await self._offline_fallback(zone, frame, exc)

    async def _analyse_with_claude(self, frame: VideoFrame, zone: ZoneDefinition) -> VisionAnalysisResult:
        _ = frame
        _ = zone
        try:
            importlib.import_module("anthropic")
        except ImportError as exc:
            raise VisionAnalyserError("Anthropic SDK is not installed") from exc
        raise VisionAnalyserError("Claude Vision integration not implemented in Phase 1")

    def _parse_detected_objects(self, raw_text: str) -> list[DetectedObject]:
        normalized = self._strip_markdown_fence(raw_text)
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise VisionAnalyserError(f"Failed to parse detected objects JSON: {exc}") from exc

        if isinstance(payload, dict):
            payload = payload.get("objects")
        if not isinstance(payload, list):
            raise VisionAnalyserError("Detected objects payload must be a JSON list or {'objects': [...]} object")

        objects: list[DetectedObject] = []
        for item in payload:
            if not isinstance(item, dict):
                raise VisionAnalyserError("Each detected object must be a JSON object")
            objects.append(
                DetectedObject(
                    label=item.get("label", ""),
                    threat_level=item.get("threat_level", ""),
                    confidence=item.get("confidence", 0.0),
                    reason=item.get("reason", ""),
                    location_hint=item.get("location_hint"),
                )
            )
        return objects

    def _strip_markdown_fence(self, raw_text: str) -> str:
        if not isinstance(raw_text, str) or not raw_text.strip():
            raise VisionAnalyserError("raw_text must not be empty")
        stripped = raw_text.strip()
        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            if len(lines) >= 2:
                return "\n".join(lines[1:-1]).strip()
        return stripped

    async def _offline_fallback(
        self,
        zone: ZoneDefinition,
        frame: VideoFrame,
        exc: Exception,
    ) -> VisionAnalysisResult:
        error_message = str(exc)
        if self.offline_fallback_mode == "local_model":
            analyser = self._local_yolo_analyser or LocalYOLOAnalyser()
            try:
                result = await analyser.analyse(frame)
                return self._with_zone_id(result, zone.zone_id)
            except (LocalYOLOUnavailableError, Exception):
                logger.debug("Local YOLO fallback unavailable", extra={"zone_id": zone.zone_id})
                return self._conservative_fallback(zone.zone_id, error_message)

        if self.offline_fallback_mode == "disabled":
            return VisionAnalysisResult(
                zone_id=zone.zone_id,
                objects_detected=[],
                analysis_source="stub",
                error=error_message,
            )

        return self._conservative_fallback(zone.zone_id, error_message)

    @staticmethod
    def _conservative_fallback(zone_id: str, error_message: str) -> VisionAnalysisResult:
        return VisionAnalysisResult(
            zone_id=zone_id,
            objects_detected=[
                DetectedObject(
                    label="unknown",
                    threat_level="SUSPICIOUS",
                    confidence=0.5,
                    reason="Vision API unavailable — conservative fallback",
                )
            ],
            analysis_source="offline_conservative",
            error=error_message,
        )

    @staticmethod
    def _with_zone_id(result: VisionAnalysisResult, zone_id: str) -> VisionAnalysisResult:
        return VisionAnalysisResult(
            zone_id=zone_id,
            objects_detected=result.objects_detected,
            analysis_source=result.analysis_source,
            raw_response=result.raw_response,
            error=result.error,
        )


vision_analyser = VisionAnalyser()


def get_vision_analyser() -> VisionAnalyser:
    return vision_analyser


__all__ = [
    "VisionAnalyser",
    "VisionAnalyserError",
    "get_vision_analyser",
    "vision_analyser",
]

from __future__ import annotations

"""Speaker arrival alert — plays an audio file on successful navigation completion."""

import asyncio
import shutil
import subprocess
from pathlib import Path

from shared.core.event_bus import EventBus, EventName
from shared.core.logger import get_logger


logger = get_logger(__name__)


class SpeakerAlert:
    def __init__(
        self,
        enabled: bool,
        arrival_sound: str,
        volume_pct: int = 80,
        player_cmd: str = "aplay",
    ) -> None:
        self._enabled = bool(enabled)
        self._arrival_sound = str(arrival_sound)
        self._volume_pct = int(volume_pct)
        self._player_cmd = str(player_cmd)
        self._started = False

    def start(self, event_bus: EventBus) -> None:
        if not self._enabled:
            logger.debug("Speaker alert disabled, skipping subscription")
            return
        if self._started:
            return
        event_bus.subscribe(
            EventName.NAVIGATION_COMPLETED,
            self._on_navigation_completed,
            subscriber_name="speaker_alert",
        )
        self._started = True
        logger.info("Speaker alert subscribed to navigation.completed")

    async def _on_navigation_completed(self, event) -> None:
        if not event.payload.get("success"):
            return
        try:
            await asyncio.to_thread(self._play)
        except Exception as exc:
            logger.warning("Speaker alert play failed", extra={"error": str(exc)})

    def _play(self) -> None:
        if shutil.which(self._player_cmd) is None:
            logger.warning("Speaker player not found", extra={"player_cmd": self._player_cmd})
            return
        if not Path(self._arrival_sound).exists():
            logger.warning("Speaker arrival sound not found", extra={"path": self._arrival_sound})
            return
        amixer = shutil.which("amixer")
        if amixer is not None:
            try:
                subprocess.run(
                    [amixer, "set", "Master", f"{self._volume_pct}%"],
                    capture_output=True,
                    timeout=2,
                )
            except Exception:
                pass
        try:
            subprocess.run(
                [self._player_cmd, self._arrival_sound],
                capture_output=True,
                timeout=5,
            )
        except Exception as exc:
            logger.warning("Speaker subprocess failed", extra={"error": str(exc)})


__all__ = ["SpeakerAlert"]

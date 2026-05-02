from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _make_event(success: bool):
    from shared.core.event_bus import EventName
    return SimpleNamespace(
        name=EventName.NAVIGATION_COMPLETED,
        payload={"success": success, "route_id": "r1"},
    )


class FakeEventBus:
    def __init__(self):
        self.subscriptions: list[tuple] = []

    def subscribe(self, event_name, callback, *, subscriber_name=None):
        self.subscriptions.append((event_name, callback, subscriber_name))
        return "fake-sub-id"


# ---------------------------------------------------------------------------
# start() subscription behaviour
# ---------------------------------------------------------------------------

def test_disabled_speaker_does_not_subscribe():
    from shared.hardware.speaker import SpeakerAlert
    bus = FakeEventBus()
    speaker = SpeakerAlert(enabled=False, arrival_sound="data/audio/arrival.wav")
    speaker.start(bus)
    assert len(bus.subscriptions) == 0


def test_enabled_speaker_subscribes_to_navigation_completed():
    from shared.core.event_bus import EventName
    from shared.hardware.speaker import SpeakerAlert
    bus = FakeEventBus()
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav")
    speaker.start(bus)
    assert len(bus.subscriptions) == 1
    assert bus.subscriptions[0][0] == EventName.NAVIGATION_COMPLETED


def test_start_called_twice_subscribes_only_once():
    from shared.hardware.speaker import SpeakerAlert
    bus = FakeEventBus()
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav")
    speaker.start(bus)
    speaker.start(bus)
    assert len(bus.subscriptions) == 1


# ---------------------------------------------------------------------------
# _on_navigation_completed handler
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_unsuccessful_navigation_does_not_call_play():
    from shared.hardware.speaker import SpeakerAlert
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav")
    play_calls: list[int] = []
    speaker._play = lambda: play_calls.append(1)
    await speaker._on_navigation_completed(_make_event(success=False))
    assert play_calls == []


@pytest.mark.asyncio
async def test_successful_navigation_calls_play():
    from shared.hardware.speaker import SpeakerAlert
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav")
    play_calls: list[int] = []
    speaker._play = lambda: play_calls.append(1)
    await speaker._on_navigation_completed(_make_event(success=True))
    assert play_calls == [1]


# ---------------------------------------------------------------------------
# _play guard tests — no real subprocess
# ---------------------------------------------------------------------------

def test_play_does_not_raise_when_player_missing():
    from shared.hardware.speaker import SpeakerAlert
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav", player_cmd="aplay")
    with patch("shutil.which", return_value=None):
        speaker._play()


def test_play_does_not_raise_when_sound_file_missing():
    from shared.hardware.speaker import SpeakerAlert
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav", player_cmd="aplay")
    with patch("shutil.which", return_value="/usr/bin/aplay"), \
         patch("pathlib.Path.exists", return_value=False):
        speaker._play()


def test_play_does_not_raise_on_subprocess_failure():
    from shared.hardware.speaker import SpeakerAlert
    speaker = SpeakerAlert(enabled=True, arrival_sound="data/audio/arrival.wav", player_cmd="aplay")
    with patch("shutil.which", return_value="/usr/bin/aplay"), \
         patch("pathlib.Path.exists", return_value=True), \
         patch("subprocess.run", side_effect=subprocess.TimeoutExpired(cmd="aplay", timeout=5)):
        speaker._play()


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------

def test_config_speaker_defaults():
    from shared.core.config import SpeakerSection
    s = SpeakerSection()
    assert s.enabled is False
    assert s.arrival_sound == "data/audio/arrival.wav"
    assert s.volume_pct == 80
    assert s.player_cmd == "aplay"

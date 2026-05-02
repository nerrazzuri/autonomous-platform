from __future__ import annotations

"""Small TJC/Nextion-compatible serial protocol helpers."""

from dataclasses import dataclass
from math import isfinite


TERMINATOR = b"\xff\xff\xff"


@dataclass(frozen=True)
class TjcTouchEvent:
    page_id: int
    component_id: int
    touch_event: str


@dataclass(frozen=True)
class TjcPageEvent:
    page_id: int


@dataclass(frozen=True)
class TjcStringReturn:
    value: str


@dataclass(frozen=True)
class TjcNumericReturn:
    value: int


@dataclass(frozen=True)
class TjcReadyEvent:
    pass


def encode_command(command: str) -> bytes:
    return command.encode("utf-8") + TERMINATOR


def _escape_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def set_text(component: str, value: str) -> bytes:
    return encode_command(f'{component}.txt="{_escape_text(value)}"')


def set_number(component: str, value: int | float) -> bytes:
    if isinstance(value, float) and not isfinite(value):
        raise ValueError("TJC numeric value must be finite")
    return encode_command(f"{component}.val={value}")


def goto_page(page: str) -> bytes:
    return encode_command(f"page {page}")


class TjcFrameSplitter:
    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, data: bytes) -> list[bytes]:
        if data:
            self._buffer.extend(data)

        frames: list[bytes] = []
        while True:
            idx = self._buffer.find(TERMINATOR)
            if idx < 0:
                break
            frames.append(bytes(self._buffer[:idx]))
            del self._buffer[: idx + len(TERMINATOR)]
        return frames


def parse_frame(frame: bytes) -> object | None:
    if not frame:
        return None

    frame_type = frame[0]
    payload = frame[1:]

    if frame_type == 0x65 and len(payload) >= 3:
        touch_state = payload[2]
        if touch_state == 1:
            touch_event = "press"
        elif touch_state == 0:
            touch_event = "release"
        else:
            return None
        return TjcTouchEvent(
            page_id=payload[0],
            component_id=payload[1],
            touch_event=touch_event,
        )

    if frame_type == 0x66 and len(payload) >= 1:
        return TjcPageEvent(page_id=payload[0])

    if frame_type == 0x70:
        return TjcStringReturn(value=payload.decode("utf-8", errors="replace"))

    if frame_type == 0x71 and len(payload) >= 4:
        return TjcNumericReturn(value=int.from_bytes(payload[:4], "little", signed=True))

    if frame_type == 0x88:
        return TjcReadyEvent()

    return None


__all__ = [
    "TERMINATOR",
    "TjcFrameSplitter",
    "TjcNumericReturn",
    "TjcPageEvent",
    "TjcReadyEvent",
    "TjcStringReturn",
    "TjcTouchEvent",
    "encode_command",
    "goto_page",
    "parse_frame",
    "set_number",
    "set_text",
]

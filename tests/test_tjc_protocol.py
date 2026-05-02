from __future__ import annotations

from apps.hmi_agent.protocol import (
    TERMINATOR,
    TjcFrameSplitter,
    TjcPageEvent,
    TjcReadyEvent,
    TjcStringReturn,
    TjcTouchEvent,
    encode_command,
    goto_page,
    parse_frame,
    set_number,
    set_text,
)


def test_encode_command_appends_tjc_terminator() -> None:
    assert encode_command("page home") == b"page home" + TERMINATOR


def test_set_text_escapes_quotes_and_backslashes() -> None:
    assert (
        set_text("home.t_status", 'Robot "Online" \\ OK')
        == b'home.t_status.txt="Robot \\"Online\\" \\\\ OK"' + TERMINATOR
    )


def test_set_number_sets_component_value() -> None:
    assert set_number("home.n_battery", 82) == b"home.n_battery.val=82" + TERMINATOR


def test_goto_page_emits_page_command() -> None:
    assert goto_page("running") == b"page running" + TERMINATOR


def test_frame_splitter_handles_partial_frame() -> None:
    splitter = TjcFrameSplitter()

    assert splitter.feed(b"\x65\x01") == []
    assert splitter.feed(b"\x03\x01\xff\xff\xff") == [b"\x65\x01\x03\x01"]


def test_frame_splitter_handles_multiple_frames_and_empty_data() -> None:
    splitter = TjcFrameSplitter()

    frames = splitter.feed(b"\x88\xff\xff\xff\x66\x02\xff\xff\xff")

    assert frames == [b"\x88", b"\x66\x02"]
    assert splitter.feed(b"") == []


def test_unknown_frame_type_returns_none() -> None:
    assert parse_frame(b"\x99\x01\x02") is None


def test_touch_frame_parses_press_event() -> None:
    event = parse_frame(b"\x65\x01\x03\x01")

    assert event == TjcTouchEvent(page_id=1, component_id=3, touch_event="press")


def test_touch_frame_parses_release_event() -> None:
    event = parse_frame(b"\x65\x01\x03\x00")

    assert event == TjcTouchEvent(page_id=1, component_id=3, touch_event="release")


def test_page_frame_parses_page_id() -> None:
    assert parse_frame(b"\x66\x04") == TjcPageEvent(page_id=4)


def test_string_frame_parses_utf8_value() -> None:
    assert parse_frame("pallet ready".encode("utf-8").join([b"\x70", b""])) == TjcStringReturn(
        value="pallet ready"
    )


def test_numeric_frame_parses_little_endian_value() -> None:
    assert parse_frame(b"\x71\x52\x00\x00\x00").value == 82


def test_ready_frame_parses_ready_event() -> None:
    assert parse_frame(b"\x88") == TjcReadyEvent()

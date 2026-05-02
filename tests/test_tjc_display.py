from __future__ import annotations

from apps.hmi_agent.display import display_response_to_commands
from apps.hmi_agent.protocol import TERMINATOR, goto_page, set_number, set_text


def test_success_response_with_page_and_text_converts_to_commands() -> None:
    response = {
        "type": "hmi.action_response",
        "success": True,
        "message": "Load confirmed",
        "display": {"page": "in_transit", "text": "Delivering..."},
    }

    assert display_response_to_commands(response) == [
        goto_page("in_transit"),
        set_text("home.t_status", "Delivering..."),
    ]


def test_error_response_without_display_sets_status_message() -> None:
    response = {
        "type": "hmi.action_response",
        "success": False,
        "message": "Task is not awaiting load",
        "display": None,
    }

    assert display_response_to_commands(response) == [
        set_text("home.t_status", "Task is not awaiting load")
    ]


def test_command_objects_convert_to_tjc_commands() -> None:
    response = {
        "success": True,
        "message": "ok",
        "display": {
            "commands": [
                {"cmd": "set_text", "component": "home.t_status", "value": "Task Queued"},
                {"cmd": "set_number", "component": "home.n_battery", "value": 82},
                {"cmd": "goto_page", "page": "running"},
            ]
        },
    }

    assert display_response_to_commands(response) == [
        set_text("home.t_status", "Task Queued"),
        set_number("home.n_battery", 82),
        goto_page("running"),
    ]


def test_every_display_command_ends_with_tjc_terminator() -> None:
    commands = display_response_to_commands(
        {
            "success": False,
            "message": "No route",
            "display": {"page": "idle", "text": "No route"},
        }
    )

    assert commands
    assert all(command.endswith(TERMINATOR) for command in commands)

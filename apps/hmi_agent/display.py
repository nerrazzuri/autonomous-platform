from __future__ import annotations

"""Convert backend HMI display JSON into TJC commands."""

from collections.abc import Mapping

from apps.hmi_agent.protocol import goto_page, set_number, set_text


DEFAULT_STATUS_COMPONENT = "home.t_status"


def display_response_to_commands(
    response: dict,
    *,
    status_component: str = DEFAULT_STATUS_COMPONENT,
) -> list[bytes]:
    display = response.get("display")
    commands: list[bytes] = []

    if isinstance(display, Mapping):
        for item in _display_command_items(display):
            encoded = _encode_display_command(item)
            if encoded is not None:
                commands.append(encoded)

        page = display.get("page")
        if isinstance(page, str) and page:
            commands.append(goto_page(page))

        text = display.get("text")
        if isinstance(text, str):
            commands.append(set_text(status_component, text))

    if not commands and response.get("success") is False:
        message = response.get("message")
        if isinstance(message, str) and message:
            commands.append(set_text(status_component, message))

    return commands


def _display_command_items(display: Mapping) -> list[Mapping]:
    raw_commands = display.get("commands")
    if not isinstance(raw_commands, list):
        return []
    return [item for item in raw_commands if isinstance(item, Mapping)]


def _encode_display_command(command: Mapping) -> bytes | None:
    cmd = command.get("cmd")
    if cmd == "set_text":
        component = command.get("component")
        value = command.get("value")
        if isinstance(component, str):
            return set_text(component, "" if value is None else str(value))
    if cmd == "set_number":
        component = command.get("component")
        value = command.get("value")
        if isinstance(component, str) and isinstance(value, int | float):
            return set_number(component, value)
    if cmd == "goto_page":
        page = command.get("page")
        if isinstance(page, str):
            return goto_page(page)
    return None


__all__ = [
    "DEFAULT_STATUS_COMPONENT",
    "display_response_to_commands",
]

from __future__ import annotations

from pathlib import Path

from shared.diagnostics import error_codes as shared_error_codes


APP_SPECIFIC_SHARED_NAMES = (
    "ROUTE_NOT_FOUND",
    "TASK_WAITING_LOAD_CONFIRMATION",
    "TASK_WAITING_UNLOAD_CONFIRMATION",
    "DISPATCHER_NO_AVAILABLE_ROBOT",
    "HMI_ACTION_RECEIVED",
    "TJC_SERIAL_PORT_MISSING",
    "COMMISSIONING_ROUTE_READY",
    "AUDIO_PLAYBACK_FAILED",
)


def test_shared_error_codes_do_not_expose_app_specific_constants() -> None:
    for name in APP_SPECIFIC_SHARED_NAMES:
        assert not hasattr(shared_error_codes, name)


def test_logistics_error_codes_expose_moved_constants() -> None:
    from apps.logistics.diagnostics import error_codes as logistics_error_codes

    assert logistics_error_codes.ROUTE_NOT_FOUND == "route.not_found"
    assert logistics_error_codes.TASK_WAITING_LOAD_CONFIRMATION == "task.waiting_load_confirmation"
    assert logistics_error_codes.DISPATCHER_NO_AVAILABLE_ROBOT == "dispatcher.no_available_robot"
    assert logistics_error_codes.HMI_TOKEN_INVALID == "hmi.token_invalid"
    assert logistics_error_codes.AUDIO_FILE_MISSING == "audio.file_missing"


def test_shared_suggested_actions_are_platform_only() -> None:
    assert shared_error_codes.get_suggested_action(shared_error_codes.SDK_CONNECT_FAILED)
    assert shared_error_codes.get_suggested_action(shared_error_codes.LIDAR_SCAN_TIMEOUT)
    assert shared_error_codes.get_suggested_action(shared_error_codes.CONFIG_PLACEHOLDER_TOKEN)
    assert shared_error_codes.get_suggested_action("route.not_found") is None


def test_logistics_suggested_actions_cover_workflow_codes() -> None:
    from apps.logistics.diagnostics import error_codes as logistics_error_codes

    assert logistics_error_codes.get_suggested_action(logistics_error_codes.ROUTE_NOT_FOUND)
    assert logistics_error_codes.get_suggested_action(logistics_error_codes.ROUTE_PLACEHOLDER_BLOCKED)
    assert logistics_error_codes.get_suggested_action(logistics_error_codes.HMI_TOKEN_INVALID)
    assert logistics_error_codes.get_suggested_action(logistics_error_codes.AUDIO_FILE_MISSING)
    assert logistics_error_codes.get_suggested_action("sdk.connect_failed") is None


def test_shared_diagnostics_has_no_apps_dependency() -> None:
    shared_diagnostics = Path(__file__).resolve().parents[1] / "shared" / "diagnostics"

    for path in shared_diagnostics.rglob("*.py"):
        content = path.read_text(encoding="utf-8")
        assert "from apps" not in content
        assert "import apps" not in content

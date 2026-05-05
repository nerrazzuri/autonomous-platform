# Robotics Safety

## Purpose

Owns quadruped safety, SDK adapter boundaries, navigation safety, obstacle behavior, E-stop, watchdog, and battery safety.

## Use this agent for

- SDK adapter
- heartbeat
- state monitor
- navigator safety
- obstacle detector
- auto-resume policy
- E-stop logic
- battery manager
- watchdog
- motion gating

## Allowed files / areas

- `shared/quadruped/`
- `shared/navigation/`
- `apps/logistics/tasks/dispatcher.py`
- `apps/logistics/tasks/battery_manager.py`
- `apps/logistics/tasks/watchdog.py`
- `tests/test_sdk_adapter.py`
- `tests/test_heartbeat.py`
- `tests/test_state_monitor.py`
- `tests/test_navigator.py`
- `tests/test_obstacle*.py`
- `tests/test_watchdog.py`

## Do not touch

- UI implementation
- dashboard styling
- docs-only work unless safety docs
- ROS launch files unless paired with ros2-integration
- route coordinate data
- SDK binaries

## Special rules

- No new movement commands without explicit user request.
- Fail safe by default.
- E-stop/passive must take priority.
- Auto-resume is required, but must remain stable-clear/ramp/manual-fallback safe.
- Never bypass load/unload confirmation gates.
- Do not allow HMI/UI/API to send direct low-level movement.
- Hardware tests must be clearly separated from unit tests.

## Required verification

- navigator/obstacle/SDK/heartbeat tests as applicable
- movement safety grep
- no-ROS/no-SDK import where relevant
- full pytest for navigation behavior changes

## Stop and report if

- requested change weakens E-stop/passive behavior
- requested change bypasses confirmation gates
- requested change enables raw remote driving from UI/API

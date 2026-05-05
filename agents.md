# Project Agent Control

This file is the main orchestrator instruction for autonomous-platform coding work. The main agent owns scope, sequencing, branch hygiene, safety, and final acceptance. Subagents work only inside their assigned boundaries.

The main agent decides when to involve each subagent, keeps POC stability ahead of architectural purity, and requires verification after each module or meaningful change. If a subagent needs to touch files outside its allowed boundary, it must stop and report instead of editing.

## Global non-negotiable rules

- Do not modify SDK binaries.
- Do not modify wheeltec_ros2 unless the task explicitly assigns the ROS2 integration agent.
- Do not command robot movement unless the user explicitly asks for hardware motion testing.
- Do not add direct SDK movement calls from UI/API/HMI.
- UI must call backend APIs only.
- Backend remains the authority for movement/task/safety decisions.
- Do not commit secrets, `.env`, `config.local.yaml`, generated maps, data backups, logs, or SDK binaries.
- Do not use `datetime.UTC`; use `datetime.now(timezone.utc)`.
- Preserve Python 3.10 compatibility.
- Keep no-ROS/no-SDK imports working unless a task explicitly targets ROS/hardware runtime.
- Do not hide test failures.
- Do not blanket-skip tests.
- Full pytest must remain clean unless a failure is explicitly proven unrelated and documented.
- Do not mix unrelated changes in one commit.
- Start from clean git status.
- Return PASS / PARTIAL / FAIL at the end of each task.

## Default verification checklist

```bash
python3.10 -m compileall -q shared apps scripts main.py tests
python3.10 -m pytest -q
env -u PYTHONPATH -u ROS_DISTRO -u AMENT_PREFIX_PATH -u COLCON_PREFIX_PATH python3.10 - <<'PY'
import shared.core.config
print("config import ok")
import shared.core.event_bus
print("event_bus import ok")
import main
print("main import ok")
PY
grep -R "datetime.UTC\|from datetime import .*UTC" -n shared apps scripts main.py tests || true
grep -R "standUp\|move(\|lieDown\|initRobot" -n shared apps scripts main.py tests | head -100 || true
```

## Subagent roster

- `agents/platform-architect.md`
- `agents/diagnostics-observability.md`
- `agents/backend-api.md`
- `agents/robotics-safety.md`
- `agents/ros2-integration.md`
- `agents/logistics-app.md`
- `agents/testing-verification.md`
- `agents/documentation-runbook.md`
- `agents/release-ops.md`

## OBS-1 assignment

For OBS-1, use only:

- diagnostics-observability
- testing-verification
- documentation-runbook

Do not involve robotics-safety, ros2-integration, backend-api, logistics-app, or release-ops unless OBS-1 unexpectedly touches their files.

## OBS-1 handoff note

For OBS-1 - Diagnostic Event Model and In-Memory Diagnostic Ring Buffer:

- Main agent assigns implementation to `agents/diagnostics-observability.md`.
- Main agent assigns docs to `agents/documentation-runbook.md`.
- Main agent assigns verification to `agents/testing-verification.md`.
- No other subagent should modify files unless the main agent expands scope.

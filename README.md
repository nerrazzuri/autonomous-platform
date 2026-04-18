# Autonomous Logistic

Local-first foundation for a future indoor factory transport system using an Agibot D1 EDU or similar quadruped robot.

This phase builds only the lower-layer framework. It avoids final path planning, final operator workflow, hardcoded screen behavior, speaker behavior, and fixed sensor assumptions.

## Architecture

The application is organized around replaceable lower-layer boundaries:

- `core/` contains domain models, statuses, capabilities, and typed errors.
- `state/` contains the task state machine and SQLite repositories.
- `adapters/` contains robot control interfaces and the Agibot D1 SDK integration seam.
- `simulation_or_mock/` contains the runnable fake robot adapter.
- `services/` contains task and system orchestration logic.
- `api/` contains the local FastAPI service for future HMI, button panel, or central PC integration.
- `logging/` contains structured audit event recording.
- `config/` contains local-only runtime settings.

Business logic depends on adapter interfaces, not on D1-specific SDK calls. The real Agibot adapter is intentionally a seam until the vendor SDK package is installed and its Python import names are confirmed.

## Setup

Use Python 3.10 or newer.

Install the project in editable mode before running the service. This makes the `src` package importable without setting `PYTHONPATH` manually.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[test]"
```

The default runtime uses mock mode and SQLite:

```powershell
python -m uvicorn autonomous_logistic.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

For a no-install local check, set `PYTHONPATH` to `src` before running the same command:

```powershell
$env:PYTHONPATH='src'
python -m uvicorn autonomous_logistic.api.app:create_app --factory --host 127.0.0.1 --port 8000
```

Check health from the same machine:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
```

## Configuration

Local defaults live in `config/app.local.json`.

Important values:

- `app_mode`: default `mock`.
- `db_path`: SQLite database path.
- `robot.adapter`: `mock` or future `agibot_d1`.
- `robot.robot_ip`, `robot.client_ip`, `robot.sdk_port`: network values for future SDK control.
- `capabilities`: feature flags for LiDAR, speaker, screen, touch input, button panel, remote dispatch, and local HMI.

Environment variables prefixed with `AL_` can override local config, for example:

```powershell
$env:AL_HAS_SCREEN="true"
$env:AL_ROBOT_ADAPTER="mock"
```

## API Surface

The REST API is internal-facing and local/LAN oriented:

- `POST /tasks`
- `GET /tasks`
- `GET /tasks/{task_id}`
- `POST /tasks/{task_id}/cancel`
- `POST /tasks/{task_id}/pause`
- `POST /tasks/{task_id}/resume`
- `GET /stations`
- `GET /health`
- `GET /capabilities`

Example task creation:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/tasks -Method Post -ContentType "application/json" -Body '{
  "source_point": "STATION_A",
  "destination_point": "STATION_B",
  "requested_by": "operator-1",
  "request_source": "remote_dispatch",
  "notes": "deliver parts"
}'
```

## Mock Mode

Mock mode is mandatory and enabled by default. It allows the API, task model, state machine, audit log, and station listing to run without the real robot or SDK.

The mock adapter reports deterministic health and sensor status and accepts movement/navigation commands without performing real path planning.

## Future HMI Integration

A future screen, touch panel, button panel, or central PC should integrate through the API and capability flags. Upper-layer HMI code should check `/capabilities` before assuming local touch input, screen availability, speaker output, or remote dispatch support.

Operator interactions should produce structured audit events instead of hardcoding screen-specific workflows into the task service.

## Agibot D1 SDK Notes

The SDK guide indicates the application should run as an external LAN service rather than placing business logic on the robot. The first real adapter should use the high-level SDK interface and keep IP, client IP, and SDK port config-driven.

High-level and low-level SDK control should not be mixed. Low-level motor control is outside this foundation phase.

## Verification

Run the automated tests:

```powershell
python -m pytest
```

Run the service locally and query health:

```powershell
python -m uvicorn autonomous_logistic.api.app:create_app --factory --host 127.0.0.1 --port 8000
Invoke-RestMethod http://127.0.0.1:8000/health
```

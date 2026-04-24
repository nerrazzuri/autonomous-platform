# Phase 1 Runbook

## Overview

Phase 1 provides a backend skeleton and static browser UIs for the Sumitomo Quadruped Factory Logistics project. The current system can load configuration, initialize the database, manage queued transport tasks, expose REST and WebSocket interfaces, serve static operator and supervisor pages, and run safely in a development environment without real quadruped hardware.

Several modules are intentionally stubbed or no-op in Phase 1. The hardware relay, video reader, QR anchor localization, MES bridge, obstacle detector integrations, and most physical quadruped interactions are contract-first implementations that validate inputs and maintain safe internal state but do not drive real hardware. These modules are present so the rest of the system can be wired and tested now.

Real hardware validation is still required later for the Agibot quadruped connection, physical e-stop procedures, route commissioning in the factory, station alert lights and buzzers, camera/video flow, QR marker correction, and any production-grade obstacle sensing.

## Project Structure

- `core/`: shared foundations such as configuration loading, logging, event bus, and SQLite persistence.
- `quadruped/`: SDK adapter contract, heartbeat loop, and telemetry/state monitoring.
- `navigation/`: route and station storage, navigator logic, SLAM placeholder, and obstacle detector placeholder.
- `tasks/`: queue lifecycle, dispatcher, battery handling, and watchdog coordination.
- `api/`: FastAPI REST endpoints, auth helpers, WebSocket broker, and alert manager.
- `ui/`: static browser UIs including operator, supervisor, kiosk, and the reusable floor-map script.
- `hardware/`: Phase 1 hardware contracts for GPIO alerts, video, QR anchor correction, and MES bridge.
- `data/`: runtime database plus route and station definition files.
- `tests/`: unit, API, UI, hardware-stub, and integration smoke tests.

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create the local config and commissioning data from the example files:

```bash
cp config.yaml.example config.yaml
cp data/routes.json.example data/routes.json
cp data/stations.json.example data/stations.json
```

Edit `config.yaml` before running the backend. At minimum, review:

- `auth.operator_token`
- `auth.qa_token`
- `auth.supervisor_token`
- `quadruped.quadruped_ip`
- `quadruped.sdk_port`
- `workstation.local_ip`
- `database.sqlite_path`

## Run Tests

Run the full test suite with:

```bash
.venv/bin/python -m pytest -q
```

Current Phase 1 baseline: `362` tests passing. If your result is lower, treat that as a setup or regression signal before proceeding with manual smoke checks.

## Start Backend

Start the backend with:

```bash
.venv/bin/python main.py
```

Default API binding comes from `config.yaml` and is `0.0.0.0:8080` in the example config.

Useful endpoints and UI URLs:

- Health check: `http://localhost:8080/health`
- Operator UI: `http://localhost:8080/ui/operator.html?station_id=A&token=<operator-token>`
- Supervisor UI: `http://localhost:8080/ui/supervisor.html?token=<supervisor-token>`
- Kiosk UI: `http://localhost:8080/ui/kiosk.html?station_id=A&token=<operator-token>`

## Config Notes

- `quadruped.quadruped_ip`: target IP address for the quadruped SDK endpoint. This is only meaningful once real hardware validation starts.
- `workstation.local_ip`: bind address for the workstation-side backend process.
- `quadruped.sdk_port`: quadruped SDK port used by the adapter layer.
- `database.sqlite_path`: SQLite file path for runtime task, telemetry, event, and route persistence.
- `auth.*_token`: static bearer tokens for operator, QA, and supervisor access. These are acceptable only for isolated Phase 1 LAN use.
- `battery.warn_pct`, `battery.critical_pct`, `battery.resume_pct`: queueing and alert thresholds used by battery-related orchestration.
- `routes.routes_file`: JSON file containing route definitions.
- `routes.stations_file`: JSON file containing station definitions.

## Route Commissioning

Phase 1 uses file-based commissioning data.

1. Edit `data/routes.json` to define allowed station-to-station transport paths.
2. Edit `data/stations.json` to define station IDs, names, types, and placeholder positions.
3. Use `data/routes.json.example` and `data/stations.json.example` as templates.

Coordinates in these files are Phase 1 odometry and factory-frame placeholders. They are suitable for skeleton wiring and UI visualization, but they are not yet validated against a commissioned factory map or real localization stack.

## Safety Notes

- Phase 1 must be supervised during any live hardware trial.
- Do not use Phase 1 for autonomous unsupervised production operation.
- The obstacle detector is currently a null/stub implementation.
- SLAM is odometry fallback only in Phase 1.
- GPIO relay, video reader, QR anchor, and MES bridge are Phase 1 stubs.
- The software e-stop path may move the adapter into a passive or stopped state, but it must not replace physical emergency procedures, physical interlocks, or site safety controls.

## Smoke Test Checklist

Use this checklist after setup:

1. Start the backend and confirm it stays up without hardware attached.
2. Open `/health` and confirm it returns `{"status":"ok", ...}`.
3. Load `/ui/operator.html`, `/ui/supervisor.html`, and `/ui/kiosk.html`.
4. Confirm the UI connection indicator shows an active WebSocket session after passing a valid token.
5. Submit a task from the operator or kiosk UI and confirm the request succeeds.
6. Verify queue status updates are visible in the UI after task submission.
7. Trigger the supervisor e-stop and confirm the endpoint responds without crashing the backend.
8. Confirm the route list loads in the supervisor dashboard.
9. Run `.venv/bin/python -m pytest -q` and confirm the integration tests pass along with the rest of the suite.

## Known Limitations

- Confirm-load and confirm-unload backend endpoints are not implemented yet.
- Real Agibot SDK and hardware validation are still pending.
- Route accuracy is still pending factory commissioning.
- There is no real obstacle detection in Phase 1.
- There is no real camera, QR, GPIO, or MES integration in Phase 1.
- Static-token authentication is suitable only for isolated LAN use during Phase 1.

## Next Development Steps

- Add confirm-load and confirm-unload REST endpoints.
- Run a live hardware connection test with the real quadruped.
- Commission and validate the route and station files against the factory floor.
- Perform a full manual operator flow test from request through completion.
- Run a supervisor dashboard live soak test with telemetry and alerts.
- Implement Phase 2 hardware integrations.

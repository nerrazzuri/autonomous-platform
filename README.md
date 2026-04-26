# Agibot D1 EDU Autonomous Platform

This repository is a reusable autonomous platform for the Agibot D1 EDU, organized so shared robot capabilities can support multiple mission applications.

## Architecture

- `shared/` contains the reusable platform layer: configuration, persistence, REST and WebSocket plumbing, navigation, quadruped integration, hardware contracts, and runtime startup helpers.
- `apps/logistics/` is the current mission app. It packages the Phase 1 logistics workflow, app-specific runtime wiring, documentation, smoke script, and browser UIs.
- `apps/patrol/` is a placeholder for a future mission app that will reuse the shared platform modules but does not yet include runtime behavior.

## Current Phase 1 Capabilities

Phase 1 is aimed at supervised development and integration testing. Today the repository can:

- load configuration and route/station definitions
- initialize the SQLite-backed task and event data layer
- expose FastAPI REST endpoints and a WebSocket stream
- manage a logistics task queue and dispatcher flow
- serve the logistics operator, kiosk, and supervisor UIs
- run a manual HTTP smoke workflow without requiring live quadruped hardware

## Mocked And Stubbed Components

Several integrations are intentionally mocked, stubbed, or safe no-ops in Phase 1:

- most physical quadruped behavior can run in mock mode when the vendor SDK is unavailable
- obstacle detection is a null/stub implementation
- SLAM currently falls back to odometry-oriented placeholder behavior
- GPIO relay, video reader, QR anchor, and MES bridge modules are contract-first stubs
- route and station coordinates are still development/commissioning placeholders

## Safety Warning

Phase 1 is not a production autonomy release. The software e-stop may move the adapter into a passive or stopped state, but software e-stop is not physical safety and does not replace plant emergency procedures, interlocks, spotters, or other physical safeguards.

## Setup

Create a virtual environment and install dependencies:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create local runtime configuration from the examples:

```bash
cp config.yaml.example config.yaml
cp data/routes.json.example data/routes.json
cp data/stations.json.example data/stations.json
```

## Common Commands

Test suite:

```bash
.venv/bin/python -m pytest -q
```

Runtime:

```bash
.venv/bin/python main.py
```

Manual smoke:

```bash
./apps/logistics/scripts/manual_e2e_smoke.sh
```

## Mission Docs

- Root docs index: `docs/README.md`
- Logistics runbook: `apps/logistics/docs/phase1_runbook.md`
- Logistics deployment checklist: `apps/logistics/docs/deployment_checklist.md`

## Current Limitations

- `apps/logistics` is the only implemented mission app today
- `apps/patrol` is documentation-only placeholder content
- confirm-load and confirm-unload flows are still not implemented in the logistics backend
- there is no real obstacle detection, camera pipeline, QR localization, GPIO actuation, or MES integration in Phase 1
- live hardware validation, route commissioning, and production safety review are still required
- static token auth is only suitable for isolated Phase 1 LAN usage

## Next Milestones

- validate the Agibot D1 EDU against the real vendor SDK and hardware
- commission routes and station coordinates on the target floor
- expand mission-app coverage beyond logistics, starting with patrol
- replace Phase 1 stubs with real hardware integrations
- harden safety, operations, and deployment workflows for later phases

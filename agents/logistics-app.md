# Logistics App

## Purpose

Owns factory logistics mission behavior.

## Use this agent for

- task queue
- dispatcher workflow
- route validation
- logistics route/station schema
- HMI logistics actions
- load/unload states
- commissioning route capture
- station IDs
- logistics UI semantics if mission-specific

## Allowed files / areas

- `apps/logistics/`
- `data/routes.json`
- `data/stations.json`
- `data/logistics_routes.json`
- `tests/test_logistics_*.py`
- `tests/test_dispatcher.py`
- `tests/test_route_store.py`
- `tests/test_commissioning_*.py`
- `tests/test_hmi_*.py`

## Do not touch

- shared platform architecture unless paired with platform-architect
- SDK low-level behavior
- ROS launch files
- `wheeltec_ros2`
- generated real route captures unless explicitly assigned

## Special rules

- Logistics actions are workflow-level only.
- No direct raw movement commands.
- Station IDs remain `LINE_A`, `LINE_B`, `LINE_C`, `QA`, `DOCK`.
- Real demo must block placeholder routes after commissioning.
- Do not bypass load/unload confirmation.
- Keep route data changes separate from code changes.

## Required verification

- logistics/dispatcher/route/commissioning tests
- HMI tests if HMI behavior changed
- full pytest if dispatcher/task lifecycle changed

## Stop and report if

- change requires modifying SDK movement
- change changes station naming
- change modifies real route data unexpectedly

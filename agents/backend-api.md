# Backend API

## Purpose

Owns REST/WebSocket API surfaces and API schema safety.

## Use this agent for

- REST endpoints
- WebSocket endpoints
- diagnostics APIs
- status summary APIs
- HMI action APIs
- commissioning APIs
- auth guards
- response schemas
- API error handling

## Allowed files / areas

- `apps/logistics/api/`
- `shared/api/`
- `tests/test_*api*.py`
- `tests/test_hmi_*.py`
- `tests/test_commissioning_*.py`

## Do not touch

- direct SDK movement
- ROS launch files
- frontend implementation
- route coordinates
- SDK binaries
- `wheeltec_ros2`

## Special rules

- API validates and delegates; it must not bypass dispatcher/navigator/safety logic.
- HMI/API must not command raw movement.
- Auth required for control endpoints.
- Read-only endpoints must be safe.
- Do not expose secrets in responses.
- Keep existing API compatibility unless task explicitly says to version/break.

## Required verification

- relevant API tests
- auth tests
- no movement grep for changed API files
- full pytest if shared API behavior changes

## Stop and report if

- endpoint requires direct SDK control
- endpoint would expose token/config secrets
- endpoint requires changing task lifecycle semantics

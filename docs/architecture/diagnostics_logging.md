# Diagnostics Logging Architecture

This document defines the shared diagnostics logging contract for developers and operators. The shared diagnostics package is reusable platform infrastructure: it records compact diagnostic events and routes structured logs for troubleshooting without changing robot motion, ROS behavior, SDK behavior, API behavior, or UI behavior.

## Purpose

- Provide a shared `DiagnosticEvent` shape for module health and fault reporting.
- Keep diagnostic reporting separate from normal application logs.
- Store recent diagnostic events in memory for later inspection by future tools.
- Give future modules a consistent severity, platform error-code, redaction, and suggested-action vocabulary.
- Fail quietly if diagnostics reporting itself fails; diagnostics must not interrupt robot operation.
- Keep shared diagnostics focused on mechanisms. App-specific diagnostic meaning belongs under app packages such as `apps/logistics/diagnostics/`.

## Signal Types

Use these streams for different jobs:

- Normal application logs: developer-facing log lines emitted through the project logger. Use them for routine execution details, stack traces, and debugging context.
- Diagnostic events: structured, JSONL-compatible records for notable health, safety, configuration, dependency, network, runtime, or operator-action conditions. Use them when another module or operator may need to diagnose state.
- Module logs: per-module JSONL files created by the OBS-2 logging router. OBS-2 provides the file routing foundation, while later phases may add broader module instrumentation.
- Future dashboard status: summarized health indicators may be shown in UI later. OBS-1 does not add dashboard widgets or status polling.

## DiagnosticEvent Schema

Each diagnostic event must be JSON-serializable and safe to expose after redaction.

Required fields:

- `event_id`: unique event identifier string.
- `ts`: UTC ISO-8601 timestamp string.
- `module`: stable module name, for example `sdk_adapter` or `custom_app_module`.
- `event`: stable dotted or snake_case event name.
- `severity`: one of `debug`, `info`, `warning`, `error`, `critical`.
- `error_code`: stable string error code, or `null` when the event is informational.
- `message`: short human-readable summary.

Optional fields:

- `subsystem`: subsystem label when finer grouping is useful.
- `robot_id`: robot identifier when the event is robot-specific.
- `task_id`: task identifier when the event is task-specific.
- `route_id`: route identifier when the event is route-specific.
- `station_id`: station identifier when the event is station-specific.
- `waypoint_id`: waypoint identifier when the event is waypoint-specific.
- `correlation_id`: request, cycle, job, or trace identifier used to group related events.
- `source`: component or process that emitted the event.
- `details`: small redacted dictionary with structured context.
- `suggested_action`: concise next diagnostic step for an operator or developer.

The optional `task_id`, `route_id`, `station_id`, and `waypoint_id` fields are generic correlation/context fields. They are not a reason to add more app-specific schema to the shared `DiagnosticEvent`; app-specific diagnostic meaning and error-code constants should live under the app package.

Do not put raw sensor data, map files, SDK payload dumps, authorization headers, tokens, passwords, private keys, attendee data, or full local config files in diagnostic events.

## Severity Levels

- `debug`: developer-only diagnostic detail. Do not use for operator-facing problems.
- `info`: expected state transition or successful health check.
- `warning`: degraded condition or recoverable issue that may need attention.
- `error`: failed operation, invalid configuration, unavailable dependency, or condition that blocks the current workflow.
- `critical`: condition requiring immediate attention, such as loss of required telemetry or a system safety state. A critical diagnostic event must not command movement.

Use the lowest severity that accurately describes the impact. Escalate only when the condition blocks operation, hides safety state, or requires operator intervention.

## Error Code Categories

Use stable string codes. The shared taxonomy exposes uppercase Python constants such as `SDK_CONNECT_FAILED` whose values are JSON-friendly strings such as `sdk.connect_failed`. Shared error codes are limited to platform infrastructure, robotics, startup, configuration, and generic navigation/obstacle conditions:

- `config.*`: missing, invalid, or placeholder configuration.
- `network.*`: unavailable host, timeout, disconnected client, or broken transport.
- `sdk.*`: SDK initialization, session, command, or dependency issue.
- `ros2.*`, `lidar.*`, `odom.*`, `tf.*`, `localization.*`, `map.*`: ROS, LiDAR, transform, localization, and map issues.
- `navigation.*`: generic navigation lifecycle issues.
- `obstacle.*`: obstacle detection, clear, and auto-resume conditions.

App-specific taxonomies belong under app packages. For example, logistics workflow codes such as `route.*`, `task.*`, `dispatcher.*`, `hmi.*`, `tjc.*`, `commissioning.*`, and `audio.*` live under `apps.logistics.diagnostics.error_codes`, not in `shared.diagnostics`.

Use `null` for `error_code` when an event is normal status rather than a fault.

## Redaction Rules

Before storing or forwarding a diagnostic event:

- Replace token, password, secret, credential, API key, bearer token, private key, and authorization values with `[REDACTED]`.
- Redact secrets in nested details dictionaries and lists.
- Do not include full local config files, `.env` content, private keys, robot passwords, SDK credentials, or raw logs that may contain credentials.
- Use placeholders such as `<robot-id>`, `<task-id>`, `<operator-id>`, `<host>`, and `<token>` in examples.
- Keep details small and diagnostic. Avoid raw sensor frames, raw ROS messages, image data, map data, or large payload dumps.

## Example Event

This example uses fake placeholder values only:

```json
{
  "event_id": "diag-00000000-0000-4000-8000-000000000000",
  "ts": "2026-01-01T00:00:00+00:00",
  "module": "sdk_adapter",
  "event": "telemetry_stale",
  "severity": "warning",
  "error_code": "sdk.telemetry_stale",
  "message": "Robot telemetry has not updated within the configured window.",
  "subsystem": "sdk",
  "robot_id": "<robot-id>",
  "task_id": "<task-id>",
  "route_id": "<route-id>",
  "station_id": null,
  "waypoint_id": null,
  "correlation_id": "<correlation-id>",
  "source": "sdk_adapter",
  "suggested_action": "Check robot connection status and confirm telemetry resumes before continuing operations.",
  "details": {
    "age_seconds": 6.5,
    "threshold_seconds": 5.0,
    "host": "<host>",
    "authorization": "[REDACTED]"
  }
}
```

## Future Module Use

When adding diagnostics to a module:

1. Emit a diagnostic event only for meaningful health, fault, lifecycle, or operator-action conditions.
2. Keep normal debug traces in application logs.
3. Use a stable `module`, `event`, and `error_code`; do not generate new names from exception text.
4. Include `suggested_action` for `warning`, `error`, and `critical` events.
5. Keep `details` compact, JSON-safe, and redacted before publication.
6. Do not let diagnostics publication raise into robot control, task execution, ROS integration, SDK calls, or API request handling.
7. Add replayable or no-hardware tests when diagnostics behavior becomes part of a module contract.

## OBS-2 Logging Router

OBS-2 adds a standalone structured logging router under `shared.diagnostics.logging_router`. It is a foundation for future module instrumentation; it does not change existing runtime logging by itself.

Default layout:

```text
logs/
  app.log
  app.jsonl
  modules/
    <module>.jsonl
```

The router creates `logs/` and `logs/modules/`, writes a human-readable master log to `app.log`, writes a structured master stream to `app.jsonl`, and routes each structured record to a module-specific JSONL file. Unknown module names are sanitized before becoming filenames, so module strings cannot create paths outside `logs/modules/`.

Example module files:

- `sdk_adapter.jsonl`
- `ros2_bridge.jsonl`
- `navigation.jsonl`
- `custom_app_module.jsonl`

Use:

```python
from shared.diagnostics.logging_router import configure_diagnostics_logging, get_diagnostic_logger

configure_diagnostics_logging(log_dir="logs")
logger = get_diagnostic_logger("sdk_adapter")
logger.info(
    "SDK connection established",
    extra={
        "event": "sdk.connected",
        "robot_id": "robot_01",
        "details": {"attempt": 1},
    },
)
```

Structured records include:

- `ts`
- `level`
- `module`
- `event`
- `message`
- `robot_id`
- `task_id`
- `route_id`
- `error_code`
- `correlation_id`
- `details`

Details and extra fields are passed through the OBS-1 redaction helper before writing. Repeated configuration closes old router-owned handlers before installing new ones, and `shutdown_diagnostics_logging()` is safe to call multiple times. Rotation uses Python `RotatingFileHandler` with configurable byte and backup limits.

## Scope Limits

OBS-1 defines the diagnostic event model and in-memory diagnostic ring buffer.
OBS-2 defines the standalone logging router and module log files.

These phases do not implement:

- Diagnostics REST API.
- Heavy module-by-module instrumentation.
- Dashboard status panels or dashboard status polling.
- ROS process log capture.
- Diagnostic bundle generation.
- Log digest CLI.
- Production readiness, certification, or field accuracy guarantees.

Future agents must treat those items as separate phases with their own acceptance checks.

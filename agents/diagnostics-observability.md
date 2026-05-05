# Diagnostics Observability

## Purpose

Owns diagnostics, logging, observability, error taxonomy, status summaries, and log digest tooling.

## Use this agent for

- DiagnosticEvent model
- DiagnosticSeverity
- diagnostic error codes
- suggested actions
- redaction
- diagnostic ring buffer
- module log routing
- diagnostics reporter
- diagnostics REST API design
- status summary
- diagnostic bundle
- log digest CLI
- structured logging improvements

## Allowed files / areas

- `shared/diagnostics/`
- `shared/core/logger.py`
- `shared/core/config.py`
- `apps/logistics/api/diagnostics.py`
- `scripts/diagnostics/`
- `docs/architecture/diagnostics_logging.md`
- `tests/test_diagnostics_*.py`
- `tests/test_logger.py`
- `tests/test_logging.py`

## Do not touch

- `wheeltec_ros2`
- SDK binaries
- direct robot movement behavior
- ROS launch files
- HMI/task business logic unless only adding diagnostics
- web dashboard UI unless explicitly assigned

## Special rules

- Diagnostics must be non-invasive.
- Logging failure must not crash robot operation unless explicitly configured.
- Always redact tokens, secrets, passwords, API keys, Authorization/Bearer values, credentials, and private keys.
- Prefer JSONL-compatible structures.
- Use `error_code` and `suggested_action` for operator diagnosis.
- Keep no-ROS/no-SDK imports working.
- Do not log raw secrets or full local config files.

## Required verification

- diagnostics tests
- logger/logging tests if logger changed
- no-ROS/no-SDK import
- secret safety grep
- full pytest if `shared/core/logger.py` or `shared/core/config.py` changed

## Stop and report if

- diagnostics requires direct movement behavior changes
- diagnostics requires ROS runtime dependency at import time
- requested log capture could expose secrets

# Status Summary

OBS-4 adds an app-agnostic status summary foundation for the autonomous platform.
It is a read-only snapshot mechanism for platform health, diagnostics, alerts,
and app-owned extensions.

The shared status summary owns mechanism only. It may report generic platform
signals such as registered robots, SDK connectivity as seen through existing
telemetry state, battery level, heartbeat status, recent diagnostic counts, alert
counts, uptime, and registered extension output. It must not encode app workflow
meaning such as station workflows, confirmation steps, inspection checkpoints, or
customer-specific names.

## Shape

`build_status_summary()` returns a JSON-safe dictionary with these top-level
keys:

- `status`: `ok`, `degraded`, or `error`.
- `ts`: current UTC ISO timestamp.
- `platform`: uptime, version placeholder, and configured app name.
- `robots`: robot summaries keyed by robot ID.
- `diagnostics`: recent diagnostic event counts and the latest compact error.
- `alerts`: active alert count and latest compact alert.
- `extensions`: app-owned status provider output keyed by provider name.

App-specific data belongs under `extensions`. Shared code does not import app
providers; apps register their providers at startup.

## Provider Registry

Shared exposes:

- `register_status_provider(name, provider)`
- `unregister_status_provider(name)`
- `clear_status_providers()`
- `get_registered_status_providers()`
- `build_status_summary()`

Provider names must be simple strings containing letters, numbers, dot,
underscore, or dash. Registering the same name again replaces the previous
provider, which keeps app startup registration idempotent.

Providers must return a mapping. Their output is redacted and converted to
JSON-safe values. If a provider fails, the status endpoint still returns a
summary and reports that extension as:

```json
{
  "status": "error",
  "error": "provider_failed"
}
```

## REST Endpoint

The app FastAPI surfaces expose:

```text
GET /status/summary
```

The route is protected by the existing supervisor auth dependency. The API route
does not compute app workflow state itself; it delegates to
`build_status_summary()`.

## Scope Limits

OBS-4 does not add a dashboard, diagnostics bundle, ROS process log capture, or
heavy module instrumentation. It does not change robot movement behavior or
startup behavior beyond registering safe app-owned status extension callbacks.

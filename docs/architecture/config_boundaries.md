# Config Boundaries

`shared/core/config.py` owns reusable platform configuration: runtime mode, robot
connection basics, database, logging, auth, API, ROS2/SDK, and other shared
mechanism settings.

App workflow settings belong with the app package. Current app-owned entry
points are:

- `apps.logistics.config`
- `apps.patrol.config`

Those modules expose helper functions that read the current compatibility
sections from `AppConfig`:

- `get_logistics_config(config)`
- `get_patrol_config(config)`

`AppConfig.logistics`, `AppConfig.patrol`, and `AppConfig.logistics_routes_path`
remain available only for backward compatibility with existing POC config files
and runtime code. New app-specific config should not be added to
`shared/core/config.py`.

Post-POC target: replace the compatibility sections with an app config registry
or app startup composition layer, so new apps can add settings without editing
shared platform code.

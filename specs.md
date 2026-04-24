Let me read the full current technical proposal and SDK details carefully before designing the module plan.Good. Now I have everything I need. Let me design a fully modular app plan that is honest about what can be built now vs. what waits for hardware.Good. Now I'll draw the module map first, then write the full plan.Here is the complete modular application plan. Everything is written so your coding agent has a precise brief for each module — what it does, what it exposes, what it depends on, and exactly which parts to stub out now versus implement later when hardware arrives.

---

## Project structure

```
sumitomo-robot/
├── core/
│   ├── config.py          # MOD-00
│   ├── logger.py          # MOD-01
│   ├── event_bus.py       # MOD-02
│   └── database.py        # MOD-03
├── robot/
│   ├── sdk_adapter.py     # MOD-10
│   ├── state_monitor.py   # MOD-11
│   └── heartbeat.py       # MOD-12
├── navigation/
│   ├── route_store.py     # MOD-20
│   ├── navigator.py       # MOD-21
│   ├── obstacle.py        # MOD-22  (stub Phase 1)
│   └── slam.py            # MOD-23  (stub Phase 2)
├── tasks/
│   ├── queue.py           # MOD-30
│   ├── dispatcher.py      # MOD-31
│   ├── battery_manager.py # MOD-32
│   └── watchdog.py        # MOD-33
├── api/
│   ├── rest.py            # MOD-40
│   ├── ws_broker.py       # MOD-41
│   ├── auth.py            # MOD-42
│   └── alerts.py          # MOD-43
├── ui/                    # static files served by MOD-40
│   ├── operator.html      # MOD-50
│   ├── supervisor.html    # MOD-51
│   ├── floormap.js        # MOD-52  (stub Phase 1)
│   └── kiosk.html         # MOD-53  (stub Phase 2)
├── hardware/
│   ├── gpio_relay.py      # MOD-60  (stub Phase 1)
│   ├── video_reader.py    # MOD-61  (stub Phase 1)
│   ├── qr_anchor.py       # MOD-62  (stub Phase 1)
│   └── mes_bridge.py      # MOD-63  (stub Phase 2)
├── data/
│   ├── routes.json        # Route definitions (commissioning file)
│   ├── stations.json      # Station registry
│   └── robot.db           # SQLite runtime database
├── tests/
│   └── ...                # One test file per module
├── main.py                # Process entry point, starts all services
└── requirements.txt
```

---

## Layer 0 — Shared foundation

### MOD-00 · `core/config.py`

Loads all configuration from a single `config.yaml` file and exposes typed constants to every other module. No other module hard-codes any value — they all import from here.

Things it must hold: robot WiFi IP, robot UDP port (43988), workstation LAN IP, SQLite path, route file path, station registry path, battery thresholds (warn at 30%, charge-dock at 25%, resume at 90%), heartbeat interval (0.02s = 50 Hz), navigation waypoint tolerance (metres), obstacle hold timeout (seconds), priority level mappings, log level, web server port.

Design rule: parse `config.yaml` once at startup with Pydantic models. Every field has a default. The file can be edited without touching code.

### MOD-01 · `core/logger.py`

Wraps Python's `logging` with structured JSON output. Every log line includes timestamp, module name, severity, robot state at time of log, and task ID if one is active. Writes to both `stdout` and a rotating file under `logs/`. All other modules call `get_logger(__name__)` — they never configure logging themselves.

### MOD-02 · `core/event_bus.py`

An in-process publish/subscribe bus using Python `asyncio` queues. Modules publish events by name (`robot.arrived`, `task.completed`, `battery.low`, `estop.triggered`, etc.) and subscribe with async callbacks. This is the primary communication channel between modules — no module calls another module's functions directly except through the event bus or through the public interfaces defined below.

Define a fixed enum of all event names here so the coding agent has a contract to code against. The event bus is the backbone that lets Phase 2 modules plug in later without touching existing code.

### MOD-03 · `core/database.py`

SQLite wrapper using `aiosqlite`. Defines and manages four tables:

`tasks` — id, station_id, destination_id, batch_id, priority (0/1/2), status (queued/dispatched/awaiting_load/in_transit/awaiting_unload/completed/failed/cancelled), created_at, dispatched_at, completed_at, notes.

`robot_telemetry` — timestamp, battery_pct, pos_x, pos_y, pos_z, roll, pitch, yaw, control_mode, connection_ok. Kept for last 48 hours then pruned.

`events` — id, timestamp, event_name, payload_json. Permanent audit log.

`routes` — id, name, waypoints_json, active. Loaded from `routes.json` at startup, editable at runtime by supervisor.

Exposes a clean async interface: `create_task()`, `update_task_status()`, `get_queued_tasks()`, `log_telemetry()`, `log_event()`. No other module writes SQL directly.

---

## Layer 1 — Robot interface

### MOD-10 · `robot/sdk_adapter.py`

The only module that touches the Agibot SDK. Wraps every SDK call behind a clean async interface so the rest of the system never imports the SDK directly. If Agibot releases a new SDK version, this is the only file that changes.

Public interface:
- `connect(robot_ip, local_ip, port)` → bool
- `stand_up()` → bool
- `lie_down()` → bool
- `passive()` → bool (soft e-stop)
- `move(vx, vy, yaw_rate)` → bool
- `get_position()` → `(x, y, z)`
- `get_rpy()` → `(roll, pitch, yaw)`
- `get_battery()` → int (0–100)
- `get_control_mode()` → int
- `check_connection()` → bool

All calls are wrapped in try/except. Failures are logged and return safe defaults (False / zeroes) rather than raising exceptions into the rest of the system. The adapter also enforces the SDK's state machine: it tracks current robot mode internally and rejects invalid transitions (e.g. calling `move()` while not in stand mode) before the SDK even sees them.

### MOD-11 · `robot/state_monitor.py`

Runs a polling loop at 50 Hz (matching HighLevel SDK frequency). Every cycle it calls `sdk_adapter` for battery, position, RPY, control mode, and connection status. It writes telemetry to MOD-03, publishes `robot.telemetry` events to the bus, and owns the canonical `RobotState` object that other modules read. It also detects state transitions — when the robot goes from connected to disconnected, it publishes `robot.connection_lost`. When battery crosses the 30% warn threshold, it publishes `battery.warn`. When it crosses 25%, `battery.critical`.

### MOD-12 · `robot/heartbeat.py`

A dedicated async task that sends `move(0, 0, 0)` to the SDK adapter every 20ms regardless of what the navigator is doing. This is the SDK's keepalive — if no command is sent for 3 seconds, the robot enters damping mode. The heartbeat runs on its own asyncio task and is never blocked by navigation logic. The navigator overrides the heartbeat by writing its desired velocity into a shared `target_velocity` slot; the heartbeat loop reads that slot and sends it. When the slot is empty (robot is idle), it sends zero velocity.

---

## Layer 2 — Navigation

### MOD-20 · `navigation/route_store.py`

Loads and manages route definitions from `data/routes.json`. A route is a named list of waypoints, where each waypoint has: a name (e.g. `"station_a_approach"`), target position `(x, y)` in odometry space, approach heading in degrees, velocity to use on the segment leading to this waypoint, and a `hold` flag indicating the robot should stop and wait for a human confirmation before continuing.

Exposes: `get_route(origin_id, destination_id)` → list of waypoints, `list_routes()`, `set_route_active(name, active)`. Routes are hot-reloadable without restarting the process — the supervisor can add a new route through the API and it takes effect immediately.

The route JSON file is the commissioning artefact. During the physical site visit, an engineer drives the robot manually, notes the odometry readings at each waypoint, and fills in this file. The software does not change — only the data file.

### MOD-21 · `navigation/navigator.py`

Executes a route from MOD-20. Given a list of waypoints, it drives the robot through them sequentially by computing the velocity commands needed to reach each waypoint from the current position (from MOD-11 state). Uses a simple proportional controller: forward velocity proportional to distance to waypoint, yaw rate proportional to heading error.

At each `hold` waypoint, the navigator publishes `robot.arrived_at_waypoint` and pauses execution, waiting for a `human.confirmed_load` or `human.confirmed_unload` event from the bus before continuing. This is the moment the worker loads or unloads the tray.

If MOD-22 publishes an `obstacle.detected` event, the navigator immediately sends zero velocity and enters a waiting state. After the configured timeout, if no `obstacle.cleared` event arrives, it publishes `navigation.blocked` and reports the task as interrupted.

Phase 1 stub: position feedback uses the SDK's onboard odometry only (`getPosition()`). Drift accumulates over long routes. This is acceptable for Phase 1 POC — note it explicitly in a `TODO: integrate MOD-23 for drift correction` comment.

### MOD-22 · `navigation/obstacle.py`

Phase 1: a stub that never triggers. Returns a null detector that always reports the path as clear. The interface is fully defined so the navigator is already coded to handle obstacles correctly — the detector just doesn't detect anything yet.

Phase 2 (when camera is mounted): reads the robot's video stream from MOD-61, runs a lightweight OpenCV background subtraction or depth-based threshold on each frame, and publishes `obstacle.detected` / `obstacle.cleared` to the bus. No ML model — simple pixel-count threshold in the lower third of the frame.

The stub must match the Phase 2 interface exactly so the navigator needs zero changes when the real detector is dropped in.

### MOD-23 · `navigation/slam.py`

Phase 2 stub only. Interface defined: `get_corrected_position()` → `(x, y, heading)`. When LiDAR is available and SLAM is implemented (using `slam_toolbox` or similar via ROS2 bridge, or a direct LiDAR SDK), this module replaces odometry as the position source. MOD-21 already has a `position_source` injection point that defaults to MOD-11's odometry; MOD-23 plugs in there.

---

## Layer 3 — Task management

### MOD-30 · `tasks/queue.py`

Owns all task lifecycle operations. Wraps MOD-03's database calls with business logic: enforcing valid status transitions, calculating estimated wait times based on current queue depth and average task duration (tracked from historical data in the telemetry table), and exposing a clean interface that the dispatcher and API both call.

Public interface: `submit_task(station_id, destination_id, batch_id, priority)` → task_id, `get_next_task(robot_position)` → Task | None, `mark_dispatched(task_id)`, `mark_awaiting_load(task_id)`, `mark_in_transit(task_id)`, `mark_awaiting_unload(task_id)`, `mark_completed(task_id)`, `mark_failed(task_id, reason)`, `cancel_task(task_id)`, `get_queue_status()` → summary dict for the UI.

The `get_next_task(robot_position)` function is where the dispatch scoring logic lives. It queries all queued tasks, scores each one using: `score = priority_weight * priority + recency_weight * (1 / age_seconds) + proximity_weight * (1 / distance_to_origin)`. Returns the highest-scoring task. This function is the one place that encodes the "not simple FIFO" requirement. All weights are in `config.yaml` so they can be tuned without code changes.

Bidirectional optimisation: before scoring, if the robot's last completed task was at the QA Lab, any return task (destination = a station) gets a `direction_bonus` added to its score. This makes the system naturally chain outbound and return legs.

### MOD-31 · `tasks/dispatcher.py`

The orchestration loop. Runs continuously. Watches for the robot becoming free (via `robot.idle` event from the bus) and immediately calls `queue.get_next_task()`. If a task is returned, it calls `navigator.execute_route(origin, destination)` and monitors the task through its full lifecycle by listening to navigator events. When the navigator publishes `robot.arrived_at_waypoint` (with `hold=True`), the dispatcher updates task status and waits for the human confirmation event before telling the navigator to continue.

The dispatcher is the only module that coordinates between the queue, the navigator, and the human confirmation loop. It is intentionally thin — it does not contain scoring logic (that is MOD-30), it does not contain movement logic (that is MOD-21), it just connects them.

### MOD-32 · `tasks/battery_manager.py`

Listens for `battery.warn` and `battery.critical` events from MOD-11. On `battery.critical`, it signals the dispatcher to not accept new tasks after the current one completes, and queues a synthetic "dock" task with the highest possible priority. The dock task runs the robot through its dock route (defined in MOD-20), calls `lie_down()` at the dock waypoint, and holds there. The battery manager polls battery level every 30 seconds during charging. When it crosses the resume threshold (default 90%), it publishes `battery.recharged` and the dispatcher resumes normal operations.

### MOD-33 · `tasks/watchdog.py`

Listens for `robot.connection_lost` and `robot.telemetry` events. If no telemetry is received for 5 seconds (the robot's SDK firmware will have already entered damping mode after 3s), the watchdog publishes `system.alert` with severity CRITICAL, marks the active task as interrupted in the database, and notifies all connected UI clients via the WebSocket broker. It also detects if the robot's battery drops to 0% while in transit (implying an unexpected shutdown) and handles that case gracefully by marking the task failed with reason "robot_power_loss".

The watchdog is the system's last line of defence. It does not try to recover — it documents, alerts, and preserves state so a human can make an informed decision.

---

## Layer 4 — API server

### MOD-40 · `api/rest.py`

FastAPI application. Serves both the REST endpoints and the static UI files from `ui/`. All endpoints require a role token from MOD-42 except `/health`. Runs on port 8080 (configurable).

Endpoints the coding agent must implement:

`POST /tasks` — submit a new task. Body: `{station_id, destination_id, batch_id, priority}`. Returns task_id and estimated wait.

`GET /tasks` — list all tasks with current status. Supports `?status=queued` filter.

`DELETE /tasks/{id}` — cancel a queued task (not an in-progress one).

`POST /tasks/{id}/confirm-load` — human confirms loading at the station. Triggers navigator to continue.

`POST /tasks/{id}/confirm-unload` — human confirms unloading. Triggers navigator to continue.

`GET /robot/status` — current robot state: battery, position, mode, active task.

`GET /queue/status` — queue depth, estimated wait per station.

`POST /estop` — immediately calls `sdk_adapter.passive()`. Does not require a task context.

`POST /estop/release` — supervisor-only. Calls `sdk_adapter.stand_up()` after e-stop.

`GET /routes` — list all routes. Supervisor only.

`PUT /routes/{name}` — update a route's waypoints. Supervisor only.

`GET /logs` — paginated event log. Supervisor only.

### MOD-41 · `api/ws_broker.py`

WebSocket endpoint at `/ws`. All connected clients receive push updates whenever the event bus fires any of the UI-relevant events: `robot.telemetry`, `task.status_changed`, `system.alert`, `battery.warn`, `robot.arrived_at_waypoint`. This is what makes the operator UI update in real time without polling. The broker maps bus events to JSON messages and broadcasts them. Each client identifies itself with a station_id so the broker can filter station-specific messages (e.g. "robot arrived" only goes to the station that submitted the task).

### MOD-42 · `api/auth.py`

Simple role-based token auth. Three roles: `operator` (station workers — can submit tasks, confirm load/unload, see own queue), `qa` (lab technician — same as operator but for lab), `supervisor` (full access including routes, logs, e-stop release, config). Tokens are static strings stored in `config.yaml` — no user database, no sessions, no OAuth. This is an internal factory system on an isolated LAN. Simple is appropriate. Tokens are checked via a FastAPI dependency injected into each endpoint.

### MOD-43 · `api/alerts.py`

Listens for `system.alert` events on the bus. Formats them and: (1) pushes to all connected WebSocket clients via MOD-41, (2) writes to the events table in MOD-03, (3) if configured, sends an email via `smtplib` to the supervisor address in `config.yaml`. Email is optional and off by default — enabled by setting `alerts.email_enabled: true` in config.

Phase 2: when the GPIO relay module (MOD-60) is available, the alert manager also triggers the physical alert light at the relevant station.

---

## Layer 5 — Web UI

All UI is plain HTML + JavaScript served as static files. No build step, no framework, no npm. This is intentional — it must run in any browser on the factory LAN with zero installation, including on cheap panel PCs running old Chrome or Firefox. Styling with vanilla CSS only.

### MOD-50 · `ui/operator.html`

The station worker's view. Rendered by the browser at `http://<workstation_ip>:8080/operator?station=A`. Shows: a single large "Request robot" button, current queue position if a request is pending, estimated arrival time, "Confirm load" button (appears only when the robot has arrived), and a status indicator (robot on the way / robot arrived / robot departed / idle). Connects to the WebSocket in MOD-41 on load and updates the UI reactively.

Designed for a touchscreen — large tap targets, minimal text, no navigation menus.

### MOD-51 · `ui/supervisor.html`

Full dashboard at `http://<workstation_ip>:8080/supervisor`. Shows: robot battery and status, full task queue with all columns, event log (scrollable, filterable by severity), route list with active/inactive toggle, system health indicators (WebSocket connected, SDK connected, database OK), and e-stop / e-stop release controls. Uses the same WebSocket connection for live updates.

### MOD-52 · `ui/floormap.js`

Phase 1 stub: a static SVG floor plan image with the robot's last known position plotted as a dot, updated every 2 seconds by polling `GET /robot/status`. No live tracking animation.

Phase 2: when LiDAR SLAM provides accurate position data (MOD-23), upgrade to a live-tracked dot that moves smoothly as the robot moves, using WebSocket telemetry events for sub-second updates.

The floor plan SVG itself is a commissioning artefact — drawn once based on the factory layout and embedded in the HTML. It does not come from the robot.

### MOD-53 · `ui/kiosk.html`

Phase 2 stub. This is the touchscreen interface intended to be mounted at each physical station terminal (Raspberry Pi + display). Functionally identical to MOD-50 (operator.html) but designed for a locked-down kiosk mode — no browser chrome, no address bar, auto-reload on disconnect, auto-fullscreen. Phase 1 workaround: workers use MOD-50 on any tablet or laptop browser pointed at the workstation.

---

## Layer 6 — Hardware bridges (all Phase 2)

All of these are fully stubbed in Phase 1 with no-op implementations. The interface contracts are defined and the modules exist — they just return safe dummy values or do nothing. Every module that depends on them (`obstacle.py` depends on `video_reader.py`, `alerts.py` depends on `gpio_relay.py`, etc.) already imports them through the stub — so the real implementation slots in with zero changes to callers.

### MOD-60 · `hardware/gpio_relay.py`

Controls the alert light (and optionally an audible buzzer) at each station. Interface: `trigger_alert(station_id, level)` → None, `clear_alert(station_id)` → None. Phase 2 implementation drives a GPIO pin on a Raspberry Pi at each station via a network relay command (the workstation sends an HTTP request to a small Flask server running on each station Pi). Phase 1 stub: logs the call and returns.

### MOD-61 · `hardware/video_reader.py`

Reads the robot's camera stream from the SDK video interface. Interface: `get_latest_frame()` → numpy array | None. Phase 2 connects to the SDK's video stream endpoint (available in SDK v0.2.6 as the Video stream data interface) and decodes frames using OpenCV. Phase 1 stub: always returns None, which MOD-22 handles gracefully by never triggering obstacle detection.

### MOD-62 · `hardware/qr_anchor.py`

Reads QR codes from `video_reader` frames and returns a corrected position when a known floor marker is detected. Interface: `check_frame(frame)` → `CorrectionResult | None`. Phase 2 uses `pyzbar` or `opencv` QR decoder. Phase 1 stub: always returns None, and MOD-21 already has the code path to apply a correction when one is returned.

### MOD-63 · `hardware/mes_bridge.py`

Allows an external MES or LIMS system to submit tasks programmatically via a local REST call rather than a human pressing a button. Interface: `start_listener()` → None (starts a background HTTP listener on a configurable port that translates incoming MES events into `queue.submit_task()` calls). Phase 1 stub: the listener starts but immediately returns without binding to any port.

---

## Build order for the coding agent

Tell your agent to build in this sequence. Each phase is independently testable before the next begins.

Phase 1A (foundation): MOD-00 → MOD-01 → MOD-02 → MOD-03. Run unit tests. All other modules depend on these.

Phase 1B (robot interface): MOD-10 → MOD-12 → MOD-11. At this point the agent can run a standalone test that connects to the robot, reads telemetry, and maintains the heartbeat. Test against the real robot over WiFi before proceeding.

Phase 1C (navigation): MOD-20 → MOD-21. With all stubs for MOD-22 and MOD-23 in place. Test by having the robot walk a commissioning route with a human watching.

Phase 1D (task layer): MOD-30 → MOD-32 → MOD-33 → MOD-31. Test the full task lifecycle in the database and dispatcher loop without the UI.

Phase 1E (API + UI): MOD-42 → MOD-40 → MOD-41 → MOD-43 → MOD-50 → MOD-51 → MOD-52 (stub). End-to-end test: submit a task through the browser, watch the robot walk the route, confirm load via the browser, watch it complete.

Phase 2 (when hardware arrives): drop in real implementations of MOD-22, MOD-60, MOD-61, MOD-62. Upgrade MOD-52 to live tracking. Commission MOD-53 on station Pis. Add MOD-23 and MOD-63 last.

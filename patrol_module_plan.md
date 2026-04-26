**AUTONOMOUS PLATFORM**

Patrol Application — Full Module Plan

|                      |                                                        |
|----------------------|--------------------------------------------------------|
| **Document**         | autonomous-platform · apps/patrol                      |
| **Version**          | 1.0 — Patrol Planning                                  |
| **Status**           | Phase 1 scaffold ready · Phase 2 hardware pending      |
| **Robot**            | Agibot D1 EDU                                          |
| **Vision provider**  | Anthropic Claude Vision API (claude-sonnet-4-20250514) |
| **Offline fallback** | Conservative mode / local YOLO (optional)              |

**1. Overview**

This document is the complete module-by-module build plan for the patrol application that runs on the autonomous-platform repository. It covers every file that needs to be created, what each file does, which shared modules it reuses unchanged, which shared modules need extending, and the exact build order for the coding agent.

The patrol app shares the entire robot platform with the logistics app — SDK adapter, heartbeat, navigator, route store, event bus, database, auth, WebSocket broker, and alerts. None of that code is touched. Patrol adds three new layers on top: a patrol-specific task model, an observation pipeline with LLM-powered object classification, and its own REST API and UI.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Key architectural decision — LLM-based object classification</strong></p>
<p>Patrol requires context-aware classification: a wild boar is a threat in a plantation but not an obstacle on a factory floor. A person is normal at 9am but a critical alert at 2am in a restricted zone. Classical CV classifiers cannot handle this reasoning. The solution is Claude Vision API called per observation waypoint — one API call per waypoint, passing the camera frame and a zone-specific system prompt. The zone prompt is a YAML file edited by the operator, not code. Changing the rules for a new site means editing text, not retraining a model.</p></td>
</tr>
</tbody>
</table>

**2. What Is Already Done — Shared Platform**

Every module below is fully implemented, tested, and requires zero modification for patrol. The coding agent must import these but must not edit them.

|                   |                                   |                                                     |                      |
|-------------------|-----------------------------------|-----------------------------------------------------|----------------------|
| **Module**        | **Location**                      | **What patrol uses it for**                         | **Status**           |
| SDK adapter       | shared/quadruped/sdk_adapter.py   | Robot connection, move, stand, passive, battery     | **✅ Reuse as-is**   |
| Heartbeat         | shared/quadruped/heartbeat.py     | 50 Hz keepalive — identical for patrol              | **✅ Reuse as-is**   |
| State monitor     | shared/quadruped/state_monitor.py | Telemetry, battery events, position                 | **✅ Reuse as-is**   |
| Navigator         | shared/navigation/navigator.py    | Waypoint execution along patrol routes              | **✅ Reuse as-is**   |
| Route store       | shared/navigation/route_store.py  | Patrol route definitions, hot reload                | **✅ Reuse as-is**   |
| Obstacle detector | shared/navigation/obstacle.py     | Blocks nav if physical obstacle in path             | **✅ Reuse as-is**   |
| SLAM provider     | shared/navigation/slam.py         | Position correction stub                            | **✅ Reuse as-is**   |
| Event bus         | shared/core/event_bus.py          | Publish/subscribe — needs patrol events added       | **🔧 Extend shared** |
| Database          | shared/core/database.py           | Persist patrol cycles and anomaly records           | **✅ Reuse as-is**   |
| Config            | shared/core/config.py             | Typed config — needs PatrolSection added            | **🔧 Extend shared** |
| Logger            | shared/core/logger.py             | Structured JSON logs                                | **✅ Reuse as-is**   |
| Auth              | shared/api/auth.py                | Role-based tokens (supervisor role used by patrol)  | **✅ Reuse as-is**   |
| WebSocket broker  | shared/api/ws_broker.py           | Push patrol events to browser — needs patrol events | **🔧 Extend shared** |
| Alert manager     | shared/api/alerts.py              | Email / GPIO escalation for anomalies               | **✅ Reuse as-is**   |
| Base startup      | shared/runtime/base_startup.py    | Platform boot — patrol startup calls this first     | **✅ Reuse as-is**   |
| Video reader      | shared/hardware/video_reader.py   | Camera frame source — Phase 2 override point        | **✅ Reuse as-is**   |
| GPIO relay        | shared/hardware/gpio_relay.py     | Alert light at nearest post — already stubbed       | **✅ Reuse as-is**   |
| QR anchor         | shared/hardware/qr_anchor.py      | Position correction from floor markers              | **✅ Reuse as-is**   |

**3. Shared Module Extensions**

Three shared modules need new entries added. These are small, targeted changes — no existing code is removed or modified.

**3.1 shared/core/event_bus.py — add patrol EventNames**

Add the following entries to the EventName enum. They belong here because the WebSocket broker, alert manager, and any future cross-app logic can subscribe to them without importing patrol-specific code.

|                          |                          |                      |
|--------------------------|--------------------------|----------------------|
| **New EventName value**  | **String value**         | **Published by**     |
| PATROL_CYCLE_STARTED     | patrol.cycle.started     | PatrolScheduler      |
| PATROL_CYCLE_COMPLETED   | patrol.cycle.completed   | PatrolDispatcher     |
| PATROL_CYCLE_FAILED      | patrol.cycle.failed      | PatrolDispatcher     |
| PATROL_WAYPOINT_OBSERVED | patrol.waypoint.observed | Observer             |
| PATROL_ANOMALY_DETECTED  | patrol.anomaly.detected  | AnomalyDecider       |
| PATROL_ANOMALY_CLEARED   | patrol.anomaly.cleared   | AnomalyDecider       |
| PATROL_SUSPENDED         | patrol.suspended         | PatrolWatchdog / API |
| PATROL_RESUMED           | patrol.resumed           | API                  |

**3.2 shared/core/config.py — add PatrolSection**

Add a PatrolSection Pydantic model and a VisionSection Pydantic model, then include both as fields on AppConfig. Add a corresponding patrol: and vision: block to config.yaml.example.

|                                  |          |                          |                                                   |
|----------------------------------|----------|--------------------------|---------------------------------------------------|
| **Config key**                   | **Type** | **Default**              | **Purpose**                                       |
| patrol.schedule_enabled          | bool     | true                     | Enable automatic timed cycles                     |
| patrol.patrol_interval_seconds   | int      | 1800                     | Gap between cycle starts (30 min)                 |
| patrol.observation_dwell_seconds | float    | 3.0                      | Seconds to capture frames at each waypoint        |
| patrol.anomaly_cooldown_seconds  | float    | 300.0                    | Min gap between alerts from same zone             |
| patrol.max_consecutive_failures  | int      | 3                        | Failures before auto-suspend                      |
| patrol.alert_on_anomaly          | bool     | true                     | Trigger GPIO + email on THREAT detection          |
| vision.enabled                   | bool     | false                    | Master camera switch — false until hardware ready |
| vision.provider                  | str      | claude                   | claude \| local_yolo \| none                      |
| vision.claude_model              | str      | claude-sonnet-4-20250514 | Model string                                      |
| vision.claude_max_tokens         | int      | 500                      | Max tokens per API response                       |
| vision.frame_width               | int      | 640                      | Resize frame before encoding                      |
| vision.frame_height              | int      | 480                      | Resize frame before encoding                      |
| vision.sharpness_threshold       | float    | 50.0                     | Laplacian variance — skip blurry frames           |
| vision.offline_fallback_mode     | str      | conservative             | conservative \| local_model \| disabled           |
| vision.zones_file                | str      | data/zones.yaml          | Per-zone object classification rules              |
| vision.api_timeout_seconds       | float    | 10.0                     | Claude API call timeout                           |

**3.3 shared/api/ws_broker.py — add patrol events to \_RELEVANT_EVENT_NAMES**

Add the 8 new PATROL\_\* and vision-related EventNames to the \_RELEVANT_EVENT_NAMES set inside WebSocketBroker. This is a single set addition — no other logic changes.

**4. New Data Files**

**4.1 data/zones.yaml**

This is the operator-facing commissioning file that controls what the vision LLM considers normal, suspicious, or a threat at each observation waypoint zone. It is loaded by ZoneConfig at startup and hot-reloaded on file change. Each patrol route waypoint has a zone_id field that maps to an entry here.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Example zone entry structure</strong></p>
<p>zones: PLANTATION_NORTH: description: Palm oil northern sector, active daylight hours normal_objects: [palm trees, fallen fronds, plantation machinery, authorized vehicles] suspicious_objects: [unfamiliar vehicles, people outside working hours] threat_objects: [fire, smoke, wild boar, deer, person in restricted area] time_rules: - after: '18:00' before: '06:00' escalate_suspicious_to: THREAT</p></td>
</tr>
</tbody>
</table>

**4.2 data/patrol_routes.json**

Patrol routes follow the same Waypoint schema as logistics routes with two additions per waypoint: a zone_id (string, maps to zones.yaml) and observe (bool, true means the robot pauses and takes a camera observation at this waypoint). Waypoints where observe is false are transit-only — the robot passes through without stopping.

**5. Full Module Plan — apps/patrol/**

Every module below is new and lives entirely within apps/patrol/. The directory structure follows the same pattern as apps/logistics/. All modules are fully async.

**5.1 Layer 0 — Config & Events**

**P-00 · apps/patrol/\_\_init\_\_.py**

Package marker only. No imports.

**5.2 Layer 1 — Patrol Task Model**

**P-10 · apps/patrol/tasks/patrol_record.py**

Data model for a single patrol cycle. Dataclass PatrolRecord with fields: cycle_id (UUID str), route_id, status, triggered_by (schedule \| manual \| alert), created_at, started_at, completed_at, waypoints_total, waypoints_observed, anomaly_ids (list\[str\]), failure_reason.

Enum PatrolCycleStatus: SCHEDULED, ACTIVE, COMPLETED, FAILED, SUSPENDED.

PatrolCycleStateMachine class with ALLOWED_TRANSITIONS dict and transition_status() method that raises InvalidCycleTransition on illegal moves. Valid transitions: SCHEDULED→ACTIVE, ACTIVE→COMPLETED, ACTIVE→FAILED, ACTIVE→SUSPENDED, SUSPENDED→ACTIVE.

Tests: all valid transitions succeed, all invalid transitions raise, field validation catches bad inputs.

**P-11 · apps/patrol/tasks/patrol_queue.py**

Manages the lifecycle of patrol cycles in the database. Wraps database calls with business logic.

Public interface: submit_cycle(route_id, triggered_by) → cycle_id. get_next_cycle() → PatrolRecord \| None (returns oldest SCHEDULED cycle). mark_active(cycle_id). mark_completed(cycle_id, stats_dict). mark_failed(cycle_id, reason). suspend_cycle(cycle_id). resume_cycle(cycle_id). get_queue_status() → dict. get_cycle_history(limit) → list\[PatrolRecord\].

Database table patrol_cycles: cycle_id TEXT PK, route_id, status, triggered_by, created_at, started_at, completed_at, waypoints_total, waypoints_observed, anomaly_ids_json, failure_reason.

Tests: submit returns valid UUID, get_next_cycle returns oldest SCHEDULED, invalid transitions raise, history is ordered by created_at desc.

**P-12 · apps/patrol/tasks/patrol_scheduler.py**

Background async loop that creates scheduled patrol cycles at the configured interval.

Behaviour: on start(), subscribes to BATTERY_RECHARGED (resume scheduling after charge), PATROL_SUSPENDED (pause scheduling), PATROL_RESUMED (resume scheduling). Main loop sleeps for patrol_interval_seconds then calls patrol_queue.submit_cycle(route_id, triggered_by='schedule') and publishes PATROL_CYCLE_STARTED. Respects schedule_enabled config flag — if false, loop runs but creates nothing.

Scheduling is inhibited during: battery charging (BATTERY_CRITICAL received, not yet BATTERY_RECHARGED), patrol suspension, and when a cycle is already ACTIVE (no concurrent cycles).

Tests: scheduler creates cycle after interval, respects schedule_enabled=false, inhibits during active cycle, resumes after PATROL_RESUMED.

**5.3 Layer 2 — Observation Pipeline**

This is the patrol-specific layer. It has no equivalent in logistics. The pipeline has four stages executed in sequence at each observation waypoint.

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Observation pipeline — four stages</strong></p>
<p>1. VideoCapture → captures frames during dwell period, picks sharpest frame 2. VisionAnalyser → sends frame + zone context to Claude API, returns list of detected objects with threat level 3. AnomalyDecider → applies zone rules, time-of-day escalation, consecutive-pass escalation 4. Observer (orchestrator) → calls all three stages, publishes events, returns ObservationSummary</p></td>
</tr>
</tbody>
</table>

**P-20 · apps/patrol/observation/zone_config.py**

Loads and validates data/zones.yaml. Exposes get_zone(zone_id) → ZoneDefinition \| None. Hot-reloads on file mtime change (same pattern as RouteStore). Validates at load time that every zone_id referenced in patrol_routes.json has a corresponding zones.yaml entry — raises ZoneConfigError with a list of missing zones so commissioning errors are caught before the first patrol cycle runs.

ZoneDefinition dataclass: zone_id, description, normal_objects (list\[str\]), suspicious_objects (list\[str\]), threat_objects (list\[str\]), time_rules (list\[TimeRule\]). TimeRule dataclass: after (str HH:MM), before (str HH:MM), escalate_suspicious_to (str).

to_prompt_fragment() method — returns a formatted multi-line string ready to embed in the Claude system prompt. This is the bridge between the YAML file and the API call.

Tests: valid YAML loads correctly, missing zone_id raises, time rule parsing handles midnight crossover, to_prompt_fragment produces non-empty string.

**P-21 · apps/patrol/observation/video_capture.py**

Captures frames during the observation dwell period and returns the single sharpest frame.

Phase 1 stub: start(), stop(), capture(dwell_seconds, zone_id) → VideoFrame \| None always returns None. Logs that capture was called. The observer handles None gracefully — vision analysis is skipped, observation summary records no visual data.

Phase 2 implementation (when camera is mounted): reads from VideoReader.read_once() in a loop for dwell_seconds. For each frame where frame.data is not None, computes Laplacian variance (sharpness score) using OpenCV. Discards frames below config.vision.sharpness_threshold. Returns the sharpest frame, or None if no frame exceeded the threshold.

The \_capture_frames() method is the single override point. VideoCapture is its own class (not a subclass of VideoReader) because its concern is selection, not acquisition.

Tests: returns None when VideoReader returns None, returns sharpest frame when multiple frames have different variance scores (mocked), respects dwell_seconds.

**P-22 · apps/patrol/observation/vision_analyser.py**

The LLM integration layer. Takes a VideoFrame and a ZoneDefinition. Returns a VisionAnalysisResult.

**VisionAnalysisResult dataclass:**

zone_id (str), objects_detected (list\[DetectedObject\]), analysis_source (str: 'claude' \| 'local_yolo' \| 'stub' \| 'offline_conservative'), raw_response (str \| None), error (str \| None).

DetectedObject dataclass: label (str), threat_level (str: 'NORMAL' \| 'SUSPICIOUS' \| 'THREAT'), confidence (float 0–1), reason (str), location_hint (str \| None — rough position like 'lower left' if model mentions it).

**Claude API call design:**

System prompt is built from three parts: (1) a fixed platform preamble describing the robot and its role, (2) the zone's to_prompt_fragment() output describing what is normal, suspicious, and threatening in this zone, (3) an instruction to respond only in valid JSON matching the DetectedObject schema. The image is passed as a base64-encoded JPEG in the user message. Uses the anthropic Python SDK (import anthropic).

Response parsing: the raw text is stripped of any markdown fences, then JSON-parsed into a list of DetectedObject. If parsing fails, the error is recorded and an empty detection list is returned — the decider handles empty results as 'no issues detected'.

Offline fallback: if the API call raises an exception (connection error, timeout), the analyser checks config.vision.offline_fallback_mode. If 'conservative', returns a single DetectedObject with label='unknown', threat_level='SUSPICIOUS', confidence=0.5, reason='Vision API unavailable — conservative fallback'. If 'local_model', attempts to call the LocalYOLOAnalyser (P-23). If 'disabled', returns empty detection list.

Phase 1 stub: when config.vision.enabled is false, analyse() immediately returns VisionAnalysisResult with empty objects_detected and analysis_source='stub'. No API call is made.

Tests: stub returns empty result when vision disabled, JSON response parsed correctly into DetectedObject list, malformed JSON returns error result not exception, offline fallback returns SUSPICIOUS when API raises, timeout is respected.

**P-23 · apps/patrol/observation/local_yolo_analyser.py**

Optional local fallback using an ultralytics YOLO model. Only instantiated when offline_fallback_mode is 'local_model' and the ultralytics package is installed.

analyse(frame) → VisionAnalysisResult. Loads the model lazily on first call (model path configurable as vision.local_yolo_model_path, defaults to 'yolov8n.pt'). Runs inference on the frame. Maps YOLO class labels to DetectedObject with threat_level always set to 'SUSPICIOUS' — local YOLO cannot reason about context, so everything it detects is flagged for human review rather than classified as NORMAL or THREAT.

If ultralytics is not installed, analyse() raises LocalYOLOUnavailableError and the vision analyser falls back to conservative mode.

Tests: LocalYOLOUnavailableError raised when package absent (mock the import), analysis_source is 'local_yolo' when successful.

**P-24 · apps/patrol/observation/anomaly_decider.py**

Pure business logic. No API calls, no I/O. Takes a VisionAnalysisResult, a ZoneDefinition, and the current datetime. Returns a DecisionResult.

DecisionResult dataclass: alert_required (bool), severity (str: 'info' \| 'warning' \| 'critical'), threat_objects (list\[DetectedObject\] — only those that triggered the alert), zone_id, reason (str), escalated (bool — true if time-of-day rule escalated a SUSPICIOUS to THREAT).

Decision logic: iterate over detected objects. Any THREAT → alert_required=True, severity='critical'. Any SUSPICIOUS → check time rules from ZoneDefinition. If current time falls within a time rule's window and escalate_suspicious_to='THREAT', escalate and set escalated=True. If no escalation, severity='warning'. NORMAL objects → ignored. If no objects detected → alert_required=False.

Consecutive-pass escalation: the decider receives an optional previous_result argument. If the previous observation for this zone was already SUSPICIOUS and this one is also SUSPICIOUS, escalate to THREAT. This catches persistent anomalies that individually would not trigger a critical alert.

Tests: THREAT object → critical alert, SUSPICIOUS within time window → escalated to critical, SUSPICIOUS outside time window → warning, NORMAL only → no alert, consecutive SUSPICIOUS → escalated, empty detections → no alert.

**P-25 · apps/patrol/observation/anomaly_log.py**

Persists anomaly records to the database and enforces the cooldown rule.

AnomalyRecord dataclass: anomaly_id (UUID str), cycle_id, zone_id, waypoint_name, detected_at, severity, threat_objects_json, confidence_max (float), resolved_at (datetime \| None), resolved_by (str \| None), metadata_json.

Database table patrol_anomalies: anomaly_id TEXT PK, cycle_id, zone_id, waypoint_name, detected_at, severity, threat_objects_json, confidence_max, resolved_at, resolved_by, metadata_json.

Public interface: record(cycle_id, zone_id, waypoint_name, decision_result) → AnomalyRecord \| None. Returns None if a recent anomaly for the same zone is within the cooldown window (checks detected_at of last unresolved record for that zone). resolve(anomaly_id, resolved_by) → AnomalyRecord. list_unresolved(zone_id=None) → list\[AnomalyRecord\]. get_last_for_zone(zone_id) → AnomalyRecord \| None.

Tests: record creates entry, second record within cooldown returns None, record after cooldown creates new entry, resolve sets resolved_at, list_unresolved excludes resolved records.

**P-26 · apps/patrol/observation/observer.py**

The orchestrator. This is the only module the dispatcher calls. Internal pipeline stages are hidden.

ObservationSummary dataclass: waypoint_name, zone_id, observed_at, frame_captured (bool), analysis_source (str), objects_detected (list\[DetectedObject\]), alert_required (bool), severity (str), anomaly_id (str \| None), error (str \| None).

Public method: observe(waypoint_name, zone_id, cycle_id, task_id=None) → ObservationSummary.

Internal sequence: (1) Get ZoneDefinition from ZoneConfig — if zone not found, log warning, return summary with alert_required=False and error='zone not configured'. (2) Call VideoCapture.capture() → frame. (3) If frame is None and vision.enabled is False, skip to step 6. (4) Call VisionAnalyser.analyse(frame, zone_definition) → VisionAnalysisResult. (5) Call AnomalyDecider.decide(analysis_result, zone_definition, current_datetime, previous_result) → DecisionResult. (6) If decision.alert_required, call AnomalyLog.record() → anomaly_record. (7) Publish PATROL_WAYPOINT_OBSERVED event. If anomaly recorded, also publish PATROL_ANOMALY_DETECTED. (8) Return ObservationSummary.

All exceptions from any stage are caught, logged, and recorded in summary.error — the observer never raises to the dispatcher.

Tests: full pipeline stub test (all stages mocked, verify event sequence), zone not found returns graceful summary, stage exception captured in error field, anomaly published when decider returns alert_required=True.

**5.4 Layer 3 — Patrol Dispatcher**

**P-30 · apps/patrol/tasks/patrol_dispatcher.py**

Orchestrates the full patrol cycle lifecycle. Structurally mirrors the logistics dispatcher but with no human handshake.

State: \_active_cycle (PatrolRecord \| None), \_suspended (bool), \_consecutive_failures (int).

On start(): subscribes to QUADRUPED_IDLE, BATTERY_RECHARGED, PATROL_SUSPENDED, PATROL_RESUMED, ESTOP_TRIGGERED. Starts the dispatch loop.

Dispatch loop: while running, wait for robot idle (QUADRUPED_IDLE event). If suspended or active cycle exists, skip. Call patrol_queue.get_next_cycle(). If None, sleep 5s and retry. If cycle found, run \_execute_cycle(cycle).

\_execute_cycle(cycle) sequence: mark cycle ACTIVE → call navigator.execute_route_by_id(cycle.route_id) with a waypoint arrival callback → for each QUADRUPED_ARRIVED_AT_WAYPOINT event where waypoint.observe=True, call observer.observe() → after navigation completes, mark cycle COMPLETED with stats → route robot to dock via dock route → publish PATROL_CYCLE_COMPLETED → reset \_consecutive_failures to 0.

On NavigationBlockedError or NavigationFailedError: increment \_consecutive_failures, mark cycle FAILED, check if consecutive failures exceed config.patrol.max_consecutive_failures → if so, publish PATROL_SUSPENDED and set \_suspended=True.

On PATROL_SUSPENDED: cancel current navigation, set \_suspended=True. On PATROL_RESUMED: set \_suspended=False. On ESTOP_TRIGGERED: cancel navigation, set \_active_cycle=None.

Tests: full cycle executes in correct order (mocked nav + observer), consecutive failures trigger suspension, ESTOP cancels active cycle, PATROL_RESUMED allows next cycle, dock routing called after completion.

**P-31 · apps/patrol/tasks/patrol_watchdog.py**

Monitors patrol health beyond what the dispatcher handles.

Stall detection: tracks timestamp of last PATROL_CYCLE_COMPLETED event. If schedule_enabled=True and no cycle has completed in 3 × patrol_interval_seconds, publishes SYSTEM_ALERT with reason='patrol stalled — no cycle completed in expected window'.

Missed cycle detection: if patrol is not suspended and the queue has SCHEDULED cycles older than 2 × patrol_interval_seconds, publishes SYSTEM_ALERT with reason='patrol cycles accumulating in queue — dispatcher may be stuck'.

Tests: stall alert fires after 3x interval with no completion, missed cycle alert fires for old SCHEDULED cycles, no alert when suspended.

**5.5 Layer 4 — REST API**

**P-40 · apps/patrol/api/rest.py**

Separate FastAPI app from logistics. Default port 8081. Uses shared/api/auth.py, shared/api/ws_broker.py, shared/api/alerts.py unchanged. Has its own lifespan that calls apps.patrol.runtime.startup.startup_system() and apps.patrol.runtime.startup.shutdown_system().

|            |                                |            |                                                                 |
|------------|--------------------------------|------------|-----------------------------------------------------------------|
| **Method** | **Endpoint**                   | **Auth**   | **Description**                                                 |
| GET        | /health                        | none       | Service health — no auth                                        |
| GET        | /patrol/status                 | supervisor | Current cycle, route, last obs, next scheduled, suspension flag |
| GET        | /patrol/cycles                 | supervisor | Cycle history — ?limit=N, ?status=completed                     |
| GET        | /patrol/cycles/{id}            | supervisor | Full cycle detail with per-waypoint observations                |
| POST       | /patrol/trigger                | supervisor | Immediately schedule a manual patrol cycle                      |
| POST       | /patrol/suspend                | supervisor | Suspend patrol after current route completes                    |
| POST       | /patrol/resume                 | supervisor | Resume patrol scheduling                                        |
| GET        | /patrol/anomalies              | supervisor | All anomalies — ?resolved=false&zone_id=X                       |
| POST       | /patrol/anomalies/{id}/resolve | supervisor | Mark anomaly resolved                                           |
| GET        | /patrol/routes                 | supervisor | List active patrol routes                                       |
| POST       | /patrol/routes                 | supervisor | Add or update patrol route definition                           |
| GET        | /patrol/zones                  | supervisor | List zone configurations from zones.yaml                        |
| POST       | /estop                         | supervisor | Immediate robot stop — calls passive()                          |
| POST       | /estop/release                 | supervisor | Resume from e-stop — calls stand_up()                           |
| WS         | /ws                            | operator+  | WebSocket — patrol events pushed to browser                     |

**5.6 Layer 5 — Web UI**

All UI is plain HTML + vanilla JavaScript served as static files. No build step. Runs in any factory browser. Patrol UI is supervisor-only — no worker interaction needed.

**P-50 · apps/patrol/ui/supervisor.html**

Main patrol control panel. Sections: (1) Status bar — patrol active/idle/suspended, battery, robot mode. (2) Current cycle panel — route name, waypoints completed/total, last observation zone and result. (3) Anomaly feed — live list of unresolved anomalies with severity badge, zone, time, and Resolve button. (4) Patrol controls — Trigger Now, Suspend, Resume buttons. (5) Floor map panel (delegates to floormap.js). All sections update via WebSocket events without page reload.

**P-51 · apps/patrol/ui/anomaly_log.html**

Anomaly history and resolution view. Table of all anomalies filterable by date range, zone, and resolved status. Each row shows: anomaly ID, cycle ID, zone, detected_at, severity badge, confidence, resolution status. Resolve button calls POST /patrol/anomalies/{id}/resolve. Designed to be printable as a shift security report.

**P-52 · apps/patrol/ui/floormap.js**

Shared floor map JavaScript module imported by supervisor.html. Phase 1: static SVG with patrol route overlaid as a dashed path between waypoints, zone labels at observation waypoints, robot position dot polling GET /patrol/status every 2s, and anomaly markers (red dot) at zones where unresolved anomalies exist. Phase 2: live robot position via WebSocket QUADRUPED_TELEMETRY events.

**5.7 Layer 6 — Runtime**

**P-60 · apps/patrol/runtime/startup.py**

Mirrors apps/logistics/runtime/startup.py exactly in structure.

startup_system(): calls base_startup.startup_system() first, then starts patrol-specific services in order: ZoneConfig.load() → PatrolQueue initialise → PatrolScheduler.start() → PatrolDispatcher.start() → PatrolWatchdog.start(). On failure in any patrol service, rolls back started patrol services then calls base_startup.shutdown_system().

shutdown_system(): stops patrol services in reverse order (watchdog → dispatcher → scheduler), then calls base_startup.shutdown_system().

create_uvicorn_config(): returns {'app': 'apps.patrol.api.rest:app', 'host': config.api.host, 'port': config.api.patrol_port} where patrol_port defaults to 8081.

main(): calls uvicorn.run(\*\*create_uvicorn_config()).

**5.8 Layer 7 — Hardware (patrol-specific)**

**P-70 · apps/patrol/hardware/alert_notifier.py**

Handles digital escalation beyond the GPIO relay light. Called by the observer when an anomaly is recorded with severity='critical'.

Phase 1 stub: notify(anomaly_record) → None. Logs the call. Returns immediately.

Phase 2: sends an HTTP POST to config.patrol.webhook_url if configured (Teams, Slack, or custom endpoint). Payload includes: zone_id, detected_at, severity, threat objects list, cycle_id, and — if the VideoFrame had image data — a base64 JPEG thumbnail. Uses httpx for the async POST. On connection error, logs and does not raise (alert is already in the database).

Tests: stub completes without error, Phase 2 webhook called with correct payload (mocked httpx), connection error does not raise.

**6. Complete File Listing**

Every file that must exist when the patrol app is fully built. Files marked with an asterisk (\*) require hardware to be fully implemented.

|                                                |                      |                                                                  |
|------------------------------------------------|----------------------|------------------------------------------------------------------|
| **File**                                       | **Status**           | **Notes**                                                        |
| shared/core/event_bus.py                       | **🔧 Extend shared** | Add 8 PATROL\_\* EventNames to enum                              |
| shared/core/config.py                          | **🔧 Extend shared** | Add PatrolSection + VisionSection to AppConfig                   |
| shared/api/ws_broker.py                        | **🔧 Extend shared** | Add PATROL\_\* events to \_RELEVANT_EVENT_NAMES                  |
| data/zones.yaml                                | **❌ Not built**     | Operator commissioning file — zone rules                         |
| data/patrol_routes.json                        | **❌ Not built**     | Patrol route waypoints with zone_id + observe fields             |
| config.yaml.example                            | **🔧 Extend shared** | Add patrol: and vision: sections                                 |
| apps/patrol/\_\_init\_\_.py                    | **❌ Not built**     | Package marker                                                   |
| apps/patrol/tasks/\_\_init\_\_.py              | **❌ Not built**     | Package marker                                                   |
| apps/patrol/tasks/patrol_record.py             | **❌ Not built**     | Cycle dataclass + state machine (P-10)                           |
| apps/patrol/tasks/patrol_queue.py              | **❌ Not built**     | Cycle lifecycle + DB persistence (P-11)                          |
| apps/patrol/tasks/patrol_scheduler.py          | **❌ Not built**     | Timed cycle creation loop (P-12)                                 |
| apps/patrol/tasks/patrol_dispatcher.py         | **❌ Not built**     | Cycle orchestration loop (P-30)                                  |
| apps/patrol/tasks/patrol_watchdog.py           | **❌ Not built**     | Stall + failure monitoring (P-31)                                |
| apps/patrol/observation/\_\_init\_\_.py        | **❌ Not built**     | Package marker                                                   |
| apps/patrol/observation/zone_config.py         | **❌ Not built**     | Load + validate zones.yaml (P-20)                                |
| apps/patrol/observation/video_capture.py       | **❌ Not built**     | Frame capture + sharpness selection (P-21)\*                     |
| apps/patrol/observation/vision_analyser.py     | **❌ Not built**     | Claude Vision API + offline fallback (P-22)\*                    |
| apps/patrol/observation/local_yolo_analyser.py | **❌ Not built**     | Optional local YOLO fallback (P-23)\*                            |
| apps/patrol/observation/anomaly_decider.py     | **❌ Not built**     | Pure decision logic (P-24)                                       |
| apps/patrol/observation/anomaly_log.py         | **❌ Not built**     | DB persistence + cooldown (P-25)                                 |
| apps/patrol/observation/observer.py            | **❌ Not built**     | Pipeline orchestrator (P-26)                                     |
| apps/patrol/api/\_\_init\_\_.py                | **❌ Not built**     | Package marker                                                   |
| apps/patrol/api/rest.py                        | **❌ Not built**     | FastAPI app port 8081 (P-40)                                     |
| apps/patrol/ui/supervisor.html                 | **❌ Not built**     | Main patrol dashboard (P-50)                                     |
| apps/patrol/ui/anomaly_log.html                | **❌ Not built**     | History + resolution view (P-51)                                 |
| apps/patrol/ui/floormap.js                     | **❌ Not built**     | Route + anomaly overlay (P-52)                                   |
| apps/patrol/runtime/\_\_init\_\_.py            | **❌ Not built**     | Package marker                                                   |
| apps/patrol/runtime/startup.py                 | **❌ Not built**     | App boot sequence (P-60)                                         |
| apps/patrol/hardware/\_\_init\_\_.py           | **❌ Not built**     | Package marker                                                   |
| apps/patrol/hardware/alert_notifier.py         | **❌ Not built**     | Webhook escalation stub → real (P-70)                            |
| requirements.txt                               | **🔧 Extend shared** | Add: anthropic, opencv-python-headless, httpx (already in reqs?) |
| tests/patrol/test_patrol_record.py             | **❌ Not built**     | State machine transitions                                        |
| tests/patrol/test_patrol_queue.py              | **❌ Not built**     | Cycle lifecycle + DB                                             |
| tests/patrol/test_patrol_scheduler.py          | **❌ Not built**     | Timing + inhibition                                              |
| tests/patrol/test_zone_config.py               | **❌ Not built**     | Load, validate, hot-reload                                       |
| tests/patrol/test_video_capture.py             | **❌ Not built**     | Frame selection, sharpness                                       |
| tests/patrol/test_vision_analyser.py           | **❌ Not built**     | API call, offline fallback, JSON parse                           |
| tests/patrol/test_anomaly_decider.py           | **❌ Not built**     | All decision paths                                               |
| tests/patrol/test_anomaly_log.py               | **❌ Not built**     | Cooldown, resolve, history                                       |
| tests/patrol/test_observer.py                  | **❌ Not built**     | Full pipeline integration (mocked)                               |
| tests/patrol/test_patrol_dispatcher.py         | **❌ Not built**     | Cycle lifecycle, suspension, ESTOP                               |
| tests/patrol/test_patrol_watchdog.py           | **❌ Not built**     | Stall detection, missed cycles                                   |
| tests/patrol/test_patrol_rest_api.py           | **❌ Not built**     | All endpoints, auth, WebSocket                                   |
| tests/patrol/test_patrol_startup.py            | **❌ Not built**     | Boot sequence, rollback on failure                               |

**7. Build Order for the Coding Agent**

Follow this sequence strictly. Each step is independently testable before the next begins. Do not start a step until all tests from the previous step pass.

**Step 1 — Shared extensions (no new files, only additions)**

- Add 8 PATROL\_\* EventNames to EventName enum in shared/core/event_bus.py

- Add PatrolSection and VisionSection Pydantic models to shared/core/config.py, add fields to AppConfig

- Add PATROL\_\* events to \_RELEVANT_EVENT_NAMES in shared/api/ws_broker.py

- Add anthropic and opencv-python-headless to requirements.txt

- Add patrol: and vision: blocks to config.yaml.example

- Run existing 401 tests — all must still pass before proceeding

**Step 2 — Data files**

- Create data/zones.yaml with example entries for at least two zones

- Create data/patrol_routes.json with one complete patrol route using zone_id and observe fields

**Step 3 — Task model (P-10, P-11)**

- Build patrol_record.py — dataclass and state machine

- Build patrol_queue.py — lifecycle and DB persistence

- Write and pass tests for both. All state machine paths must be tested.

**Step 4 — Zone config and anomaly decider (P-20, P-24)**

- Build zone_config.py — load, validate, hot-reload, to_prompt_fragment()

- Build anomaly_decider.py — all decision paths, time rules, consecutive-pass logic

- Write and pass tests for both. These are pure logic — 100% branch coverage expected.

**Step 5 — Anomaly log (P-25)**

- Build anomaly_log.py — DB schema, record(), cooldown, resolve(), list_unresolved()

- Write and pass tests including cooldown boundary conditions

**Step 6 — Observation pipeline stubs (P-21, P-22, P-23, P-26)**

- Build video_capture.py as Phase 1 stub (returns None)

- Build vision_analyser.py as Phase 1 stub (returns empty VisionAnalysisResult when vision.enabled=false)

- Build local_yolo_analyser.py — implement unavailable error path, stub the actual inference

- Build observer.py — full orchestrator using all stub stages

- Write and pass integration test: full pipeline with all stages mocked, verify event sequence and ObservationSummary fields

**Step 7 — Scheduler (P-12)**

- Build patrol_scheduler.py — interval loop, inhibition logic

- Write and pass tests with mocked event bus and queue

**Step 8 — Dispatcher and watchdog (P-30, P-31)**

- Build patrol_dispatcher.py — full cycle orchestration

- Build patrol_watchdog.py — stall and missed-cycle detection

- Write and pass integration tests. Test the ESTOP path, suspension, and consecutive failure threshold.

**Step 9 — Alert notifier stub (P-70)**

- Build alert_notifier.py as Phase 1 stub

- Write test confirming stub completes without error

**Step 10 — Runtime startup (P-60)**

- Build apps/patrol/runtime/startup.py

- Write startup sequence test verifying correct boot order and rollback on failure (mirror of logistics startup test)

**Step 11 — REST API (P-40)**

- Build apps/patrol/api/rest.py with all endpoints

- Write endpoint tests using TestClient — all happy paths and auth rejections

- Write lifespan test confirming startup/shutdown sequence matches expected call order

**Step 12 — Web UI (P-50, P-51, P-52)**

- Build supervisor.html with WebSocket event handling and anomaly feed

- Build anomaly_log.html with filter controls and resolve button

- Build floormap.js with static SVG patrol route overlay

**Step 13 — End-to-end smoke test**

- Start the patrol app on the workstation

- Trigger a manual cycle via POST /patrol/trigger

- Verify cycle appears in GET /patrol/cycles with status ACTIVE

- Verify PATROL_WAYPOINT_OBSERVED events arrive via WebSocket

- Verify cycle completes and appears in history with status COMPLETED

- Confirm no existing logistics tests broken (run full 401+ suite)

**8. Phase 2 — When Camera Hardware Arrives**

The following items are intentionally left as stubs. When the upper structure with camera is manufactured and mounted, implement these in this order:

- **1.** VideoReader.\_read_frame()

- **2.** VideoCapture.\_capture_frames()

- **3.** VisionAnalyser — set vision.enabled: true

- **4.** Configure zones.yaml

- **5.** Configure data/patrol_routes.json

- **6.** Optional: LocalYOLOAnalyser.\_run_inference()

- **7.** Optional: AlertNotifier Phase 2

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<tbody>
<tr class="odd">
<td><p><strong>Nothing else changes</strong></p>
<p>The dispatcher, watchdog, API, UI, database schema, event bus, and all shared modules remain exactly as built in Phase 1. Phase 2 is entirely contained to the override methods listed above plus configuration file edits.</p></td>
</tr>
</tbody>
</table>

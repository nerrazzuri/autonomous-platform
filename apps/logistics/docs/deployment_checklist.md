# Deployment Checklist

Practical checklist for preparing, deploying, and validating the Phase 1 Sumitomo Quadruped Factory Logistics system on the on-premise Linux workstation.

## 1. Pre-Deployment Checks

- Confirm Python 3 is installed on the workstation.
- Confirm a project virtual environment exists at `.venv/`.
- Confirm requirements are installed with `.venv/bin/pip install -r requirements.txt`.
- Confirm `config.yaml` has been created from `config.yaml.example`.
- Confirm `data/routes.json` and `data/stations.json` have been created from their example files.
- Confirm all default auth tokens have been changed from `change-me-*`.
- Confirm `quadruped.quadruped_ip` matches the target robot network address.
- Confirm `workstation.local_ip` matches the intended bind interface on the Linux workstation.
- Confirm `quadruped.sdk_port` matches the expected SDK port.
- Confirm Linux firewall and LAN access allow browser clients to reach the backend port, default `8080`.
- Confirm the workstation can reach the quadruped network segment if live hardware testing is planned.

## 2. Safety Checks

- Phase 1 deployment is for supervised testing only.
- Confirm the physical emergency stop procedure with the site team before any live run.
- Confirm the test lane and surrounding floor area are clear of people, carts, and obstacles.
- Confirm payload limits and carried sample weight are within the planned safe test envelope.
- Confirm operators understand that the obstacle detector is currently stubbed.
- Confirm operators understand that SLAM is currently odometry fallback only.
- Confirm site personnel understand software e-stop does not replace plant emergency procedures.

## 3. Startup Checklist

- Run the full automated test suite:
  ```bash
  uv run pytest -q
  ```
- Start the backend:
  ```bash
  uv run python main.py
  ```
- Alternative backend start command if needed:
  ```bash
  .venv/bin/python main.py
  ```
- Confirm the refactored Uvicorn app target is:
  `apps.logistics.api.rest:app`
- Confirm the health endpoint responds:
  `http://<workstation-host>:8080/health`
- Open the supervisor UI:
  `http://<workstation-host>:8080/ui/supervisor.html?token=<supervisor-token>`
- Open the operator UI:
  `http://<workstation-host>:8080/ui/operator.html?station_id=A&token=<operator-token>`
- Open the kiosk UI if used on station terminals:
  `http://<workstation-host>:8080/ui/kiosk.html?station_id=A&token=<operator-token>`

## 4. Functional Smoke Checklist

- Submit a task from the operator or kiosk UI.
- Confirm load from the operator or kiosk UI.
- Confirm unload from the operator or kiosk UI.
- Check that queue status updates in the operator or supervisor view.
- Test the e-stop endpoint from the supervisor flow.
- Test e-stop release from the supervisor flow.
- Check that the UI reconnects if the WebSocket is briefly interrupted.
- Check that the supervisor alerts panel updates when alerts are raised.
- Run the manual HTTP smoke script:
  ```bash
  ./apps/logistics/scripts/manual_e2e_smoke.sh
  ```
- Note that root `scripts/manual_e2e_smoke.sh` is only a compatibility wrapper around the canonical app script.

## 4a. Post-Refactor Runtime Smoke Test

1. Start backend:
   ```bash
   uv run python main.py
   ```
2. Check health:
   ```bash
   curl http://localhost:8080/health
   ```
3. Open:
   - `/ui/supervisor.html?token=<supervisor-token>`
   - `/ui/operator.html?station_id=A&token=<operator-token>`
   - `/ui/kiosk.html?station_id=A&token=<operator-token>`
4. Run manual smoke script:
   ```bash
   ./apps/logistics/scripts/manual_e2e_smoke.sh
   ```
5. Root `scripts/manual_e2e_smoke.sh` remains a compatibility wrapper only.

## 5. Route Commissioning Checklist

- Confirm each station ID in `data/stations.json` matches the physical station label on site.
- Confirm station coordinates are reasonable for the intended factory reference frame.
- Confirm waypoint sequence in `data/routes.json` matches the intended travel path.
- Test one route first at low speed and under close supervision.
- Validate each hold point used for load and unload interaction.
- Validate the return route back from QA or destination stations.
- Update `data/routes.json` after each commissioning correction and keep a copy of the previous version.

## 6. Failure Testing Checklist

- Briefly disconnect the workstation or robot network path in a controlled test.
- If practical in the test environment, stop or interrupt telemetry/state monitoring and observe system behavior.
- Verify the watchdog raises an alert for telemetry timeout or connection loss.
- Trigger a battery critical condition manually if a safe test method exists.
- Verify expected dock-task behavior after a battery-critical condition.
- Confirm the supervisor dashboard surfaces the resulting alerts and status changes.

## 7. Go / No-Go Criteria

- All automated tests pass.
- The manual smoke script passes.
- The supervisor dashboard shows quadruped and queue status.
- The operator or kiosk UI can submit a task successfully.
- E-stop works according to the current Phase 1 test plan.
- At least one route completes successfully under supervision.
- No unexplained errors remain in backend logs after the smoke run.

## 8. Rollback Notes

- Stop the backend process cleanly.
- Preserve the current SQLite database and log files before changing anything.
- Restore the previous known-good `config.yaml`, `data/routes.json`, and `data/stations.json`.
- Restart the backend with the restored configuration.
- Re-run `/health` and the basic UI load checks before resuming testing.

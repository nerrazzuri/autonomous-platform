# Multi-Robot Setup & Provisioning

This guide covers the operator-facing setup flow for the fleet-ready logistics and patrol platform.

## Install Dependencies

From the repository root:

```bash
pip install -r requirements.txt
```

Provisioning uses SSH to prepare robots over their default access point, so `paramiko` must be installed in the active Python environment.

## Browser Provisioning

Use the logistics provisioning page for non-technical setup staff:

1. Start the logistics app normally.
2. Open the provisioning page in the browser.
3. Click **Scan WiFi / Robot APs**.
4. Select the robot AP or type it manually.
5. Enter the factory WiFi SSID and password.
6. Choose the robot role: `logistics` or `patrol`.
7. Optionally enter `robot_id`, `display_name`, `pc_wifi_iface`, and SSH credentials.
8. Click **Start Provisioning** and wait for the job to finish.

The provisioning page will poll job status until it succeeds or fails, and it will refresh the provisioned robot list automatically after success.

## CLI Dry Run

You can validate the provisioning request shape without touching the robot:

```bash
python scripts/provision_cli.py \
  --dog-ap-ssid "D1-Ultra:aa:bb:cc:dd:ee" \
  --target-wifi-ssid "FACTORY_WIFI" \
  --target-wifi-password "secret" \
  --role logistics \
  --pc-wifi-iface wlan0 \
  --dry-run
```

Dry-run never provisions the robot and never writes `robots.yaml`.

## CLI Provisioning

For a real CLI-driven provisioning run:

```bash
python scripts/provision_cli.py \
  --dog-ap-ssid "D1-Ultra:aa:bb:cc:dd:ee" \
  --target-wifi-ssid "FACTORY_WIFI" \
  --target-wifi-password "secret" \
  --role logistics \
  --pc-wifi-iface wlan0 \
  --display-name "Logistics Robot 1"
```

On successful provisioning, the CLI persists the robot entry into `data/robots.yaml`.

## robots.yaml Location

Provisioned robots are stored in:

```text
data/robots.yaml
```

This file is used by the runtime loader during startup.

## Start the System After Provisioning

After at least one robot has been provisioned, start the platform the usual way for your deployment target. On startup, the runtime will load enabled robots from `data/robots.yaml`, create one platform per robot, and register them in the fleet registry.

## Verify Fleet Visibility

After startup, verify the fleet through the REST APIs:

- Logistics robots: `GET /robots` on the logistics app
- Patrol robots: `GET /robots` on the patrol app
- Legacy single-robot compatibility: `GET /quadruped/status`

You can also confirm the supervisor pages show the fleet panels and per-robot controls.

## Remove a Provisioned Robot

From the browser provisioning page:

1. Find the robot in the provisioned robots panel.
2. Click the remove button.
3. Confirm the delete action.

This updates `data/robots.yaml`. The change affects the next startup or reload cycle; it does not directly modify the live runtime registry in the current session.

## Network Requirements

Real robot provisioning requires:

- workstation WiFi access to the target factory network
- temporary reachability to the robot access point
- SSH reachability to the robot while connected through the AP

If the workstation cannot reach the robot AP or the factory WiFi, provisioning will fail even though the browser and CLI flows are available.

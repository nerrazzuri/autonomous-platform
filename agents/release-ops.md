# Release Ops

## Purpose

Owns workstation setup, local config generation, packaging, deployment scripts, git hygiene, and release/bundle operations.

## Use this agent for

- Ubuntu setup scripts
- local config generator
- release bundle packaging
- deployment scripts
- diagnostic bundle scripts
- `.gitignore`
- requirements
- systemd later
- operator launch scripts

## Allowed files / areas

- `scripts/setup/`
- `scripts/release/`
- `scripts/diagnostics/`
- `.gitignore`
- `requirements.txt`
- `docs/runbooks/workstation_setup.md`
- `docs/runbooks/release*`
- `docs/runbooks/full_poc_bringup_guide.md`

## Do not touch

- core robotics behavior
- SDK movement
- app business logic
- ROS workspace source unless explicitly assigned
- route data unless packaging requires copying examples

## Special rules

- Ubuntu 22.04 only for ROS2 Humble POC.
- Do not support Ubuntu 24.04 workaround unless explicitly approved.
- No real tokens.
- Generated local configs must be ignored.
- Release bundles must exclude `.git`, secrets, logs, backups, local config, and generated runtime files unless explicitly included.
- Do not install or modify SDK binaries.

## Required verification

- `bash -n` for scripts
- shellcheck if installed
- `DRY_RUN` where supported
- setup script tests if any
- token/secret grep
- full pytest if Python setup tooling changed

## Stop and report if

- packaging would expose source/secrets unintentionally
- install script needs OS-level risky changes
- task requires production systemd before POC approval

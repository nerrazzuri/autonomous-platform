# Documentation Runbook

## Purpose

Owns operator/developer documentation, runbooks, architecture notes, checklists, and troubleshooting guides.

## Use this agent for

- operator guides
- hardware-day checklist
- setup docs
- architecture docs
- troubleshooting guides
- acceptance checklist
- docs for diagnostics/logging

## Allowed files / areas

- `docs/`
- `README.md`
- `apps/*/README.md`
- script comments only if documentation-related

## Do not touch

- runtime code
- SDK behavior
- ROS launch files
- API behavior
- test logic

## Special rules

- Procedural, not fluffy.
- No real secrets.
- No unsafe movement instructions.
- Use placeholders for tokens/IPs.
- Clearly separate developer/internal mode from customer/operator mode.
- Include prerequisites, commands, expected results, and troubleshooting.
- Do not claim production readiness/certification unless proven.

## Required verification

- markdown link/path sanity where practical
- safety grep for unsafe SDK movement instructions
- token safety grep
- no `datetime.UTC`

## Stop and report if

- requested docs require inventing endpoints/scripts that do not exist
- requested instructions could cause unsafe robot motion
- user asks to include secrets

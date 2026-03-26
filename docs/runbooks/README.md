# Operational Runbooks

Role-based daily operating guides for the local LeanKit platform.

Each runbook follows the same structure: run the command, confirm the expected outcome, then use the troubleshooting notes when the result differs from the target state.

## Runbook standards

- Every numbered step includes a command or explicit action.
- Every step defines the expected outcome before moving on.
- Every step includes troubleshooting notes for the first likely failure modes.
- Escalation stays role-based so operators, admins, and developers do not improvise overlapping recovery flows.

## Daily operations workflow

1. Start with the runbook that matches the person performing the work.
2. Run each step in order and stop when the expected outcome does not match.
3. Use the troubleshooting notes to recover or collect evidence.
4. Escalate to the next role listed in the runbook instead of improvising a different flow.
5. Finish with the handoff checklist so the next person inherits the current state.

## Available runbooks

- [Operator runbook](./operator-runbook.md) for daily service checks, approvals, execution monitoring, and manual digest recovery.
- [Admin runbook](./admin-runbook.md) for configuration validation, migrations, channel readiness, and controlled restarts.
- [Developer runbook](./developer-runbook.md) for dependency sync, local startup, validation, and debugging.

## Role guide

| Role | Primary focus | Start with |
| --- | --- | --- |
| Operator | Daily readiness, work monitoring, and escalation evidence | `operator-runbook.md` |
| Admin | Configuration, migrations, secrets, and service recovery | `admin-runbook.md` |
| Developer | Local setup, validation, and debugging during delivery | `developer-runbook.md` |

## Daily start points

| Role | First command set | Success signal | Next runbook if blocked |
| --- | --- | --- | --- |
| Operator | Load `.env`, then `docker compose up -d archon-server archon-mcp archon-frontend` | Containers are up and `/api/services/heartbeat` returns `ready` | Admin for secrets, restarts, or migrations |
| Admin | Load `.env`, then check required vars and `/api/migrations/status` | Required config is present and `has_pending` matches expectation | Developer if clean restart still leaves the platform degraded |
| Developer | `cd python && uv sync --group all --group dev`, then `cd ../archon-ui-main && npm install` | Dependencies install cleanly and local services start | Admin if environment or secrets block reproduction |

## Recommended usage

- Start with the runbook that matches the role performing the work.
- Run commands from the repository root unless a step says otherwise.
- Load `.env` before any workflow that depends on platform host, port, or secret configuration.
- Capture the final handoff checklist items before closing the session so the next person inherits the current state instead of rediscovering it.

## Related references

- `docs/api-contracts/external-requests.md` for channel and service health endpoints.
- `docs/api-contracts/execution-runs.md` for execution-run query fields and payloads.
- `docs/api-contracts/approval-requests.md` for approval request list and decision routes.
- `docs/api-contracts/observability.md` for baseline health endpoints.
- `docs/operations/event-troubleshooting-runbook.md` for deeper event-path diagnosis after operator checks identify a delivery problem.

## Escalation map

| Situation | Primary role | Escalate to |
| --- | --- | --- |
| Service heartbeat is degraded or execution runs are stuck | Operator | Admin for configuration fixes, Developer for code regressions |
| Secrets, migrations, or container restarts are required | Admin | Developer when the issue reproduces after a clean restart |
| Local validation, debugging, or feature delivery work is in progress | Developer | Admin when environment or secret state blocks reproduction |

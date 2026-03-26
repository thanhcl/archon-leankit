# Operator Runbook

Daily operating guide for the person watching service readiness, execution flow, and outbound notifications.

Use each step in order during a normal shift: run the command, compare it to the expected outcome, and only move on once the result is understood.

## Shift objective

- Confirm the core platform is reachable.
- Detect stalled work, pending approvals, and degraded channels early.
- Capture enough evidence that the next role can continue without repeating the same checks.

## Related references

- `docs/api-contracts/external-requests.md`
- `docs/api-contracts/execution-runs.md`
- `docs/api-contracts/approval-requests.md`
- `docs/operations/event-troubleshooting-runbook.md`

## Scope

- Run commands from the repository root.
- Use this runbook for normal daily checks, manual recovery, and notification verification.
- This platform is deployed locally, so commands assume direct access to the machine running the stack.

## Session setup

Load the environment before running any checks:

```bash
set -a
[ -f .env ] && . ./.env
set +a
export HOST="${HOST:-localhost}"
export ARCHON_SERVER_PORT="${ARCHON_SERVER_PORT:-8181}"
export ARCHON_MCP_PORT="${ARCHON_MCP_PORT:-8051}"
```

Expected outcome:

- The shell session has the same host and port values used by Docker and the API.

Troubleshooting:

- If `.env` is missing, copy `.env.example` to `.env` and populate required values before continuing.
- If custom ports are in use, export them explicitly before running the checks.

## 1. Start the core platform

```bash
docker compose up -d archon-server archon-mcp archon-frontend
```

Expected outcome:

- Docker starts or refreshes the API server, MCP server, and frontend containers.
- `docker compose ps` shows the containers in `Up` or `healthy` state after startup finishes.

Troubleshooting:

- If a container exits immediately, inspect it with `docker compose logs --tail=200 archon-server` or the matching service name.
- If startup fails after an environment change, re-check `.env` for missing required settings such as `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`.

## 2. Confirm container state

```bash
docker compose ps
```

Expected outcome:

- `archon-server`, `archon-mcp`, and `archon-frontend` appear in the list.
- The server health column eventually reports `healthy` for `archon-server`.

Troubleshooting:

- If `archon-server` is `unhealthy`, inspect `docker compose logs --tail=200 archon-server`.
- If `archon-mcp` is not running, confirm that `archon-server` is healthy first because MCP depends on it.

## 3. Check platform heartbeat

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/services/heartbeat" | python -m json.tool
```

Expected outcome:

- The payload returns `status`.
- A healthy platform reports `status: "ready"`.
- `control_plane`, `archon_mcp`, and `external_channels` are present in the response.

Troubleshooting:

- If the request fails entirely, confirm that `archon-server` is running and listening on `ARCHON_SERVER_PORT`.
- If the response is `degraded`, inspect the `issues` array first because it contains the failing dependency names.

## 4. Check channel readiness

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/channels/heartbeat" | python -m json.tool
```

Expected outcome:

- The response includes `telegram` and `openclaw` health objects.
- A fully ready channel set reports `status: "ready"` with empty `issues`.

Troubleshooting:

- If Telegram is degraded with `bot-token-missing`, `chat-id-missing`, or `webhook-secret-missing`, hand off to the admin runbook to correct `.env` and restart the server.
- If OpenClaw is degraded with `ingest-secret-missing`, confirm `LEANKIT_OPENCLAW_INGEST_SECRET` is set and restart `archon-server`.

## 5. Review active and failed execution runs

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/execution-runs?status=running&limit=20" | python -m json.tool
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/execution-runs?status=failed&limit=20" | python -m json.tool
```

Expected outcome:

- The first command shows currently executing work.
- The second command highlights runs that need operator attention.
- Each response includes `runs`, `total_count`, and applied filters.

Troubleshooting:

- If failed runs accumulate, inspect recent server logs with `docker compose logs --tail=200 archon-server`.
- If a run appears stuck in `running`, review the linked task and recent engine activity before retrying or escalating.

## 6. Check pending approval requests

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/approval-requests?status=pending&limit=20" | python -m json.tool
```

Expected outcome:

- The response lists approvals waiting on a person or automation.
- An empty result means there is no current approval backlog.

Troubleshooting:

- If approvals remain pending longer than expected, confirm the originating workflow is still active and route the request to the appropriate approver.
- If the endpoint errors, inspect `archon-server` logs for approval service failures.

## 7. Run a manual Telegram digest cycle

Use this only when scheduled delivery needs verification or recovery.

```bash
curl -sS \
  -X POST "http://${HOST}:${ARCHON_SERVER_PORT}/api/channels/telegram/due-digest-cycle" \
  -H "Content-Type: application/json" \
  -d '{"force": true, "scheduler_origin": "manual-operator", "delivery_required": false}' \
  | python -m json.tool
```

Expected outcome:

- The response includes `delivery_mode`, `scheduler_mode`, `scheduler_origin`, and `delivery_ok`.
- A successful manual run returns a built digest summary and indicates whether delivery happened.

Troubleshooting:

- If `delivery_ok` is `false`, check the Telegram channel heartbeat and verify token and chat configuration.
- If the scheduler reports replay issues, inspect `/api/services/heartbeat` for `observability_replay` degradation.
- If the event exists but the live UI or digest path still looks wrong, continue with `docs/operations/event-troubleshooting-runbook.md`.

## 8. Capture logs for escalation

```bash
docker compose logs --tail=200 archon-server archon-mcp archon-frontend
```

Expected outcome:

- The command returns recent logs that can be attached to an issue or shared during handoff.

Troubleshooting:

- If logs are too noisy, rerun the command with a single service name.
- If the relevant event is older than 200 lines, increase the `--tail` value.

## Operator handoff checklist

- Record current `/api/services/heartbeat` status.
- Record whether any execution runs are failed or stuck.
- Record whether any approvals are pending.
- Record whether external channels are `ready` or `degraded`.
- Record whether deeper investigation moved into `docs/operations/event-troubleshooting-runbook.md`.

## Escalate when

- Move to the admin runbook when `.env`, secrets, migration status, or container restarts need to change.
- Move to the developer runbook when failed runs or approval errors reproduce after the platform is healthy.
- Open an incident with the captured logs when the same degraded status persists across repeated checks.

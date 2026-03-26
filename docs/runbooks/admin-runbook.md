# Admin Runbook

Daily administration guide for configuration, migrations, channel readiness, and controlled service restarts.

Use each step in order when making admin changes so configuration, restart, and verification work stays tied to a clear expected outcome.

## Change objective

- Validate required configuration before touching running services.
- Apply migrations in a controlled order.
- Leave the platform in a known healthy state with clear post-change evidence.

## Related references

- `docs/api-contracts/external-requests.md`
- `docs/api-contracts/observability.md`

## Scope

- Run commands from the repository root.
- Use this runbook when validating configuration, applying LeanKit migrations, and restoring degraded channel settings.
- Keep secrets in `.env`; do not paste them into command output or tickets.

## Session setup

```bash
set -a
[ -f .env ] && . ./.env
set +a
export HOST="${HOST:-localhost}"
export ARCHON_SERVER_PORT="${ARCHON_SERVER_PORT:-8181}"
```

Expected outcome:

- The shell uses the same environment values as the running platform.

Troubleshooting:

- If `.env` is absent, create it from `.env.example` before continuing.
- If the shell reports unset variables later in the runbook, reload the block above after editing `.env`.

## 1. Capture the pre-change baseline

```bash
docker compose ps
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/services/health" | python -m json.tool
```

Expected outcome:

- Container state and current service health are captured before any admin change.
- You can compare post-change health against this baseline instead of relying on memory.

Troubleshooting:

- If the API health endpoint is unavailable, start `archon-server` first with `docker compose up -d archon-server`.
- If the current baseline is already degraded, record the issue strings before applying migrations or rotating config.

## 2. Verify required configuration is present

```bash
for name in SUPABASE_URL SUPABASE_SERVICE_KEY; do
  if [ -n "${!name:-}" ]; then
    echo "OK ${name}"
  else
    echo "MISSING ${name}"
  fi
done

for name in LEANKIT_TELEGRAM_BOT_TOKEN LEANKIT_TELEGRAM_CHAT_ID LEANKIT_TELEGRAM_WEBHOOK_SECRET LEANKIT_OPENCLAW_INGEST_SECRET; do
  if [ -n "${!name:-}" ]; then
    echo "SET ${name}"
  else
    echo "UNSET ${name}"
  fi
done
```

Expected outcome:

- Required platform settings print as `OK`.
- Channel-specific settings print as `SET` when those integrations are enabled.

Troubleshooting:

- If `SUPABASE_URL` or `SUPABASE_SERVICE_KEY` is missing, stop here and fix `.env` before restarting anything.
- If Telegram or OpenClaw settings are `UNSET`, expect the related channel heartbeat to remain degraded until corrected.

## 3. Check migration status before changes

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/migrations/status" | python -m json.tool
```

Expected outcome:

- The response includes `has_pending`, `bootstrap_required`, `pending_count`, and `applied_count`.
- A stable environment normally reports `has_pending: false`.

Troubleshooting:

- If the endpoint is unavailable, start `archon-server` first with `docker compose up -d archon-server`.
- If `bootstrap_required` is `true`, initialize the base schema using `migration/complete_setup.sql` before applying LeanKit-specific migrations.

## 4. Apply local LeanKit migrations

```bash
make migrate-leankit-local
```

Expected outcome:

- The command completes without error and prints the LeanKit migration success message.

Troubleshooting:

- If the script fails, review the shell output for the specific SQL file or Supabase connection failure.
- If migrations partially apply, rerun `/api/migrations/status` to confirm which versions remain pending.

## 5. Re-check migration status after applying

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/migrations/status" | python -m json.tool
```

Expected outcome:

- `has_pending` is `false`.
- `pending_count` is `0`.

Troubleshooting:

- If pending migrations remain, confirm the migration script targeted the same Supabase instance configured in `.env`.
- If the response includes unexpected history, compare the migration list with files under `migration/0.1.0-leankit/`.

## 6. Perform a controlled platform restart

Run this after changing `.env`, updating code, or applying migrations that require a process reload.

```bash
docker compose up -d --build archon-server archon-mcp archon-frontend
docker compose ps
```

Expected outcome:

- Refreshed containers come back in `Up` state.
- `archon-server` transitions to `healthy` after its health check passes.

Troubleshooting:

- If the rebuild stalls, inspect Docker Desktop or daemon state first.
- If `archon-mcp` stays down, confirm the API server is healthy because MCP waits on it.

## 7. Validate service and channel health after admin changes

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/services/health" | python -m json.tool
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/channels/health" | python -m json.tool
```

Expected outcome:

- Both responses return structured JSON.
- Healthy services report `status: "ready"` and channel-specific `issues` are empty.

Troubleshooting:

- If `archon_mcp` is degraded, confirm the MCP container is running and reachable on its configured port.
- If `observability_replay` is degraded, verify the replay URL configuration before attempting notification recovery.

## 8. Verify Telegram readiness after secret or schedule changes

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/channels/telegram/health" | python -m json.tool
```

Expected outcome:

- `bot_configured`, `chat_configured`, and `webhook_secret_configured` reflect the current `.env` state.
- `send_ready` is `true` when bot token and chat id are configured.
- `digest_ready` is `true` when digest prerequisites are satisfied.

Troubleshooting:

- If `digest_ready` is `false` with `digest-timezone-invalid`, fix the timezone value in `.env` and restart `archon-server`.
- If `digest_scheduler_enabled` is `false`, confirm whether this is intentional before treating it as an incident.

## 9. Verify OpenClaw readiness after secret changes

```bash
curl -sS "http://${HOST}:${ARCHON_SERVER_PORT}/api/channels/openclaw/health" | python -m json.tool
```

Expected outcome:

- `ingest_secret_configured` is `true` when the ingress secret is present.
- `ingest_ready` is `true` when the route is ready to accept channel requests.

Troubleshooting:

- If `ingest_secret_configured` is `false`, set `LEANKIT_OPENCLAW_INGEST_SECRET` in `.env` and restart `archon-server`.
- If the route stays degraded after restart, inspect `docker compose logs --tail=200 archon-server` for channel initialization errors.

## Admin handoff checklist

- Record the current migration status.
- Record any `.env` variables that were added or rotated without including secret values.
- Record final `/api/services/health` and `/api/channels/health` status.
- Record any remaining degraded dependencies and the exact issue strings.

## Escalate when

- Move to the developer runbook when the platform stays degraded after configuration fixes, migrations, and a clean restart.
- Return work to the operator runbook after service and channel health are back to `ready`.
- Open a higher-severity incident when required configuration or migration state cannot be restored on the local stack.

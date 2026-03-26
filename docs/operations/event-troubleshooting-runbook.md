# Event Troubleshooting Runbook

## Purpose

Use this runbook when an operator needs to investigate missing, delayed, duplicated, or mis-correlated events across the LeanKit platform event flow.

The goal is to identify where the event stopped moving, capture enough evidence to explain the failure, and restore the broken dependency before retrying the originating action.

## Scope

- Run commands from the repository root unless a step says otherwise.
- Use this guide after the daily checks in `docs/runbooks/operator-runbook.md` confirm an event-path issue.
- Switch to `docs/runbooks/admin-runbook.md` when the diagnosis points to broken credentials, invalid environment variables, or service restarts.

## Scenario coverage

This runbook covers these common operator investigations:

1. Missing events
2. Duplicate events
3. Correlation failures
4. WebSocket disconnects or empty live feed
5. Event lag or delayed digests
6. Telegram delivery failures with healthy event creation

This guide is written for the current platform behavior:

- `Notifier` emits task and control-plane events
- the notifier posts live event payloads to the WebSocket bridge through `POST <websocket_url>/events`
- the notifier sends unified events to Observability ingest
- Telegram scheduled digests read recent unified events back from Observability replay
- external ingress requests deduplicate on `correlation_id`

The live WebSocket bridge in this runbook is a best-effort UI delivery path. It is separate from the polling-based Observability and progress APIs documented under `docs/api-contracts/observability.md`.

## Quick event path

1. An upstream action creates a task event or an external ingress request.
2. `ExternalRequestService` stores ingress records in `archon_external_requests` and deduplicates on `correlation_id`.
3. `Notifier` appends the event to its in-memory event log, posts to the WebSocket bridge, sends a unified event to Observability ingest, and fans out channel delivery.
4. `TelegramChannelService` reads recent unified events from Observability replay for due-digest cycles.

## Investigation workflow

Use the same sequence for every scenario so the first confirmed failure point is clear:

1. Confirm the source action and capture the earliest known UTC timestamp.
2. Locate the primary identifier for the flow, usually a `correlation_id`, request id, approval id, or digest key.
3. Check persistence, emission, replay, live delivery, and channel delivery in that order.
4. Confirm whether the failure affects one delivery path or every downstream path before retrying anything.
5. Restore the broken dependency before retrying the originating action.
6. Capture evidence before any replay or resend operation changes the observable state.

## Common investigation commands

Load the environment and set a base URL first:

```bash
set -a
[ -f .env ] && . ./.env
set +a
export HOST="${HOST:-localhost}"
export ARCHON_SERVER_PORT="${ARCHON_SERVER_PORT:-8181}"
export ARCHON_BASE="http://${HOST}:${ARCHON_SERVER_PORT}"
```

Check platform health:

```bash
curl -s "$ARCHON_BASE/api/services/health" | jq
curl -s "$ARCHON_BASE/api/channels/health" | jq
curl -s "$ARCHON_BASE/api/channels/telegram/health" | jq
```

Inspect recent server logs:

```bash
docker compose logs archon-server --tail=200 | rg "Event emitted|WebSocket send failed|Observability send failed|Failed to collect due digest events"
```

Inspect recent external requests:

```bash
curl -s "$ARCHON_BASE/api/external-requests?limit=20" | jq
```

Inspect recent approval requests:

```bash
curl -s "$ARCHON_BASE/api/approval-requests?limit=20" | jq
```

Filter logs around one event or request once you know the key:

```bash
export CORRELATION_ID="replace-with-correlation-id"
docker compose logs archon-server --tail=400 | rg "$CORRELATION_ID|Event emitted|WebSocket send failed|Observability send failed|Failed to collect due digest events"
```

## Baseline checklist

Before diving into one scenario, answer these five questions:

1. Did the source action complete successfully?
2. Did the platform store the source request or approval record?
3. Did the event reach Observability ingest and replay?
4. Did the live WebSocket bridge receive the event?
5. Did the downstream channel route the event immediately, to digest, or skip it by policy?

Record the first confirmed `correlation_id`, related object id, and UTC timestamp before you retry anything so the original failure evidence is preserved.

## Fast triage map

| Operator symptom | First place to look | Likely break point |
| --- | --- | --- |
| Event missing everywhere | Source record + `Event emitted` log | Upstream action or notifier emission |
| Live feed empty but digests still work | WebSocket bridge logs | Live bridge or client reconnect path |
| One user action creates repeats | `external_requests` records + dedupe inputs | Correlation or retry payload drift |
| Follow-up action becomes detached | `correlation_id`, `command_sequence_id`, `step_key` | Correlation rules or caller payload |
| Digest arrives late or empty | Replay health + digest scheduler state | Observability replay lag or digest window |
| Telegram send fails with healthy records | Telegram health issues | Channel credentials or delivery route |

---

## Scenario 1: Missing events

### Symptoms

- An operator expects a task or ingress event, but it never appears in the live UI feed.
- The Telegram digest does not include the expected event.
- An external request or approval record exists, but no downstream event appears.

### Diagnosis

1. Confirm the source record exists.
   - For Telegram or OpenClaw ingress, check `GET /api/external-requests`.
   - For approvals, check `GET /api/approval-requests`.
2. Inspect server logs for `Event emitted | event=...`.
   - If the source record exists but this log line is missing, the issue is before notifier emission.
3. Check platform health with `GET /api/services/health`.
   - A degraded `observability_replay` or downstream channel makes the event harder to find later.
4. If the event should have gone to Telegram, check `GET /api/channels/telegram/health`.
   - Look at `notify_events`, `digest_events`, `send_ready`, and `digest_ready`.
5. Distinguish live-feed loss from full event loss.
   - If downstream summaries still appear but the UI feed is empty, investigate the WebSocket bridge.
   - If neither replay nor delivery sees the event, investigate notifier emission and Observability ingest.

### Resolution

- Restore the failing dependency first instead of manually inserting partial state.
- If the source request was never stored, replay the original ingress action.
- If the source request exists but notifier emission failed upstream, rerun the originating workflow step.
- If the event was emitted but downstream delivery was degraded, recover the dependency and retry the operator action or digest cycle.
- If only one downstream path is missing, recover that path first instead of replaying the whole workflow blindly.
- Do not create synthetic replacement records without a clear audit trail.

---

## Scenario 2: Duplicate events

### Symptoms

- The same user action creates multiple external requests.
- Telegram sends duplicate notifications for what appears to be one event.
- Digest content repeats semantically identical items from the same replay window.

### Diagnosis

1. Identify whether the duplicates share business intent or share the exact same `correlation_id`.
2. Inspect recent `external_requests` records.
   - If the API caller received a deduplicated response, the service reused an existing record.
   - If multiple rows were created, the dedupe inputs changed between retries.
3. For OpenClaw ingress, inspect payload fields that affect dedupe:
   - `correlation_id`
   - `command_sequence_id`
   - `step_key`
   - normalized `title`, `summary`, `input_text`, and payload content
4. Determine which dedupe path was used.
   - explicit correlation id
   - `openclaw:<request_id>`
   - `openclaw-seq:<fingerprint>:<bucket>`
   - `openclaw-sem:<fingerprint>:<bucket>`
5. Check whether the duplicate is storage duplication or only delivery duplication.
   - Storage duplication creates extra persisted records.
   - Delivery duplication can happen when the same upstream action is retried with a different dedupe key.

### Resolution

- For retried caller operations, send a stable explicit `correlation_id`.
- For multi-step voice or command flows, keep `command_sequence_id` stable and use the correct `step_key` for the current phase.
- Avoid changing normalized retry inputs when you want deduplication to succeed.
- If one retry was legitimate new work, document the new business reason and give it a new explicit `correlation_id`.
- Clean up duplicate downstream work items through normal state transitions instead of deleting audit history.
- After the duplicate source is fixed, rerun only the missing downstream work, not the entire batch.

---

## Scenario 3: Correlation failures

### Symptoms

- A follow-up action does not attach to the expected prior request.
- Clarification turns create disconnected records instead of continuing the same chain.
- Approval or materialization artifacts cannot be traced back to the expected ingress event.

### Diagnosis

1. Find the source record and note its `correlation_id`.
2. For Telegram message ingress, confirm the generated key matches the expected pattern:
   - `telegram-update:<update_id>` or a message-id fallback
3. For OpenClaw ingress, confirm which correlation rule applied:
   - caller-supplied `correlation_id`
   - `request_id`-based key
   - sequence-step key based on `command_sequence_id` and `step_key`
   - semantic-window key based on normalized request content
4. If this is a clarification flow, inspect:
   - `command_sequence_id`
   - `step_key`
   - `clarification_response_to_request_id`
5. Compare the failed record with the prior successful record.
   - If the sequence id changed, the flow split.
   - If the step key changed unexpectedly, dedupe and correlation can miss.
   - If normalized content changed, semantic dedupe can generate a different key.

### Resolution

- Reissue the request with an explicit stable `correlation_id` when correlation must survive retries.
- Keep one `command_sequence_id` across one conversational flow.
- Use a new `step_key` only when moving to a genuinely new step, not when retrying the same step.
- Populate `clarification_response_to_request_id` for follow-up clarification answers.
- Confirm the caller is not regenerating identifiers between retries, refreshes, or reconnects.
- Prefer correcting the caller payload shape over patching stored correlation values after the fact.

---

## Scenario 4: WebSocket disconnects or empty live feed

### Symptoms

- Operators report that the live UI event stream is stale or empty.
- Workflows still complete and replay-backed summaries still work.
- Logs show event emission succeeded, but users do not see live updates.

### Diagnosis

1. Search server logs for `WebSocket send failed (non-fatal)`.
   - The notifier treats this path as best-effort, so the engine can keep running while live updates fail.
2. Confirm the configured bridge target.
   - The notifier converts `websocket_url` into an HTTP endpoint and posts to `/events`.
   - The default bridge target is `ws://localhost:4000`.
3. Check whether this is only a live-feed issue.
   - If Observability replay and Telegram digest still contain the event, the break is isolated to the bridge.
4. Verify reverse proxy or local bridge availability on the expected host and port.
5. Confirm clients are reconnecting after the bridge recovers.

### Resolution

- Restart or restore the WebSocket bridge service.
- Fix any proxy or routing break between the API process and the bridge `/events` endpoint.
- Once the bridge is healthy, reconnect the affected UI clients.
- If operators only need state visibility during the outage, use health and replay-backed APIs until live updates recover.
- Treat bridge recovery and client recovery as separate checks; a healthy bridge does not guarantee every browser session has re-subscribed.
- Do not treat WebSocket loss as proof that the control plane lost the event.

---

## Scenario 5: Event lag or delayed digests

### Symptoms

- Events arrive minutes late in downstream views.
- `due-digest-cycle` returns no recent events even though work clearly happened.
- A digest is skipped until the next schedule window.

### Diagnosis

1. Check `GET /api/services/health` for degraded `observability_replay`.
2. Check `GET /api/channels/telegram/health` and note:
   - `digest_scheduler_enabled`
   - `observability_replay_ready`
   - `digest_lookback_minutes`
   - `next_due_at`
   - `last_digest_sent_at`
   - `last_digest_key`
3. Compare the expected event timestamp with the replay collection cutoff.
   - `collect_due_digest_events()` excludes events older than the configured lookback window.
4. Validate scheduler timing.
   - A digest only sends in the configured due minute unless `force=true` is used.
5. Check timezone validity.
   - An invalid digest timezone degrades digest readiness.

### Resolution

- Restore Observability replay reachability before retrying scheduled digest work.
- Correct invalid digest timezone or scheduler configuration.
- If the event missed the window, rerun the digest cycle with `force=true` after replay is healthy.
- Increase the lookback window if normal replay delay exceeds the current setting.
- If lag is isolated to one scheduler origin, compare the current scheduler host with the expected primary or fallback origin before changing timing.
- If lag affects all downstream paths, investigate ingest latency before changing digest settings.

---

## Scenario 6: Telegram delivery failures with healthy event creation

### Symptoms

- The event exists in platform records, but no Telegram notification arrives.
- Digest preview works, but digest send fails.
- Health shows a degraded Telegram channel even though the rest of the platform is ready.

### Diagnosis

1. Check `GET /api/channels/telegram/health`.
2. Focus on these fields:
   - `send_ready`
   - `digest_ready`
   - `issues`
   - `notify_events`
   - `digest_events`
3. Interpret the common health issues:
   - `bot-token-missing`
   - `chat-id-missing`
   - `webhook-secret-missing`
   - `digest-scheduler-disabled`
   - `observability-replay-missing`
   - `digest-timezone-invalid`
4. Confirm the event is routed the way you expect.
   - Critical events send immediately.
   - Configured notify events send immediately.
   - Configured digest events wait for the digest path.
5. If you are troubleshooting a scheduled digest, remember that summary generation can succeed even when actual delivery is best-effort.

### Resolution

- Add the missing bot token, chat id, webhook secret, or replay URL.
- Correct routing configuration if the event was assigned to the wrong path.
- Re-run `POST /api/channels/telegram/digest-preview` first to confirm content, then `POST /api/channels/telegram/digest-send` or `POST /api/channels/telegram/due-digest-cycle` after the channel is ready.
- If immediate sends still fail after health is green, inspect channel transport logs and retry one event instead of replaying the full backlog.

## Evidence to capture before escalation

Capture the following before handing the issue to engineering:

- the exact event type, request type, or digest job being investigated
- the affected `correlation_id`, request id, approval id, or digest key
- UTC timestamps for source creation, expected delivery, and observed failure
- health payloads from `/api/services/health`, `/api/channels/health`, and `/api/channels/telegram/health`
- the relevant log lines around storage, notifier emission, replay collection, and downstream delivery

## Escalation guidance

Escalate beyond normal operator handling when any of these are true:

- events are being stored with the wrong correlation or linked object ids
- Observability ingest is silently dropping events across multiple workflows
- duplicate records are materializing downstream despite stable dedupe keys
- WebSocket bridge recovery does not restore live delivery after client reconnects
- replay delay exceeds the digest lookback even after service recovery

When escalating, include:

- the exact event or request type
- the affected `correlation_id`
- relevant request ids or approval ids
- timestamps in UTC
- health payloads from `/api/services/health` and `/api/channels/telegram/health`
- the relevant log lines around emission, replay collection, and delivery

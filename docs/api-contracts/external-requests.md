# External Requests API

## Purpose

First-class control-plane ingress for requests coming from external channels like
Telegram or OpenClaw.

## Endpoints

### `GET /api/external-requests`

Query params:

| Name | Type | Required | Description |
| --- | --- | --- | --- |
| `project_id` | string | No | Filter by project |
| `source_channel` | string | No | Filter by ingress channel |
| `request_type` | string | No | Filter by request type such as `architect-request` |
| `status` | string | No | `received`, `materialized`, `failed`, `cancelled` |
| `limit` | integer | No | Max rows, default `50` |

### `GET /api/external-requests/{request_id}`

Returns one persisted external request.

### `POST /api/external-requests`

Creates an external request and can optionally materialize it as either:

- a real task in `archon_tasks`
- a real approval workflow in `archon_approval_requests`

Example body:

```json
{
  "source_channel": "telegram",
  "request_type": "task-request",
  "title": "Fix auth bug",
  "summary": "Investigate login failures reported by owner",
  "materialize_as": "task",
  "project_id": "proj-001",
  "source_app": "archon-leankit",
  "actor_display": "Owner",
  "task_template": {
    "priority": "high",
    "task_type": "bug",
    "tags": ["external", "owner-request"]
  }
}
```

### `POST /api/channels/openclaw/ingest`

OpenClaw-specific ingress route that wraps the same control-plane
`external_requests` model.

Example body:

```json
{
  "request_type": "task-request",
  "title": "Create auth backlog",
  "summary": "Generate implementation tasks for auth hardening",
  "materialize_as": "task",
  "project_id": "proj-001",
  "input_modality": "voice",
  "input_text": "create tasks for auth hardening",
  "actor_display": "openclaw",
  "payload": {
    "voice_transcript": "create tasks for auth hardening"
  }
}
```

### `POST /api/channels/openclaw/architect-plan`

OpenClaw-specific architect planning route. This records an
`external_request` with `request_type=architect-request`, then resolves a
structured plan through the architect-provider boundary.

Example body:

```json
{
  "title": "Plan auth hardening",
  "summary": "Propose the next execution steps for auth hardening",
  "project_id": "proj-001",
  "input_modality": "text",
  "input_text": "Plan auth hardening and create the first execution tasks",
  "command_sequence_id": "seq-123",
  "step_key": "architect-plan",
  "architect_provider": "chatgpt-codex",
  "architect_model": "gpt-5.4"
}
```

Follow-up clarification example:

```json
{
  "title": "Plan auth hardening",
  "summary": "Yes, decompose this into tasks now.",
  "project_id": "proj-001",
  "input_modality": "voice",
  "input_text": "Yes, decompose this into tasks now.",
  "command_sequence_id": "seq-123",
  "step_key": "clarification-answer",
  "clarification_response_to_request_id": "ext-open-prev",
  "clarification_answers": [
    "Yes, decompose this into tasks now."
  ],
  "architect_provider": "chatgpt-codex"
}
```

Clarification-resolution behavior:

- follow-up clarification turns should reuse the same `command_sequence_id`
- `step_key` should move to a resolution step such as `clarification-answer`
- `clarification_response_to_request_id` should point at the prior architect
  request that asked the unresolved question
- Archon records the new answer on the follow-up request payload and also marks
  the prior architect request payload with:
  - `clarification_status=resolved`
  - `clarification_resolved_by_request_id`
  - `clarification_resolved_at`
  - `clarification_resolution_step_key`
  - the normalized clarification answers
- this keeps one auditable conversation chain across voice and text follow-up
  turns without creating a second planner-specific state model

### `GET /api/channels/openclaw/health`

Returns OpenClaw ingress readiness, replay-protection flags, and any
configuration issues.

### `GET /api/channels/openclaw/heartbeat`

Automation-friendly alias of the health surface. Use this for lightweight
readiness checks before sending voice or message ingress.

### `GET /api/channels/health`

Returns an aggregated heartbeat across Telegram and OpenClaw, including overall
status, ready/degraded counts, prefixed issues, and the nested per-channel
health payloads used by governance and operator surfaces.

### `GET /api/channels/heartbeat`

Automation-friendly alias of the aggregated channel heartbeat surface.

### `GET /api/services/health`

Returns a broader pilot-readiness snapshot that aggregates:

- Archon control-plane self status
- Archon MCP reachability
- Observability replay reachability
- nested external-channel heartbeat across Telegram and OpenClaw

### `GET /api/services/heartbeat`

Automation-friendly alias of the aggregated service-health surface.

### `POST /api/channels/telegram/due-digest-cycle`

Runs one scheduler-backed digest cycle. Unlike `due-digest`, this route does
not require caller-supplied events. It pulls recent unified events from
Observability replay, applies the current digest lookback window, and sends a
scheduled digest if the configured window is due.

Scheduler-related request fields now accepted by both `due-digest` and
`due-digest-cycle`:

- `scheduler_origin`: identifies the caller such as `primary` or `fallback`
- `delivery_required`: when `false`, delivery failure remains a best-effort
  result for a summary-only job instead of becoming a control-plane failure

Digest scheduler responses may now also include:

- `scheduler_origin`
- `authority_scope` with `summary-only`
- `job_status`
- `delivery_ok`

## Notes

- `correlation_id` is generated automatically if omitted.
- `materialize_as=task` requires `project_id`.
- `input_modality` accepts `voice` or `text`; both now flow through the same
  `external_requests` control-plane model instead of separate channel-specific
  task creation paths.
- `input_text`, `transcript_confidence`, and `audio_reference` are normalized
  into request payload metadata so governance and operator surfaces can inspect
  the original ingress context.
- all created records are auditable and emit unified events through the engine
  notifier pipeline.
- OpenClaw ingress is persisted as `source_channel=openclaw` and does not bypass
  the `external_requests` control-plane model.
- architect-plan responses are stored back into the request payload as
  `architect_plan` metadata for traceability and later replay
- when an architect plan recommends task materialization and has suggested task
  items, Archon now materializes those tasks directly into `archon_tasks`
  instead of stopping at a plan-only external request record
- OpenClaw now applies semantic duplicate detection for retries when explicit
  request identifiers are absent, while preserving explicit `correlation_id`
  precedence when callers provide one
- OpenClaw multi-step flows may provide `command_sequence_id` and `step_key`
  so replay protection stays scoped to one sequence step
- semantic and sequence-based OpenClaw dedupe now operate inside a replay
  window instead of collapsing identical requests forever
- OpenClaw command sequences now persist a lightweight
  `openclaw_sequence_snapshot` into request payloads so governance and operator
  surfaces can see prior step count, current step, closure state, and
  recommended next request types
- architect planning requests now also persist `openclaw_sequence_context`
  into request payloads so planner calls can carry recent multi-turn context
  from the same `command_sequence_id`
- architect-plan responses may now include `clarifying_questions`; callers can
  use those to drive a follow-up voice or text turn before materializing more
  work
- follow-up clarification turns should reuse the same `command_sequence_id` and
  switch `step_key` to a resolution step such as `clarification-answer`
- when a follow-up clarification references `clarification_response_to_request_id`,
  Archon now marks the earlier architect request as resolved by the newer turn
  so governance and operator surfaces can follow the conversation chain
- Telegram due-digest scheduler flows now record `scheduler_origin`,
  `authority_scope=summary-only`, and `delivery_required` in their audit
  payloads so fallback schedulers do not become workflow authority

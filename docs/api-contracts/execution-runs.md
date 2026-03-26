# Execution Runs API Contract

Base URL: `/api/execution-runs`

Execution runs are runtime records for task execution attempts, retries, and review stages.

## Field Naming Convention

All fields use **snake_case**.

**Execution Run Statuses**: `queued`, `running`, `reviewing`, `completed`, `failed`, `cancelled`

**Execution Run Stages**: `execute`, `architect-review`, `code-review`, `retry`

---

## GET /api/execution-runs

List execution runs with optional filtering.

### Query Parameters

| Parameter    | Type   | Required | Default | Description                |
|--------------|--------|----------|---------|----------------------------|
| `task_id`    | string | No       | —       | Filter by task             |
| `project_id` | string | No       | —       | Filter by project          |
| `bootstrap_plan_id` | string | No | — | Filter by bootstrap plan correlation id |
| `status`     | string | No       | —       | Filter by run status       |
| `stage`      | string | No       | —       | Filter by run stage        |
| `limit`      | int    | No       | `50`    | Maximum runs to return     |

### Response Example

```json
{
  "runs": [
    {
      "id": "run-abc123",
      "task_id": "task-def456",
      "project_id": "proj-ghi789",
      "status": "running",
      "stage": "execute",
      "started_at": "2026-03-20T03:00:00Z",
      "engine_id": "engine-main",
      "session_id": "sess-001",
      "model": "claude-sonnet-4-6",
      "retry_index": 0,
      "finished_at": null,
      "duration_seconds": null,
      "token_input": null,
      "token_output": null,
      "cost_usd": null,
      "result_summary": null,
      "error_summary": null,
      "metadata": {
        "bootstrap_plan_id": "plan-001"
      }
    }
  ],
  "total_count": 1,
  "filters_applied": "task_id=task-def456, bootstrap_plan_id=plan-001"
}
```

### Notes

- `bootstrap_plan_id` is resolved from normalized execution-run metadata and is intended for tracing bootstrap-plan materialization into runtime attempts.

---

## GET /api/execution-runs/{run_id}

Get a single execution run by ID.

---

## POST /api/execution-runs

Create a new execution run.

### Request Body

```json
{
  "task_id": "task-def456",
  "project_id": "proj-ghi789",
  "status": "queued",
  "stage": "execute",
  "engine_id": "engine-main",
  "session_id": "sess-001",
  "model": "claude-sonnet-4-6",
  "retry_index": 0,
  "metadata": {}
}
```

---

## PATCH /api/execution-runs/{run_id}

Update an execution run.

Typical updates include:

- moving `status` from `queued` to `running`
- attaching `finished_at`
- storing tokens and cost
- adding `result_summary` or `error_summary`

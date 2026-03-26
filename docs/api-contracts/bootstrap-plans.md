# Bootstrap Plans API

Bootstrap plans are first-class control-plane records that capture:

- the requested architect provider
- the resolved provider or fallback strategy
- the generated bootstrap task graph
- any provider-generated backlog items stored for later expansion
- the materialized tasks created from that plan

---

## GET /api/projects/{project_id}/bootstrap-plans

List bootstrap plans for a project.

### Query Parameters

| Name | Type | Required | Description |
|------|------|----------|-------------|
| `limit` | int | No | Maximum number of plans to return. Default: `20` |

### Response

```json
{
  "plans": [
    {
      "id": "plan-001",
      "project_id": "proj-001",
      "requested_provider": "chatgpt-codex",
      "resolved_provider": "chatgpt-codex",
      "strategy": "provider-generated",
      "model": "gpt-5.4",
      "template": "nextjs-app",
      "project_type": "web-app",
      "bootstrap_policy": "strict",
      "source_app": "virtual-office",
      "status": "materialized",
      "plan_items": [],
      "created_tasks": [],
      "metadata": {},
      "created_at": "2026-03-21T10:00:00Z",
      "updated_at": "2026-03-21T10:00:00Z"
    }
  ],
  "total_count": 1
}
```

---

## GET /api/bootstrap-plans/{plan_id}

Get a single bootstrap plan by ID.

### Response

Returns one bootstrap plan record with the same shape as the list item above.

---

## POST /api/bootstrap-plans/{plan_id}/materialize-backlog

Materialize any backlog items stored on a persisted bootstrap plan into real
project tasks.

This endpoint is intended for later expansion flows after initial project
creation. It only creates backlog items that have not already been materialized.

### Response

```json
{
  "plan": {
    "id": "plan-001",
    "project_id": "proj-001",
    "status": "expanded",
    "created_tasks": []
  },
  "created_tasks": [
    {
      "id": "task-010",
      "plan_key": "api-contracts",
      "title": "Implement API contracts",
      "status": "approved",
      "tags": ["project-bootstrap", "bootstrap-derived-backlog", "bootstrap-plan:plan-001"],
      "created_from": "project-bootstrap-derived-1",
      "blocked_by": ["task-bootstrap-followup"]
    }
  ],
  "skipped": 0
}
```

### Notes

- Derived backlog tasks remain tagged with `bootstrap-plan:<plan_id>`
- Dependency chaining is preserved through `blocked_by`
- Already materialized backlog items are skipped rather than duplicated

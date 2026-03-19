# Tasks API Contract

Base URL: `/api/tasks`

## Field Naming Convention

All fields use **snake_case**. Database values are used directly (no mapping layers).

**Task Statuses**: `draft`, `proposed`, `approved`, `planning`, `owner-qa`, `assigned`, `executing`, `architect-review`, `review`, `done`, `failed`, `escalated`, `on-hold`, `cancelled`

---

## GET /api/tasks

List tasks with optional filtering and pagination.

### Query Parameters

| Parameter             | Type    | Required | Default | Description                          |
|-----------------------|---------|----------|---------|--------------------------------------|
| `status`              | string  | No       | —       | Filter by task status                |
| `project_id`          | string  | No       | —       | Filter by project                    |
| `include_closed`      | bool    | No       | `true`  | Include done/cancelled tasks         |
| `page`                | int     | No       | `1`     | Page number                          |
| `per_page`            | int     | No       | `10`    | Items per page                       |
| `exclude_large_fields`| bool    | No       | `false` | Omit large fields from response      |
| `q`                   | string  | No       | —       | Keyword search query                 |

### Response Example

```json
{
  "tasks": [
    {
      "id": "t-abc123",
      "project_id": "proj-def456",
      "title": "Implement auth middleware",
      "description": "Add JWT validation to all protected routes",
      "status": "assigned",
      "assignee": "AI IDE Agent",
      "task_order": 80,
      "priority": "high",
      "feature": "authentication",
      "owner": "User",
      "acceptance_criteria": [
        "JWT tokens are validated on every request",
        "Invalid tokens return 401"
      ],
      "execution_result": null,
      "architect_review": null,
      "execution_prompt": "Implement JWT middleware...",
      "source_app": "virtual-office",
      "complexity": "complex",
      "max_retries": 3,
      "created_at": "2026-03-15T10:30:00Z",
      "updated_at": "2026-03-16T14:20:00Z"
    }
  ],
  "pagination": {
    "total": 42,
    "page": 1,
    "per_page": 10,
    "pages": 5
  }
}
```

---

## GET /api/tasks/{task_id}

Get a single task by ID.

### Path Parameters

| Parameter | Type   | Required | Description     |
|-----------|--------|----------|-----------------|
| `task_id` | string | Yes      | The task UUID   |

### Response Example

```json
{
  "id": "t-abc123",
  "project_id": "proj-def456",
  "title": "Implement auth middleware",
  "description": "Add JWT validation to all protected routes",
  "status": "assigned",
  "assignee": "AI IDE Agent",
  "task_order": 80,
  "priority": "high",
  "feature": "authentication",
  "owner": "User",
  "acceptance_criteria": [
    "JWT tokens are validated on every request",
    "Invalid tokens return 401"
  ],
  "execution_result": null,
  "architect_review": null,
  "execution_prompt": "Implement JWT middleware...",
  "source_app": "virtual-office",
  "complexity": "complex",
  "max_retries": 3,
  "created_at": "2026-03-15T10:30:00Z",
  "updated_at": "2026-03-16T14:20:00Z"
}
```

---

## POST /api/tasks

Create a new task.

### Request Body

```json
{
  "project_id": "proj-def456",
  "title": "Implement auth middleware",
  "description": "Add JWT validation to all protected routes",
  "status": "draft",
  "assignee": "User",
  "task_order": 80,
  "priority": "high",
  "feature": "authentication",
  "owner": "User",
  "acceptance_criteria": ["JWT tokens are validated"],
  "execution_prompt": "Implement JWT middleware...",
  "source_app": "virtual-office",
  "complexity": "complex",
  "max_retries": 3
}
```

| Field                | Type     | Required | Default    | Description                    |
|----------------------|----------|----------|------------|--------------------------------|
| `project_id`         | string   | Yes      | —          | Parent project ID              |
| `title`              | string   | Yes      | —          | Task title                     |
| `description`        | string   | No       | —          | Task description               |
| `status`             | string   | No       | `"draft"`  | Initial status                 |
| `assignee`           | string   | No       | `"User"`   | Assignee name                  |
| `task_order`         | int      | No       | `0`        | Priority order (0-100)         |
| `priority`           | string   | No       | `"medium"` | Priority level                 |
| `feature`            | string   | No       | —          | Feature grouping               |
| `owner`              | string   | No       | —          | Task owner                     |
| `acceptance_criteria`| array    | No       | —          | List of criteria strings        |
| `execution_prompt`   | string   | No       | —          | Prompt for AI execution        |
| `source_app`         | string   | No       | —          | Originating application        |
| `complexity`         | string   | No       | `"simple"` | `simple` or `complex`          |
| `max_retries`        | int      | No       | `3`        | Max execution retry attempts   |

### Response

Returns the created task object (same shape as GET single task).

---

## PUT /api/tasks/{task_id}

Update a task. All fields are optional.

### Request Body

```json
{
  "title": "Updated title",
  "status": "review",
  "priority": "critical"
}
```

### Response

Returns the updated task object.

---

## DELETE /api/tasks/{task_id}

Delete a task.

### Response Example

```json
{
  "message": "Task t-abc123 deleted"
}
```

---

## POST /api/tasks/{task_id}/transition

Transition a task to a new status with state machine validation.

### Request Body

```json
{
  "new_status": "executing",
  "changed_by": "virtual-office",
  "reason": "Starting automated execution"
}
```

| Field        | Type   | Required | Default | Description                                    |
|--------------|--------|----------|---------|------------------------------------------------|
| `new_status` | string | Yes      | —       | Target status                                  |
| `changed_by` | string | No       | `"api"` | Who triggered the transition                   |
| `reason`     | string | No       | —       | Required for cancellations and rejections      |

### Response Example

```json
{
  "message": "Task transitioned successfully",
  "task": {
    "id": "t-abc123",
    "status": "executing"
  },
  "transition": {
    "from": "assigned",
    "to": "executing",
    "changed_by": "virtual-office",
    "timestamp": "2026-03-18T09:15:00Z"
  }
}
```

### Valid State Transitions

| From                | Allowed Transitions                                          |
|---------------------|--------------------------------------------------------------|
| `draft`             | `proposed`, `approved`, `cancelled`                          |
| `proposed`          | `approved`, `cancelled`                                      |
| `approved`          | `planning` (complex), `assigned` (simple), `cancelled`       |
| `planning`          | `owner-qa`, `assigned`, `cancelled`                          |
| `owner-qa`          | `assigned`, `cancelled`                                      |
| `assigned`          | `executing`, `cancelled`                                     |
| `executing`         | `architect-review`, `failed`, `cancelled`                    |
| `architect-review`  | `review`, `assigned` (retry), `escalated`, `cancelled`       |
| `review`            | `done`, `assigned` (owner reject), `cancelled`               |
| `failed`            | `assigned` (retry), `escalated`, `cancelled`                 |
| `escalated`         | `assigned`, `on-hold`, `cancelled`                           |
| `on-hold`           | `approved` (resume), `cancelled`                             |
| `done`, `cancelled` | Terminal — no further transitions                            |

---

## GET /api/tasks/{task_id}/history

Get the state transition history for a task.

### Response Example

```json
{
  "task_id": "t-abc123",
  "state_history": [
    {
      "from": "draft",
      "to": "approved",
      "changed_by": "User",
      "timestamp": "2026-03-15T10:30:00Z",
      "reason": null
    },
    {
      "from": "approved",
      "to": "assigned",
      "changed_by": "director",
      "timestamp": "2026-03-15T11:00:00Z",
      "reason": null
    }
  ]
}
```

---

## GET /api/tasks/{task_id}/next-states

Get the valid next states for a task's current status.

### Response Example

```json
{
  "task_id": "t-abc123",
  "current_status": "assigned",
  "next_states": ["executing", "cancelled"]
}
```

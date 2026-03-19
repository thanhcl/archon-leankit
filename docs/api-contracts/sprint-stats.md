# Sprint Stats & Metrics API Contract

There is no dedicated `/api/projects/{id}/sprint-stats` endpoint. Sprint-level statistics are derived from the following endpoints.

---

## GET /api/projects/task-counts

Primary source for project-level task statistics. See [projects.md](projects.md#get-apiprojectstask-counts) for full contract.

### Response Example

```json
{
  "projects": [
    {
      "id": "proj-def456",
      "title": "Virtual Office Backend",
      "task_counts": {
        "total": 25,
        "draft": 3,
        "proposed": 2,
        "approved": 1,
        "planning": 0,
        "owner-qa": 1,
        "assigned": 4,
        "executing": 2,
        "architect-review": 1,
        "review": 3,
        "done": 6,
        "failed": 1,
        "escalated": 0,
        "on-hold": 1,
        "cancelled": 0
      }
    }
  ],
  "timestamp": "2026-03-18T12:00:00Z"
}
```

---

## GET /api/learnings/stats

Learning and improvement statistics.

### Response Example

```json
{
  "total_learnings": 45,
  "by_type": {
    "error": 12,
    "correction": 8,
    "best_practice": 15,
    "knowledge_gap": 10
  },
  "by_area": {
    "backend": 20,
    "frontend": 10,
    "tests": 8,
    "security": 7
  },
  "by_status": {
    "active": 30,
    "promoted": 10,
    "dismissed": 5
  }
}
```

---

## GET /api/patterns/stats

Code pattern statistics.

### Response Example

```json
{
  "total_patterns": 18,
  "by_category": {
    "security": 4,
    "error-handling": 5,
    "testing": 3,
    "architecture": 4,
    "performance": 2
  },
  "by_status": {
    "active": 12,
    "validated": 4,
    "deprecated": 2
  }
}
```

---

## Deriving Sprint Statistics

To build a sprint dashboard, combine data from:

1. **Task counts** (`GET /api/projects/task-counts`) — status distribution per project
2. **Task list** (`GET /api/tasks?project_id=X&status=doing`) — active work items
3. **Task history** (`GET /api/tasks/{id}/history`) — state transition timestamps for velocity
4. **Learnings stats** (`GET /api/learnings/stats`) — improvement metrics
5. **Pattern stats** (`GET /api/patterns/stats`) — code quality metrics

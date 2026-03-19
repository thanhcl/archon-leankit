# Projects API Contract

Base URL: `/api/projects`

## Field Naming Convention

All fields use **snake_case**. ETag caching is supported on list endpoints via `If-None-Match` / `304 Not Modified`.

---

## GET /api/projects

List all projects.

### Query Parameters

| Parameter         | Type   | Required | Default | Description                                        |
|-------------------|--------|----------|---------|----------------------------------------------------|
| `include_content` | bool   | No       | `true`  | If false, returns lightweight metadata + statistics |

### Request Headers

| Header           | Description                           |
|------------------|---------------------------------------|
| `If-None-Match`  | ETag from previous response (caching) |

### Response Headers

| Header           | Description                           |
|------------------|---------------------------------------|
| `ETag`           | Hash of current data (RFC 7232)       |
| `Cache-Control`  | `no-cache, must-revalidate`           |
| `Last-Modified`  | Data timestamp                        |

### Response Example

```json
{
  "projects": [
    {
      "id": "proj-def456",
      "title": "Virtual Office Backend",
      "description": "Backend services for the Virtual Office application",
      "github_repo": "org/virtual-office",
      "docs": [],
      "features": [
        {
          "name": "Task Management",
          "status": "in_progress",
          "components": ["task-board", "kanban"]
        }
      ],
      "data": [],
      "technical_sources": ["src_abc123"],
      "business_sources": ["src_xyz789"],
      "pinned": true,
      "source_app": "virtual-office",
      "layout_id": "layout-001",
      "team_config": [
        {
          "role": "backend-developer",
          "agent_id": "agent-001",
          "capabilities": ["python", "fastapi"]
        }
      ],
      "director_config": {
        "model": "claude-opus-4-6",
        "strategy": "parallel"
      },
      "team_lead_config": {
        "review_mode": "self-review",
        "auto_assign": true
      },
      "office_settings": {
        "theme": "tron",
        "notifications": true
      },
      "created_at": "2026-03-01T08:00:00Z",
      "updated_at": "2026-03-18T12:00:00Z"
    }
  ],
  "timestamp": "2026-03-18T12:00:00Z",
  "count": 1
}
```

---

## GET /api/projects/office-configs

Optimized endpoint for Virtual Office — returns only office-related fields.

### Response Example

```json
{
  "office_configs": [
    {
      "id": "proj-def456",
      "title": "Virtual Office Backend",
      "description": "Backend services for the Virtual Office application",
      "source_app": "virtual-office",
      "layout_id": "layout-001",
      "team_config": [
        {
          "role": "backend-developer",
          "agent_id": "agent-001",
          "capabilities": ["python", "fastapi"]
        }
      ],
      "director_config": {
        "model": "claude-opus-4-6",
        "strategy": "parallel"
      },
      "team_lead_config": {
        "review_mode": "self-review",
        "auto_assign": true
      },
      "office_settings": {
        "theme": "tron",
        "notifications": true
      }
    }
  ],
  "count": 1
}
```

---

## GET /api/projects/{project_id}

Get a single project by ID.

### Response

Returns a full project object (same shape as items in the list response).

---

## POST /api/projects

Create a new project.

### Request Body

```json
{
  "title": "New Project",
  "description": "Project description",
  "github_repo": "org/repo",
  "docs": [],
  "features": [],
  "data": [],
  "technical_sources": [],
  "business_sources": [],
  "pinned": false,
  "source_app": "virtual-office",
  "layout_id": "layout-001",
  "team_config": [],
  "director_config": {},
  "team_lead_config": {},
  "office_settings": {}
}
```

| Field              | Type    | Required | Description                        |
|--------------------|---------|----------|------------------------------------|
| `title`            | string  | Yes      | Project title                      |
| `description`      | string  | No       | Project description                |
| `github_repo`      | string  | No       | GitHub repository reference        |
| `docs`             | array   | No       | Project documentation              |
| `features`         | array   | No       | Feature definitions                |
| `data`             | array   | No       | Project data                       |
| `technical_sources`| array   | No       | Knowledge source IDs (technical)   |
| `business_sources` | array   | No       | Knowledge source IDs (business)    |
| `pinned`           | bool    | No       | Pin project to top                 |
| `source_app`       | string  | No       | Originating application            |
| `layout_id`        | string  | No       | Virtual Office layout ID           |
| `team_config`      | array   | No       | Agent team configuration           |
| `director_config`  | object  | No       | Director agent configuration       |
| `team_lead_config` | object  | No       | Team lead configuration            |
| `office_settings`  | object  | No       | Virtual Office settings            |

### Response

Returns the created project object.

---

## PUT /api/projects/{project_id}

Update a project. All fields are optional.

### Request Body

```json
{
  "title": "Updated Title",
  "office_settings": { "theme": "dark" }
}
```

### Response

Returns the updated project object.

---

## DELETE /api/projects/{project_id}

Delete a project and all associated tasks/documents.

### Response Example

```json
{
  "message": "Project proj-def456 deleted"
}
```

---

## GET /api/projects/{project_id}/tasks

Get tasks scoped to a project.

### Query Parameters

| Parameter        | Type   | Required | Default | Description                  |
|------------------|--------|----------|---------|------------------------------|
| `status`         | string | No       | —       | Filter by task status        |
| `include_closed` | bool   | No       | `true`  | Include done/cancelled tasks |

### Response Example

```json
{
  "tasks": [],
  "project_id": "proj-def456",
  "total_count": 0
}
```

---

## GET /api/projects/{project_id}/features

Get project features.

### Response Example

```json
{
  "id": "proj-def456",
  "features": [
    {
      "name": "Task Management",
      "status": "in_progress",
      "components": ["task-board", "kanban"]
    }
  ]
}
```

---

## GET /api/projects/task-counts

Get task count breakdown for all projects.

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

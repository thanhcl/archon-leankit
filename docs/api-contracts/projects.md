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
  "create_bootstrap_task": true,
  "bootstrap_template": "default-app",
  "project_type": "web-app",
  "bootstrap_policy": "standard",
  "bootstrap_architect_provider": "chatgpt-codex",
  "bootstrap_architect_model": "gpt-5.4",
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
| `create_bootstrap_task` | bool | No      | Auto-create a runner-owned bootstrap task for the new project |
| `bootstrap_template` | string | No      | Template hint used by the bootstrap task and runner routing |
| `project_type`      | string  | No       | Project shape hint for bootstrap prompts, tags, and validation criteria |
| `bootstrap_policy`  | string  | No       | Bootstrap strictness hint such as `standard`, `rapid`, or `strict` |
| `bootstrap_architect_provider` | string | No | Requested architect-provider for bootstrap planning, for example `rule-based`, `chatgpt-codex`, or `claude-chat` |
| `bootstrap_architect_model` | string | No | Optional model hint passed through bootstrap planning metadata |
| `team_config`      | array   | No       | Agent team configuration           |
| `director_config`  | object  | No       | Director agent configuration       |
| `team_lead_config` | object  | No       | Team lead configuration            |
| `office_settings`  | object  | No       | Virtual Office settings            |

Legacy fallback note: if older projects still store `team_lead_config.review_mode`,
`director_config.review_mode`, `office_settings.review_mode`,
`office_settings.preferred_runner` / `office_settings.default_runner`, or
`office_settings.isolation_mode` / `office_settings.worktree_mode` plus matching
copies in `team_lead_config` / `director_config`, the backend
still reads those values as fallbacks. The canonical project-level location is
`/api/engine-policies/{project_id}`. Project create/update flows now mirror
recognized legacy policy values into `archon_engine_policies`, and the database
backfill migration preserves older records. New writes should use
`/api/engine-policies/{project_id}` for runner, review, and isolation policy.

### Response

Returns the created project object. When bootstrap is enabled, the project
payload may include:

- `bootstrap_task`: backward-compatible summary of the first scaffold task
- `bootstrap_tasks`: ordered task-pack summaries for scaffold, validation, and
  follow-up planning
- `bootstrap_plan`: persisted bootstrap plan record summary when plan storage is
  available
- `bootstrap_template`, `project_type`, `bootstrap_policy`: normalized control
  hints used when generating the bootstrap task pack
- `bootstrap_architect_provider`, `bootstrap_architect_model`: architect-provider
  planning metadata for the bootstrap plan request

The bootstrap task pack is created with dependency chaining via `blocked_by`
so the execution engine can run the project bootstrap flow sequentially.
The default planner emits:

- scaffold
- validation
- follow-up planning

When `bootstrap_policy` is `strict`, an additional architecture-baseline task is
inserted between validation and follow-up planning.

Current baseline note: non-`rule-based` architect providers are accepted as
request metadata and traced into bootstrap task tags. In the AI-assisted project
creation path, Archon now defaults supported project types such as `web-app`,
`api-service`, `library`, and `automation` to provider-generated bootstrap
planning when no explicit `bootstrap_architect_provider` is supplied. Archon
still falls back to the internal rule-based planner when the selected provider
fails, is unavailable, or the project type is outside the supported default set.

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
        "code-review": 1,
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

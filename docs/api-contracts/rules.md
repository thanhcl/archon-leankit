# Rules API Contract

Base URL: `/api/rules`

## Field Naming Convention

All fields use **snake_case**. Rules support both global scope (`project_id: null`) and project-scoped rules.

---

## GET /api/rules

List rules with optional filtering.

### Query Parameters

| Parameter        | Type   | Required | Default | Description                             |
|------------------|--------|----------|---------|-----------------------------------------|
| `project_id`     | string | No       | —       | Filter by project                       |
| `section`        | string | No       | —       | Filter by rule section                  |
| `enabled_only`   | bool   | No       | `true`  | Only return enabled rules               |
| `include_global` | bool   | No       | `true`  | Include global (non-project) rules      |

### Response Example

```json
{
  "rules": [
    {
      "id": "rule-001",
      "section": "error-handling",
      "rule_text": "Always use specific exception types, not generic Exception catching",
      "priority": 90,
      "source": "manual",
      "enabled": true,
      "project_id": null,
      "created_at": "2026-03-10T08:00:00Z"
    },
    {
      "id": "rule-002",
      "section": "testing",
      "rule_text": "Integration tests must hit a real database, not mocks",
      "priority": 85,
      "source": "auto",
      "enabled": true,
      "project_id": "proj-def456",
      "created_at": "2026-03-12T14:00:00Z"
    }
  ],
  "total_count": 2
}
```

---

## GET /api/rules/{rule_id}

Get a single rule by ID.

### Response Example

```json
{
  "id": "rule-001",
  "section": "error-handling",
  "rule_text": "Always use specific exception types, not generic Exception catching",
  "priority": 90,
  "source": "manual",
  "enabled": true,
  "project_id": null,
  "created_at": "2026-03-10T08:00:00Z"
}
```

---

## POST /api/rules

Create a new rule.

### Request Body

```json
{
  "section": "security",
  "rule_text": "Never store session tokens in localStorage",
  "project_id": "proj-def456",
  "priority": 95,
  "source": "manual"
}
```

| Field        | Type   | Required | Default    | Description                              |
|--------------|--------|----------|------------|------------------------------------------|
| `section`    | string | Yes      | —          | Rule category/section                    |
| `rule_text`  | string | Yes      | —          | The rule content                         |
| `project_id` | string | No       | `null`     | Project scope (null = global)            |
| `priority`   | int    | No       | `100`      | Priority (0-100, higher = more important)|
| `source`     | string | No       | `"manual"` | Origin: `manual`, `auto`, or `system`    |

### Response Example

```json
{
  "rule": {
    "id": "rule-003",
    "section": "security",
    "rule_text": "Never store session tokens in localStorage",
    "priority": 95,
    "source": "manual",
    "enabled": true,
    "project_id": "proj-def456",
    "created_at": "2026-03-18T10:00:00Z"
  }
}
```

---

## PUT /api/rules/{rule_id}

Update a rule. All fields are optional.

### Request Body

```json
{
  "rule_text": "Updated rule text",
  "priority": 80,
  "enabled": false
}
```

| Field        | Type   | Required | Description                    |
|--------------|--------|----------|--------------------------------|
| `section`    | string | No       | Rule category                  |
| `rule_text`  | string | No       | The rule content               |
| `project_id` | string | No       | Project scope                  |
| `priority`   | int    | No       | Priority (0-100)               |
| `source`     | string | No       | Origin type                    |
| `enabled`    | bool   | No       | Enable/disable the rule        |

### Response

Returns the updated rule object.

---

## DELETE /api/rules/{rule_id}

Delete a rule.

### Response Example

```json
{
  "message": "Rule rule-003 deleted"
}
```

---

## GET /api/rules/generate-claude-md/{project_id}

Generate assembled CLAUDE.md markdown combining global + project rules.

### Path Parameters

| Parameter    | Type   | Required | Description     |
|--------------|--------|----------|-----------------|
| `project_id` | string | Yes      | The project ID  |

### Response Example

```json
{
  "markdown": "# Error Handling\n\n- Always use specific exception types...\n\n# Security\n\n- Never store session tokens...\n",
  "rule_count": 12,
  "sections": ["error-handling", "security", "testing", "code-quality"]
}
```

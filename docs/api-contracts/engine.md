# Engine API Contract

Base URL: `/api/engine`

## Field Naming Convention

All fields use **snake_case**.

---

## GET /api/engine/review-config

Get the current review configuration for the rules engine.

This endpoint returns the global fallback baseline. Project-level review mode is
resolved from `/api/engine-policies/{project_id}` first and only falls back to
this configuration when the project policy does not define `review_policy.review_mode`.

### Response Example

```json
{
  "review_mode": "self-review",
  "security_override_to_api": false,
  "provider": "anthropic",
  "model": "claude-opus-4-6",
  "temperature": 0.3,
  "max_tokens": 4096,
  "timeout": 30,
  "api_fallback_to_self_review": true,
  "confidence_approve_threshold": 0.8,
  "confidence_retry_threshold": 0.5
}
```

### Fields

| Field                          | Type   | Description                                        |
|--------------------------------|--------|----------------------------------------------------|
| `review_mode`                  | string | `self-review`, `api`, or `multi-perspective`       |
| `security_override_to_api`     | bool   | Force API mode for security-sensitive reviews      |
| `provider`                     | string | LLM provider: `anthropic`, `openai`, `google`     |
| `model`                        | string | Model identifier                                   |
| `temperature`                  | float  | LLM temperature (0.0-1.0)                          |
| `max_tokens`                   | int    | Max response tokens                                |
| `timeout`                      | int    | Request timeout in seconds                         |
| `api_fallback_to_self_review`  | bool   | Fall back to self-review if API fails              |
| `confidence_approve_threshold` | float  | Confidence score to auto-approve (0.0-1.0)         |
| `confidence_retry_threshold`   | float  | Confidence score below which to retry (0.0-1.0)    |

---

## PUT /api/engine/review-config

Update the review configuration. All fields are optional.

This updates the global fallback baseline. Use `/api/engine-policies/{project_id}`
to set project-specific `review_policy.review_mode`.

### Request Body

```json
{
  "review_mode": "api",
  "provider": "anthropic",
  "model": "claude-opus-4-6",
  "confidence_approve_threshold": 0.85
}
```

### Response Example

```json
{
  "message": "Review config updated",
  "config": {
    "review_mode": "api",
    "security_override_to_api": false,
    "provider": "anthropic",
    "model": "claude-opus-4-6",
    "temperature": 0.3,
    "max_tokens": 4096,
    "timeout": 30,
    "api_fallback_to_self_review": true,
    "confidence_approve_threshold": 0.85,
    "confidence_retry_threshold": 0.5
  }
}
```

---

## GET /api/engine-policies/{project_id}

Get the effective project engine policy.

Project policy columns now normalize the project-level execution settings:

- `model_routing.default_runner` is the canonical runner preference
- `review_policy.review_mode` is the canonical project review mode
- `isolation_policy.worktree_mode` is the canonical project isolation mode

When those values are absent from `archon_engine_policies`, the backend falls
back to legacy project metadata and the global `REVIEW_CONFIG` baseline.

Migration note: `migration/0.1.0-leankit/025_backfill_project_policy_sources_into_engine_policies.sql`
backfills legacy project-level runner, review, and isolation settings into
`archon_engine_policies` without overwriting existing canonical policy values.

Legacy fallback order is deterministic during migration:

- runner preference: `office_settings.*` first, then adjacent project config copies
- review mode: `team_lead_config.review_mode`, then `director_config.review_mode`, then `office_settings.review_mode`, then the global `REVIEW_CONFIG`
- isolation mode: `office_settings.isolation_mode` / `office_settings.worktree_mode`, then matching copies in `team_lead_config` and `director_config`

Runtime note: the task engine resolves project policy through `EnginePolicyService.get_active_policy()`, so execution runner selection, architect review mode, and worktree isolation all read `archon_engine_policies` first and only fall back when the canonical columns are unset.

## PUT /api/engine-policies/{project_id}

Create or replace the project engine policy.

Relevant keys for policy normalization:

- `model_routing.default_runner`
- `review_policy.review_mode`
- `isolation_policy.worktree_mode`

---

## GET /api/engine/runner-capabilities

Get the current runner capability matrix and default runner baseline used by the
task engine.

Runtime note:

- `default_runner` is environment-sensitive and reflects any global runner kill
  switches such as `LEANKIT_ENGINE_DISABLE_CLAUDE_CODE` or
  `LEANKIT_ENGINE_DISABLE_CODEX`
- disabled runners are omitted from the `runners` array

### Response Example

```json
{
  "default_runner": "claude-code-cli",
  "runners": [
    {
      "runner_key": "claude-code-cli",
      "label": "Claude Code CLI",
      "source_app": "claude-code-cli",
      "supports_execute": true,
      "supports_review": true,
      "strengths": ["complex-implementation", "security-review", "high-risk-tasks"],
      "preferred_task_types": ["bug", "feature", "improvement"],
      "preferred_tags": ["security", "backend", "api", "migration"],
      "preferred_created_from": [],
      "preferred_complexities": ["complex"],
      "preferred_priorities": ["high", "critical"]
    },
    {
      "runner_key": "codex-cli",
      "label": "Codex CLI",
      "source_app": "codex-cli",
      "supports_execute": true,
      "supports_review": true,
      "strengths": ["bootstrap", "scaffolding", "refactor", "docs", "tests"],
      "preferred_task_types": ["docs", "refactor", "test"],
      "preferred_tags": ["bootstrap", "project-bootstrap", "scaffold", "template", "docs", "tests", "refactor"],
      "preferred_created_from": ["bootstrap", "project-bootstrap", "template"],
      "preferred_complexities": ["simple"],
      "preferred_priorities": ["low"]
    }
  ]
}
```

### Notes

- explicit task-level `runner_key` still overrides policy routing
- the matrix describes current engine heuristics, not a permanent compatibility contract
- project-level runner preference now resolves from `archon_engine_policies.model_routing.default_runner`

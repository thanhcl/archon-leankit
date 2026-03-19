# Engine API Contract

Base URL: `/api/engine`

## Field Naming Convention

All fields use **snake_case**.

---

## GET /api/engine/review-config

Get the current review configuration for the rules engine.

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
| `review_mode`                  | string | `self-review` or `api`                             |
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

# Architect-Provider Boundary Contract

Ref: ADR-0012 / C-P3-01

## Overview

The architect-provider boundary defines a single, provider-agnostic contract for decomposing work into structured task proposals.  All providers — rule-based, Claude Chat, and ChatGPT/Codex — receive an `ArchitectRequest` and must return a validated `ArchitectResponse` before any tasks are created in Archon.

---

## Schemas

### ArchitectRequest

Input contract for any architect provider.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Short title of the work to plan |
| `description` | string | Yes | Detailed description of the request |
| `project_id` | string \| null | No | Target project for task creation |
| `task_id` | string \| null | No | Parent task context, if applicable |
| `context` | string \| null | No | Additional context (code snippets, specs) |
| `clarification_answers` | string[] | No | Answers to prior clarifying questions |
| `requested_provider` | string \| null | No | Preferred provider key (see Supported Providers) |
| `model` | string \| null | No | Override model for LLM providers |
| `payload` | object | No | Extra fields (e.g. `pending_clarifying_questions`) |

---

### ArchitectProposedTask

A single decomposed task proposal returned inside `ArchitectResponse.proposed_tasks`.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `title` | string | Yes | Task title |
| `description` | string | Yes | What the task involves |
| `task_type` | string | No | `feature`, `bug`, `docs`, etc. (default: `feature`) |
| `priority` | string | No | `low`, `medium`, `high`, `critical` (default: `medium`) |
| `complexity` | string | No | `simple`, `complex` (default: `simple`) |
| `risk_level` | ArchitectRiskLevel | No | `low`, `medium`, `high`, `critical` (default: `low`) |
| `acceptance_criteria` | string[] | No | Verifiable completion checks |
| `dependencies` | string[] | No | Titles/keys of tasks this depends on |
| `tags` | string[] | No | Labels for filtering and routing |
| `suggested_decomposition` | string[] | No | Sub-task titles when further breakdown is advisable |

---

### ArchitectResponse

Output contract for any architect provider.  All provider output is validated against this schema before task creation.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `requested_provider` | string | Yes | Provider key that was requested |
| `resolved_provider` | string | Yes | Provider key that actually ran |
| `strategy` | string | Yes | `rule-based`, `provider-generated`, `fallback-rule-based` |
| `model` | string \| null | No | Model used by LLM providers |
| `summary` | string | Yes | Human-readable plan summary |
| `risk_level` | ArchitectRiskLevel | No | Overall risk assessment (default: `low`) |
| `proposed_tasks` | ArchitectProposedTask[] | No | Decomposed task proposals |
| `clarifying_questions` | string[] | No | Questions to resolve before task creation |
| `recommended_materialization` | string | No | `none`, `task`, `approval` (default: `none`) |
| `metadata` | object | No | Provider-specific metadata |

---

### ArchitectRiskLevel

```
low | medium | high | critical
```

---

## Supported Providers

| Key | Backend | Default Model |
|-----|---------|---------------|
| `rule-based` | Local (no LLM) | — |
| `chatgpt-codex` | OpenAI | `gpt-5.4` |
| `claude-chat` | Anthropic | `claude-sonnet-4-5` |

---

## Validation Boundary

All architect output passes through `validate_architect_response()` before tasks are created.

```python
from src.server.models.api_contracts import validate_architect_response

response = validate_architect_response(raw_provider_output)
# ValidationError raised if contract is not satisfied
```

The single entry point for all architect planning:

```python
from src.server.services.projects.architect_provider import run_architect_plan, ArchitectRequest

response = await run_architect_plan(
    ArchitectRequest(
        title="Harden auth",
        description="Add MFA and rate limiting",
        project_id="proj-1",
        requested_provider="claude-chat",
    )
)
```

---

## Provider Selection

```
requested_provider=None / "rule-based"  →  RuleBasedArchitectProvider
requested_provider="chatgpt-codex"      →  LLMArchitectProvider (OpenAI)
requested_provider="claude-chat"        →  LLMArchitectProvider (Anthropic)
unknown value                           →  RuleBasedArchitectProvider (fallback)
```

LLM providers automatically fall back to `rule-based` when the LLM call fails, ensuring task creation is never blocked by provider availability.

---

## Implementation

| Component | Path |
|-----------|------|
| Schemas | `python/src/server/models/api_contracts.py` |
| Provider adapters | `python/src/server/services/projects/architect_provider.py` |
| Tests | `python/tests/server/services/projects/test_architect_provider.py` |

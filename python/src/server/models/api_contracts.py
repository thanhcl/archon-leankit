"""
Typed API contracts with runtime validation.

Single source of truth for API request/response shapes.
Used by API routes for response validation before returning data.
"""

from __future__ import annotations

# ── Enums ──────────────────────────────────────────────────────────────
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    PLANNING = "planning"
    OWNER_QA = "owner-qa"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    ARCHITECT_REVIEW = "architect-review"
    REVIEW = "review"
    DONE = "done"
    FAILED = "failed"
    ESCALATED = "escalated"
    ON_HOLD = "on-hold"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class TaskComplexity(str, Enum):
    SIMPLE = "simple"
    COMPLEX = "complex"


class TaskType(str, Enum):
    BUG = "bug"
    FEATURE = "feature"
    IMPROVEMENT = "improvement"
    DOCS = "docs"
    REFACTOR = "refactor"
    TEST = "test"


class RuleSource(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"
    SYSTEM = "system"


# ── Task Contracts ─────────────────────────────────────────────────────


class TaskResponse(BaseModel):
    """Response shape for a single task."""

    id: str
    project_id: str
    title: str
    description: str = ""
    status: TaskStatus
    assignee: str = "User"
    task_order: int = 0
    priority: TaskPriority = TaskPriority.MEDIUM
    feature: str | None = None
    complexity: TaskComplexity = TaskComplexity.SIMPLE
    owner: str | None = None
    source_app: str | None = None
    retry_count: int = 0
    max_retries: int = 3
    state_changed_at: str | None = None
    created_at: str
    updated_at: str
    archived: bool = False
    archived_at: str | None = None
    archived_by: str | None = None
    parent_task_id: str | None = None
    blocked_by: list[str] | None = None
    task_type: TaskType = TaskType.FEATURE
    phase: str | None = None
    module: str | None = None
    sprint: str | None = None
    tags: list[str] | None = None

    # Ownership tracking
    created_by: str | None = None  # who created: "owner" | "engine" | "auto-breakdown" | "code-review"
    created_from: str | None = None  # how created: "api" | "telegram" | "board-ui" | "mcp" | "auto"
    executed_by: dict[str, Any] | None = None  # {model, session_id, source_app}
    reviewed_by: list[dict[str, Any]] | None = None  # [{stage, agent, model, actor}]

    # Large fields (may be excluded via exclude_large_fields)
    sources: list[dict[str, Any]] | None = None
    code_examples: list[dict[str, Any]] | None = None
    acceptance_criteria: list[Any] | None = None
    execution_result: dict[str, Any] | None = None
    architect_review: dict[str, Any] | None = None
    execution_prompt: str | None = None
    state_history: list[dict[str, Any]] | None = None
    review_history: list[dict[str, Any]] | None = None

    # Stats (present when exclude_large_fields=True)
    stats: dict[str, int] | None = None

    # UI extension
    featureColor: str | None = None

    model_config = {"use_enum_values": True}


class TaskListResponse(BaseModel):
    """Response shape for task list endpoint."""

    tasks: list[TaskResponse]
    total_count: int
    filters_applied: str = "none"
    include_closed: bool = False

    model_config = {"use_enum_values": True}


class CreateTaskRequest(BaseModel):
    """Request shape for creating a task."""

    project_id: str
    title: str
    description: str | None = None
    status: TaskStatus = TaskStatus.DRAFT
    assignee: str = "User"
    task_order: int = 0
    priority: TaskPriority = TaskPriority.MEDIUM
    feature: str | None = None
    owner: str | None = None
    acceptance_criteria: list[Any] | None = None
    execution_prompt: str | None = None
    source_app: str | None = None
    complexity: TaskComplexity = TaskComplexity.SIMPLE
    max_retries: int = 3
    parent_task_id: str | None = None
    blocked_by: list[str] | None = None
    sources: list[dict[str, Any]] | None = None
    code_examples: list[dict[str, Any]] | None = None
    created_by: str | None = None
    created_from: str | None = None
    task_type: TaskType = TaskType.FEATURE
    phase: str | None = None
    module: str | None = None
    sprint: str | None = None
    tags: list[str] | None = None

    model_config = {"use_enum_values": True}


class UpdateTaskRequest(BaseModel):
    """Request shape for updating a task."""

    title: str | None = None
    description: str | None = None
    status: TaskStatus | None = None
    assignee: str | None = None
    task_order: int | None = None
    priority: TaskPriority | None = None
    feature: str | None = None
    complexity: TaskComplexity | None = None
    owner: str | None = None
    execution_prompt: str | None = None
    source_app: str | None = None
    max_retries: int | None = None
    acceptance_criteria: list[Any] | None = None
    execution_result: dict[str, Any] | None = None
    architect_review: dict[str, Any] | None = None
    rejection_reason: str | None = None
    hold_reason: str | None = None
    blocked_by: list[str] | None = None
    executed_by: dict[str, Any] | None = None
    reviewed_by: list[dict[str, Any]] | None = None
    task_type: TaskType | None = None
    phase: str | None = None
    module: str | None = None
    sprint: str | None = None
    tags: list[str] | None = None

    model_config = {"use_enum_values": True}


class TransitionTaskRequest(BaseModel):
    """Request shape for task state transition."""

    new_status: TaskStatus
    changed_by: str = "api"
    reason: str | None = None

    model_config = {"use_enum_values": True}


class TransitionResponse(BaseModel):
    """Response shape for task transition."""

    message: str
    task: TaskResponse
    transition: dict[str, Any]

    model_config = {"use_enum_values": True}


# ── Project Contracts ──────────────────────────────────────────────────


class ProjectResponse(BaseModel):
    """Response shape for a single project."""

    id: str
    title: str
    description: str | None = None
    github_repo: str | None = None
    docs: list[Any] | None = None
    features: list[Any] | None = None
    data: list[Any] | None = None
    technical_sources: list[str] | None = None
    business_sources: list[str] | None = None
    pinned: bool = False
    source_app: str | None = None
    layout_id: str | None = None
    team_config: list[dict[str, Any]] | None = None
    director_config: dict[str, Any] | None = None
    team_lead_config: dict[str, Any] | None = None
    office_settings: dict[str, Any] | None = None
    created_at: str
    updated_at: str

    model_config = {"use_enum_values": True}


class ProjectListResponse(BaseModel):
    """Response shape for project list endpoint."""

    projects: list[ProjectResponse]
    timestamp: str
    count: int

    model_config = {"use_enum_values": True}


class CreateProjectRequest(BaseModel):
    """Request shape for creating a project."""

    title: str
    description: str | None = None
    github_repo: str | None = None
    docs: list[Any] | None = None
    features: list[Any] | None = None
    data: list[Any] | None = None
    technical_sources: list[str] | None = None
    business_sources: list[str] | None = None
    pinned: bool | None = None
    source_app: str | None = None
    layout_id: str | None = None
    team_config: list[dict[str, Any]] | None = None
    director_config: dict[str, Any] | None = None
    team_lead_config: dict[str, Any] | None = None
    office_settings: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class UpdateProjectRequest(BaseModel):
    """Request shape for updating a project."""

    title: str | None = None
    description: str | None = None
    github_repo: str | None = None
    docs: list[Any] | None = None
    features: list[Any] | None = None
    data: list[Any] | None = None
    technical_sources: list[str] | None = None
    business_sources: list[str] | None = None
    pinned: bool | None = None
    source_app: str | None = None
    layout_id: str | None = None
    team_config: list[dict[str, Any]] | None = None
    director_config: dict[str, Any] | None = None
    team_lead_config: dict[str, Any] | None = None
    office_settings: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


# ── Office Config Contract ─────────────────────────────────────────────


class OfficeConfigResponse(BaseModel):
    """Response shape for office config endpoint."""

    id: str
    title: str
    description: str | None = None
    source_app: str | None = None
    layout_id: str | None = None
    team_config: list[dict[str, Any]] | None = None
    director_config: dict[str, Any] | None = None
    team_lead_config: dict[str, Any] | None = None
    office_settings: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class OfficeConfigListResponse(BaseModel):
    """Response shape for office config list."""

    office_configs: list[OfficeConfigResponse]
    count: int

    model_config = {"use_enum_values": True}


# ── Task Counts Contract ───────────────────────────────────────────────


class TaskCountsResponse(BaseModel):
    """Response shape for task counts per project."""

    id: str
    title: str
    task_counts: dict[str, int]

    model_config = {"use_enum_values": True}


class TaskCountsListResponse(BaseModel):
    """Response shape for all project task counts."""

    projects: list[TaskCountsResponse]
    timestamp: str

    model_config = {"use_enum_values": True}


# ── Rule Contracts ─────────────────────────────────────────────────────


class RuleResponse(BaseModel):
    """Response shape for a single rule."""

    id: str
    section: str
    rule_text: str
    priority: int = 100
    source: RuleSource = RuleSource.MANUAL
    enabled: bool = True
    project_id: str | None = None
    created_at: str

    model_config = {"use_enum_values": True}


class RuleListResponse(BaseModel):
    """Response shape for rule list endpoint."""

    rules: list[RuleResponse]
    total_count: int

    model_config = {"use_enum_values": True}


class CreateRuleRequest(BaseModel):
    """Request shape for creating a rule."""

    section: str
    rule_text: str
    project_id: str | None = None
    priority: int = 100
    source: RuleSource = RuleSource.MANUAL

    model_config = {"use_enum_values": True}


class UpdateRuleRequest(BaseModel):
    """Request shape for updating a rule."""

    section: str | None = None
    rule_text: str | None = None
    project_id: str | None = None
    priority: int | None = None
    source: RuleSource | None = None
    enabled: bool | None = None

    model_config = {"use_enum_values": True}


# ── Engine Contracts ───────────────────────────────────────────────────


class ReviewConfigRequest(BaseModel):
    """Request shape for engine review configuration."""

    review_mode: str | None = None
    security_override_to_api: bool | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    api_fallback_to_self_review: bool | None = None
    confidence_approve_threshold: float | None = None
    confidence_retry_threshold: float | None = None

    model_config = {"use_enum_values": True}


class ReviewConfigResponse(BaseModel):
    """Response shape for engine review configuration."""

    review_mode: str
    security_override_to_api: bool
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: int | None = None
    api_fallback_to_self_review: bool
    confidence_approve_threshold: float | None = None
    confidence_retry_threshold: float | None = None

    model_config = {"use_enum_values": True}


# ── Sprint Stats Contracts ─────────────────────────────────────────────


class SprintSummary(BaseModel):
    """Summary stats for a project's sprint."""

    total_tasks: int = 0
    done: int = 0
    first_pass_rate: float = 0.0
    avg_retries: float = 0.0
    avg_duration_hours: float = 0.0
    estimated_cost_usd: float = 0.0


class SprintDay(BaseModel):
    """Stats for a single day in a sprint."""

    date: str
    tasks_completed: int = 0
    first_pass_rate: float = 0.0
    avg_retries: float = 0.0
    avg_duration_hours: float = 0.0
    estimated_cost_usd: float = 0.0


class SprintTrends(BaseModel):
    """Trend data comparing periods."""

    available: bool = False
    previous_date: str | None = None
    current_date: str | None = None
    tasks_completed_delta: int = 0
    first_pass_rate_delta: float = 0.0


class SprintStatsResponse(BaseModel):
    """Response shape for sprint stats endpoint."""

    project_id: str
    summary: SprintSummary
    sprints: list[SprintDay]
    trends: SprintTrends
    top_learnings: list[str] = Field(default_factory=list)
    code_patterns_count: int = 0


# ── Validation Helpers ─────────────────────────────────────────────────


def validate_response(data: dict[str, Any], model: type[BaseModel]) -> BaseModel:
    """Validate a response dict against a Pydantic model.

    Raises ValidationError with detailed field information on mismatch.
    """
    return model.model_validate(data)


def validate_response_safe(data: dict[str, Any], model: type[BaseModel]) -> tuple[bool, BaseModel | dict[str, Any]]:
    """Validate response without raising - returns (success, result_or_errors)."""
    try:
        validated = model.model_validate(data)
        return True, validated
    except Exception as e:
        return False, {"validation_error": str(e), "model": model.__name__}

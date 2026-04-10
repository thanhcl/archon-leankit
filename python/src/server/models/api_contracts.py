"""
Typed API contracts with runtime validation.

Single source of truth for API request/response shapes.
Used by API routes for response validation before returning data.
"""

from __future__ import annotations

# ── Enums ──────────────────────────────────────────────────────────────
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class TaskStatus(str, Enum):
    DRAFT = "draft"
    PROPOSED = "proposed"
    APPROVED = "approved"
    PLANNING = "planning"
    OWNER_QA = "owner-qa"
    ASSIGNED = "assigned"
    EXECUTING = "executing"
    ARCHITECT_REVIEW = "architect-review"
    CODE_REVIEW = "code-review"
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


class ArchitectRiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ExecutionRunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExecutionRunStage(str, Enum):
    EXECUTE = "execute"
    ARCHITECT_REVIEW = "architect-review"
    CODE_REVIEW = "code-review"
    RETRY = "retry"


class AgentRuntimeStatus(str, Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"


class AssigneeType(str, Enum):
    AGENT = "agent"
    HUMAN = "human"
    UNASSIGNED = "unassigned"


class RuleSource(str, Enum):
    MANUAL = "manual"
    AUTO = "auto"
    SYSTEM = "system"


class ExternalRequestType(str, Enum):
    TASK_REQUEST = "task-request"
    APPROVAL_REQUEST = "approval-request"
    PROJECT_BOOTSTRAP = "project-bootstrap"
    MESSAGE = "message"
    STATUS_QUERY = "status-query"
    APPROVAL_ACTION = "approval-action"
    ARCHITECT_REQUEST = "architect-request"
    COMMAND = "command"


class ExternalRequestStatus(str, Enum):
    RECEIVED = "received"
    MATERIALIZED = "materialized"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ExternalRequestMaterialization(str, Enum):
    NONE = "none"
    TASK = "task"
    APPROVAL = "approval"


class ApprovalRequestStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class ApprovalDecision(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"


class ExternalInputModality(str, Enum):
    VOICE = "voice"
    TEXT = "text"


# ── Task Contracts ─────────────────────────────────────────────────────


class TaskRepoGuidancePack(BaseModel):
    """One repo-scoped guidance pack attached to a task."""

    title: str
    guidance: str
    path_scope: list[str] = Field(default_factory=list)


class PipelineStep(BaseModel):
    """One step in a sequential pipeline attached to a task.

    Steps are executed in order. Each step runs as an independent CC session
    with its own prompt_template. On completion the step result is stored as
    a checkpoint so a failed step can be retried without re-running earlier
    steps.
    """

    stage: str
    prompt_template: str
    checkpoint: str | None = None


class OwnerFeedbackRequest(BaseModel):
    """Request shape for submitting owner feedback on a completed task."""

    owner_rating: int = Field(..., ge=1, le=5, description="Quality rating 1–5")
    owner_notes: str | None = Field(default=None, description="Qualitative observations")
    improvement_tags: list[str] = Field(default_factory=list, description="Improvement hint tags")

    model_config = {"use_enum_values": True}


class OwnerFeedbackResponse(BaseModel):
    """Response shape after storing owner feedback."""

    message: str
    task_id: str
    owner_rating: int
    owner_notes: str | None
    improvement_tags: list[str]

    model_config = {"use_enum_values": True}


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
    allowed_paths: list[str] = Field(default_factory=list)
    forbidden_paths: list[str] = Field(default_factory=list)
    repo_guidance_packs: list[TaskRepoGuidancePack] = Field(default_factory=list)
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

    # Plan item traceability
    plan_item_id: str | None = None
    linked_plan_item: LinkedPlanItemInfo | None = None

    # Pipeline execution
    pipeline_steps: list[PipelineStep] | None = None

    # Owner feedback (populated after task completion)
    owner_rating: int | None = None
    owner_notes: str | None = None
    improvement_tags: list[str] | None = None

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
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] | None = None
    repo_guidance_packs: list[TaskRepoGuidancePack] | None = None
    sources: list[dict[str, Any]] | None = None
    code_examples: list[dict[str, Any]] | None = None
    created_by: str | None = None
    created_from: str | None = None
    task_type: TaskType = TaskType.FEATURE
    phase: str | None = None
    module: str | None = None
    sprint: str | None = None
    tags: list[str] | None = None
    pipeline_steps: list[PipelineStep] | None = None

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
    allowed_paths: list[str] | None = None
    forbidden_paths: list[str] | None = None
    repo_guidance_packs: list[TaskRepoGuidancePack] | None = None
    executed_by: dict[str, Any] | None = None
    reviewed_by: list[dict[str, Any]] | None = None
    task_type: TaskType | None = None
    phase: str | None = None
    module: str | None = None
    sprint: str | None = None
    tags: list[str] | None = None
    pipeline_steps: list[PipelineStep] | None = None

    model_config = {"use_enum_values": True}


class TransitionTaskRequest(BaseModel):
    """Request shape for task state transition."""

    new_status: TaskStatus
    changed_by: str = "api"
    reason: str | None = None

    model_config = {"use_enum_values": True}


class RePlanTaskRequest(BaseModel):
    """Request shape for re-planning a task."""

    updated_description: str | None = None
    changed_by: str = "api"
    reason: str | None = None


class ContinueTaskRequest(BaseModel):
    """Request shape for continuing a task after clarification."""

    guidance: str
    changed_by: str = "api"


class TransitionResponse(BaseModel):
    """Response shape for task transition."""

    message: str
    task: TaskResponse
    transition: dict[str, Any]

    model_config = {"use_enum_values": True}


class ExecutionRunResponse(BaseModel):
    """Response shape for a single execution run."""

    id: str
    task_id: str
    project_id: str
    status: ExecutionRunStatus
    stage: ExecutionRunStage
    started_at: str
    engine_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    retry_index: int = 0
    finished_at: str | None = None
    heartbeat_at: str | None = None
    duration_seconds: float | None = None
    token_input: int | None = None
    token_output: int | None = None
    total_tokens: int | None = None
    thinking_tokens: int | None = None
    cost_usd: float | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    workspace_path: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None

    model_config = {"use_enum_values": True}


class ExecutionRunListResponse(BaseModel):
    """Response shape for execution run list endpoint."""

    runs: list[ExecutionRunResponse]
    total_count: int
    filters_applied: str = "none"

    model_config = {"use_enum_values": True}


class BootstrapPlanResponse(BaseModel):
    """Response shape for a single bootstrap plan."""

    id: str
    project_id: str
    requested_provider: str
    resolved_provider: str
    strategy: str
    model: str | None = None
    template: str
    project_type: str
    bootstrap_policy: str
    source_app: str | None = None
    status: str
    plan_items: list[dict[str, Any]] | None = None
    created_tasks: list[dict[str, Any]] | None = None
    metadata: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class BootstrapPlanListResponse(BaseModel):
    """Response shape for bootstrap plan list endpoint."""

    plans: list[BootstrapPlanResponse]
    total_count: int


class BootstrapPlanMaterializeResponse(BaseModel):
    """Response shape for materializing stored bootstrap-plan backlog items."""

    plan: BootstrapPlanResponse
    created_tasks: list[dict[str, Any]] = Field(default_factory=list)
    skipped: int = 0


class BootstrapPlanPreviewItem(BaseModel):
    """A single task item generated by the bootstrap planner (not yet persisted)."""

    key: str
    title: str
    description: str
    task_type: str
    priority: str
    complexity: str
    max_retries: int
    created_from: str
    execution_prompt: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    blocked_on_key: str | None = None
    is_backlog: bool = False


class BootstrapPlanPreviewResponse(BaseModel):
    """Response shape for a dry-run bootstrap plan preview — no tasks are created."""

    project_id: str
    requested_provider: str
    resolved_provider: str
    strategy: str
    model: str | None = None
    template: str
    project_type: str
    bootstrap_policy: str
    source_app: str | None = None
    dry_run: bool = True
    plan_items: list[BootstrapPlanPreviewItem]
    backlog_items: list[BootstrapPlanPreviewItem] = Field(default_factory=list)
    total_task_count: int


class ExternalRequestResponse(BaseModel):
    """Response shape for a single external ingress request."""

    id: str
    source_channel: str
    request_type: ExternalRequestType
    status: ExternalRequestStatus
    materialize_as: ExternalRequestMaterialization
    title: str
    summary: str
    correlation_id: str
    deduplicated: bool = False
    dedupe_strategy: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    execution_run_id: str | None = None
    bootstrap_plan_id: str | None = None
    source_app: str | None = None
    actor_id: str | None = None
    actor_display: str | None = None
    input_modality: ExternalInputModality | None = None
    input_text: str | None = None
    transcript_confidence: float | None = None
    audio_reference: str | None = None
    payload: dict[str, Any] | None = None
    linked_task_id: str | None = None
    linked_approval_request_id: str | None = None
    created_at: str
    updated_at: str

    model_config = {"use_enum_values": True}


class ExternalRequestListResponse(BaseModel):
    """Response shape for listing external ingress requests."""

    requests: list[ExternalRequestResponse]
    total_count: int
    filters_applied: str = "none"

    model_config = {"use_enum_values": True}


class CreateExternalRequestRequest(BaseModel):
    """Request shape for creating an external ingress request."""

    source_channel: str
    request_type: ExternalRequestType = ExternalRequestType.TASK_REQUEST
    title: str
    summary: str
    materialize_as: ExternalRequestMaterialization = ExternalRequestMaterialization.NONE
    correlation_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    execution_run_id: str | None = None
    bootstrap_plan_id: str | None = None
    source_app: str | None = None
    actor_id: str | None = None
    actor_display: str | None = None
    input_modality: ExternalInputModality | None = None
    input_text: str | None = None
    transcript_confidence: float | None = Field(default=None, ge=0, le=1)
    audio_reference: str | None = None
    payload: dict[str, Any] | None = None
    task_template: dict[str, Any] | None = None
    approval_template: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class ApprovalRequestResponse(BaseModel):
    """Response shape for a single approval request."""

    id: str
    status: ApprovalRequestStatus
    title: str
    summary: str
    requested_by: str
    requested_channel: str
    project_id: str | None = None
    task_id: str | None = None
    execution_run_id: str | None = None
    bootstrap_plan_id: str | None = None
    external_request_id: str | None = None
    actor_id: str | None = None
    actor_display: str | None = None
    context: dict[str, Any] | None = None
    decided_by: str | None = None
    decision_comment: str | None = None
    decided_at: str | None = None
    created_at: str
    updated_at: str

    model_config = {"use_enum_values": True}


class ApprovalRequestListResponse(BaseModel):
    """Response shape for listing approval requests."""

    approvals: list[ApprovalRequestResponse]
    total_count: int
    filters_applied: str = "none"

    model_config = {"use_enum_values": True}


class CreateApprovalRequestRequest(BaseModel):
    """Request shape for creating an approval request."""

    title: str
    summary: str
    requested_by: str
    requested_channel: str
    project_id: str | None = None
    task_id: str | None = None
    execution_run_id: str | None = None
    bootstrap_plan_id: str | None = None
    external_request_id: str | None = None
    actor_id: str | None = None
    actor_display: str | None = None
    context: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class ApprovalDecisionRequest(BaseModel):
    """Request shape for recording an approval decision."""

    decision: ApprovalDecision
    decided_by: str
    decision_comment: str | None = None

    model_config = {"use_enum_values": True}


class ApprovalBatchDecisionRequest(BaseModel):
    """Request shape for recording the same decision across multiple approval requests."""

    approval_ids: list[str] = Field(min_length=1)
    decision: ApprovalDecision
    decided_by: str
    decision_comment: str | None = None
    bundle_label: str | None = None
    minimum_required: int | None = Field(default=None, ge=1)
    stop_on_error: bool = False

    model_config = {"use_enum_values": True}


class ApprovalBatchDecisionResponse(BaseModel):
    """Response shape for a batch approval decision operation."""

    approvals: list[ApprovalRequestResponse]
    failed_ids: list[str] = Field(default_factory=list)
    processed_count: int
    failed_count: int
    bundle_label: str | None = None
    minimum_required: int | None = None
    threshold_met: bool = True

    model_config = {"use_enum_values": True}


class OpenClawIngestRequest(BaseModel):
    """Request shape for OpenClaw channel ingestion."""

    request_type: ExternalRequestType = ExternalRequestType.MESSAGE
    title: str
    summary: str
    materialize_as: ExternalRequestMaterialization = ExternalRequestMaterialization.NONE
    correlation_id: str | None = None
    project_id: str | None = None
    task_id: str | None = None
    execution_run_id: str | None = None
    bootstrap_plan_id: str | None = None
    actor_id: str | None = None
    actor_display: str | None = None
    input_modality: ExternalInputModality | None = None
    input_text: str | None = None
    transcript_confidence: float | None = Field(default=None, ge=0, le=1)
    audio_reference: str | None = None
    payload: dict[str, Any] | None = None
    task_template: dict[str, Any] | None = None
    approval_template: dict[str, Any] | None = None
    architect_provider: str | None = None
    architect_model: str | None = None
    command_sequence_id: str | None = None
    step_key: str | None = None

    model_config = {"use_enum_values": True}


class ArchitectTaskSuggestionResponse(BaseModel):
    """One architect-generated suggested task."""

    title: str
    summary: str
    task_type: str
    priority: str
    complexity: str
    tags: list[str] = Field(default_factory=list)


class ArchitectApprovalSuggestionResponse(BaseModel):
    """One architect-generated suggested approval action."""

    title: str
    summary: str


class ArchitectRequestPlanResponse(BaseModel):
    """Structured architect-planning response for external channels."""

    requested_provider: str
    resolved_provider: str
    strategy: str
    model: str | None = None
    summary: str
    recommended_materialization: str = "none"
    clarifying_questions: list[str] = Field(default_factory=list)
    suggested_tasks: list[ArchitectTaskSuggestionResponse] = Field(default_factory=list)
    suggested_approval: ArchitectApprovalSuggestionResponse | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class OpenClawArchitectRequest(BaseModel):
    """Request shape for OpenClaw architect-planning calls."""

    title: str
    summary: str
    project_id: str | None = None
    task_id: str | None = None
    execution_run_id: str | None = None
    bootstrap_plan_id: str | None = None
    correlation_id: str | None = None
    actor_id: str | None = None
    actor_display: str | None = None
    input_modality: ExternalInputModality | None = None
    input_text: str | None = None
    transcript_confidence: float | None = Field(default=None, ge=0, le=1)
    audio_reference: str | None = None
    clarification_answers: list[str] = Field(default_factory=list)
    clarification_response_to_request_id: str | None = None
    command_sequence_id: str | None = None
    step_key: str | None = None
    architect_provider: str | None = None
    architect_model: str | None = None
    payload: dict[str, Any] | None = None


class OpenClawArchitectResponse(BaseModel):
    """Response shape for OpenClaw architect-planning calls."""

    request: ExternalRequestResponse
    architect_plan: ArchitectRequestPlanResponse


# ── Architect-Provider Boundary Contract ───────────────────────────────
# Formal provider-agnostic contract for architect providers (ChatGPT/Codex,
# Claude Chat) to decompose work into structured task proposals.


class ArchitectRequest(BaseModel):
    """Input contract for any architect provider.

    All providers receive this shape regardless of backend (OpenAI, Anthropic).
    """

    title: str
    description: str
    project_id: str | None = None
    task_id: str | None = None
    context: str | None = None
    clarification_answers: list[str] = Field(default_factory=list)
    requested_provider: str | None = None
    model: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


class ArchitectProposedTask(BaseModel):
    """A single decomposed task proposal returned by an architect provider."""

    title: str
    description: str
    task_type: str = "feature"
    priority: str = "medium"
    complexity: str = "simple"
    risk_level: ArchitectRiskLevel = ArchitectRiskLevel.LOW
    acceptance_criteria: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(
        default_factory=list,
        description="Keys or titles of tasks this proposal depends on.",
    )
    tags: list[str] = Field(default_factory=list)
    suggested_decomposition: list[str] = Field(
        default_factory=list,
        description="Sub-task titles when further breakdown is advisable.",
    )


class ArchitectResponse(BaseModel):
    """Output contract for any architect provider.

    All architect output flows through validation against this schema before
    tasks are created in Archon. Provider-agnostic: identical shape for
    Claude Chat, ChatGPT, and Codex providers.
    """

    requested_provider: str
    resolved_provider: str
    strategy: str
    model: str | None = None
    summary: str
    risk_level: ArchitectRiskLevel = ArchitectRiskLevel.LOW
    proposed_tasks: list[ArchitectProposedTask] = Field(default_factory=list)
    clarifying_questions: list[str] = Field(default_factory=list)
    recommended_materialization: str = "none"
    metadata: dict[str, Any] = Field(default_factory=dict)


def validate_architect_response(data: dict[str, Any]) -> ArchitectResponse:
    """Validate raw architect provider output against the formal boundary contract.

    Raises ValidationError when the response does not conform to the contract.
    All architect output must pass through this gate before task creation.
    """
    return ArchitectResponse.model_validate(data)


class NotificationEventInput(BaseModel):
    """Minimal event payload accepted by digest-preview APIs."""

    event: str
    task_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    is_critical: bool = False
    timestamp: str | None = None


class TelegramDigestRequest(BaseModel):
    """Request shape for Telegram digest preview or delivery."""

    events: list[NotificationEventInput] = Field(default_factory=list)
    send: bool = False


class TelegramDueDigestRequest(BaseModel):
    """Request shape for schedule-aware Telegram digest delivery."""

    events: list[NotificationEventInput] = Field(default_factory=list)
    force: bool = False
    now_override: str | None = None
    scheduler_origin: str = "primary"
    delivery_required: bool = False


class TelegramDigestCycleRequest(BaseModel):
    """Request shape for scheduler-backed Telegram digest execution."""

    force: bool = False
    now_override: str | None = None
    limit: int = Field(default=25, ge=1, le=200)
    scheduler_origin: str = "primary"
    delivery_required: bool = False


class TelegramDigestResponse(BaseModel):
    """Response shape for Telegram digest preview or delivery."""

    text: str
    total_events: int
    routed_events: int
    delivery_mode: str = "preview"
    ready: bool = False
    due: bool | None = None
    digest_key: str | None = None
    skipped_reason: str | None = None
    scheduler_mode: str | None = None
    scheduler_origin: str | None = None
    authority_scope: str | None = None
    job_status: str | None = None
    delivery_ok: bool | None = None
    sent: bool = False


class TelegramChannelHealthResponse(BaseModel):
    """Health snapshot for Telegram channel readiness."""

    bot_configured: bool
    chat_configured: bool
    webhook_secret_configured: bool
    send_ready: bool = False
    digest_ready: bool = False
    status: str = "unknown"
    issues: list[str] = Field(default_factory=list)
    last_checked_at: str
    digest_schedule_hour: int = 9
    digest_schedule_minute: int = 0
    digest_timezone: str = "UTC"
    digest_lookback_minutes: int = 1440
    digest_scheduler_enabled: bool = True
    observability_replay_ready: bool = False
    next_due_at: str | None = None
    last_digest_sent_at: str | None = None
    last_digest_key: str | None = None
    digest_events: list[str] = Field(default_factory=list)
    notify_events: list[str] = Field(default_factory=list)


class OpenClawChannelHealthResponse(BaseModel):
    """Health snapshot for OpenClaw channel readiness."""

    ingest_secret_configured: bool
    ingest_ready: bool = False
    status: str = "unknown"
    issues: list[str] = Field(default_factory=list)
    replay_guard_strategy: str = "semantic-correlation-v2"
    replay_window_minutes: int = 30
    sequence_guard_enabled: bool = True
    conversational_policy_enabled: bool = True
    last_checked_at: str
    architect_route_enabled: bool = True
    semantic_dedupe_enabled: bool = True


class ExternalChannelHeartbeatResponse(BaseModel):
    """Aggregated readiness snapshot across supported external channels."""

    status: str = "unknown"
    total_channels: int = 0
    ready_channels: int = 0
    degraded_channels: int = 0
    ready_channel_keys: list[str] = Field(default_factory=list)
    degraded_channel_keys: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    last_checked_at: str
    telegram: TelegramChannelHealthResponse
    openclaw: OpenClawChannelHealthResponse


class ServiceDependencyHealthResponse(BaseModel):
    """Health snapshot for one platform dependency or service surface."""

    key: str
    label: str
    status: str = "unknown"
    configured: bool = False
    reachable: bool = False
    url: str | None = None
    http_status: int | None = None
    latency_ms: float | None = None
    issues: list[str] = Field(default_factory=list)
    last_checked_at: str


class PlatformServiceHealthResponse(BaseModel):
    """Aggregated readiness snapshot across pilot-critical platform services."""

    status: str = "unknown"
    total_services: int = 0
    ready_services: int = 0
    degraded_services: int = 0
    ready_service_keys: list[str] = Field(default_factory=list)
    degraded_service_keys: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    last_checked_at: str
    control_plane: ServiceDependencyHealthResponse
    archon_mcp: ServiceDependencyHealthResponse
    observability_replay: ServiceDependencyHealthResponse
    external_channels: ExternalChannelHeartbeatResponse


class CreateExecutionRunRequest(BaseModel):
    """Request shape for creating an execution run."""

    task_id: str
    project_id: str
    status: ExecutionRunStatus = ExecutionRunStatus.QUEUED
    stage: ExecutionRunStage = ExecutionRunStage.EXECUTE
    engine_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    retry_index: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    duration_seconds: float | None = None
    token_input: int | None = None
    token_output: int | None = None
    total_tokens: int | None = None
    thinking_tokens: int | None = None
    cost_usd: float | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class UpdateExecutionRunRequest(BaseModel):
    """Request shape for updating an execution run."""

    status: ExecutionRunStatus | None = None
    stage: ExecutionRunStage | None = None
    engine_id: str | None = None
    session_id: str | None = None
    model: str | None = None
    retry_index: int | None = None
    started_at: str | None = None
    finished_at: str | None = None
    heartbeat_at: str | None = None
    duration_seconds: float | None = None
    token_input: int | None = None
    token_output: int | None = None
    total_tokens: int | None = None
    thinking_tokens: int | None = None
    cost_usd: float | None = None
    result_summary: str | None = None
    error_summary: str | None = None
    workspace_path: str | None = None
    metadata: dict[str, Any] | None = None

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
    bootstrap_task: dict[str, Any] | None = None
    bootstrap_tasks: list[dict[str, Any]] | None = None
    bootstrap_plan: dict[str, Any] | None = None
    bootstrap_template: str | None = None
    project_type: str | None = None
    bootstrap_policy: str | None = None
    bootstrap_architect_provider: str | None = None
    bootstrap_architect_model: str | None = None
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
    create_bootstrap_task: bool | None = True
    bootstrap_template: str | None = None
    project_type: str | None = None
    bootstrap_policy: str | None = None
    bootstrap_architect_provider: str | None = None
    bootstrap_architect_model: str | None = None

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


# ── Project Template Contracts ─────────────────────────────────────────


class ProjectTemplateTaskResponse(BaseModel):
    """Response shape for a single task in a project template's task pack."""

    key: str
    title: str
    description: str
    task_type: str
    priority: str
    complexity: str
    tags: list[str]
    blocked_on_key: str | None = None


class ProjectTemplateResponse(BaseModel):
    """Response shape for a project template."""

    id: str
    name: str
    description: str
    project_type: str
    bootstrap_policy: str
    bootstrap_architect_provider: str | None = None
    default_model_routing: dict[str, Any]
    default_review_policy: dict[str, Any]
    task_pack: list[ProjectTemplateTaskResponse]
    icon: str = ""
    color: str = ""


class ProjectTemplateListResponse(BaseModel):
    """Response shape for a list of project templates."""

    templates: list[ProjectTemplateResponse]


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


# ── Plan-Item Traceability Helpers ──────────────────────────────────────


class LinkedPlanItemInfo(BaseModel):
    """Compact plan item summary embedded in task detail responses."""

    id: str
    item_key: str | None = None
    title: str
    status: str


class PlanItemLinkedTaskInfo(BaseModel):
    """Compact task summary embedded in plan item detail responses."""

    id: str
    title: str
    status: str
    assignee: str = "User"
    link_type: str = "implements"


class TaskLinkResponse(BaseModel):
    """Response shape for a single task link record."""

    id: str
    item_id: str
    task_id: str
    link_type: str
    created_at: str


class TaskLinkListResponse(BaseModel):
    """Response shape for the task link list endpoint."""

    links: list[TaskLinkResponse]
    total_count: int


class CreateTaskLinkRequest(BaseModel):
    """Request shape for creating a plan-item–task link."""

    item_id: str
    task_id: str
    link_type: str = "implements"


class AutoLinkResult(BaseModel):
    """Result returned by the auto-link parser endpoint."""

    scanned: int
    linked: int
    skipped: int
    errors: list[str] = Field(default_factory=list)


# ── Implementation Plan Contracts ──────────────────────────────────────


class PlanItemStatus(str, Enum):
    PLANNED = "planned"
    READY = "ready"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    REVIEW = "review"
    DONE = "done"
    DEFERRED = "deferred"
    CANCELLED = "cancelled"


class DependencyType(str, Enum):
    BLOCKS = "blocks"
    REQUIRES = "requires"
    RELATED_TO = "related_to"


class ImplementationPlanResponse(BaseModel):
    """Response shape for a single implementation plan."""

    id: str
    project_id: str
    title: str
    description: str | None = None
    status: str
    created_by: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class ImplementationPlanListResponse(BaseModel):
    """Response shape for implementation plan list endpoint."""

    plans: list[ImplementationPlanResponse]
    total_count: int


class CreateImplementationPlanRequest(BaseModel):
    """Request shape for creating an implementation plan."""

    project_id: str
    title: str
    description: str | None = None
    status: str = "draft"
    created_by: str | None = None
    metadata: dict[str, Any] | None = None


class UpdateImplementationPlanRequest(BaseModel):
    """Request shape for updating an implementation plan."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    metadata: dict[str, Any] | None = None


class ImplementationPhaseResponse(BaseModel):
    """Response shape for a single implementation phase."""

    id: str
    plan_id: str
    title: str
    description: str | None = None
    phase_order: int
    metadata: dict[str, Any] | None = None
    created_at: str
    updated_at: str


class ImplementationPhaseListResponse(BaseModel):
    """Response shape for implementation phase list endpoint."""

    phases: list[ImplementationPhaseResponse]
    total_count: int


class CreateImplementationPhaseRequest(BaseModel):
    """Request shape for creating an implementation phase."""

    title: str
    description: str | None = None
    phase_order: int = 0
    metadata: dict[str, Any] | None = None


class ImplementationItemResponse(BaseModel):
    """Response shape for a single implementation item."""

    id: str
    plan_id: str
    phase_id: str | None = None
    title: str
    description: str | None = None
    status: PlanItemStatus
    item_order: int
    priority: str
    complexity: str
    item_key: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str
    updated_at: str
    linked_tasks: list[PlanItemLinkedTaskInfo] | None = None

    model_config = {"use_enum_values": True}


class ImplementationItemListResponse(BaseModel):
    """Response shape for implementation item list endpoint."""

    items: list[ImplementationItemResponse]
    total_count: int

    model_config = {"use_enum_values": True}


class CreateImplementationItemRequest(BaseModel):
    """Request shape for creating an implementation item."""

    phase_id: str | None = None
    title: str
    description: str | None = None
    status: PlanItemStatus = PlanItemStatus.PLANNED
    item_order: int = 0
    priority: str = "medium"
    complexity: str = "simple"
    item_key: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class UpdateImplementationItemRequest(BaseModel):
    """Request shape for updating an implementation item."""

    phase_id: str | None = None
    title: str | None = None
    description: str | None = None
    status: PlanItemStatus | None = None
    item_order: int | None = None
    priority: str | None = None
    complexity: str | None = None
    item_key: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class ImplementationDependencyResponse(BaseModel):
    """Response shape for a single item dependency."""

    id: str
    dependent_id: str
    dependency_id: str
    dependency_type: DependencyType
    metadata: dict[str, Any] | None = None
    created_at: str

    model_config = {"use_enum_values": True}


class ImplementationDependencyListResponse(BaseModel):
    """Response shape for item dependency list endpoint."""

    dependencies: list[ImplementationDependencyResponse]
    total_count: int

    model_config = {"use_enum_values": True}


class CreateImplementationDependencyRequest(BaseModel):
    """Request shape for creating an item dependency."""

    dependency_id: str
    dependency_type: DependencyType = DependencyType.BLOCKS
    metadata: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


# ── Plan Rollup ────────────────────────────────────────────────────────


class ItemRollup(BaseModel):
    """Rollup statistics for a single implementation item."""

    item_id: str
    item_key: str | None = None
    title: str
    status: str
    task_count: int
    status_distribution: dict[str, int]
    run_count: int
    cost_usd: float | None = None
    progress_percent: float


class PhaseRollup(BaseModel):
    """Rollup statistics for a single implementation phase, aggregating child items."""

    phase_id: str
    title: str
    phase_order: int
    item_count: int
    task_count: int
    status_distribution: dict[str, int]
    run_count: int
    cost_usd: float | None = None
    progress_percent: float
    items: list[ItemRollup]


class PlanRollup(BaseModel):
    """Rollup statistics for an entire implementation plan."""

    plan_id: str
    title: str
    phase_count: int
    item_count: int
    task_count: int
    status_distribution: dict[str, int]
    run_count: int
    cost_usd: float | None = None
    progress_percent: float
    phases: list[PhaseRollup]
    unphased_items: list[ItemRollup]


# ── Plan Import ────────────────────────────────────────────────────────


class ImportPlanRequest(BaseModel):
    """Request shape for importing a plan from a markdown document."""

    project_id: str
    content: str | None = None
    file_path: str | None = None
    plan_id: str | None = None
    preview_only: bool = True

    @model_validator(mode="after")
    def require_content_or_file_path(self) -> "ImportPlanRequest":
        if not self.content and not self.file_path:
            raise ValueError("Either 'content' or 'file_path' must be provided")
        return self


class ImportDiffItem(BaseModel):
    """A single item entry in an import diff."""

    item_key: str
    title: str
    phase: str | None = None
    status: str | None = None
    changes: list[dict[str, Any]] | None = None
    note: str | None = None


class ImportPlanDiff(BaseModel):
    """Diff between current DB state and incoming markdown document."""

    added: list[ImportDiffItem]
    removed: list[ImportDiffItem]
    changed: list[ImportDiffItem]
    unchanged: list[ImportDiffItem]


class ImportPlanResponse(BaseModel):
    """Response shape for POST /api/plans/import."""

    action: str
    plan_id: str | None = None
    source_hash: str
    hash_changed: bool | None = None
    phases_created: int | None = None
    items_created: int | None = None
    dependencies_created: int | None = None
    dependencies_updated: int | None = None
    diff: ImportPlanDiff | None = None


# ── Agent Definitions ──────────────────────────────────────────────────


class AgentDefinitionResponse(BaseModel):
    """A specialized agent role definition."""

    id: str
    slug: str
    name: str
    description: str | None = None
    capabilities: list[str] = []
    model_preferences: dict[str, Any] = {}
    prompt_template: str | None = None
    is_active: bool = True
    created_at: str | None = None
    updated_at: str | None = None


class AgentDefinitionListResponse(BaseModel):
    agent_definitions: list[AgentDefinitionResponse]
    total_count: int


class CreateAgentDefinitionRequest(BaseModel):
    slug: str
    name: str
    description: str | None = None
    capabilities: list[str] = []
    model_preferences: dict[str, Any] = {}
    prompt_template: str | None = None
    is_active: bool = True


class UpdateAgentDefinitionRequest(BaseModel):
    slug: str | None = None
    name: str | None = None
    description: str | None = None
    capabilities: list[str] | None = None
    model_preferences: dict[str, Any] | None = None
    prompt_template: str | None = None
    is_active: bool | None = None


# ── Admin UI Contracts ─────────────────────────────────────────────────


class ReleaseAsset(BaseModel):
    """One downloadable asset attached to a release."""

    name: str
    size: int
    download_count: int
    browser_download_url: str
    content_type: str


class VersionCheckResponse(BaseModel):
    """Update-check payload for the settings UI."""

    current: str
    latest: str | None
    update_available: bool
    release_url: str | None
    release_notes: str | None
    published_at: datetime | None
    check_error: str | None = None
    assets: list[ReleaseAsset] | None = None
    author: str | None = None


class CurrentVersionResponse(BaseModel):
    """Installed version metadata."""

    version: str
    timestamp: datetime


class VersionCacheClearResponse(BaseModel):
    """Result for clearing cached version data."""

    message: str
    success: bool


class MigrationRecord(BaseModel):
    """One applied database migration."""

    version: str
    migration_name: str
    applied_at: datetime
    checksum: str | None = None


class PendingMigration(BaseModel):
    """One migration waiting to be applied."""

    version: str
    name: str
    sql_content: str
    file_path: str
    checksum: str | None = None


class MigrationStatusResponse(BaseModel):
    """Current migration state for the admin settings UI."""

    pending_migrations: list[PendingMigration]
    applied_migrations: list[MigrationRecord]
    has_pending: bool
    bootstrap_required: bool
    current_version: str
    pending_count: int
    applied_count: int


class MigrationHistoryResponse(BaseModel):
    """Applied migration history payload."""

    migrations: list[MigrationRecord]
    total_count: int
    current_version: str


# ── Review Feedback ─────────────────────────────────────────────────────


class ReviewFeedbackFinding(BaseModel):
    """Per-criterion finding from a review stage."""

    criterion: str
    score: float = Field(ge=0.0, le=10.0)
    passed: bool
    details: str | None = None
    evidence: str | None = None


class ReviewFeedbackResponse(BaseModel):
    """Structured feedback artifact written after architect-review or code-review rejection."""

    id: str
    task_id: str
    run_id: str | None = None
    contract_revision: int | None = None
    findings: list[dict[str, Any]] = Field(default_factory=list)
    overall_score: float | None = None
    verdict: str
    suggested_retry_direction: str | None = None
    reviewer_identity: str
    created_at: str


class ReviewFeedbackListResponse(BaseModel):
    """Paginated list of review feedback records."""

    feedback: list[ReviewFeedbackResponse] = Field(default_factory=list)
    total_count: int = 0


# ── Agent Runtime Models ──────────────────────────────────────────────


class AgentRuntimeResponse(BaseModel):
    """Response shape for a single agent runtime."""

    id: str
    device_name: str
    status: AgentRuntimeStatus
    capabilities: list[str] = Field(default_factory=list)
    supported_runners: list[str] = Field(default_factory=list)
    max_concurrent_tasks: int = 1
    current_task_count: int = 0
    agent_id: str | None = None
    version: str | None = None
    last_heartbeat: str | None = None
    registered_at: str | None = None
    unregistered_at: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None

    model_config = {"use_enum_values": True}


class AgentRuntimeListResponse(BaseModel):
    """Response shape for runtime list endpoint."""

    runtimes: list[AgentRuntimeResponse]
    total_count: int
    filters_applied: str = "none"

    model_config = {"use_enum_values": True}


class RegisterRuntimeRequest(BaseModel):
    """Request shape for registering a new agent runtime."""

    device_name: str
    capabilities: list[str]
    supported_runners: list[str]
    max_concurrent_tasks: int = 1
    agent_id: str | None = None
    version: str | None = None
    metadata: dict[str, Any] | None = None

    model_config = {"use_enum_values": True}


class RuntimeHeartbeatRequest(BaseModel):
    """Request shape for runtime heartbeat."""

    current_task_count: int | None = None


class RuntimeProgressRequest(BaseModel):
    """Request shape for task execution progress report."""

    runtime_id: str
    step_count: int | None = None
    percentage: float | None = None
    current_action: str | None = None


class ClaimTaskResponse(BaseModel):
    """Response shape for task claim endpoint."""

    task: dict[str, Any] | None = None
    message: str | None = None


class AssignTaskRequest(BaseModel):
    """Request shape for polymorphic task assignment."""

    assignee_type: AssigneeType
    assignee_id: str | None = None
    reason: str | None = None

    model_config = {"use_enum_values": True}


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

"""Server models package - Pydantic models for API contracts and progress tracking."""

from .api_contracts import (
    CreateProjectRequest,
    CreateRuleRequest,
    CreateTaskRequest,
    OfficeConfigListResponse,
    OfficeConfigResponse,
    ProjectListResponse,
    ProjectResponse,
    ReviewConfigRequest,
    ReviewConfigResponse,
    RuleListResponse,
    RuleResponse,
    RuleSource,
    SprintDay,
    SprintStatsResponse,
    SprintSummary,
    SprintTrends,
    TaskComplexity,
    TaskCountsListResponse,
    TaskCountsResponse,
    TaskListResponse,
    TaskPriority,
    TaskResponse,
    TaskStatus,
    TransitionResponse,
    TransitionTaskRequest,
    UpdateProjectRequest,
    UpdateRuleRequest,
    UpdateTaskRequest,
    validate_response,
    validate_response_safe,
)
from .progress_models import (
    BaseProgressResponse,
    CrawlProgressResponse,
    ProgressDetails,
    ProjectCreationProgressResponse,
    UploadProgressResponse,
    create_progress_response,
)

__all__ = [
    # API contract enums
    "TaskStatus",
    "TaskPriority",
    "TaskComplexity",
    "RuleSource",
    # Task contracts
    "TaskResponse",
    "TaskListResponse",
    "CreateTaskRequest",
    "UpdateTaskRequest",
    "TransitionTaskRequest",
    "TransitionResponse",
    # Project contracts
    "ProjectResponse",
    "ProjectListResponse",
    "CreateProjectRequest",
    "UpdateProjectRequest",
    # Office config contracts
    "OfficeConfigResponse",
    "OfficeConfigListResponse",
    # Task counts contracts
    "TaskCountsResponse",
    "TaskCountsListResponse",
    # Rule contracts
    "RuleResponse",
    "RuleListResponse",
    "CreateRuleRequest",
    "UpdateRuleRequest",
    # Engine contracts
    "ReviewConfigRequest",
    "ReviewConfigResponse",
    # Sprint stats contracts
    "SprintSummary",
    "SprintDay",
    "SprintTrends",
    "SprintStatsResponse",
    # Validation helpers
    "validate_response",
    "validate_response_safe",
    # Progress models
    "ProgressDetails",
    "BaseProgressResponse",
    "CrawlProgressResponse",
    "UploadProgressResponse",
    "ProjectCreationProgressResponse",
    "create_progress_response",
]

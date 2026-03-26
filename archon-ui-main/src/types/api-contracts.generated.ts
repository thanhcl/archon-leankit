// ─── AUTO-GENERATED ─────────────────────────────────────────────────
// Source: python/src/server/models/api_contracts.py
// Generator: scripts/generate-ts-types.py
//
// DO NOT EDIT MANUALLY. Run `pnpm generate:types` to regenerate.
// ─────────────────────────────────────────────────────────────────────

// ── Enums ──────────────────────────────────────────────────────────

export type TaskStatus =
  | "draft"
  | "proposed"
  | "approved"
  | "planning"
  | "owner-qa"
  | "assigned"
  | "executing"
  | "architect-review"
  | "code-review"
  | "review"
  | "done"
  | "failed"
  | "escalated"
  | "on-hold"
  | "cancelled";

export type TaskPriority =
  | "low"
  | "medium"
  | "high"
  | "critical";

export type TaskComplexity =
  | "simple"
  | "complex";

export type TaskType =
  | "bug"
  | "feature"
  | "improvement"
  | "docs"
  | "refactor"
  | "test";

export type ExecutionRunStatus =
  | "queued"
  | "running"
  | "reviewing"
  | "completed"
  | "failed"
  | "cancelled";

export type ExecutionRunStage =
  | "execute"
  | "architect-review"
  | "code-review"
  | "retry";

export type RuleSource =
  | "manual"
  | "auto"
  | "system";

export type ExternalRequestType =
  | "task-request"
  | "approval-request"
  | "project-bootstrap"
  | "message"
  | "status-query"
  | "approval-action"
  | "architect-request"
  | "command";

export type ExternalRequestStatus =
  | "received"
  | "materialized"
  | "failed"
  | "cancelled";

export type ExternalRequestMaterialization =
  | "none"
  | "task"
  | "approval";

export type ExternalInputModality =
  | "voice"
  | "text";

export type ApprovalRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "cancelled";

export type ApprovalDecision =
  | "approve"
  | "reject";

// ── Interfaces ─────────────────────────────────────────────────────

export interface TaskRepoGuidancePack {
  title: string;
  guidance: string;
  path_scope: string[];
}

export interface TaskResponse {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: TaskStatus;
  assignee: string;
  task_order: number;
  priority: TaskPriority;
  feature?: string | null;
  complexity: TaskComplexity;
  owner?: string | null;
  source_app?: string | null;
  retry_count: number;
  max_retries: number;
  state_changed_at?: string | null;
  created_at: string;
  updated_at: string;
  archived: boolean;
  archived_at?: string | null;
  archived_by?: string | null;
  parent_task_id?: string | null;
  blocked_by?: string[] | null;
  allowed_paths: string[];
  forbidden_paths: string[];
  repo_guidance_packs: TaskRepoGuidancePack[];
  task_type: TaskType;
  phase?: string | null;
  module?: string | null;
  sprint?: string | null;
  tags?: string[] | null;
  created_by?: string | null;
  created_from?: string | null;
  executed_by?: Record<string, unknown> | null;
  reviewed_by?: Record<string, unknown>[] | null;
  sources?: Record<string, unknown>[] | null;
  code_examples?: Record<string, unknown>[] | null;
  acceptance_criteria?: unknown[] | null;
  execution_result?: Record<string, unknown> | null;
  architect_review?: Record<string, unknown> | null;
  execution_prompt?: string | null;
  state_history?: Record<string, unknown>[] | null;
  review_history?: Record<string, unknown>[] | null;
  stats?: Record<string, number> | null;
  featureColor?: string | null;
  plan_item_id?: string | null;
  linked_plan_item: unknown;
  pipeline_steps?: PipelineStep[] | null;
  owner_rating?: number | null;
  owner_notes?: string | null;
  improvement_tags?: string[] | null;
}

export interface TaskListResponse {
  tasks: TaskResponse[];
  total_count: number;
  filters_applied: string;
  include_closed: boolean;
}

export interface CreateTaskRequest {
  project_id: string;
  title: string;
  description?: string | null;
  status: TaskStatus;
  assignee: string;
  task_order: number;
  priority: TaskPriority;
  feature?: string | null;
  owner?: string | null;
  acceptance_criteria?: unknown[] | null;
  execution_prompt?: string | null;
  source_app?: string | null;
  complexity: TaskComplexity;
  max_retries: number;
  parent_task_id?: string | null;
  blocked_by?: string[] | null;
  allowed_paths?: string[] | null;
  forbidden_paths?: string[] | null;
  repo_guidance_packs?: TaskRepoGuidancePack[] | null;
  sources?: Record<string, unknown>[] | null;
  code_examples?: Record<string, unknown>[] | null;
  created_by?: string | null;
  created_from?: string | null;
  task_type: TaskType;
  phase?: string | null;
  module?: string | null;
  sprint?: string | null;
  tags?: string[] | null;
  pipeline_steps?: PipelineStep[] | null;
}

export interface UpdateTaskRequest {
  title?: string | null;
  description?: string | null;
  status?: TaskStatus | null;
  assignee?: string | null;
  task_order?: number | null;
  priority?: TaskPriority | null;
  feature?: string | null;
  complexity?: TaskComplexity | null;
  owner?: string | null;
  execution_prompt?: string | null;
  source_app?: string | null;
  max_retries?: number | null;
  acceptance_criteria?: unknown[] | null;
  execution_result?: Record<string, unknown> | null;
  architect_review?: Record<string, unknown> | null;
  rejection_reason?: string | null;
  hold_reason?: string | null;
  blocked_by?: string[] | null;
  allowed_paths?: string[] | null;
  forbidden_paths?: string[] | null;
  repo_guidance_packs?: TaskRepoGuidancePack[] | null;
  executed_by?: Record<string, unknown> | null;
  reviewed_by?: Record<string, unknown>[] | null;
  task_type?: TaskType | null;
  phase?: string | null;
  module?: string | null;
  sprint?: string | null;
  tags?: string[] | null;
  pipeline_steps?: PipelineStep[] | null;
}

export interface TransitionTaskRequest {
  new_status: TaskStatus;
  changed_by: string;
  reason?: string | null;
}

export interface TransitionResponse {
  message: string;
  task: TaskResponse;
  transition: Record<string, unknown>;
}

export interface OwnerFeedbackRequest {
  owner_rating: number;
  owner_notes?: string | null;
  improvement_tags: string[];
}

export interface OwnerFeedbackResponse {
  message: string;
  task_id: string;
  owner_rating: number;
  owner_notes?: string | null;
  improvement_tags: string[];
}

export interface ExecutionRunResponse {
  id: string;
  task_id: string;
  project_id: string;
  status: ExecutionRunStatus;
  stage: ExecutionRunStage;
  started_at: string;
  engine_id?: string | null;
  session_id?: string | null;
  model?: string | null;
  retry_index: number;
  finished_at?: string | null;
  duration_seconds?: number | null;
  token_input?: number | null;
  token_output?: number | null;
  total_tokens?: number | null;
  thinking_tokens?: number | null;
  cost_usd?: number | null;
  result_summary?: string | null;
  error_summary?: string | null;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface ExecutionRunListResponse {
  runs: ExecutionRunResponse[];
  total_count: number;
  filters_applied: string;
}

export interface CreateExecutionRunRequest {
  task_id: string;
  project_id: string;
  status: ExecutionRunStatus;
  stage: ExecutionRunStage;
  engine_id?: string | null;
  session_id?: string | null;
  model?: string | null;
  retry_index: number;
  started_at?: string | null;
  finished_at?: string | null;
  duration_seconds?: number | null;
  token_input?: number | null;
  token_output?: number | null;
  total_tokens?: number | null;
  thinking_tokens?: number | null;
  cost_usd?: number | null;
  result_summary?: string | null;
  error_summary?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface UpdateExecutionRunRequest {
  status?: ExecutionRunStatus | null;
  stage?: ExecutionRunStage | null;
  engine_id?: string | null;
  session_id?: string | null;
  model?: string | null;
  retry_index?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_seconds?: number | null;
  token_input?: number | null;
  token_output?: number | null;
  total_tokens?: number | null;
  thinking_tokens?: number | null;
  cost_usd?: number | null;
  result_summary?: string | null;
  error_summary?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface ProjectResponse {
  id: string;
  title: string;
  description?: string | null;
  github_repo?: string | null;
  docs?: unknown[] | null;
  features?: unknown[] | null;
  data?: unknown[] | null;
  technical_sources?: string[] | null;
  business_sources?: string[] | null;
  pinned: boolean;
  source_app?: string | null;
  layout_id?: string | null;
  team_config?: Record<string, unknown>[] | null;
  director_config?: Record<string, unknown> | null;
  team_lead_config?: Record<string, unknown> | null;
  office_settings?: Record<string, unknown> | null;
  bootstrap_task?: Record<string, unknown> | null;
  bootstrap_tasks?: Record<string, unknown>[] | null;
  bootstrap_plan?: Record<string, unknown> | null;
  bootstrap_template?: string | null;
  project_type?: string | null;
  bootstrap_policy?: string | null;
  bootstrap_architect_provider?: string | null;
  bootstrap_architect_model?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ProjectListResponse {
  projects: ProjectResponse[];
  timestamp: string;
  count: number;
}

export interface CreateProjectRequest {
  title: string;
  description?: string | null;
  github_repo?: string | null;
  docs?: unknown[] | null;
  features?: unknown[] | null;
  data?: unknown[] | null;
  technical_sources?: string[] | null;
  business_sources?: string[] | null;
  pinned?: boolean | null;
  source_app?: string | null;
  layout_id?: string | null;
  team_config?: Record<string, unknown>[] | null;
  director_config?: Record<string, unknown> | null;
  team_lead_config?: Record<string, unknown> | null;
  office_settings?: Record<string, unknown> | null;
  create_bootstrap_task?: boolean | null;
  bootstrap_template?: string | null;
  project_type?: string | null;
  bootstrap_policy?: string | null;
  bootstrap_architect_provider?: string | null;
  bootstrap_architect_model?: string | null;
}

export interface UpdateProjectRequest {
  title?: string | null;
  description?: string | null;
  github_repo?: string | null;
  docs?: unknown[] | null;
  features?: unknown[] | null;
  data?: unknown[] | null;
  technical_sources?: string[] | null;
  business_sources?: string[] | null;
  pinned?: boolean | null;
  source_app?: string | null;
  layout_id?: string | null;
  team_config?: Record<string, unknown>[] | null;
  director_config?: Record<string, unknown> | null;
  team_lead_config?: Record<string, unknown> | null;
  office_settings?: Record<string, unknown> | null;
}

export interface OfficeConfigResponse {
  id: string;
  title: string;
  description?: string | null;
  source_app?: string | null;
  layout_id?: string | null;
  team_config?: Record<string, unknown>[] | null;
  director_config?: Record<string, unknown> | null;
  team_lead_config?: Record<string, unknown> | null;
  office_settings?: Record<string, unknown> | null;
}

export interface OfficeConfigListResponse {
  office_configs: OfficeConfigResponse[];
  count: number;
}

export interface TaskCountsResponse {
  id: string;
  title: string;
  task_counts: Record<string, number>;
}

export interface TaskCountsListResponse {
  projects: TaskCountsResponse[];
  timestamp: string;
}

export interface RuleResponse {
  id: string;
  section: string;
  rule_text: string;
  priority: number;
  source: RuleSource;
  enabled: boolean;
  project_id?: string | null;
  created_at: string;
}

export interface RuleListResponse {
  rules: RuleResponse[];
  total_count: number;
}

export interface CreateRuleRequest {
  section: string;
  rule_text: string;
  project_id?: string | null;
  priority: number;
  source: RuleSource;
}

export interface UpdateRuleRequest {
  section?: string | null;
  rule_text?: string | null;
  project_id?: string | null;
  priority?: number | null;
  source?: RuleSource | null;
  enabled?: boolean | null;
}

export interface ReviewConfigRequest {
  review_mode?: string | null;
  security_override_to_api?: boolean | null;
  provider?: string | null;
  model?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  timeout?: number | null;
  api_fallback_to_self_review?: boolean | null;
  confidence_approve_threshold?: number | null;
  confidence_retry_threshold?: number | null;
}

export interface ReviewConfigResponse {
  review_mode: string;
  security_override_to_api: boolean;
  provider?: string | null;
  model?: string | null;
  temperature?: number | null;
  max_tokens?: number | null;
  timeout?: number | null;
  api_fallback_to_self_review: boolean;
  confidence_approve_threshold?: number | null;
  confidence_retry_threshold?: number | null;
}

export interface SprintSummary {
  total_tasks: number;
  done: number;
  first_pass_rate: number;
  avg_retries: number;
  avg_duration_hours: number;
  estimated_cost_usd: number;
}

export interface SprintDay {
  date: string;
  tasks_completed: number;
  first_pass_rate: number;
  avg_retries: number;
  avg_duration_hours: number;
  estimated_cost_usd: number;
}

export interface SprintTrends {
  available: boolean;
  previous_date?: string | null;
  current_date?: string | null;
  tasks_completed_delta: number;
  first_pass_rate_delta: number;
}

export interface SprintStatsResponse {
  project_id: string;
  summary: SprintSummary;
  sprints: SprintDay[];
  trends: SprintTrends;
  top_learnings: string[];
  code_patterns_count: number;
}

export interface ReleaseAsset {
  name: string;
  size: number;
  download_count: number;
  browser_download_url: string;
  content_type: string;
}

export interface VersionCheckResponse {
  current: string;
  latest?: string | null;
  update_available: boolean;
  release_url?: string | null;
  release_notes?: string | null;
  published_at?: string | null;
  check_error?: string | null;
  assets?: ReleaseAsset[] | null;
  author?: string | null;
}

export interface CurrentVersionResponse {
  version: string;
  timestamp: string;
}

export interface VersionCacheClearResponse {
  message: string;
  success: boolean;
}

export interface MigrationRecord {
  version: string;
  migration_name: string;
  applied_at: string;
  checksum?: string | null;
}

export interface PendingMigration {
  version: string;
  name: string;
  sql_content: string;
  file_path: string;
  checksum?: string | null;
}

export interface MigrationStatusResponse {
  pending_migrations: PendingMigration[];
  applied_migrations: MigrationRecord[];
  has_pending: boolean;
  bootstrap_required: boolean;
  current_version: string;
  pending_count: number;
  applied_count: number;
}

export interface MigrationHistoryResponse {
  migrations: MigrationRecord[];
  total_count: number;
  current_version: string;
}

export interface BootstrapPlanResponse {
  id: string;
  project_id: string;
  requested_provider: string;
  resolved_provider: string;
  strategy: string;
  model?: string | null;
  template: string;
  project_type: string;
  bootstrap_policy: string;
  source_app?: string | null;
  status: string;
  plan_items?: Record<string, unknown>[] | null;
  created_tasks?: Record<string, unknown>[] | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface BootstrapPlanListResponse {
  plans: BootstrapPlanResponse[];
  total_count: number;
}

export interface BootstrapPlanMaterializeResponse {
  plan: BootstrapPlanResponse;
  created_tasks: Record<string, unknown>[];
  skipped: number;
}

export interface BootstrapPlanPreviewItem {
  key: string;
  title: string;
  description: string;
  task_type: string;
  priority: string;
  complexity: string;
  max_retries: number;
  created_from: string;
  execution_prompt: string;
  acceptance_criteria: string[];
  tags: string[];
  blocked_on_key?: string | null;
  is_backlog: boolean;
}

export interface BootstrapPlanPreviewResponse {
  project_id: string;
  requested_provider: string;
  resolved_provider: string;
  strategy: string;
  model?: string | null;
  template: string;
  project_type: string;
  bootstrap_policy: string;
  source_app?: string | null;
  dry_run: boolean;
  plan_items: BootstrapPlanPreviewItem[];
  backlog_items: BootstrapPlanPreviewItem[];
  total_task_count: number;
}

export interface ExternalRequestResponse {
  id: string;
  source_channel: string;
  request_type: ExternalRequestType;
  status: ExternalRequestStatus;
  materialize_as: ExternalRequestMaterialization;
  title: string;
  summary: string;
  correlation_id: string;
  deduplicated: boolean;
  dedupe_strategy?: string | null;
  project_id?: string | null;
  task_id?: string | null;
  execution_run_id?: string | null;
  bootstrap_plan_id?: string | null;
  source_app?: string | null;
  actor_id?: string | null;
  actor_display?: string | null;
  input_modality?: ExternalInputModality | null;
  input_text?: string | null;
  transcript_confidence?: number | null;
  audio_reference?: string | null;
  payload?: Record<string, unknown> | null;
  linked_task_id?: string | null;
  linked_approval_request_id?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ExternalRequestListResponse {
  requests: ExternalRequestResponse[];
  total_count: number;
  filters_applied: string;
}

export interface ApprovalRequestResponse {
  id: string;
  status: ApprovalRequestStatus;
  title: string;
  summary: string;
  requested_by: string;
  requested_channel: string;
  project_id?: string | null;
  task_id?: string | null;
  execution_run_id?: string | null;
  bootstrap_plan_id?: string | null;
  external_request_id?: string | null;
  actor_id?: string | null;
  actor_display?: string | null;
  context?: Record<string, unknown> | null;
  decided_by?: string | null;
  decision_comment?: string | null;
  decided_at?: string | null;
  created_at: string;
  updated_at: string;
}

export interface ApprovalRequestListResponse {
  approvals: ApprovalRequestResponse[];
  total_count: number;
  filters_applied: string;
}

export interface TelegramChannelHealthResponse {
  bot_configured: boolean;
  chat_configured: boolean;
  webhook_secret_configured: boolean;
  send_ready: boolean;
  digest_ready: boolean;
  status: string;
  issues: string[];
  last_checked_at: string;
  digest_schedule_hour: number;
  digest_schedule_minute: number;
  digest_timezone: string;
  digest_lookback_minutes: number;
  digest_scheduler_enabled: boolean;
  observability_replay_ready: boolean;
  next_due_at?: string | null;
  last_digest_sent_at?: string | null;
  last_digest_key?: string | null;
  digest_events: string[];
  notify_events: string[];
}

export interface OpenClawChannelHealthResponse {
  ingest_secret_configured: boolean;
  ingest_ready: boolean;
  status: string;
  issues: string[];
  replay_guard_strategy: string;
  replay_window_minutes: number;
  sequence_guard_enabled: boolean;
  conversational_policy_enabled: boolean;
  last_checked_at: string;
  architect_route_enabled: boolean;
  semantic_dedupe_enabled: boolean;
}

export interface ServiceDependencyHealthResponse {
  key: string;
  label: string;
  status: string;
  configured: boolean;
  reachable: boolean;
  url?: string | null;
  http_status?: number | null;
  latency_ms?: number | null;
  issues: string[];
  last_checked_at: string;
}

export interface ExternalChannelHeartbeatResponse {
  status: string;
  total_channels: number;
  ready_channels: number;
  degraded_channels: number;
  ready_channel_keys: string[];
  degraded_channel_keys: string[];
  issues: string[];
  last_checked_at: string;
  telegram: TelegramChannelHealthResponse;
  openclaw: OpenClawChannelHealthResponse;
}

export interface PlatformServiceHealthResponse {
  status: string;
  total_services: number;
  ready_services: number;
  degraded_services: number;
  ready_service_keys: string[];
  degraded_service_keys: string[];
  issues: string[];
  last_checked_at: string;
  control_plane: ServiceDependencyHealthResponse;
  archon_mcp: ServiceDependencyHealthResponse;
  observability_replay: ServiceDependencyHealthResponse;
  external_channels: ExternalChannelHeartbeatResponse;
}

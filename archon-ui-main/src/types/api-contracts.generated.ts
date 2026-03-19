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

export type RuleSource =
  | "manual"
  | "auto"
  | "system";

// ── Interfaces ─────────────────────────────────────────────────────

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
  sources?: Record<string, unknown>[] | null;
  code_examples?: Record<string, unknown>[] | null;
  acceptance_criteria?: unknown[] | null;
  execution_result?: Record<string, unknown> | null;
  architect_review?: Record<string, unknown> | null;
  execution_prompt?: string | null;
  state_history?: Record<string, unknown>[] | null;
  stats?: Record<string, number> | null;
  featureColor?: string | null;
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
  sources?: Record<string, unknown>[] | null;
  code_examples?: Record<string, unknown>[] | null;
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

/**
 * Core Task Types
 *
 * Main task interfaces and types following vertical slice architecture
 */

import type {
  CreateTaskRequest as GeneratedCreateTaskRequest,
  OwnerFeedbackRequest,
  OwnerFeedbackResponse,
  SprintDay,
  SprintStatsResponse as GeneratedSprintStatsResponse,
  SprintSummary,
  SprintTrends,
  TaskCountsResponse as GeneratedTaskCountsResponse,
  TaskComplexity,
  TaskRepoGuidancePack,
  TaskResponse,
  TaskStatus,
  TaskType,
  UpdateTaskRequest as GeneratedUpdateTaskRequest,
} from "../../../../types/api-contracts.generated";

export type { OwnerFeedbackRequest, OwnerFeedbackResponse };

// Import priority type from priority.ts to avoid duplication
import type { TaskPriority } from "./priority";
export type { TaskPriority };
export type { TaskComplexity };

// Task type classification - using database values directly
export type TaskTypeName = TaskType;

// Database status enum - using database values directly
export type BoardTaskStatus = "todo" | "doing";
export type DatabaseTaskStatus = TaskStatus | BoardTaskStatus;
export type TaskBoardStatus = BoardTaskStatus | "review" | "done";

// Assignee type - flexible string to support any agent name
export type Assignee = string;

// Common assignee options for UI suggestions
export const COMMON_ASSIGNEES = ["User", "Archon", "Coding Agent"] as const;
export type CommonAssignee = (typeof COMMON_ASSIGNEES)[number];

// Task counts for project overview
export type TaskCounts = GeneratedTaskCountsResponse["task_counts"] & Partial<Record<DatabaseTaskStatus, number>>;
export type ProjectTaskCountsMap = Record<string, TaskCounts>;

export const EMPTY_TASK_COUNTS: TaskCounts = {};

export function getTaskCount(taskCounts: TaskCounts, status: DatabaseTaskStatus): number {
  return taskCounts[status] ?? 0;
}

// Task source and code example types (replacing any)
export type TaskSource =
  | {
      url: string;
      type: string;
      relevance: string;
    }
  | Record<string, unknown>;

export type TaskCodeExample =
  | {
      file: string;
      function: string;
      purpose: string;
    }
  | Record<string, unknown>;

type TaskContractFields = Omit<
  TaskResponse,
  | "description"
  | "status"
  | "task_order"
  | "complexity"
  | "retry_count"
  | "max_retries"
  | "archived"
  | "archived_at"
  | "archived_by"
  | "blocked_by"
  | "allowed_paths"
  | "forbidden_paths"
  | "repo_guidance_packs"
  | "task_type"
  | "phase"
  | "module"
  | "sprint"
  | "tags"
  | "sources"
  | "code_examples"
  | "acceptance_criteria"
  | "execution_prompt"
  | "featureColor"
  | "linked_plan_item"
>;

// Base task type aligned with the API contract and extended for UI-only fields
export type Task = TaskContractFields & {
  description?: string | null;
  status: DatabaseTaskStatus;
  task_order?: number | null;
  complexity?: TaskComplexity;
  retry_count?: number;
  max_retries?: number;
  archived?: boolean;
  archived_at?: string | null;
  archived_by?: string | null;
  blocked_by?: string[] | null;
  allowed_paths?: string[] | null;
  forbidden_paths?: string[] | null;
  repo_guidance_packs?: TaskRepoGuidancePack[] | null;
  task_type?: TaskTypeName;
  phase?: string | null;
  module?: string | null;
  sprint?: string | null;
  tags?: string[] | null;
  acceptance_criteria?: unknown[] | null;
  execution_prompt?: string | null;
  featureColor?: string;
  sources?: TaskSource[] | null;
  code_examples?: TaskCodeExample[] | null;
  linked_plan_item?: unknown;
  estimate_duration_seconds?: number;
  estimate_cost_usd?: number;
  estimate_confidence?: "none" | "low" | "medium" | "high";
  // Owner feedback fields (populated after task completion)
  owner_rating?: number | null;
  owner_notes?: string | null;
  improvement_tags?: string[] | null;
};

// Estimation types
export interface TaskEstimate {
  task_id: string;
  title: string;
  estimate_duration_seconds: number;
  estimate_cost_usd: number;
  sample_size: number;
  confidence: "none" | "low" | "medium" | "high";
  reason?: string;
}

export interface PredictedVsActual {
  task_id: string;
  title: string;
  predicted_duration_seconds: number;
  actual_duration_seconds: number;
  predicted_cost_usd: number;
  actual_cost_usd: number;
  duration_accuracy_pct: number;
}

// Sprint stats types (from GET /api/projects/{id}/sprint-stats)
export type SprintEntry = SprintDay;
export type SprintStatsSummary = SprintSummary;

export type SprintStatsResponse = Omit<GeneratedSprintStatsResponse, "trends"> & {
  group_by?: string;
  trends: Omit<SprintTrends, "tasks_completed_delta" | "first_pass_rate_delta"> & {
    tasks_completed_delta?: number;
    first_pass_rate_delta?: number;
    avg_retries_delta?: number;
    avg_duration_hours_delta?: number;
    estimated_cost_usd_delta?: number;
    message?: string;
  };
  injection_metrics?: Record<string, number>;
};

// Budget configuration (from GET/PUT /api/projects/{id}/budget-config)
export interface BudgetConfig {
  max_cost_per_day: number;
  max_cost_per_sprint: number;
}

export interface UpdateBudgetConfigRequest {
  max_cost_per_day: number;
  max_cost_per_sprint: number;
}

// Cost budget types (from GET /api/projects/{id}/cost-status)
export interface CostStatusResponse {
  project_id: string;
  status: "ok" | "warning" | "exceeded";
  today: {
    date: string;
    cost_usd: number;
    budget_usd: number;
    usage_pct: number;
    exceeded: boolean;
    warning: boolean;
  };
  sprint: {
    total_cost_usd: number;
    budget_usd: number;
    usage_pct: number;
    exceeded: boolean;
    warning: boolean;
  };
  daily_breakdown: Record<string, number>;
  budget_config: {
    max_cost_per_day: number;
    max_cost_per_sprint: number;
  };
}

type CreateTaskContractFields = Omit<
  GeneratedCreateTaskRequest,
  "status" | "assignee" | "task_order" | "priority" | "complexity" | "task_type" | "max_retries" | "sources" | "code_examples"
>;

type UpdateTaskContractFields = Omit<
  GeneratedUpdateTaskRequest,
  "status" | "assignee" | "priority" | "complexity" | "task_type" | "sources" | "code_examples"
>;

export type CreateTaskRequest = CreateTaskContractFields & {
  status?: DatabaseTaskStatus;
  assignee?: Assignee;
  task_order?: number;
  featureColor?: string;
  priority?: TaskPriority;
  sources?: TaskSource[];
  code_examples?: TaskCodeExample[];
  task_type?: TaskTypeName;
  complexity?: TaskComplexity;
  max_retries?: number;
};

export type UpdateTaskRequest = UpdateTaskContractFields & {
  status?: DatabaseTaskStatus;
  assignee?: Assignee;
  featureColor?: string;
  priority?: TaskPriority;
  sources?: TaskSource[];
  code_examples?: TaskCodeExample[];
  task_type?: TaskTypeName;
  complexity?: TaskComplexity;
};

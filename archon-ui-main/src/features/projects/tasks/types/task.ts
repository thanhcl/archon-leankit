/**
 * Core Task Types
 *
 * Main task interfaces and types following vertical slice architecture
 */

// Import priority type from priority.ts to avoid duplication
import type { TaskPriority } from "./priority";
export type { TaskPriority };

// Task type classification - using database values directly
export type TaskTypeName = "bug" | "feature" | "improvement" | "docs" | "refactor" | "test";

// Database status enum - using database values directly
export type DatabaseTaskStatus =
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

// Assignee type - flexible string to support any agent name
export type Assignee = string;

// Common assignee options for UI suggestions
export const COMMON_ASSIGNEES = ["User", "Archon", "Coding Agent"] as const;
export type CommonAssignee = (typeof COMMON_ASSIGNEES)[number];

// Task counts for project overview
export interface TaskCounts {
  draft: number;
  proposed: number;
  approved: number;
  planning: number;
  "owner-qa": number;
  assigned: number;
  executing: number;
  "architect-review": number;
  review: number;
  done: number;
  failed: number;
  escalated: number;
  "on-hold": number;
  cancelled: number;
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

// Base Task interface (matches database schema)
export interface Task {
  id: string;
  project_id: string;
  title: string;
  description: string;
  status: DatabaseTaskStatus;
  assignee: Assignee; // Can be any string - agent names, "User", etc.
  task_order: number;
  feature?: string;
  sources?: TaskSource[];
  code_examples?: TaskCodeExample[];
  created_at: string;
  updated_at: string;

  // Soft delete fields
  archived?: boolean;
  archived_at?: string;
  archived_by?: string;

  // Priority field (required database field)
  priority: TaskPriority;

  // Extended UI properties
  featureColor?: string;

  // Categorization fields
  task_type?: TaskTypeName;
  phase?: string;
  module?: string;
  sprint?: string;
  tags?: string[];

  // Lifecycle fields
  parent_task_id?: string;
  blocked_by?: string[];
  complexity?: "simple" | "complex";
  owner?: string;
  source_app?: string;
  retry_count?: number;
  max_retries?: number;
  state_changed_at?: string;

  // Ownership tracking
  created_by?: string; // "owner" | "engine" | "auto-breakdown" | "code-review"
  created_from?: string; // "api" | "telegram" | "board-ui" | "mcp" | "auto"
  executed_by?: { model: string; session_id: string; source_app: string; duration_seconds?: number } | null;
  reviewed_by?: Array<{
    stage: string;
    agent: string;
    model: string;
    actor: string;
    verdict?: string;
    confidence?: number;
    mode?: string;
  }>;

  // Large fields (may be absent when exclude_large_fields=true)
  acceptance_criteria?: unknown[];
  execution_result?: Record<string, unknown> | null;
  architect_review?: Record<string, unknown> | null;
  execution_prompt?: string;
  state_history?: Record<string, unknown>[];

  // Stats (present when exclude_large_fields=true)
  stats?: { sources_count: number; code_examples_count: number };

  // Estimation fields (populated from task-estimates endpoint)
  estimate_duration_seconds?: number;
  estimate_cost_usd?: number;
  estimate_confidence?: "none" | "low" | "medium" | "high";
}

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
export interface SprintEntry {
  date: string;
  tasks_completed: number;
  first_pass_rate: number;
  avg_retries: number;
  avg_duration_hours: number;
  estimated_cost_usd: number;
}

export interface SprintStatsSummary {
  total_tasks: number;
  done: number;
  first_pass_rate: number;
  avg_retries: number;
  avg_duration_hours: number;
  estimated_cost_usd: number;
}

export interface SprintStatsResponse {
  project_id: string;
  group_by: string;
  summary: SprintStatsSummary;
  sprints: SprintEntry[];
  trends: {
    available: boolean;
    previous_date?: string;
    current_date?: string;
    tasks_completed_delta?: number;
    first_pass_rate_delta?: number;
    avg_retries_delta?: number;
    avg_duration_hours_delta?: number;
    estimated_cost_usd_delta?: number;
    message?: string;
  };
  top_learnings: string[];
  code_patterns_count: number;
  injection_metrics: Record<string, number>;
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

// Request types
export interface CreateTaskRequest {
  project_id: string;
  title: string;
  description: string;
  status?: DatabaseTaskStatus;
  assignee?: Assignee; // Optional assignee string
  task_order?: number;
  feature?: string;
  featureColor?: string;
  priority?: TaskPriority;
  sources?: TaskSource[];
  code_examples?: TaskCodeExample[];
  // Categorization fields
  task_type?: TaskTypeName;
  phase?: string;
  module?: string;
  sprint?: string;
  tags?: string[];
  // Lifecycle fields
  parent_task_id?: string;
  blocked_by?: string[];
  owner?: string;
  acceptance_criteria?: unknown[];
  execution_prompt?: string;
  source_app?: string;
  complexity?: "simple" | "complex";
  max_retries?: number;
  // Ownership tracking
  created_by?: string;
  created_from?: string;
}

export interface UpdateTaskRequest {
  title?: string;
  description?: string;
  status?: DatabaseTaskStatus;
  assignee?: Assignee; // Optional assignee string
  task_order?: number;
  feature?: string;
  featureColor?: string;
  priority?: TaskPriority;
  sources?: TaskSource[];
  code_examples?: TaskCodeExample[];
  // Categorization fields
  task_type?: TaskTypeName;
  phase?: string;
  module?: string;
  sprint?: string;
  tags?: string[];
  // Lifecycle fields
  parent_task_id?: string;
  blocked_by?: string[];
  owner?: string;
  acceptance_criteria?: unknown[];
  execution_prompt?: string;
  source_app?: string;
  complexity?: "simple" | "complex";
  max_retries?: number;
}

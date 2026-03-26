/**
 * Core Project Types
 *
 * Properly typed project interfaces following vertical slice architecture
 */

import type {
  ApprovalRequestResponse,
  BootstrapPlanListResponse as GeneratedBootstrapPlanListResponse,
  BootstrapPlanMaterializeResponse as GeneratedBootstrapPlanMaterializeResponse,
  BootstrapPlanResponse,
  CreateProjectRequest as GeneratedCreateProjectRequest,
  ExecutionRunResponse,
  ExternalChannelHeartbeatResponse,
  ExternalRequestResponse,
  OpenClawChannelHealthResponse,
  PlatformServiceHealthResponse,
  ProjectResponse,
  ServiceDependencyHealthResponse,
  TelegramChannelHealthResponse,
  UpdateProjectRequest as GeneratedUpdateProjectRequest,
} from "../../../types/api-contracts.generated";

// Project JSONB field types - replacing any with proper unions
export type ProjectPRD = Record<string, unknown>;
export type ProjectDocs = unknown[]; // Will be refined to ProjectDocument[] when fully migrated
export type ProjectFeature = {
  id: string;
  label: string;
  type?: string;
  color?: string;
};

export type ProjectFeatures = ProjectFeature[];
export type ProjectData = unknown[];

// Project creation progress tracking
export interface ProjectCreationProgress {
  progressId: string;
  status:
    | "starting"
    | "initializing_agents"
    | "generating_docs"
    | "processing_requirements"
    | "ai_generation"
    | "finalizing_docs"
    | "saving_to_database"
    | "completed"
    | "error";
  percentage: number;
  logs: string[];
  error?: string;
  step?: string;
  currentStep?: string;
  eta?: string;
  duration?: string;
  project?: Project; // Forward reference - will be resolved
}

type ProjectContractFields = Omit<ProjectResponse, "docs" | "features" | "data">;

// Base Project type aligned with the API contract and extended for UI state
export type Project = ProjectContractFields & {
  prd?: ProjectPRD;
  docs?: ProjectDocs | null;
  features?: ProjectFeatures | null;
  data?: ProjectData | null;

  description?: string;
  progress?: number;
  updated?: string; // Human-readable format
  creationProgress?: ProjectCreationProgress;
};

// Bootstrap plan task summary used when rendering materialized task lists
export interface BootstrapPlanTaskSummary {
  id: string;
  plan_key?: string;
  title: string;
  status: string;
  tags?: string[];
  created_from?: string;
  blocked_by?: string[];
}

// BootstrapPlan extends the generated contract to use typed created_tasks
export type BootstrapPlan = Omit<BootstrapPlanResponse, "created_tasks"> & {
  created_tasks?: BootstrapPlanTaskSummary[] | null;
};

// List and materialize responses re-typed to use the extended BootstrapPlan
export type BootstrapPlanListResponse = Omit<GeneratedBootstrapPlanListResponse, "plans"> & {
  plans: BootstrapPlan[];
};

export type BootstrapPlanMaterializeResponse = Omit<
  GeneratedBootstrapPlanMaterializeResponse,
  "plan" | "created_tasks"
> & {
  plan: BootstrapPlan;
  created_tasks: BootstrapPlanTaskSummary[];
};

export type BootstrapPlanExecutionRun = Omit<ExecutionRunResponse, "retry_index"> & {
  retry_index?: number;
};

// BootstrapTraceEvent - comes from the observability service (not the main API contract)
export interface BootstrapTraceEvent {
  id: string;
  type: string;
  event: string;
  source: string;
  sourceApp: string;
  projectId?: string;
  agentId?: string;
  taskId?: string;
  runId?: string;
  executionRunId?: string;
  bootstrapPlanId?: string;
  timestamp: string;
  data: Record<string, unknown>;
}

// ExternalRequest extends the generated contract to preserve structured payload type
export type ExternalRequest = Omit<ExternalRequestResponse, "payload"> & {
  payload?: {
    architect_plan?: {
      resolved_provider?: string;
      strategy?: string;
      summary?: string;
      recommended_materialization?: string;
      clarifying_questions?: string[];
      suggested_tasks?: unknown[];
    };
    clarification_status?: string;
    clarification_response_to_request_id?: string;
    clarification_resolved_by_request_id?: string;
    clarification_answers?: string[];
    openclaw_sequence_snapshot?: {
      step_key?: string | null;
      history_count?: number;
      sequence_closed?: boolean;
      next_recommended_request_types?: string[];
    };
  } | null;
};

// ApprovalRequest - direct alias to the generated contract
export type ApprovalRequest = ApprovalRequestResponse;

// Channel health types - direct aliases to the generated contracts
export type TelegramChannelHealth = TelegramChannelHealthResponse;
export type OpenClawChannelHealth = OpenClawChannelHealthResponse;
export type ExternalChannelHeartbeat = ExternalChannelHeartbeatResponse;
export type ServiceDependencyHealth = ServiceDependencyHealthResponse;
export type PlatformServiceHealth = PlatformServiceHealthResponse;

type CreateProjectContractFields = Omit<GeneratedCreateProjectRequest, "description" | "github_repo" | "docs" | "features" | "data">;
type UpdateProjectContractFields = Omit<GeneratedUpdateProjectRequest, "description" | "github_repo" | "docs" | "features" | "data">;

export type CreateProjectRequest = CreateProjectContractFields & {
  description?: string;
  github_repo?: string;
  docs?: ProjectDocs | null;
  features?: ProjectFeatures | null;
  data?: ProjectData | null;
};

export type UpdateProjectRequest = UpdateProjectContractFields & {
  prd?: ProjectPRD;
  description?: string;
  github_repo?: string;
  docs?: ProjectDocs | null;
  features?: ProjectFeatures | null;
  data?: ProjectData | null;
};

// Utility types
export interface MCPToolResponse<T = unknown> {
  success: boolean;
  data?: T;
  error?: string;
  message?: string;
}

export interface PaginatedResponse<T> {
  items: T[];
  total: number;
  page: number;
  limit: number;
  hasMore: boolean;
}

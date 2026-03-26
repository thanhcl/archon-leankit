/**
 * Project Feature Types
 *
 * Central barrel export for all project-related types.
 * Following vertical slice architecture - types are co-located with features.
 */

// Document-related types from documents feature
export type * from "../documents/types";

// Task-related types from tasks feature
export type * from "../tasks/types";
// Core project types (vertical slice architecture)
export type {
  ApprovalRequest,
  BootstrapPlan,
  BootstrapPlanExecutionRun,
  BootstrapPlanListResponse,
  BootstrapPlanMaterializeResponse,
  BootstrapTraceEvent,
  BootstrapPlanTaskSummary,
  CreateProjectRequest,
  ExternalChannelHeartbeat,
  ExternalRequest,
  MCPToolResponse,
  OpenClawChannelHealth,
  PaginatedResponse,
  PlatformServiceHealth,
  Project,
  ProjectCreationProgress,
  ProjectData,
  ProjectDocs,
  ProjectFeatures,
  ProjectPRD,
  ServiceDependencyHealth,
  TelegramChannelHealth,
  UpdateProjectRequest,
} from "./project";

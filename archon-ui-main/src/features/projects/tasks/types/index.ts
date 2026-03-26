/**
 * Task Types
 *
 * All task-related types for the projects feature.
 */

// Hook return types
export type { UseTaskActionsReturn, UseTaskEditorReturn } from "./hooks";
// Core task types (vertical slice architecture)
export type {
  Assignee,
  BudgetConfig,
  BoardTaskStatus,
  CommonAssignee,
  CostStatusResponse,
  CreateTaskRequest,
  DatabaseTaskStatus,
  OwnerFeedbackRequest,
  OwnerFeedbackResponse,
  PredictedVsActual,
  ProjectTaskCountsMap,
  SprintEntry,
  SprintStatsResponse,
  SprintStatsSummary,
  Task,
  TaskBoardStatus,
  TaskCodeExample,
  TaskCounts,
  TaskEstimate,
  TaskPriority,
  TaskSource,
  TaskTypeName,
  UpdateBudgetConfigRequest,
  UpdateTaskRequest,
} from "./task";

// Export constants
export { COMMON_ASSIGNEES, EMPTY_TASK_COUNTS, getTaskCount } from "./task";

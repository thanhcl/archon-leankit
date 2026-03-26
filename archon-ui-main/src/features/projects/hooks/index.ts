/**
 * Project Hooks
 *
 * All React hooks for the projects feature.
 * Includes:
 * - Data fetching hooks (useProjects, useTasks, useDocuments)
 * - Mutation hooks (useCreateProject, useUpdateTask, etc.)
 * - UI state hooks (useProjectSelection, useTaskFilters)
 * - Business logic hooks (useTaskDragDrop, useDocumentEditor)
 */

export {
  useBootstrapPlanExecutionRuns,
  useBootstrapPlanTraceEvents,
  useExternalChannelHeartbeat,
  useOpenClawChannelHealth,
  projectKeys,
  useProjectApprovalRequests,
  useProjectArchitectRequests,
  useProjectExternalRequests,
  useBootstrapPlans,
  useCreateProject,
  useDeleteProject,
  useMaterializeBootstrapPlanBacklog,
  usePlatformServiceHealth,
  useProjectFeatures,
  useProjects,
  useTelegramChannelHealth,
  useUpdateProject,
} from "./useProjectQueries";

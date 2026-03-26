import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useSmartPolling } from "@/features/shared/hooks";
import { useToast } from "@/features/shared/hooks/useToast";
import {
  createOptimisticEntity,
  type OptimisticEntity,
  removeDuplicateEntities,
  replaceOptimisticEntity,
} from "@/features/shared/utils/optimistic";
import { DISABLED_QUERY_KEY, STALE_TIMES } from "../../shared/config/queryPatterns";
import { bootstrapPlanService, bootstrapTraceService, externalChannelService, projectService } from "../services";
import type {
  ApprovalRequest,
  BootstrapPlan,
  BootstrapPlanExecutionRun,
  BootstrapPlanMaterializeResponse,
  BootstrapTraceEvent,
  CreateProjectRequest,
  ExternalChannelHeartbeat,
  ExternalRequest,
  OpenClawChannelHealth,
  PlatformServiceHealth,
  Project,
  TelegramChannelHealth,
  UpdateProjectRequest,
} from "../types";

// Query keys factory for better organization
export const projectKeys = {
  all: ["projects"] as const,
  lists: () => [...projectKeys.all, "list"] as const,
  detail: (id: string) => [...projectKeys.all, "detail", id] as const,
  features: (id: string) => [...projectKeys.all, id, "features"] as const,
  bootstrapPlans: (id: string) => [...projectKeys.all, id, "bootstrap-plans"] as const,
  bootstrapPlanRuns: (id: string) => [...projectKeys.all, "bootstrap-plans", id, "execution-runs"] as const,
  bootstrapPlanTrace: (id: string) => [...projectKeys.all, "bootstrap-plans", id, "trace-events"] as const,
  externalRequests: (id: string) => [...projectKeys.all, id, "external-requests"] as const,
  architectRequests: (id: string) => [...projectKeys.all, id, "architect-requests"] as const,
  approvalRequests: (id: string) => [...projectKeys.all, id, "approval-requests"] as const,
  serviceHealth: () => [...projectKeys.all, "service-health"] as const,
  channelHeartbeat: () => [...projectKeys.all, "channel-heartbeat"] as const,
  channelHealth: (channel: "telegram" | "openclaw") => [...projectKeys.all, "channel-health", channel] as const,
  // Documents keys moved to documentKeys in documents feature
  // Tasks keys moved to taskKeys in tasks feature
};

// Fetch all projects with smart polling
export function useProjects() {
  const { refetchInterval } = useSmartPolling(2000); // 2 second base interval for active polling

  return useQuery<Project[]>({
    queryKey: projectKeys.lists(),
    queryFn: () => projectService.listProjects(),
    refetchInterval, // Smart interval based on page visibility/focus
    refetchOnWindowFocus: true, // Refetch immediately when tab gains focus (ETag makes this cheap)
    staleTime: STALE_TIMES.normal,
  });
}

// Fetch project features
export function useProjectFeatures(projectId: string | undefined) {
  return useQuery<Awaited<ReturnType<typeof projectService.getProjectFeatures>>>({
    queryKey: projectId ? projectKeys.features(projectId) : DISABLED_QUERY_KEY,
    queryFn: () => (projectId ? projectService.getProjectFeatures(projectId) : Promise.reject("No project ID")),
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

// Fetch bootstrap plans for a project
export function useBootstrapPlans(projectId: string | undefined) {
  return useQuery<BootstrapPlan[]>({
    queryKey: projectId ? projectKeys.bootstrapPlans(projectId) : DISABLED_QUERY_KEY,
    queryFn: () => (projectId ? bootstrapPlanService.listBootstrapPlans(projectId) : Promise.reject("No project ID")),
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

// Fetch recent execution runs linked to a persisted bootstrap plan
export function useBootstrapPlanExecutionRuns(planId: string | undefined, limit = 8) {
  return useQuery<BootstrapPlanExecutionRun[]>({
    queryKey: planId ? [...projectKeys.bootstrapPlanRuns(planId), limit] : DISABLED_QUERY_KEY,
    queryFn: () =>
      planId ? bootstrapPlanService.listExecutionRunsByBootstrapPlan(planId, limit) : Promise.reject("No plan ID"),
    enabled: !!planId,
    staleTime: STALE_TIMES.normal,
  });
}

// Fetch recent unified trace events linked to a persisted bootstrap plan
export function useBootstrapPlanTraceEvents(planId: string | undefined, limit = 8) {
  return useQuery<BootstrapTraceEvent[]>({
    queryKey: planId ? [...projectKeys.bootstrapPlanTrace(planId), limit] : DISABLED_QUERY_KEY,
    queryFn: () =>
      planId ? bootstrapTraceService.listTraceEventsByBootstrapPlan(planId, limit) : Promise.reject("No plan ID"),
    enabled: !!planId,
    staleTime: STALE_TIMES.normal,
  });
}

export function useProjectExternalRequests(projectId: string | undefined, limit = 6) {
  return useQuery<ExternalRequest[]>({
    queryKey: projectId ? [...projectKeys.externalRequests(projectId), limit] : DISABLED_QUERY_KEY,
    queryFn: () =>
      projectId ? externalChannelService.listProjectExternalRequests(projectId, limit) : Promise.reject("No project ID"),
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

export function useProjectArchitectRequests(projectId: string | undefined, limit = 6) {
  return useQuery<ExternalRequest[]>({
    queryKey: projectId ? [...projectKeys.architectRequests(projectId), limit] : DISABLED_QUERY_KEY,
    queryFn: () =>
      projectId
        ? externalChannelService.listProjectExternalRequests(projectId, limit, "architect-request")
        : Promise.reject("No project ID"),
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

export function useProjectApprovalRequests(projectId: string | undefined, limit = 6) {
  return useQuery<ApprovalRequest[]>({
    queryKey: projectId ? [...projectKeys.approvalRequests(projectId), limit] : DISABLED_QUERY_KEY,
    queryFn: () =>
      projectId ? externalChannelService.listProjectApprovalRequests(projectId, limit) : Promise.reject("No project ID"),
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

export function useTelegramChannelHealth() {
  return useQuery<TelegramChannelHealth>({
    queryKey: projectKeys.channelHealth("telegram"),
    queryFn: () => externalChannelService.getTelegramChannelHealth(),
    staleTime: STALE_TIMES.normal,
  });
}

export function useOpenClawChannelHealth() {
  return useQuery<OpenClawChannelHealth>({
    queryKey: projectKeys.channelHealth("openclaw"),
    queryFn: () => externalChannelService.getOpenClawChannelHealth(),
    staleTime: STALE_TIMES.normal,
  });
}

export function useExternalChannelHeartbeat() {
  return useQuery<ExternalChannelHeartbeat>({
    queryKey: projectKeys.channelHeartbeat(),
    queryFn: () => externalChannelService.getExternalChannelHeartbeat(),
    staleTime: STALE_TIMES.normal,
  });
}

export function usePlatformServiceHealth() {
  return useQuery<PlatformServiceHealth>({
    queryKey: projectKeys.serviceHealth(),
    queryFn: () => externalChannelService.getPlatformServiceHealth(),
    staleTime: STALE_TIMES.normal,
  });
}

// Create project mutation with optimistic updates
export function useCreateProject() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<
    Awaited<ReturnType<typeof projectService.createProject>>,
    Error,
    CreateProjectRequest,
    { previousProjects?: Project[]; optimisticId: string }
  >({
    mutationFn: (projectData: CreateProjectRequest) => projectService.createProject(projectData),
    onMutate: async (newProjectData) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: projectKeys.lists() });

      // Snapshot the previous value
      const previousProjects = queryClient.getQueryData<Project[]>(projectKeys.lists());

      // Create optimistic project with stable ID
      const optimisticProject = createOptimisticEntity<Project>({
        title: newProjectData.title,
        description: newProjectData.description,
        github_repo: newProjectData.github_repo,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
        docs: [],
        features: [],
        prd: undefined,
        data: undefined,
        pinned: false,
      });

      // Optimistically add the new project
      queryClient.setQueryData(projectKeys.lists(), (old: Project[] | undefined) => {
        if (!old) return [optimisticProject];
        // Add new project at the beginning of the list
        return [optimisticProject, ...old];
      });

      return { previousProjects, optimisticId: optimisticProject._localId };
    },
    onError: (error, variables, context) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error("Failed to create project:", error, { variables });

      // Rollback on error
      if (context?.previousProjects) {
        queryClient.setQueryData(projectKeys.lists(), context.previousProjects);
      }

      showToast(`Failed to create project: ${errorMessage}`, "error");
    },
    onSuccess: (response, _variables, context) => {
      // Extract the actual project from the response
      const newProject = response.project;

      // Replace optimistic with server data
      queryClient.setQueryData(projectKeys.lists(), (projects: (Project & Partial<OptimisticEntity>)[] = []) => {
        const replaced = replaceOptimisticEntity(projects, context?.optimisticId || "", newProject);
        return removeDuplicateEntities(replaced);
      });

      showToast("Project created successfully!", "success");
    },
    onSettled: () => {
      // Always refetch to ensure consistency after operation completes
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });
    },
  });
}

// Update project mutation (for pinning, etc.)
export function useUpdateProject() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: ({ projectId, updates }: { projectId: string; updates: UpdateProjectRequest }) =>
      projectService.updateProject(projectId, updates),
    onMutate: async ({ projectId, updates }) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: projectKeys.lists() });

      // Snapshot the previous value
      const previousProjects = queryClient.getQueryData<Project[]>(projectKeys.lists());

      // Optimistically update
      queryClient.setQueryData(projectKeys.lists(), (old: Project[] | undefined) => {
        if (!old) return old;

        // If pinning a project, unpin all others first
        if (updates.pinned === true) {
          return old.map((p) => ({
            ...p,
            pinned: p.id === projectId,
          }));
        }

        return old.map((p) => (p.id === projectId ? { ...p, ...updates } : p));
      });

      return { previousProjects };
    },
    onError: (_err, _variables, context) => {
      // Rollback on error
      if (context?.previousProjects) {
        queryClient.setQueryData(projectKeys.lists(), context.previousProjects);
      }
      showToast("Failed to update project", "error");
    },
    onSuccess: (data, variables) => {
      // Invalidate and refetch
      queryClient.invalidateQueries({ queryKey: projectKeys.lists() });

      if (variables.updates.pinned !== undefined) {
        const message = variables.updates.pinned
          ? `Pinned "${data.title}" as default project`
          : `Removed "${data.title}" from default selection`;
        showToast(message, "info");
      }
    },
  });
}

// Delete project mutation with optimistic updates
export function useDeleteProject() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: (projectId: string) => projectService.deleteProject(projectId),
    onMutate: async (projectId) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: projectKeys.lists() });

      // Snapshot the previous value
      const previousProjects = queryClient.getQueryData<Project[]>(projectKeys.lists());

      // Optimistically remove the project
      queryClient.setQueryData(projectKeys.lists(), (old: Project[] | undefined) => {
        if (!old) return old;
        return old.filter((project) => project.id !== projectId);
      });

      return { previousProjects };
    },
    onError: (error, projectId, context) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error("Failed to delete project:", error, { projectId });

      // Rollback on error
      if (context?.previousProjects) {
        queryClient.setQueryData(projectKeys.lists(), context.previousProjects);
      }

      showToast(`Failed to delete project: ${errorMessage}`, "error");
    },
    onSuccess: (_, projectId) => {
      // Don't refetch on success - trust optimistic update
      // Only remove the specific project's detail data (including nested keys)
      queryClient.removeQueries({ queryKey: projectKeys.detail(projectId), exact: false });
      // Also remove the project's feature queries
      queryClient.removeQueries({ queryKey: projectKeys.features(projectId), exact: false });
      showToast("Project deleted successfully", "success");
    },
  });
}

// Materialize persisted bootstrap-plan backlog items
export function useMaterializeBootstrapPlanBacklog(projectId: string | undefined) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<BootstrapPlanMaterializeResponse, Error, string>({
    mutationFn: (planId: string) => bootstrapPlanService.materializeBootstrapPlanBacklog(planId),
    onSuccess: (response) => {
      if (projectId) {
        queryClient.invalidateQueries({ queryKey: projectKeys.bootstrapPlans(projectId) });
        queryClient.invalidateQueries({ queryKey: ["projects", projectId, "tasks"] });
      }

      showToast(
        `Materialized ${response.created_tasks.length} tasks and skipped ${response.skipped} existing items`,
        "success",
      );
    },
    onError: (error) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      showToast(`Failed to materialize bootstrap backlog: ${errorMessage}`, "error");
    },
  });
}

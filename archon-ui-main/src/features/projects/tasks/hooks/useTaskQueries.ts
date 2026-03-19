import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createOptimisticEntity,
  type OptimisticEntity,
  removeDuplicateEntities,
  replaceOptimisticEntity,
} from "@/features/shared/utils/optimistic";
import { DISABLED_QUERY_KEY, STALE_TIMES } from "../../../shared/config/queryPatterns";
import { useSmartPolling } from "../../../shared/hooks";
import { useToast } from "../../../shared/hooks/useToast";
import { taskService } from "../services";
import type {
  BudgetConfig,
  CostStatusResponse,
  CreateTaskRequest,
  PredictedVsActual,
  SprintStatsResponse,
  Task,
  TaskEstimate,
  UpdateBudgetConfigRequest,
  UpdateTaskRequest,
} from "../types";

// Query keys factory for tasks - supports dual backend nature
export const taskKeys = {
  all: ["tasks"] as const,
  lists: () => [...taskKeys.all, "list"] as const, // For /api/tasks
  detail: (id: string) => [...taskKeys.all, "detail", id] as const, // For /api/tasks/{id}
  byProject: (projectId: string) => ["projects", projectId, "tasks"] as const, // For /api/projects/{id}/tasks
  counts: () => [...taskKeys.all, "counts"] as const, // For /api/projects/task-counts
  estimates: (projectId: string) => ["projects", projectId, "task-estimates"] as const, // For /api/projects/{id}/task-estimates
  sprintStats: (projectId: string) => ["projects", projectId, "sprint-stats"] as const,
  costStatus: (projectId: string) => ["projects", projectId, "cost-status"] as const,
  budgetConfig: (projectId: string) => ["projects", projectId, "budget-config"] as const,
};

// Fetch tasks for a specific project
export function useProjectTasks(projectId: string | undefined, enabled = true) {
  const { refetchInterval } = useSmartPolling(2000); // 2s active per guideline for real-time task updates

  return useQuery<Task[]>({
    queryKey: projectId ? taskKeys.byProject(projectId) : DISABLED_QUERY_KEY,
    queryFn: async () => {
      if (!projectId) throw new Error("No project ID");
      return taskService.getTasksByProject(projectId);
    },
    enabled: !!projectId && enabled,
    refetchInterval, // Smart interval based on page visibility/focus
    refetchOnWindowFocus: true, // Refetch immediately when tab gains focus (ETag makes this cheap)
    staleTime: STALE_TIMES.frequent,
  });
}

// Fetch task counts for all projects
export function useTaskCounts() {
  const { refetchInterval: countsRefetchInterval } = useSmartPolling(10_000); // 10s bg polling with smart pause
  return useQuery<Awaited<ReturnType<typeof taskService.getTaskCountsForAllProjects>>>({
    queryKey: taskKeys.counts(),
    queryFn: () => taskService.getTaskCountsForAllProjects(),
    refetchInterval: countsRefetchInterval,
    staleTime: STALE_TIMES.frequent,
  });
}

// Fetch task estimates for a project
export function useTaskEstimates(projectId: string | undefined) {
  return useQuery<{
    project_id: string;
    estimates: Record<string, TaskEstimate>;
    predicted_vs_actual: PredictedVsActual[];
  }>({
    queryKey: projectId ? taskKeys.estimates(projectId) : DISABLED_QUERY_KEY,
    queryFn: async () => {
      if (!projectId) throw new Error("No project ID");
      return taskService.getTaskEstimates(projectId);
    },
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal, // 30s - estimates don't change rapidly
  });
}

// Fetch sprint stats for a project (cost trend data)
export function useSprintStats(projectId: string | undefined) {
  return useQuery<SprintStatsResponse>({
    queryKey: projectId ? taskKeys.sprintStats(projectId) : DISABLED_QUERY_KEY,
    queryFn: async () => {
      if (!projectId) throw new Error("No project ID");
      return taskService.getSprintStats(projectId);
    },
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

// Fetch cost budget status for a project (budget line overlay)
export function useCostStatus(projectId: string | undefined) {
  return useQuery<CostStatusResponse>({
    queryKey: projectId ? taskKeys.costStatus(projectId) : DISABLED_QUERY_KEY,
    queryFn: async () => {
      if (!projectId) throw new Error("No project ID");
      return taskService.getCostStatus(projectId);
    },
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

// Fetch budget config (limits) for a project
export function useBudgetConfig(projectId: string | undefined) {
  return useQuery<BudgetConfig>({
    queryKey: projectId ? taskKeys.budgetConfig(projectId) : DISABLED_QUERY_KEY,
    queryFn: async () => {
      if (!projectId) throw new Error("No project ID");
      return taskService.getBudgetConfig(projectId);
    },
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

// Update budget config mutation
export function useUpdateBudgetConfig(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<{ budget_config: BudgetConfig }, Error, UpdateBudgetConfigRequest>({
    mutationFn: (config: UpdateBudgetConfigRequest) => taskService.updateBudgetConfig(projectId, config),
    onSuccess: (data) => {
      queryClient.setQueryData(taskKeys.budgetConfig(projectId), data.budget_config);
      // Also invalidate cost-status so the budget_config inside it refreshes
      queryClient.invalidateQueries({ queryKey: taskKeys.costStatus(projectId) });
      showToast("Budget limits updated", "success");
    },
    onError: (error) => {
      showToast(`Failed to update budget: ${error.message}`, "error");
    },
  });
}

// Create task mutation with optimistic updates
export function useCreateTask() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<Task, Error, CreateTaskRequest, { previousTasks?: Task[]; optimisticId: string }>({
    mutationFn: (taskData: CreateTaskRequest) => taskService.createTask(taskData),
    onMutate: async (newTaskData) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: taskKeys.byProject(newTaskData.project_id) });

      // Snapshot the previous value
      const previousTasks = queryClient.getQueryData<Task[]>(taskKeys.byProject(newTaskData.project_id));

      // Create optimistic task with stable ID
      const optimisticTask = createOptimisticEntity<Task>({
        project_id: newTaskData.project_id,
        title: newTaskData.title,
        description: newTaskData.description || "",
        status: newTaskData.status ?? "todo",
        assignee: newTaskData.assignee ?? "User",
        feature: newTaskData.feature,
        task_order: newTaskData.task_order ?? 100,
        priority: newTaskData.priority ?? "medium",
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });

      // Optimistically add the new task
      queryClient.setQueryData(taskKeys.byProject(newTaskData.project_id), (old: Task[] | undefined) => {
        if (!old) return [optimisticTask];
        return [...old, optimisticTask];
      });

      return { previousTasks, optimisticId: optimisticTask._localId };
    },
    onError: (error, variables, context) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error("Failed to create task:", error?.message, {
        project_id: variables?.project_id,
      });
      // Rollback on error
      if (context?.previousTasks) {
        queryClient.setQueryData(taskKeys.byProject(variables.project_id), context.previousTasks);
      }
      showToast(`Failed to create task: ${errorMessage}`, "error");
    },
    onSuccess: (serverTask, variables, context) => {
      // Replace optimistic with server data
      queryClient.setQueryData(
        taskKeys.byProject(variables.project_id),
        (tasks: (Task & Partial<OptimisticEntity>)[] = []) => {
          const replaced = replaceOptimisticEntity(tasks, context?.optimisticId || "", serverTask);
          return removeDuplicateEntities(replaced);
        },
      );

      // Invalidate counts since we have a new task
      queryClient.invalidateQueries({
        queryKey: taskKeys.counts(),
      });

      showToast("Task created successfully", "success");
    },
    onSettled: (_data, _error, variables) => {
      // Always refetch to ensure consistency after operation completes
      queryClient.invalidateQueries({ queryKey: taskKeys.byProject(variables.project_id) });
    },
  });
}

// Update task mutation with optimistic updates
export function useUpdateTask(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<Task, Error, { taskId: string; updates: UpdateTaskRequest }, { previousTasks?: Task[] }>({
    mutationFn: ({ taskId, updates }: { taskId: string; updates: UpdateTaskRequest }) =>
      taskService.updateTask(taskId, updates),
    onMutate: async ({ taskId, updates }) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: taskKeys.byProject(projectId) });

      // Snapshot the previous value
      const previousTasks = queryClient.getQueryData<Task[]>(taskKeys.byProject(projectId));

      // Optimistically update
      queryClient.setQueryData<Task[]>(taskKeys.byProject(projectId), (old) => {
        if (!old) return old;
        return old.map((task) => (task.id === taskId ? { ...task, ...updates } : task));
      });

      return { previousTasks };
    },
    onError: (error, variables, context) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error("Failed to update task:", error?.message, {
        taskId: variables?.taskId,
        changedFields: Object.keys(variables?.updates ?? {}),
      });
      // Rollback on error
      if (context?.previousTasks) {
        queryClient.setQueryData(taskKeys.byProject(projectId), context.previousTasks);
      }
      showToast(`Failed to update task: ${errorMessage}`, "error");
      // Refetch on error to ensure consistency
      queryClient.invalidateQueries({ queryKey: taskKeys.byProject(projectId) });
      // Only invalidate counts if status was changed
      if (variables.updates?.status) {
        queryClient.invalidateQueries({ queryKey: taskKeys.counts() });
      }
    },
    onSuccess: (data, { updates }) => {
      // Merge server response to keep timestamps and computed fields in sync
      queryClient.setQueryData<Task[]>(taskKeys.byProject(projectId), (old) =>
        old ? old.map((t) => (t.id === data.id ? data : t)) : old,
      );
      // Only invalidate counts if status changed (which affects counts)
      if (updates.status) {
        queryClient.invalidateQueries({ queryKey: taskKeys.counts() });
        // Show toast for significant status changes
        showToast(`Task moved to ${updates.status}`, "success");
      }
    },
  });
}

// Transition task mutation (lifecycle state changes: approve, reject, etc.)
export function useTransitionTask(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<
    { task: Task; transition: { from: string; to: string } },
    Error,
    { taskId: string; newStatus: string; reason?: string },
    { previousTasks?: Task[] }
  >({
    mutationFn: ({ taskId, newStatus, reason }) => taskService.transitionTask(taskId, newStatus, "owner", reason),
    onMutate: async ({ taskId, newStatus }) => {
      await queryClient.cancelQueries({ queryKey: taskKeys.byProject(projectId) });
      const previousTasks = queryClient.getQueryData<Task[]>(taskKeys.byProject(projectId));

      // Optimistic: update status locally
      queryClient.setQueryData<Task[]>(taskKeys.byProject(projectId), (old) => {
        if (!old) return old;
        return old.map((task) => (task.id === taskId ? { ...task, status: newStatus as Task["status"] } : task));
      });

      return { previousTasks };
    },
    onError: (error, variables, context) => {
      if (context?.previousTasks) {
        queryClient.setQueryData(taskKeys.byProject(projectId), context.previousTasks);
      }
      showToast(`Failed to transition task: ${error.message}`, "error");
    },
    onSuccess: (data) => {
      // Replace with server data
      queryClient.setQueryData<Task[]>(taskKeys.byProject(projectId), (old) =>
        old ? old.map((t) => (t.id === data.task.id ? data.task : t)) : old,
      );
      showToast(`Task ${data.transition.to === "done" ? "approved" : "sent back"}`, "success");
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: taskKeys.byProject(projectId) });
      queryClient.invalidateQueries({ queryKey: taskKeys.counts() });
    },
  });
}

// Delete task mutation
export function useDeleteTask(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation<void, Error, string, { previousTasks?: Task[] }>({
    mutationFn: (taskId: string) => taskService.deleteTask(taskId),
    onMutate: async (taskId) => {
      // Cancel any outgoing refetches
      await queryClient.cancelQueries({ queryKey: taskKeys.byProject(projectId) });

      // Snapshot the previous value
      const previousTasks = queryClient.getQueryData<Task[]>(taskKeys.byProject(projectId));

      // Optimistically remove the task
      queryClient.setQueryData<Task[]>(taskKeys.byProject(projectId), (old) => {
        if (!old) return old;
        return old.filter((task) => task.id !== taskId);
      });

      return { previousTasks };
    },
    onError: (error, taskId, context) => {
      const errorMessage = error instanceof Error ? error.message : String(error);
      console.error("Failed to delete task:", error?.message, { taskId });
      // Rollback on error
      if (context?.previousTasks) {
        queryClient.setQueryData(taskKeys.byProject(projectId), context.previousTasks);
      }
      showToast(`Failed to delete task: ${errorMessage}`, "error");
    },
    onSuccess: () => {
      showToast("Task deleted successfully", "success");
    },
    onSettled: () => {
      // Always refetch counts after deletion
      queryClient.invalidateQueries({ queryKey: taskKeys.counts() });
      // Also refetch the project's task list to reconcile server-side ordering
      queryClient.invalidateQueries({ queryKey: taskKeys.byProject(projectId) });
    },
  });
}

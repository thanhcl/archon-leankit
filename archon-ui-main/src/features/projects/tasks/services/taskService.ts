/**
 * Task Management Service
 * Focused service for task CRUD operations only
 */

import { callAPIWithETag } from "../../../shared/api/apiClient";
import { formatZodErrors, ValidationError } from "../../../shared/types/errors";

import { validateCreateTask, validateUpdateTask, validateUpdateTaskStatus } from "../schemas";
import type {
  BudgetConfig,
  CostStatusResponse,
  CreateTaskRequest,
  DatabaseTaskStatus,
  PredictedVsActual,
  SprintStatsResponse,
  Task,
  TaskCounts,
  TaskEstimate,
  UpdateBudgetConfigRequest,
  UpdateTaskRequest,
} from "../types";

export const taskService = {
  /**
   * Get all tasks for a project
   */
  async getTasksByProject(projectId: string): Promise<Task[]> {
    try {
      const tasks = await callAPIWithETag<Task[]>(`/api/projects/${projectId}/tasks`);

      // Return tasks as-is; UI uses DB status values (todo/doing/review/done)
      return tasks;
    } catch (error) {
      console.error(`Failed to get tasks for project ${projectId}:`, error);
      throw error;
    }
  },

  /**
   * Get a specific task by ID
   */
  async getTask(taskId: string): Promise<Task> {
    try {
      const task = await callAPIWithETag<Task>(`/api/tasks/${taskId}`);
      return task;
    } catch (error) {
      console.error(`Failed to get task ${taskId}:`, error);
      throw error;
    }
  },

  /**
   * Create a new task
   */
  async createTask(taskData: CreateTaskRequest): Promise<Task> {
    // Validate input
    const validation = validateCreateTask(taskData);
    if (!validation.success) {
      throw new ValidationError(formatZodErrors(validation.error));
    }

    try {
      // The validation.data already has defaults from schema
      const requestData = validation.data;

      // Backend returns { message: string, task: Task } for mutations
      const response = await callAPIWithETag<{ message: string; task: Task }>("/api/tasks", {
        method: "POST",
        body: JSON.stringify(requestData),
      });

      return response.task;
    } catch (error) {
      console.error("Failed to create task:", error);
      throw error;
    }
  },

  /**
   * Update an existing task
   */
  async updateTask(taskId: string, updates: UpdateTaskRequest): Promise<Task> {
    // Validate input
    const validation = validateUpdateTask(updates);
    if (!validation.success) {
      throw new ValidationError(formatZodErrors(validation.error));
    }

    try {
      // Backend returns { message: string, task: Task } for mutations
      const response = await callAPIWithETag<{ message: string; task: Task }>(`/api/tasks/${taskId}`, {
        method: "PUT",
        body: JSON.stringify(validation.data),
      });

      return response.task;
    } catch (error) {
      console.error(`Failed to update task ${taskId}:`, error);
      throw error;
    }
  },

  /**
   * Update task status (for drag & drop operations)
   */
  async updateTaskStatus(taskId: string, status: DatabaseTaskStatus): Promise<Task> {
    // Validate input
    const validation = validateUpdateTaskStatus({
      task_id: taskId,
      status: status,
    });
    if (!validation.success) {
      throw new ValidationError(formatZodErrors(validation.error));
    }

    try {
      // Use the standard update task endpoint with JSON body
      // Backend returns { message: string, task: Task } for mutations
      const response = await callAPIWithETag<{ message: string; task: Task }>(`/api/tasks/${taskId}`, {
        method: "PUT",
        body: JSON.stringify({ status }),
      });

      return response.task;
    } catch (error) {
      console.error(`Failed to update task status ${taskId}:`, error);
      throw error;
    }
  },

  /**
   * Delete a task
   */
  async deleteTask(taskId: string): Promise<void> {
    try {
      await callAPIWithETag<void>(`/api/tasks/${taskId}`, {
        method: "DELETE",
      });
    } catch (error) {
      console.error(`Failed to delete task ${taskId}:`, error);
      throw error;
    }
  },

  /**
   * Update task order for better drag-and-drop support
   */
  async updateTaskOrder(taskId: string, newOrder: number, newStatus?: DatabaseTaskStatus): Promise<Task> {
    try {
      const updates: UpdateTaskRequest = {
        task_order: newOrder,
      };

      if (newStatus) {
        updates.status = newStatus;
      }

      const task = await this.updateTask(taskId, updates);

      return task;
    } catch (error) {
      console.error(`Failed to update task order for ${taskId}:`, error);
      throw error;
    }
  },

  /**
   * Get tasks by status across all projects
   */
  async getTasksByStatus(status: DatabaseTaskStatus): Promise<Task[]> {
    try {
      // Note: This method requires cross-project access
      // For now, we'll throw an error suggesting to use project-scoped queries
      throw new Error("getTasksByStatus requires cross-project access. Use getTasksByProject instead.");
    } catch (error) {
      console.error(`Failed to get tasks by status ${status}:`, error);
      throw error;
    }
  },

  /**
   * Execute a lifecycle transition on a task (e.g. review → done, review → assigned)
   */
  async transitionTask(
    taskId: string,
    newStatus: string,
    changedBy: string = "owner",
    reason?: string,
  ): Promise<{ task: Task; transition: { from: string; to: string } }> {
    try {
      const body: Record<string, string> = { new_status: newStatus, changed_by: changedBy };
      if (reason) body.reason = reason;

      const response = await callAPIWithETag<{ message: string; task: Task; transition: { from: string; to: string } }>(
        `/api/tasks/${taskId}/transition`,
        {
          method: "POST",
          body: JSON.stringify(body),
        },
      );

      return { task: response.task, transition: response.transition };
    } catch (error) {
      console.error(`Failed to transition task ${taskId} to ${newStatus}:`, error);
      throw error;
    }
  },

  /**
   * Get task estimates for a project (predicted duration and cost)
   */
  async getTaskEstimates(projectId: string): Promise<{
    project_id: string;
    estimates: Record<string, TaskEstimate>;
    predicted_vs_actual: PredictedVsActual[];
  }> {
    try {
      return await callAPIWithETag(`/api/projects/${projectId}/task-estimates`);
    } catch (error) {
      console.error(`Failed to get task estimates for project ${projectId}:`, error);
      throw error;
    }
  },

  /**
   * Get task counts for all projects in a single batch request
   * Optimized endpoint to avoid N+1 query problem
   */
  async getTaskCountsForAllProjects(): Promise<Record<string, TaskCounts>> {
    try {
      const response = await callAPIWithETag<Record<string, TaskCounts>>("/api/projects/task-counts");
      return response || {};
    } catch (error) {
      console.error("Failed to get task counts for all projects:", error);
      throw error;
    }
  },

  async getSprintStats(projectId: string): Promise<SprintStatsResponse> {
    try {
      return await callAPIWithETag<SprintStatsResponse>(`/api/projects/${projectId}/sprint-stats?group_by=date`);
    } catch (error) {
      console.error(`Failed to get sprint stats for project ${projectId}:`, error);
      throw error;
    }
  },

  async getCostStatus(projectId: string): Promise<CostStatusResponse> {
    try {
      return await callAPIWithETag<CostStatusResponse>(`/api/projects/${projectId}/cost-status`);
    } catch (error) {
      console.error(`Failed to get cost status for project ${projectId}:`, error);
      throw error;
    }
  },

  async getBudgetConfig(projectId: string): Promise<BudgetConfig> {
    try {
      return await callAPIWithETag<BudgetConfig>(`/api/projects/${projectId}/budget-config`);
    } catch (error) {
      console.error(`Failed to get budget config for project ${projectId}:`, error);
      throw error;
    }
  },

  async updateBudgetConfig(projectId: string, config: UpdateBudgetConfigRequest): Promise<{ budget_config: BudgetConfig }> {
    try {
      const response = await fetch(`/api/projects/${projectId}/budget-config`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(config),
      });
      if (!response.ok) {
        const err = await response.json().catch(() => ({}));
        throw new Error(err?.detail?.error || err?.error || `HTTP ${response.status}`);
      }
      return await response.json();
    } catch (error) {
      console.error(`Failed to update budget config for project ${projectId}:`, error);
      throw error;
    }
  },
};

import { callAPIWithETag } from "../../shared/api/apiClient";
import type { ExecutionRunListResponse } from "../../../types/api-contracts.generated";
import type {
  BootstrapPlan,
  BootstrapPlanExecutionRun,
  BootstrapPlanListResponse,
  BootstrapPlanMaterializeResponse,
} from "../types";

export const bootstrapPlanService = {
  /** List persisted bootstrap plans for a project. */
  async listBootstrapPlans(projectId: string): Promise<BootstrapPlan[]> {
    const response = await callAPIWithETag<BootstrapPlanListResponse>(`/api/projects/${projectId}/bootstrap-plans`);
    return response.plans ?? [];
  },

  /** Materialize deferred backlog items from a stored bootstrap plan. */
  async materializeBootstrapPlanBacklog(planId: string): Promise<BootstrapPlanMaterializeResponse> {
    return callAPIWithETag<BootstrapPlanMaterializeResponse>(`/api/bootstrap-plans/${planId}/materialize-backlog`, {
      method: "POST",
    });
  },

  /** List recent execution runs linked to a persisted bootstrap plan. */
  async listExecutionRunsByBootstrapPlan(planId: string, limit = 8): Promise<BootstrapPlanExecutionRun[]> {
    const params = new URLSearchParams({
      bootstrap_plan_id: planId,
      limit: String(limit),
    });
    const response = await callAPIWithETag<ExecutionRunListResponse>(`/api/execution-runs?${params.toString()}`);
    return response.runs ?? [];
  },
};

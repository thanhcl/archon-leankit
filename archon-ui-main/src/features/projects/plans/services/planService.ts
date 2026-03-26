import { callAPIWithETag } from "../../../shared/api/apiClient";
import type {
  ImplementationDependency,
  ImplementationItem,
  ImplementationPhase,
  ImplementationPlan,
  ImportPlanResponse,
  PlanRollup,
} from "../types";

interface PlanListResponse {
  plans: ImplementationPlan[];
  total_count: number;
}

interface PhaseListResponse {
  phases: ImplementationPhase[];
  total_count: number;
}

interface ItemListResponse {
  items: ImplementationItem[];
  total_count: number;
}

interface DependencyListResponse {
  dependencies: ImplementationDependency[];
  total_count: number;
}

export const planService = {
  async listPlans(projectId: string): Promise<ImplementationPlan[]> {
    const data = await callAPIWithETag<PlanListResponse>(`/api/plans?project_id=${encodeURIComponent(projectId)}`);
    return data.plans;
  },

  async getPlan(planId: string): Promise<ImplementationPlan> {
    return callAPIWithETag<ImplementationPlan>(`/api/plans/${planId}`);
  },

  async listPhases(planId: string): Promise<ImplementationPhase[]> {
    const data = await callAPIWithETag<PhaseListResponse>(`/api/plans/${planId}/phases`);
    return data.phases;
  },

  async listItems(planId: string): Promise<ImplementationItem[]> {
    const data = await callAPIWithETag<ItemListResponse>(`/api/plans/${planId}/items`);
    return data.items;
  },

  async getItem(itemId: string): Promise<ImplementationItem> {
    return callAPIWithETag<ImplementationItem>(`/api/plan-items/${itemId}`);
  },

  async listDependencies(itemId: string): Promise<ImplementationDependency[]> {
    const data = await callAPIWithETag<DependencyListResponse>(`/api/plan-items/${itemId}/dependencies`);
    return data.dependencies;
  },

  async getPlanRollup(planId: string): Promise<PlanRollup> {
    return callAPIWithETag<PlanRollup>(`/api/plans/${planId}/rollup`);
  },

  async updateItemMetadata(itemId: string, metadata: Record<string, unknown>): Promise<ImplementationItem> {
    return callAPIWithETag<ImplementationItem>(`/api/plan-items/${itemId}`, {
      method: "PUT",
      body: JSON.stringify({ metadata }),
    });
  },

  async importPlan(request: {
    project_id: string;
    content?: string;
    file_path?: string;
    plan_id?: string;
    preview_only: boolean;
  }): Promise<ImportPlanResponse> {
    return callAPIWithETag<ImportPlanResponse>("/api/plans/import", {
      method: "POST",
      body: JSON.stringify(request),
    });
  },

  async autoLinkTasks(
    projectId?: string,
  ): Promise<{ scanned: number; linked: number; skipped: number; errors: string[] }> {
    const url = projectId
      ? `/api/plan-items/auto-link?project_id=${encodeURIComponent(projectId)}`
      : "/api/plan-items/auto-link";
    return callAPIWithETag(url, { method: "POST" });
  },
};

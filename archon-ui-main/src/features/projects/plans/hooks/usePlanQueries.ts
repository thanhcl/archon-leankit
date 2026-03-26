import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DISABLED_QUERY_KEY, STALE_TIMES } from "../../../shared/config/queryPatterns";
import { useToast } from "../../../shared/hooks/useToast";
import { planService } from "../services/planService";
import type { ImplementationDependency, ImplementationItem, ImportPlanResponse } from "../types";

export const planKeys = {
  all: ["plans"] as const,
  lists: (projectId: string) => [...planKeys.all, "list", projectId] as const,
  detail: (planId: string) => [...planKeys.all, "detail", planId] as const,
  phases: (planId: string) => [...planKeys.all, planId, "phases"] as const,
  items: (planId: string) => [...planKeys.all, planId, "items"] as const,
  dependencies: (itemId: string) => [...planKeys.all, "item", itemId, "dependencies"] as const,
  rollup: (planId: string) => [...planKeys.all, planId, "rollup"] as const,
};

export function usePlans(projectId: string | undefined) {
  return useQuery({
    queryKey: projectId ? planKeys.lists(projectId) : DISABLED_QUERY_KEY,
    queryFn: () => {
      if (!projectId) throw new Error("No project ID");
      return planService.listPlans(projectId);
    },
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

export function usePlanPhases(planId: string | undefined) {
  return useQuery({
    queryKey: planId ? planKeys.phases(planId) : DISABLED_QUERY_KEY,
    queryFn: () => {
      if (!planId) throw new Error("No plan ID");
      return planService.listPhases(planId);
    },
    enabled: !!planId,
    staleTime: STALE_TIMES.normal,
  });
}

export function usePlanItems(planId: string | undefined) {
  return useQuery({
    queryKey: planId ? planKeys.items(planId) : DISABLED_QUERY_KEY,
    queryFn: () => {
      if (!planId) throw new Error("No plan ID");
      return planService.listItems(planId);
    },
    enabled: !!planId,
    staleTime: STALE_TIMES.normal,
  });
}

export function useItemDependencies(items: ImplementationItem[], enabled: boolean) {
  // Fetch dependencies for all items in parallel — used by the DAG view
  return useQuery({
    queryKey: enabled && items.length > 0 ? ["plan-deps", items.map((i) => i.id).join(",")] : DISABLED_QUERY_KEY,
    queryFn: async (): Promise<ImplementationDependency[]> => {
      const results = await Promise.all(items.map((item) => planService.listDependencies(item.id)));
      return results.flat();
    },
    enabled: enabled && items.length > 0,
    staleTime: STALE_TIMES.normal,
  });
}

export function usePlanRollup(planId: string | undefined) {
  return useQuery({
    queryKey: planId ? planKeys.rollup(planId) : DISABLED_QUERY_KEY,
    queryFn: () => {
      if (!planId) throw new Error("No plan ID");
      return planService.getPlanRollup(planId);
    },
    enabled: !!planId,
    staleTime: STALE_TIMES.frequent,
  });
}

export function useUpdateItemMetadata() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: ({ itemId, metadata }: { itemId: string; metadata: Record<string, unknown> }) =>
      planService.updateItemMetadata(itemId, metadata),
    onSuccess: (updatedItem) => {
      // Invalidate items list for this plan
      queryClient.invalidateQueries({ queryKey: planKeys.items(updatedItem.plan_id) });
    },
    onError: (error: Error) => {
      showToast(`Failed to update: ${error.message}`, "error");
    },
  });
}

export function useImportPlan() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: (request: {
      project_id: string;
      content?: string;
      plan_id?: string;
      preview_only: boolean;
    }): Promise<ImportPlanResponse> => planService.importPlan(request),
    onSuccess: (data, variables) => {
      if (!variables.preview_only) {
        // Invalidate plans list so new/updated plan appears
        queryClient.invalidateQueries({ queryKey: planKeys.lists(variables.project_id) });
        if (data.plan_id) {
          queryClient.invalidateQueries({ queryKey: planKeys.items(data.plan_id) });
          queryClient.invalidateQueries({ queryKey: planKeys.phases(data.plan_id) });
        }
        showToast("Plan imported successfully", "success");
      }
    },
    onError: (error: Error) => {
      showToast(`Import failed: ${error.message}`, "error");
    },
  });
}

export function useAutoLinkTasks() {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: (projectId?: string) => planService.autoLinkTasks(projectId),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ["tasks"] });
      showToast(`Auto-linked ${data.linked} tasks (${data.scanned} scanned)`, "success");
    },
    onError: (error: Error) => {
      showToast(`Auto-link failed: ${error.message}`, "error");
    },
  });
}

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DISABLED_QUERY_KEY, STALE_TIMES } from "../../../shared/config/queryPatterns";
import { useToast } from "../../../shared/hooks/useToast";
import { ruleService } from "../services/ruleService";
import type { CreateRuleRequest, OptimizeResult, UpdateRuleRequest } from "../types";

export const ruleKeys = {
  all: ["rules"] as const,
  byProject: (projectId: string) => [...ruleKeys.all, "project", projectId] as const,
  detail: (ruleId: string) => [...ruleKeys.all, "detail", ruleId] as const,
};

export function useProjectRules(projectId: string | undefined) {
  return useQuery({
    queryKey: projectId ? ruleKeys.byProject(projectId) : DISABLED_QUERY_KEY,
    queryFn: async () => {
      if (!projectId) return [];
      return await ruleService.getRulesByProject(projectId);
    },
    enabled: !!projectId,
    staleTime: STALE_TIMES.normal,
  });
}

export function useCreateRule(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: async (data: CreateRuleRequest) => {
      return await ruleService.createRule({ ...data, project_id: projectId });
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ruleKeys.byProject(projectId) });
      showToast("Rule created successfully", "success");
    },
    onError: (error: Error) => {
      showToast(`Failed to create rule: ${error.message}`, "error");
    },
  });
}

export function useUpdateRule(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: async ({ ruleId, updates }: { ruleId: string; updates: UpdateRuleRequest }) => {
      return await ruleService.updateRule(ruleId, updates);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ruleKeys.byProject(projectId) });
      showToast("Rule updated successfully", "success");
    },
    onError: (error: Error) => {
      showToast(`Failed to update rule: ${error.message}`, "error");
    },
  });
}

export function useDeleteRule(projectId: string) {
  const queryClient = useQueryClient();
  const { showToast } = useToast();

  return useMutation({
    mutationFn: async (ruleId: string) => {
      return await ruleService.deleteRule(ruleId);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ruleKeys.byProject(projectId) });
      showToast("Rule deleted successfully", "success");
    },
    onError: (error: Error) => {
      showToast(`Failed to delete rule: ${error.message}`, "error");
    },
  });
}

export function useOptimizeRules(projectId: string) {
  const { showToast } = useToast();

  return useMutation<OptimizeResult, Error>({
    mutationFn: async () => {
      return await ruleService.optimizeRules(projectId);
    },
    onError: (error: Error) => {
      showToast(`Failed to optimize rules: ${error.message}`, "error");
    },
  });
}

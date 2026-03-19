import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Rule } from "../../types";
import {
  ruleKeys,
  useCreateRule,
  useDeleteRule,
  useOptimizeRules,
  useProjectRules,
  useUpdateRule,
} from "../useRuleQueries";

// Mock the service
vi.mock("../../services/ruleService", () => ({
  ruleService: {
    getRulesByProject: vi.fn(),
    getRule: vi.fn(),
    createRule: vi.fn(),
    updateRule: vi.fn(),
    deleteRule: vi.fn(),
    optimizeRules: vi.fn(),
  },
}));

// Mock the toast hook
vi.mock("@/features/shared/hooks/useToast", () => ({
  useToast: () => ({
    showToast: vi.fn(),
  }),
}));

// Mock shared patterns
vi.mock("@/features/shared/config/queryPatterns", () => ({
  DISABLED_QUERY_KEY: ["disabled"] as const,
  STALE_TIMES: {
    instant: 0,
    realtime: 3_000,
    frequent: 5_000,
    normal: 30_000,
    rare: 300_000,
    static: Number.POSITIVE_INFINITY,
  },
}));

const createWrapper = () => {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false },
      mutations: { retry: false },
    },
  });

  return ({ children }: { children: React.ReactNode }) =>
    React.createElement(QueryClientProvider, { client: queryClient }, children);
};

const mockRule: Rule = {
  id: "rule-1",
  section: "coding-style",
  rule_text: "Use TypeScript strict mode",
  project_id: "proj-1",
  priority: 100,
  source: "manual",
  enabled: true,
  created_at: "2024-01-01T00:00:00Z",
  updated_at: "2024-01-01T00:00:00Z",
};

describe("useRuleQueries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("ruleKeys", () => {
    it("should generate correct query keys", () => {
      expect(ruleKeys.all).toEqual(["rules"]);
      expect(ruleKeys.byProject("proj-1")).toEqual(["rules", "project", "proj-1"]);
      expect(ruleKeys.detail("rule-1")).toEqual(["rules", "detail", "rule-1"]);
    });
  });

  describe("useProjectRules", () => {
    it("should fetch rules for a project", async () => {
      const { ruleService } = await import("../../services/ruleService");
      vi.mocked(ruleService.getRulesByProject).mockResolvedValue([mockRule]);

      const { result } = renderHook(() => useProjectRules("proj-1"), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data).toEqual([mockRule]);
      });

      expect(ruleService.getRulesByProject).toHaveBeenCalledWith("proj-1");
    });

    it("should not fetch when projectId is undefined", () => {
      const { result } = renderHook(() => useProjectRules(undefined), {
        wrapper: createWrapper(),
      });

      expect(result.current.isFetching).toBe(false);
    });
  });

  describe("useCreateRule", () => {
    it("should create a rule with project_id", async () => {
      const { ruleService } = await import("../../services/ruleService");
      vi.mocked(ruleService.createRule).mockResolvedValue(mockRule);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useCreateRule("proj-1"), { wrapper });

      await result.current.mutateAsync({
        section: "coding-style",
        rule_text: "Use TypeScript strict mode",
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(ruleService.createRule).toHaveBeenCalledWith({
          section: "coding-style",
          rule_text: "Use TypeScript strict mode",
          project_id: "proj-1",
        });
      });
    });
  });

  describe("useUpdateRule", () => {
    it("should update a rule", async () => {
      const { ruleService } = await import("../../services/ruleService");
      vi.mocked(ruleService.updateRule).mockResolvedValue({ ...mockRule, rule_text: "Updated text" });

      const wrapper = createWrapper();
      const { result } = renderHook(() => useUpdateRule("proj-1"), { wrapper });

      await result.current.mutateAsync({
        ruleId: "rule-1",
        updates: { rule_text: "Updated text" },
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(ruleService.updateRule).toHaveBeenCalledWith("rule-1", { rule_text: "Updated text" });
      });
    });
  });

  describe("useDeleteRule", () => {
    it("should delete a rule", async () => {
      const { ruleService } = await import("../../services/ruleService");
      vi.mocked(ruleService.deleteRule).mockResolvedValue(undefined);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useDeleteRule("proj-1"), { wrapper });

      await result.current.mutateAsync("rule-1");

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(ruleService.deleteRule).toHaveBeenCalledWith("rule-1");
      });
    });

    it("should handle delete error", async () => {
      const { ruleService } = await import("../../services/ruleService");
      vi.mocked(ruleService.deleteRule).mockRejectedValue(new Error("Not found"));

      const wrapper = createWrapper();
      const { result } = renderHook(() => useDeleteRule("proj-1"), { wrapper });

      await expect(result.current.mutateAsync("rule-1")).rejects.toThrow("Not found");
    });
  });

  describe("useOptimizeRules", () => {
    it("should return optimization suggestions", async () => {
      const mockResult = {
        suggestions: [
          {
            action: "add" as const,
            section: "testing",
            rule_text: "Add unit tests for all services",
            confidence: 0.85,
            reason: "3 tasks failed due to missing tests",
            evidence: { task_ids: ["t-1", "t-2"] },
          },
        ],
        analysis: { total_tasks: 10, failed_count: 3 },
        project_id: "proj-1",
      };

      const { ruleService } = await import("../../services/ruleService");
      vi.mocked(ruleService.optimizeRules).mockResolvedValue(mockResult);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useOptimizeRules("proj-1"), { wrapper });

      const data = await result.current.mutateAsync();

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(data.suggestions).toHaveLength(1);
      expect(data.suggestions[0].action).toBe("add");
      expect(ruleService.optimizeRules).toHaveBeenCalledWith("proj-1");
    });
  });
});

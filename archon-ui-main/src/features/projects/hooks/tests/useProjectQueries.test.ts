import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { BootstrapPlan, Project } from "../../types";
import {
  projectKeys,
  useBootstrapPlanExecutionRuns,
  useBootstrapPlanTraceEvents,
  useBootstrapPlans,
  useCreateProject,
  useDeleteProject,
  useExternalChannelHeartbeat,
  useMaterializeBootstrapPlanBacklog,
  usePlatformServiceHealth,
  useProjectApprovalRequests,
  useProjectExternalRequests,
  useProjects,
  useUpdateProject,
} from "../useProjectQueries";

// Mock the services
vi.mock("../../services", () => ({
  bootstrapPlanService: {
    listBootstrapPlans: vi.fn(),
    listExecutionRunsByBootstrapPlan: vi.fn(),
    materializeBootstrapPlanBacklog: vi.fn(),
  },
  bootstrapTraceService: {
    listTraceEventsByBootstrapPlan: vi.fn(),
  },
  externalChannelService: {
    listProjectExternalRequests: vi.fn(),
    listProjectApprovalRequests: vi.fn(),
    getExternalChannelHeartbeat: vi.fn(),
    getPlatformServiceHealth: vi.fn(),
    getTelegramChannelHealth: vi.fn(),
    getOpenClawChannelHealth: vi.fn(),
  },
  projectService: {
    listProjects: vi.fn(),
    createProject: vi.fn(),
    updateProject: vi.fn(),
    deleteProject: vi.fn(),
    getProjectFeatures: vi.fn(),
  },
  taskService: {
    getTaskCountsForAllProjects: vi.fn(),
  },
}));

// Mock the toast hook
vi.mock("@/features/shared/hooks/useToast", () => ({
  useToast: () => ({
    showToast: vi.fn(),
  }),
}));

// Mock smart polling
vi.mock("@/features/shared/hooks", () => ({
  useSmartPolling: () => ({
    refetchInterval: 5000,
    isPaused: false,
  }),
}));

// Test wrapper with QueryClient
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

describe("useProjectQueries", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe("projectKeys", () => {
    it("should generate correct query keys", () => {
      expect(projectKeys.all).toEqual(["projects"]);
      expect(projectKeys.lists()).toEqual(["projects", "list"]);
      expect(projectKeys.detail("123")).toEqual(["projects", "detail", "123"]);
      expect(projectKeys.features("123")).toEqual(["projects", "123", "features"]);
      expect(projectKeys.bootstrapPlans("123")).toEqual(["projects", "123", "bootstrap-plans"]);
      expect(projectKeys.bootstrapPlanRuns("plan-123")).toEqual(["projects", "bootstrap-plans", "plan-123", "execution-runs"]);
      expect(projectKeys.bootstrapPlanTrace("plan-123")).toEqual(["projects", "bootstrap-plans", "plan-123", "trace-events"]);
      expect(projectKeys.externalRequests("123")).toEqual(["projects", "123", "external-requests"]);
      expect(projectKeys.architectRequests("123")).toEqual(["projects", "123", "architect-requests"]);
      expect(projectKeys.approvalRequests("123")).toEqual(["projects", "123", "approval-requests"]);
      expect(projectKeys.serviceHealth()).toEqual(["projects", "service-health"]);
      expect(projectKeys.channelHeartbeat()).toEqual(["projects", "channel-heartbeat"]);
    });
  });

  describe("usePlatformServiceHealth", () => {
    it("should fetch aggregated platform service health", async () => {
      const { externalChannelService } = await import("../../services");
      vi.mocked(externalChannelService.getPlatformServiceHealth).mockResolvedValue({
        status: "ready",
        total_services: 4,
        ready_services: 4,
        degraded_services: 0,
        ready_service_keys: ["control_plane", "archon_mcp", "observability_replay", "external_channels"],
        degraded_service_keys: [],
        issues: [],
        last_checked_at: "2026-03-21T10:00:00Z",
        control_plane: {
          key: "control_plane",
          label: "Archon Control Plane",
          status: "ready",
          configured: true,
          reachable: true,
          url: "http://localhost:8181",
          http_status: 200,
          latency_ms: 0,
          issues: [],
          last_checked_at: "2026-03-21T10:00:00Z",
        },
        archon_mcp: {
          key: "archon_mcp",
          label: "Archon MCP",
          status: "ready",
          configured: true,
          reachable: true,
          url: "http://localhost:8051/health",
          http_status: 200,
          latency_ms: 5,
          issues: [],
          last_checked_at: "2026-03-21T10:00:00Z",
        },
        observability_replay: {
          key: "observability_replay",
          label: "Observability Replay",
          status: "ready",
          configured: true,
          reachable: true,
          url: "http://localhost:4000/api/unified-events/recent?limit=1",
          http_status: 200,
          latency_ms: 8,
          issues: [],
          last_checked_at: "2026-03-21T10:00:00Z",
        },
        external_channels: {
          status: "ready",
          total_channels: 2,
          ready_channels: 2,
          degraded_channels: 0,
          ready_channel_keys: ["telegram", "openclaw"],
          degraded_channel_keys: [],
          issues: [],
          last_checked_at: "2026-03-21T10:00:00Z",
          telegram: {
            bot_configured: true,
            chat_configured: true,
            webhook_secret_configured: true,
            send_ready: true,
            digest_ready: true,
            status: "ready",
            issues: [],
            last_checked_at: "2026-03-21T10:00:00Z",
            digest_schedule_hour: 9,
            digest_schedule_minute: 0,
            digest_timezone: "Asia/Ho_Chi_Minh",
            digest_lookback_minutes: 1440,
            digest_scheduler_enabled: true,
            observability_replay_ready: true,
            digest_events: [],
            notify_events: [],
          },
          openclaw: {
            ingest_secret_configured: true,
            ingest_ready: true,
            status: "ready",
            issues: [],
            replay_guard_strategy: "semantic-window-v3",
            replay_window_minutes: 30,
            sequence_guard_enabled: true,
            conversational_policy_enabled: true,
            last_checked_at: "2026-03-21T10:00:00Z",
            architect_route_enabled: true,
            semantic_dedupe_enabled: true,
          },
        },
      });

      const { result } = renderHook(() => usePlatformServiceHealth(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data?.ready_services).toBe(4);
      });

      expect(externalChannelService.getPlatformServiceHealth).toHaveBeenCalledTimes(1);
    });
  });

  describe("useExternalChannelHeartbeat", () => {
    it("should fetch aggregated channel heartbeat", async () => {
      const { externalChannelService } = await import("../../services");
      vi.mocked(externalChannelService.getExternalChannelHeartbeat).mockResolvedValue({
        status: "ready",
        total_channels: 2,
        ready_channels: 2,
        degraded_channels: 0,
        ready_channel_keys: ["telegram", "openclaw"],
        degraded_channel_keys: [],
        issues: [],
        last_checked_at: "2026-03-21T10:00:00Z",
        telegram: {
          bot_configured: true,
          chat_configured: true,
          webhook_secret_configured: true,
          send_ready: true,
          digest_ready: true,
          status: "ready",
          issues: [],
          last_checked_at: "2026-03-21T10:00:00Z",
          digest_schedule_hour: 9,
          digest_schedule_minute: 0,
          digest_timezone: "Asia/Ho_Chi_Minh",
          digest_lookback_minutes: 1440,
          digest_scheduler_enabled: true,
          observability_replay_ready: true,
          digest_events: [],
          notify_events: [],
        },
        openclaw: {
          ingest_secret_configured: true,
          ingest_ready: true,
          status: "ready",
          issues: [],
          replay_guard_strategy: "semantic-window-v3",
          replay_window_minutes: 30,
          sequence_guard_enabled: true,
          conversational_policy_enabled: true,
          last_checked_at: "2026-03-21T10:00:00Z",
          architect_route_enabled: true,
          semantic_dedupe_enabled: true,
        },
      });

      const { result } = renderHook(() => useExternalChannelHeartbeat(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data?.ready_channels).toBe(2);
      });

      expect(externalChannelService.getExternalChannelHeartbeat).toHaveBeenCalledTimes(1);
    });
  });

  describe("useProjects", () => {
    it("should fetch projects list", async () => {
      const mockProjects: Project[] = [
        {
          id: "1",
          title: "Test Project",
          description: "Test Description",
          created_at: "2024-01-01T00:00:00Z",
          updated_at: "2024-01-01T00:00:00Z",
          pinned: false,
          features: [],
          docs: [],
        },
      ];

      const { projectService } = await import("../../services");
      vi.mocked(projectService.listProjects).mockResolvedValue(mockProjects);

      const { result } = renderHook(() => useProjects(), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data).toEqual(mockProjects);
      });

      expect(projectService.listProjects).toHaveBeenCalledTimes(1);
    });
  });

  describe("useCreateProject", () => {
    it("should optimistically add project and replace with server response", async () => {
      const newProject: Project = {
        id: "real-id",
        title: "New Project",
        description: "New Description",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        pinned: false,
        features: [],
        docs: [],
      };

      const { projectService } = await import("../../services");
      vi.mocked(projectService.createProject).mockResolvedValue({
        project_id: "new-project-id",
        project: newProject,
        status: "success",
        message: "Created",
      });

      const wrapper = createWrapper();
      const { result } = renderHook(() => useCreateProject(), { wrapper });

      await result.current.mutateAsync({
        title: "New Project",
        description: "New Description",
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(projectService.createProject).toHaveBeenCalledWith({
          title: "New Project",
          description: "New Description",
        });
      });
    });

    it("should rollback on error", async () => {
      const { projectService } = await import("../../services");
      vi.mocked(projectService.createProject).mockRejectedValue(new Error("Network error"));

      const wrapper = createWrapper();
      const { result } = renderHook(() => useCreateProject(), { wrapper });

      await expect(
        result.current.mutateAsync({
          title: "New Project",
          description: "New Description",
        }),
      ).rejects.toThrow("Network error");
    });
  });

  describe("useBootstrapPlans", () => {
    it("should fetch bootstrap plans for a project", async () => {
      const mockPlans: BootstrapPlan[] = [
        {
          id: "plan-1",
          project_id: "project-123",
          requested_provider: "chatgpt-codex",
          resolved_provider: "chatgpt-codex",
          strategy: "provider-generated",
          template: "nextjs-app",
          project_type: "web-app",
          bootstrap_policy: "strict",
          status: "materialized",
          created_at: "2026-03-21T10:00:00Z",
          updated_at: "2026-03-21T10:00:00Z",
        },
      ];

      const { bootstrapPlanService } = await import("../../services");
      vi.mocked(bootstrapPlanService.listBootstrapPlans).mockResolvedValue(mockPlans);

      const { result } = renderHook(() => useBootstrapPlans("project-123"), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data).toEqual(mockPlans);
      });

      expect(bootstrapPlanService.listBootstrapPlans).toHaveBeenCalledWith("project-123");
    });
  });

  describe("useBootstrapPlanExecutionRuns", () => {
    it("should fetch recent execution runs for a bootstrap plan", async () => {
      const { bootstrapPlanService } = await import("../../services");
      vi.mocked(bootstrapPlanService.listExecutionRunsByBootstrapPlan).mockResolvedValue([
        {
          id: "run-1",
          task_id: "task-1",
          project_id: "project-123",
          status: "running",
          stage: "execute",
          started_at: "2026-03-21T11:00:00Z",
          model: "gpt-5.4-codex",
          metadata: { bootstrap_plan_id: "plan-1" },
        },
      ]);

      const { result } = renderHook(() => useBootstrapPlanExecutionRuns("plan-1", 6), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data?.[0]?.id).toBe("run-1");
      });

      expect(bootstrapPlanService.listExecutionRunsByBootstrapPlan).toHaveBeenCalledWith("plan-1", 6);
    });
  });

  describe("useBootstrapPlanTraceEvents", () => {
    it("should fetch recent unified trace events for a bootstrap plan", async () => {
      const { bootstrapTraceService } = await import("../../services");
      vi.mocked(bootstrapTraceService.listTraceEventsByBootstrapPlan).mockResolvedValue([
        {
          id: "evt-1",
          type: "task",
          event: "task_started",
          source: "archon-engine",
          sourceApp: "bootstrap-project",
          projectId: "project-123",
          agentId: "agent-123",
          taskId: "task-1",
          executionRunId: "run-1",
          bootstrapPlanId: "plan-1",
          timestamp: "2026-03-21T11:10:00Z",
          data: { status: "doing" },
        },
      ]);

      const { result } = renderHook(() => useBootstrapPlanTraceEvents("plan-1", 6), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data?.[0]?.id).toBe("evt-1");
      });

      expect(bootstrapTraceService.listTraceEventsByBootstrapPlan).toHaveBeenCalledWith("plan-1", 6);
    });
  });

  describe("project external channel queries", () => {
    it("should fetch external requests for a project", async () => {
      const { externalChannelService } = await import("../../services");
      vi.mocked(externalChannelService.listProjectExternalRequests).mockResolvedValue([
        { id: "ext-1", status: "received", source_channel: "telegram", request_type: "message", materialize_as: "none", title: "Need status", summary: "Need status", correlation_id: "corr-1", deduplicated: false, created_at: "2026-03-21T12:00:00Z", updated_at: "2026-03-21T12:00:00Z" },
      ]);

      const { result } = renderHook(() => useProjectExternalRequests("project-123", 6), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data?.[0]?.id).toBe("ext-1");
      });
    });

    it("should fetch approval requests for a project", async () => {
      const { externalChannelService } = await import("../../services");
      vi.mocked(externalChannelService.listProjectApprovalRequests).mockResolvedValue([
        { id: "apr-1", status: "pending", title: "Approve deploy", summary: "Need approval", requested_by: "Owner", requested_channel: "telegram", created_at: "2026-03-21T12:00:00Z", updated_at: "2026-03-21T12:00:00Z" },
      ]);

      const { result } = renderHook(() => useProjectApprovalRequests("project-123", 6), {
        wrapper: createWrapper(),
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(result.current.data?.[0]?.id).toBe("apr-1");
      });
    });
  });

  describe("useUpdateProject", () => {
    it("should handle pinning a project", async () => {
      const updatedProject: Project = {
        id: "1",
        title: "Test Project",
        description: "Test Description",
        created_at: "2024-01-01T00:00:00Z",
        updated_at: "2024-01-01T00:00:00Z",
        pinned: true,
        features: [],
        docs: [],
      };

      const { projectService } = await import("../../services");
      vi.mocked(projectService.updateProject).mockResolvedValue(updatedProject);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useUpdateProject(), { wrapper });

      await result.current.mutateAsync({
        projectId: "1",
        updates: { pinned: true },
      });

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(projectService.updateProject).toHaveBeenCalledWith("1", { pinned: true });
      });
    });
  });

  describe("useDeleteProject", () => {
    it("should optimistically remove project", async () => {
      const { projectService } = await import("../../services");
      vi.mocked(projectService.deleteProject).mockResolvedValue(undefined);

      const wrapper = createWrapper();
      const { result } = renderHook(() => useDeleteProject(), { wrapper });

      await result.current.mutateAsync("project-to-delete");

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
        expect(projectService.deleteProject).toHaveBeenCalledWith("project-to-delete");
      });
    });

    it("should rollback on delete error", async () => {
      const { projectService } = await import("../../services");
      vi.mocked(projectService.deleteProject).mockRejectedValue(new Error("Permission denied"));

      const wrapper = createWrapper();
      const { result } = renderHook(() => useDeleteProject(), { wrapper });

      await expect(result.current.mutateAsync("project-to-delete")).rejects.toThrow("Permission denied");
    });
  });

  describe("useMaterializeBootstrapPlanBacklog", () => {
    it("should materialize persisted backlog items for a bootstrap plan", async () => {
      const { bootstrapPlanService } = await import("../../services");
      vi.mocked(bootstrapPlanService.materializeBootstrapPlanBacklog).mockResolvedValue({
        plan: {
          id: "plan-1",
          project_id: "project-123",
          requested_provider: "chatgpt-codex",
          resolved_provider: "chatgpt-codex",
          strategy: "provider-generated",
          template: "nextjs-app",
          project_type: "web-app",
          bootstrap_policy: "strict",
          status: "expanded",
          created_at: "2026-03-21T10:00:00Z",
          updated_at: "2026-03-21T10:05:00Z",
        },
        created_tasks: [{ id: "task-1", title: "Implement contracts", status: "approved" }],
        skipped: 0,
      });

      const wrapper = createWrapper();
      const { result } = renderHook(() => useMaterializeBootstrapPlanBacklog("project-123"), { wrapper });

      await result.current.mutateAsync("plan-1");

      await waitFor(() => {
        expect(result.current.isSuccess).toBe(true);
      });

      expect(bootstrapPlanService.materializeBootstrapPlanBacklog).toHaveBeenCalledWith("plan-1");
    });
  });
});

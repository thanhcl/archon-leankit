import { callAPIWithETag } from "../../shared/api/apiClient";
import type {
  ApprovalRequest,
  ExternalChannelHeartbeat,
  ExternalRequest,
  OpenClawChannelHealth,
  PlatformServiceHealth,
  TelegramChannelHealth,
} from "../types";

interface ExternalRequestListResponse {
  requests: ExternalRequest[];
}

interface ApprovalRequestListResponse {
  approvals: ApprovalRequest[];
}

export const externalChannelService = {
  async listProjectExternalRequests(projectId: string, limit = 6, requestType?: string): Promise<ExternalRequest[]> {
    const params = new URLSearchParams({ project_id: projectId, limit: String(limit) });
    if (requestType) {
      params.set("request_type", requestType);
    }
    const response = await callAPIWithETag<ExternalRequestListResponse>(`/api/external-requests?${params.toString()}`);
    return response.requests ?? [];
  },

  async listProjectApprovalRequests(projectId: string, limit = 6): Promise<ApprovalRequest[]> {
    const params = new URLSearchParams({ project_id: projectId, limit: String(limit) });
    const response = await callAPIWithETag<ApprovalRequestListResponse>(`/api/approval-requests?${params.toString()}`);
    return response.approvals ?? [];
  },

  async getTelegramChannelHealth(): Promise<TelegramChannelHealth> {
    return callAPIWithETag<TelegramChannelHealth>("/api/channels/telegram/health");
  },

  async getOpenClawChannelHealth(): Promise<OpenClawChannelHealth> {
    return callAPIWithETag<OpenClawChannelHealth>("/api/channels/openclaw/health");
  },

  async getExternalChannelHeartbeat(): Promise<ExternalChannelHeartbeat> {
    return callAPIWithETag<ExternalChannelHeartbeat>("/api/channels/health");
  },

  async getPlatformServiceHealth(): Promise<PlatformServiceHealth> {
    return callAPIWithETag<PlatformServiceHealth>("/api/services/health");
  },
};

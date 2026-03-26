import type { BootstrapTraceEvent } from "../types";

interface UnifiedEventListResponse {
  events: BootstrapTraceEvent[];
  total_count: number;
  filters_applied: string;
}

function getObservabilityHttpUrl(): string {
  return import.meta.env.VITE_OBSERVABILITY_HTTP_URL || "http://localhost:4000";
}

export const bootstrapTraceService = {
  /** List recent unified trace events correlated to a bootstrap plan. */
  async listTraceEventsByBootstrapPlan(planId: string, limit = 8): Promise<BootstrapTraceEvent[]> {
    const params = new URLSearchParams({
      bootstrapPlanId: planId,
      limit: String(limit),
    });
    const response = await fetch(`${getObservabilityHttpUrl()}/api/unified-events/recent?${params.toString()}`);
    if (!response.ok) {
      throw new Error(`Failed to load bootstrap trace events (${response.status})`);
    }
    const payload = await response.json() as UnifiedEventListResponse;
    return Array.isArray(payload.events) ? payload.events : [];
  },
};

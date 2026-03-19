import { callAPIWithETag } from "../../../shared/api/apiClient";
import type {
  CreateRuleRequest,
  OptimizeResult,
  Rule,
  RuleSuggestion,
  RuleSuggestionsResponse,
  UpdateRuleRequest,
} from "../types";

interface RulesResponse {
  rules: Rule[];
  total_count: number;
}

interface RuleResponse {
  rule: Rule;
}

export const ruleService = {
  async getRulesByProject(projectId: string): Promise<Rule[]> {
    const response = await callAPIWithETag<RulesResponse>(
      `/api/rules?project_id=${encodeURIComponent(projectId)}&include_global=false&enabled_only=false`,
    );
    return response.rules || [];
  },

  async getRule(ruleId: string): Promise<Rule> {
    const response = await callAPIWithETag<RuleResponse>(`/api/rules/${encodeURIComponent(ruleId)}`);
    if (!response.rule) {
      throw new Error(`Rule not found: ${ruleId}`);
    }
    return response.rule;
  },

  async createRule(data: CreateRuleRequest): Promise<Rule> {
    const response = await callAPIWithETag<RuleResponse>("/api/rules", {
      method: "POST",
      body: JSON.stringify(data),
    });
    if (!response.rule) {
      throw new Error("Failed to create rule");
    }
    return response.rule;
  },

  async updateRule(ruleId: string, updates: UpdateRuleRequest): Promise<Rule> {
    const response = await callAPIWithETag<RuleResponse>(`/api/rules/${encodeURIComponent(ruleId)}`, {
      method: "PUT",
      body: JSON.stringify(updates),
    });
    if (!response.rule) {
      throw new Error(`Failed to update rule: ${ruleId}`);
    }
    return response.rule;
  },

  async deleteRule(ruleId: string): Promise<void> {
    await callAPIWithETag<{ message: string }>(`/api/rules/${encodeURIComponent(ruleId)}`, {
      method: "DELETE",
    });
  },

  async optimizeRules(projectId: string): Promise<OptimizeResult> {
    const response = await callAPIWithETag<OptimizeResult>(`/api/rules/optimize/${encodeURIComponent(projectId)}`, {
      method: "POST",
    });
    return response;
  },

  async getSuggestions(projectId: string): Promise<RuleSuggestion[]> {
    const response = await callAPIWithETag<RuleSuggestionsResponse>(
      `/api/rules/suggestions/${encodeURIComponent(projectId)}?status=pending`,
    );
    return response.suggestions || [];
  },

  async approveSuggestion(suggestionId: string): Promise<{ rule: Rule; claude_md_written: boolean }> {
    return await callAPIWithETag(`/api/rules/suggestions/${encodeURIComponent(suggestionId)}/approve`, {
      method: "POST",
    });
  },

  async rejectSuggestion(suggestionId: string): Promise<void> {
    await callAPIWithETag(`/api/rules/suggestions/${encodeURIComponent(suggestionId)}/reject`, {
      method: "POST",
    });
  },
};

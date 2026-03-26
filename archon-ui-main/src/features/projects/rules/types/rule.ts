import type {
  CreateRuleRequest as GeneratedCreateRuleRequest,
  RuleResponse as GeneratedRuleResponse,
  UpdateRuleRequest as GeneratedUpdateRuleRequest,
} from "../../../../types/api-contracts.generated";

export type RuleSection =
  | "validation"
  | "integration"
  | "security"
  | "coding-style"
  | "architecture"
  | "testing"
  | "performance"
  | "documentation";

export const RULE_SECTIONS: RuleSection[] = [
  "validation",
  "integration",
  "security",
  "coding-style",
  "architecture",
  "testing",
  "performance",
  "documentation",
];

export type Rule = Omit<GeneratedRuleResponse, "section" | "source"> & {
  section: RuleSection;
  source: string;
  project_id: string | null;
  updated_at: string;
};

export type CreateRuleRequest = Omit<GeneratedCreateRuleRequest, "section" | "source" | "priority"> & {
  section: RuleSection;
  source?: string;
  project_id?: string;
  priority?: number;
};

export type UpdateRuleRequest = Omit<GeneratedUpdateRuleRequest, "section" | "source"> & {
  section?: RuleSection;
  source?: string;
};

export interface OptimizeSuggestion {
  action: "add" | "modify" | "remove";
  section: string;
  rule_text: string;
  confidence: number;
  reason: string;
  evidence: Record<string, unknown>;
}

export interface OptimizeResult {
  suggestions: OptimizeSuggestion[];
  analysis: Record<string, unknown>;
  project_id: string;
}

export interface RuleSuggestion {
  id: string;
  project_id: string;
  learning_id: string | null;
  section: string;
  rule_text: string;
  confidence: number;
  reason: string | null;
  status: "pending" | "approved" | "rejected";
  created_at: string;
  updated_at: string;
}

export interface RuleSuggestionsResponse {
  suggestions: RuleSuggestion[];
  count: number;
}

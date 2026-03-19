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

export interface Rule {
  id: string;
  section: RuleSection;
  rule_text: string;
  project_id: string | null;
  priority: number;
  source: string;
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface CreateRuleRequest {
  section: RuleSection;
  rule_text: string;
  project_id?: string;
  priority?: number;
  source?: string;
}

export interface UpdateRuleRequest {
  section?: RuleSection;
  rule_text?: string;
  priority?: number;
  source?: string;
  enabled?: boolean;
}

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

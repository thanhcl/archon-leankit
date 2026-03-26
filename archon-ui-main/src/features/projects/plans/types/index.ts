// Plan governance types — mirrors backend api_contracts.py plan models

export type PlanItemStatus =
  | "planned"
  | "ready"
  | "in_progress"
  | "blocked"
  | "review"
  | "done"
  | "deferred"
  | "cancelled";
export type DependencyType = "blocks" | "requires" | "related_to";

export interface ImplementationPlan {
  id: string;
  project_id: string;
  title: string;
  description: string | null;
  status: string;
  created_by: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface ImplementationPhase {
  id: string;
  plan_id: string;
  title: string;
  description: string | null;
  phase_order: number;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface LinkedTaskInfo {
  id: string;
  title: string;
  status: string;
  assignee: string;
  link_type: string;
}

export interface ImplementationItem {
  id: string;
  plan_id: string;
  phase_id: string | null;
  title: string;
  description: string | null;
  status: PlanItemStatus;
  item_order: number;
  priority: string;
  complexity: string;
  item_key: string | null;
  metadata: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
  linked_tasks?: LinkedTaskInfo[] | null;
}

export interface ImplementationDependency {
  id: string;
  dependent_id: string;
  dependency_id: string;
  dependency_type: DependencyType;
  metadata: Record<string, unknown> | null;
  created_at: string;
}

export interface ItemRollup {
  item_id: string;
  item_key: string | null;
  title: string;
  status: string;
  task_count: number;
  status_distribution: Record<string, number>;
  run_count: number;
  cost_usd: number | null;
  progress_percent: number;
}

export interface PhaseRollup {
  phase_id: string;
  title: string;
  phase_order: number;
  item_count: number;
  task_count: number;
  status_distribution: Record<string, number>;
  run_count: number;
  cost_usd: number | null;
  progress_percent: number;
  items: ItemRollup[];
}

export interface PlanRollup {
  plan_id: string;
  title: string;
  phase_count: number;
  item_count: number;
  task_count: number;
  status_distribution: Record<string, number>;
  run_count: number;
  cost_usd: number | null;
  progress_percent: number;
  phases: PhaseRollup[];
  unphased_items: ItemRollup[];
}

export interface ImportDiffItem {
  item_key: string;
  title: string;
  phase: string | null;
  status: string | null;
  changes: Array<Record<string, unknown>> | null;
  note: string | null;
}

export interface ImportPlanDiff {
  added: ImportDiffItem[];
  removed: ImportDiffItem[];
  changed: ImportDiffItem[];
  unchanged: ImportDiffItem[];
}

export interface ImportPlanResponse {
  action: string;
  plan_id: string | null;
  source_hash: string;
  hash_changed: boolean | null;
  phases_created: number | null;
  items_created: number | null;
  dependencies_created: number | null;
  dependencies_updated: number | null;
  diff: ImportPlanDiff | null;
}

// Acceptance criteria stored in item metadata
export interface AcceptanceCriteria {
  text: string;
  checked: boolean;
}

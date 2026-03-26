import { ChevronDown, ChevronRight, GitBranch, Layers, MapPin, Network } from "lucide-react";
import { useState } from "react";
import { Card } from "../../ui/primitives";
import { cn } from "../../ui/primitives/styles";
import { useProjectTasks } from "../tasks/hooks";
import { AcceptanceCriteriaChecklist } from "./components/AcceptanceCriteriaChecklist";
import { CostBreakdownChart } from "./components/CostBreakdownChart";
import { DependencyDAG } from "./components/DependencyDAG";
import { ImportSyncPanel } from "./components/ImportSyncPanel";
import { QualitySignalsTable } from "./components/QualitySignalsTable";
import { UnmappedTaskReport } from "./components/UnmappedTaskReport";
import { useItemDependencies, usePlanItems, usePlanRollup, usePlans } from "./hooks/usePlanQueries";
import type { ImplementationItem, ImplementationPlan } from "./types";

interface PlansTabProps {
  projectId: string;
}

// ── Plan progress bar ──────────────────────────────────────────────────────────

function PlanProgressBar({ percent }: { percent: number }) {
  return (
    <div className="flex items-center gap-2 mt-1">
      <div className="flex-1 h-1 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-cyan-500 dark:bg-cyan-400 rounded-full transition-all"
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
      <span className="text-[10px] text-gray-500 dark:text-gray-400 w-8 text-right">{percent.toFixed(0)}%</span>
    </div>
  );
}

// ── Plan list card ─────────────────────────────────────────────────────────────

interface PlanCardProps {
  plan: ImplementationPlan;
  isSelected: boolean;
  onSelect: () => void;
}

function PlanCard({ plan, isSelected, onSelect }: PlanCardProps) {
  return (
    <button
      type="button"
      onClick={onSelect}
      className={cn(
        "w-full text-left rounded-xl p-3 transition-all",
        "border",
        isSelected
          ? "border-cyan-500/50 bg-cyan-50/50 dark:bg-cyan-900/10"
          : "border-white/10 dark:border-white/[0.06] hover:border-cyan-500/30",
        "backdrop-blur-md",
      )}
    >
      <div className="flex items-center gap-2">
        <GitBranch className="w-3.5 h-3.5 text-cyan-500 dark:text-cyan-400 flex-shrink-0" />
        <span
          className={cn(
            "text-sm font-medium flex-1 truncate",
            isSelected ? "text-cyan-700 dark:text-cyan-300" : "text-gray-700 dark:text-gray-300",
          )}
        >
          {plan.title}
        </span>
        <span
          className={cn(
            "text-[10px] px-1.5 py-0.5 rounded-full font-medium",
            plan.status === "active"
              ? "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400"
              : plan.status === "draft"
                ? "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
                : "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
          )}
        >
          {plan.status}
        </span>
      </div>
      {plan.description && (
        <p className="mt-1 text-[11px] text-gray-500 dark:text-gray-400 line-clamp-1 pl-5">{plan.description}</p>
      )}
    </button>
  );
}

// ── Item collapsible row for acceptance criteria ───────────────────────────────

interface ItemRowProps {
  item: ImplementationItem;
}

function ItemCriteriaRow({ item }: ItemRowProps) {
  const [expanded, setExpanded] = useState(false);

  const hasAC =
    Array.isArray(item.metadata?.acceptance_criteria) && (item.metadata.acceptance_criteria as unknown[]).length > 0;

  return (
    <div>
      <button
        type="button"
        onClick={() => setExpanded((v) => !v)}
        className="w-full flex items-center gap-2 py-1.5 px-2 rounded-lg hover:bg-white/5 dark:hover:bg-white/[0.03] transition-colors group text-left"
      >
        {expanded ? (
          <ChevronDown className="w-3 h-3 text-gray-400 flex-shrink-0" />
        ) : (
          <ChevronRight className="w-3 h-3 text-gray-400 flex-shrink-0" />
        )}
        {item.item_key && (
          <span className="font-mono text-[10px] text-cyan-500 dark:text-cyan-400 flex-shrink-0">{item.item_key}</span>
        )}
        <span className="flex-1 text-xs text-gray-700 dark:text-gray-300 truncate">{item.title}</span>
        {hasAC && (
          <span className="text-[10px] text-gray-400 dark:text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity">
            view AC
          </span>
        )}
      </button>
      {expanded && (
        <div className="mt-1 pl-5">
          <AcceptanceCriteriaChecklist item={item} />
        </div>
      )}
    </div>
  );
}

// ── Section tabs ──────────────────────────────────────────────────────────────

type Section = "overview" | "dag" | "quality" | "criteria" | "unmapped" | "import";

const SECTIONS: { id: Section; label: string; icon: React.ReactNode }[] = [
  { id: "overview", label: "Overview", icon: <Layers className="w-3.5 h-3.5" /> },
  { id: "dag", label: "DAG", icon: <Network className="w-3.5 h-3.5" /> },
  { id: "quality", label: "Quality", icon: <GitBranch className="w-3.5 h-3.5" /> },
  { id: "criteria", label: "Criteria", icon: <MapPin className="w-3.5 h-3.5" /> },
  { id: "unmapped", label: "Unmapped", icon: <MapPin className="w-3.5 h-3.5" /> },
  { id: "import", label: "Import", icon: <GitBranch className="w-3.5 h-3.5" /> },
];

// ── Plan detail pane ──────────────────────────────────────────────────────────

interface PlanDetailProps {
  plan: ImplementationPlan;
  projectId: string;
}

function PlanDetail({ plan, projectId }: PlanDetailProps) {
  const [section, setSection] = useState<Section>("overview");

  const { data: items = [], isLoading: itemsLoading } = usePlanItems(plan.id);
  const { data: rollup, isLoading: rollupLoading } = usePlanRollup(plan.id);
  const { data: dependencies = [], isLoading: depsLoading } = useItemDependencies(
    items,
    section === "dag" && items.length > 0,
  );
  const { data: tasks = [], isLoading: tasksLoading } = useProjectTasks(projectId);

  return (
    <div className="space-y-4">
      {/* Section navigation */}
      <div className="flex flex-wrap gap-1.5">
        {SECTIONS.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSection(s.id)}
            className={cn(
              "flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all",
              section === s.id
                ? "bg-cyan-500/20 dark:bg-cyan-500/15 text-cyan-700 dark:text-cyan-300 border border-cyan-500/30"
                : "text-gray-600 dark:text-gray-400 hover:text-gray-800 dark:hover:text-gray-200 hover:bg-white/10 dark:hover:bg-white/5 border border-transparent",
            )}
          >
            {s.icon}
            {s.label}
          </button>
        ))}
      </div>

      {/* Overview */}
      {section === "overview" && (
        <div className="space-y-4">
          {/* Plan summary */}
          <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
            <div className="flex items-start justify-between">
              <div>
                <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">{plan.title}</h3>
                {plan.description && (
                  <p className="mt-1 text-xs text-gray-500 dark:text-gray-400">{plan.description}</p>
                )}
              </div>
              <span
                className={cn(
                  "text-[10px] px-1.5 py-0.5 rounded-full font-medium flex-shrink-0 ml-2",
                  plan.status === "active"
                    ? "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400"
                    : plan.status === "draft"
                      ? "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400"
                      : "bg-blue-100 dark:bg-blue-900/30 text-blue-700 dark:text-blue-400",
                )}
              >
                {plan.status}
              </span>
            </div>

            {rollupLoading && <p className="mt-2 text-xs text-gray-400 dark:text-gray-600">Loading rollup…</p>}
            {rollup && (
              <div className="mt-3 grid grid-cols-2 md:grid-cols-4 gap-3">
                {[
                  { label: "Phases", value: rollup.phase_count },
                  { label: "Items", value: rollup.item_count },
                  { label: "Tasks", value: rollup.task_count },
                  {
                    label: "Total Cost",
                    value: rollup.cost_usd != null ? `$${rollup.cost_usd.toFixed(3)}` : "—",
                  },
                ].map((stat) => (
                  <div key={stat.label} className="text-center">
                    <p className="text-xs text-gray-500 dark:text-gray-400">{stat.label}</p>
                    <p className="text-lg font-bold text-gray-800 dark:text-gray-200">{stat.value}</p>
                  </div>
                ))}
              </div>
            )}
            {rollup && <PlanProgressBar percent={rollup.progress_percent} />}
          </Card>

          {/* Cost breakdown */}
          {rollup && rollup.phases.length > 0 && <CostBreakdownChart rollup={rollup} />}

          {/* Phase summary */}
          {rollup && rollup.phases.length > 0 && (
            <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
              <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Phase Progress</h3>
              <div className="space-y-3">
                {rollup.phases
                  .slice()
                  .sort((a, b) => a.phase_order - b.phase_order)
                  .map((phase) => (
                    <div key={phase.phase_id}>
                      <div className="flex items-center justify-between text-xs">
                        <span className="font-medium text-gray-700 dark:text-gray-300">{phase.title}</span>
                        <span className="text-gray-500 dark:text-gray-400">
                          {phase.item_count} items · {phase.task_count} tasks
                          {phase.cost_usd != null && ` · $${phase.cost_usd.toFixed(3)}`}
                        </span>
                      </div>
                      <PlanProgressBar percent={phase.progress_percent} />
                    </div>
                  ))}
              </div>
            </Card>
          )}
        </div>
      )}

      {/* DAG */}
      {section === "dag" && (
        <DependencyDAG items={items} dependencies={dependencies} isLoading={itemsLoading || depsLoading} />
      )}

      {/* Quality signals */}
      {section === "quality" && (
        <div className="space-y-4">
          {rollupLoading ? (
            <p className="text-xs text-gray-500 dark:text-gray-400">Loading quality data…</p>
          ) : rollup ? (
            <QualitySignalsTable rollup={rollup} />
          ) : (
            <p className="text-xs text-gray-500 dark:text-gray-400">No rollup data available.</p>
          )}
        </div>
      )}

      {/* Acceptance criteria */}
      {section === "criteria" && (
        <div className="space-y-2">
          <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
            <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Acceptance Criteria by Item</h3>
            {itemsLoading && <p className="text-xs text-gray-500 dark:text-gray-400">Loading items…</p>}
            {items.length === 0 && !itemsLoading && (
              <p className="text-xs text-gray-500 dark:text-gray-400">No items in this plan.</p>
            )}
            <div className="space-y-0.5">
              {items.map((item) => (
                <ItemCriteriaRow key={item.id} item={item} />
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* Unmapped tasks */}
      {section === "unmapped" && (
        <UnmappedTaskReport
          tasks={
            tasks as Array<{
              id: string;
              title: string;
              status: string;
              assignee?: string;
              plan_item_id?: string | null;
            }>
          }
          projectId={projectId}
          isLoading={tasksLoading}
        />
      )}

      {/* Import/sync */}
      {section === "import" && <ImportSyncPanel projectId={projectId} plan={plan} />}
    </div>
  );
}

// ── Main PlansTab ─────────────────────────────────────────────────────────────

export function PlansTab({ projectId }: PlansTabProps) {
  const [selectedPlanId, setSelectedPlanId] = useState<string | null>(null);
  const { data: plans = [], isLoading, error } = usePlans(projectId);

  const selectedPlan = plans.find((p) => p.id === selectedPlanId) ?? (plans.length > 0 ? plans[0] : null);

  // Auto-select first plan
  if (!selectedPlanId && plans.length > 0) {
    setSelectedPlanId(plans[0].id);
  }

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-32 text-sm text-gray-500 dark:text-gray-400">
        Loading plans…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-32 text-sm text-red-500 dark:text-red-400">
        Failed to load plans: {(error as Error).message}
      </div>
    );
  }

  if (plans.length === 0) {
    return (
      <div className="space-y-4">
        <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
          <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">No Plans Yet</h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            Import a plan from a canonical markdown document to get started.
          </p>
        </Card>
        <ImportSyncPanel projectId={projectId} />
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {/* Plan list */}
      {plans.length > 1 && (
        <div className="flex flex-wrap gap-2">
          {plans.map((plan) => (
            <PlanCard
              key={plan.id}
              plan={plan}
              isSelected={selectedPlan?.id === plan.id}
              onSelect={() => setSelectedPlanId(plan.id)}
            />
          ))}
        </div>
      )}

      {/* Plan detail */}
      {selectedPlan && <PlanDetail plan={selectedPlan} projectId={projectId} />}
    </div>
  );
}

import { Card } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import type { PhaseRollup, PlanRollup } from "../types";

interface QualitySignalsTableProps {
  rollup: PlanRollup;
}

interface WorkstreamSignals {
  name: string;
  itemCount: number;
  taskCount: number;
  runCount: number;
  doneCount: number;
  blockedCount: number;
  firstPassRate: number | null;
  avgRetries: number | null;
  escalations: number;
  progressPercent: number;
}

function computeSignals(phase: PhaseRollup): WorkstreamSignals {
  const doneCount = phase.status_distribution.done ?? 0;
  const blockedCount = phase.status_distribution.blocked ?? 0;

  // First-pass rate: items that finished with run_count <= phase's minimum (approx as items done / total)
  // More precisely: items where run_count = 1 (first attempt) — not directly available at phase level
  // Use done items as proxy: first-pass = done with no retries. We use per-item data from phase.items
  const firstPassItems = phase.items.filter((i) => i.status === "done" && i.run_count <= 1).length;
  const firstPassRate = phase.item_count > 0 ? (firstPassItems / phase.item_count) * 100 : null;

  // Avg retries = (total_runs - done_items) / done_items — proxy for retry overhead
  const avgRetries = doneCount > 0 ? (phase.run_count - doneCount) / doneCount : null;

  return {
    name: phase.title,
    itemCount: phase.item_count,
    taskCount: phase.task_count,
    runCount: phase.run_count,
    doneCount,
    blockedCount,
    firstPassRate,
    avgRetries,
    escalations: blockedCount,
    progressPercent: phase.progress_percent,
  };
}

function ProgressBar({ value }: { value: number }) {
  return (
    <div className="flex items-center gap-2">
      <div className="flex-1 h-1.5 bg-gray-200 dark:bg-gray-700 rounded-full overflow-hidden">
        <div
          className="h-full bg-cyan-500 dark:bg-cyan-400 rounded-full transition-all"
          style={{ width: `${Math.min(value, 100)}%` }}
        />
      </div>
      <span className="text-[10px] text-gray-500 dark:text-gray-400 w-8 text-right">{value.toFixed(0)}%</span>
    </div>
  );
}

function RateCell({ value, unit = "%" }: { value: number | null; unit?: string }) {
  if (value === null) return <span className="text-gray-400 dark:text-gray-600 text-[11px]">—</span>;
  const isGood = unit === "%" ? value >= 70 : value <= 1;
  return (
    <span
      className={cn(
        "text-[11px] font-medium",
        isGood
          ? "text-green-600 dark:text-green-400"
          : value > 0
            ? "text-orange-500 dark:text-orange-400"
            : "text-gray-500",
      )}
    >
      {unit === "%" ? `${value.toFixed(0)}%` : value.toFixed(1)}
    </span>
  );
}

export function QualitySignalsTable({ rollup }: QualitySignalsTableProps) {
  const workstreams = rollup.phases.map(computeSignals);

  if (workstreams.length === 0) {
    return (
      <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-2">Quality Signals</h3>
        <p className="text-xs text-gray-500 dark:text-gray-400">No phases defined for this plan.</p>
      </Card>
    );
  }

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06] overflow-hidden">
      <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200 mb-3">Quality Signals by Workstream</h3>
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-white/10 dark:border-white/[0.08]">
              {["Workstream", "Progress", "First-Pass Rate", "Avg Retries", "Escalations", "Items", "Tasks"].map(
                (h) => (
                  <th
                    key={h}
                    className="text-left pb-2 pr-4 text-[10px] font-semibold text-gray-500 dark:text-gray-400 uppercase tracking-wide whitespace-nowrap"
                  >
                    {h}
                  </th>
                ),
              )}
            </tr>
          </thead>
          <tbody>
            {workstreams.map((ws) => (
              <tr
                key={ws.name}
                className="border-b border-white/5 dark:border-white/[0.04] hover:bg-white/5 dark:hover:bg-white/[0.03] transition-colors"
              >
                <td className="py-2 pr-4 font-medium text-gray-700 dark:text-gray-300 max-w-[140px]">
                  <span className="block truncate">{ws.name}</span>
                </td>
                <td className="py-2 pr-4 min-w-[100px]">
                  <ProgressBar value={ws.progressPercent} />
                </td>
                <td className="py-2 pr-4">
                  <RateCell value={ws.firstPassRate} unit="%" />
                </td>
                <td className="py-2 pr-4">
                  <RateCell value={ws.avgRetries} unit="x" />
                </td>
                <td className="py-2 pr-4">
                  {ws.escalations > 0 ? (
                    <span className="text-[11px] font-medium text-red-500 dark:text-red-400">{ws.escalations}</span>
                  ) : (
                    <span className="text-[11px] text-green-600 dark:text-green-400">0</span>
                  )}
                </td>
                <td className="py-2 pr-4 text-gray-500 dark:text-gray-400">{ws.itemCount}</td>
                <td className="py-2 text-gray-500 dark:text-gray-400">{ws.taskCount}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="mt-2 text-[10px] text-gray-400 dark:text-gray-600">
        First-pass rate = done items with ≤1 run ÷ total items. Avg retries = (runs − done) ÷ done. Escalations =
        blocked items.
      </p>
    </Card>
  );
}

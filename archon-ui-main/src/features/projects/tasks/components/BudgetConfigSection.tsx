import { useState } from "react";
import { Card } from "../../../ui/primitives";
import { useBudgetConfig, useUpdateBudgetConfig } from "../hooks";

interface BudgetConfigSectionProps {
  projectId: string;
}

export function BudgetConfigSection({ projectId }: BudgetConfigSectionProps) {
  const { data: config, isLoading } = useBudgetConfig(projectId);
  const updateMutation = useUpdateBudgetConfig(projectId);

  const [isEditing, setIsEditing] = useState(false);
  const [dailyLimit, setDailyLimit] = useState("");
  const [sprintLimit, setSprintLimit] = useState("");

  function startEditing() {
    setDailyLimit(String(config?.max_cost_per_day ?? ""));
    setSprintLimit(String(config?.max_cost_per_sprint ?? ""));
    setIsEditing(true);
  }

  function cancelEditing() {
    setIsEditing(false);
  }

  async function handleSave() {
    const daily = parseFloat(dailyLimit);
    const sprint = parseFloat(sprintLimit);
    if (isNaN(daily) || daily <= 0 || isNaN(sprint) || sprint <= 0) return;

    await updateMutation.mutateAsync({ max_cost_per_day: daily, max_cost_per_sprint: sprint });
    setIsEditing(false);
  }

  if (isLoading) return null;

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300">Budget Limits</h3>
        {!isEditing && (
          <button
            onClick={startEditing}
            className="text-xs text-cyan-600 dark:text-cyan-400 hover:underline"
          >
            Edit
          </button>
        )}
      </div>

      {isEditing ? (
        <div className="space-y-3">
          <div className="flex items-center gap-3">
            <label className="text-xs text-gray-500 dark:text-gray-400 w-28 shrink-0">Daily limit ($)</label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={dailyLimit}
              onChange={(e) => setDailyLimit(e.target.value)}
              className="w-28 rounded border border-white/20 bg-white/10 dark:bg-gray-800/50 px-2 py-1 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            />
          </div>
          <div className="flex items-center gap-3">
            <label className="text-xs text-gray-500 dark:text-gray-400 w-28 shrink-0">Sprint limit ($)</label>
            <input
              type="number"
              min="0.01"
              step="0.01"
              value={sprintLimit}
              onChange={(e) => setSprintLimit(e.target.value)}
              className="w-28 rounded border border-white/20 bg-white/10 dark:bg-gray-800/50 px-2 py-1 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:ring-1 focus:ring-cyan-500"
            />
          </div>
          <div className="flex gap-2 pt-1">
            <button
              onClick={handleSave}
              disabled={updateMutation.isPending}
              className="text-xs px-3 py-1 rounded bg-cyan-600 hover:bg-cyan-700 text-white disabled:opacity-50"
            >
              {updateMutation.isPending ? "Saving…" : "Save"}
            </button>
            <button
              onClick={cancelEditing}
              className="text-xs px-3 py-1 rounded border border-white/20 text-gray-600 dark:text-gray-400 hover:bg-white/10"
            >
              Cancel
            </button>
          </div>
        </div>
      ) : (
        <div className="flex gap-6 text-xs text-gray-600 dark:text-gray-400">
          <span>Daily: <span className="font-medium text-gray-800 dark:text-gray-200">${config?.max_cost_per_day ?? "—"}</span></span>
          <span>Sprint: <span className="font-medium text-gray-800 dark:text-gray-200">${config?.max_cost_per_sprint ?? "—"}</span></span>
        </div>
      )}
    </Card>
  );
}

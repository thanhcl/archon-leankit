import { useMemo } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { Card } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import type { CostStatusResponse, SprintStatsResponse } from "../types";

interface CostTrendChartProps {
  sprintStats: SprintStatsResponse;
  costStatus?: CostStatusResponse;
}

interface ChartDataPoint {
  date: string;
  dailyCost: number;
  cumulativeCost: number;
  tasksCompleted: number;
}

function formatDate(dateStr: string): string {
  const d = new Date(dateStr);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number; dataKey: string; color: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;

  const cumulativeEntry = payload.find((p) => p.dataKey === "cumulativeCost");
  const dailyEntry = payload.find((p) => p.dataKey === "dailyCost");
  const tasksEntry = payload.find((p) => p.dataKey === "tasksCompleted");

  return (
    <div
      className={cn(
        "rounded-lg border border-white/10 dark:border-white/[0.06] p-3",
        "backdrop-blur-xl bg-white/90 dark:bg-gray-900/90",
        "shadow-lg text-sm",
      )}
    >
      <p className="font-medium text-gray-900 dark:text-gray-100 mb-1">{label}</p>
      {dailyEntry && <p className="text-cyan-600 dark:text-cyan-400">Daily Cost: ${dailyEntry.value.toFixed(2)}</p>}
      {cumulativeEntry && (
        <p className="text-purple-600 dark:text-purple-400">Cumulative: ${cumulativeEntry.value.toFixed(2)}</p>
      )}
      {tasksEntry && <p className="text-gray-600 dark:text-gray-400">Tasks Done: {tasksEntry.value}</p>}
    </div>
  );
}

export function CostTrendChart({ sprintStats, costStatus }: CostTrendChartProps) {
  const chartData = useMemo<ChartDataPoint[]>(() => {
    let cumulative = 0;
    return sprintStats.sprints.map((sprint) => {
      cumulative += sprint.estimated_cost_usd;
      return {
        date: formatDate(sprint.date),
        dailyCost: sprint.estimated_cost_usd,
        cumulativeCost: Math.round(cumulative * 100) / 100,
        tasksCompleted: sprint.tasks_completed,
      };
    });
  }, [sprintStats.sprints]);

  const dailyBudget = costStatus?.budget_config?.max_cost_per_day;
  const hasData = chartData.length > 0;

  if (!hasData) {
    return (
      <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
        <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Cost Trend</h3>
        <p className="text-xs text-gray-500 dark:text-gray-400">No completed tasks with cost data yet.</p>
      </Card>
    );
  }

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
      <h3 className="text-sm font-medium text-gray-700 dark:text-gray-300 mb-3">Cost Trend</h3>
      <ResponsiveContainer width="100%" height={280}>
        <LineChart data={chartData} margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,0.15)" />
          <XAxis
            dataKey="date"
            tick={{ fontSize: 11, fill: "rgb(156, 163, 175)" }}
            tickLine={false}
            axisLine={{ stroke: "rgba(128,128,128,0.2)" }}
          />
          <YAxis
            tick={{ fontSize: 11, fill: "rgb(156, 163, 175)" }}
            tickLine={false}
            axisLine={{ stroke: "rgba(128,128,128,0.2)" }}
            tickFormatter={(value: number) => `$${value}`}
          />
          <Tooltip content={<CustomTooltip />} />
          <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} />
          <Line
            type="monotone"
            dataKey="cumulativeCost"
            name="Cumulative Cost"
            stroke="rgb(168, 85, 247)"
            strokeWidth={2}
            dot={{ r: 3, fill: "rgb(168, 85, 247)" }}
            activeDot={{ r: 5 }}
          />
          <Line
            type="monotone"
            dataKey="dailyCost"
            name="Daily Cost"
            stroke="rgb(34, 211, 238)"
            strokeWidth={2}
            dot={{ r: 3, fill: "rgb(34, 211, 238)" }}
            activeDot={{ r: 5 }}
          />
          <Line
            type="monotone"
            dataKey="tasksCompleted"
            name="Tasks Done"
            stroke="rgb(156, 163, 175)"
            strokeWidth={1}
            strokeDasharray="4 4"
            dot={false}
            yAxisId={0}
          />
          {dailyBudget && (
            <ReferenceLine
              y={dailyBudget}
              stroke="rgb(239, 68, 68)"
              strokeDasharray="6 3"
              label={{
                value: `Budget $${dailyBudget}/day`,
                position: "insideTopRight",
                style: { fontSize: 10, fill: "rgb(239, 68, 68)" },
              }}
            />
          )}
        </LineChart>
      </ResponsiveContainer>
      <div className="mt-2 flex items-center gap-4 text-xs text-gray-500 dark:text-gray-400">
        <span>Total: ${sprintStats.summary.estimated_cost_usd.toFixed(2)}</span>
        <span>Tasks Done: {sprintStats.summary.done}</span>
        {costStatus?.status === "warning" && (
          <span className="text-orange-500 dark:text-orange-400 font-medium">Approaching budget limit</span>
        )}
        {costStatus?.status === "exceeded" && (
          <span className="text-red-500 dark:text-red-400 font-medium">Budget exceeded</span>
        )}
      </div>
    </Card>
  );
}

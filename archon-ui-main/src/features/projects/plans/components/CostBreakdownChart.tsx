import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card } from "../../../ui/primitives";
import type { PlanRollup } from "../types";

interface CostBreakdownChartProps {
  rollup: PlanRollup;
}

function CustomTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: Array<{ value: number; dataKey: string }>;
  label?: string;
}) {
  if (!active || !payload?.length) return null;
  const cost = payload[0]?.value ?? 0;
  return (
    <div className="rounded-lg border border-white/10 p-2.5 backdrop-blur-xl bg-gray-900/90 shadow-lg text-xs">
      <p className="font-medium text-white mb-1">{label}</p>
      <p className="text-cyan-400">Cost: ${cost.toFixed(3)}</p>
    </div>
  );
}

const PHASE_COLORS = [
  "rgb(34, 211, 238)",
  "rgb(168, 85, 247)",
  "rgb(249, 115, 22)",
  "rgb(34, 197, 94)",
  "rgb(59, 130, 246)",
  "rgb(236, 72, 153)",
];

export function CostBreakdownChart({ rollup }: CostBreakdownChartProps) {
  const chartData = rollup.phases
    .filter((p) => p.cost_usd !== null)
    .map((phase, idx) => ({
      name: phase.title.length > 14 ? `${phase.title.substring(0, 14)}…` : phase.title,
      fullName: phase.title,
      cost: phase.cost_usd ?? 0,
      colorIdx: idx,
    }));

  // Add unphased items if they have cost
  const unphasedCost = rollup.unphased_items.reduce((sum, i) => sum + (i.cost_usd ?? 0), 0);
  if (unphasedCost > 0) {
    chartData.push({ name: "Unphased", fullName: "Unphased items", cost: unphasedCost, colorIdx: chartData.length });
  }

  const hasCostData = rollup.cost_usd !== null && rollup.cost_usd > 0;

  return (
    <Card blur="md" transparency="light" size="sm" className="border-white/10 dark:border-white/[0.06]">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-800 dark:text-gray-200">Cost Breakdown by Workstream</h3>
        {hasCostData && (
          <span className="text-xs text-gray-500 dark:text-gray-400">
            Total:{" "}
            <span className="text-cyan-500 dark:text-cyan-400 font-medium">${(rollup.cost_usd ?? 0).toFixed(3)}</span>
          </span>
        )}
      </div>

      {!hasCostData ? (
        <p className="text-xs text-gray-500 dark:text-gray-400">No cost data available yet.</p>
      ) : (
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 0 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="rgba(128,128,128,0.12)" />
            <XAxis
              dataKey="name"
              tick={{ fontSize: 10, fill: "rgb(156,163,175)" }}
              tickLine={false}
              axisLine={{ stroke: "rgba(128,128,128,0.2)" }}
            />
            <YAxis
              tick={{ fontSize: 10, fill: "rgb(156,163,175)" }}
              tickLine={false}
              axisLine={{ stroke: "rgba(128,128,128,0.2)" }}
              tickFormatter={(v: number) => `$${v.toFixed(2)}`}
            />
            <Tooltip content={<CustomTooltip />} />
            <Bar dataKey="cost" name="Cost (USD)" radius={[4, 4, 0, 0]}>
              {chartData.map((entry) => (
                <Cell key={entry.fullName} fill={PHASE_COLORS[entry.colorIdx % PHASE_COLORS.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </Card>
  );
}

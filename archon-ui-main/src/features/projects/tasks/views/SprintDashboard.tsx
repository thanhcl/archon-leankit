import { BudgetConfigSection } from "../components/BudgetConfigSection";
import { CostTrendChart } from "../components/CostTrendChart";
import { useCostStatus, useSprintStats } from "../hooks";

interface SprintDashboardProps {
  projectId: string;
}

export function SprintDashboard({ projectId }: SprintDashboardProps) {
  const { data: sprintStats, isLoading: isLoadingStats } = useSprintStats(projectId);
  const { data: costStatus } = useCostStatus(projectId);

  if (isLoadingStats) {
    return (
      <div className="flex items-center justify-center h-40">
        <div className="animate-spin rounded-full h-6 w-6 border-b-2 border-cyan-500" />
      </div>
    );
  }

  if (!sprintStats) {
    return null;
  }

  return (
    <div className="space-y-4">
      <CostTrendChart sprintStats={sprintStats} costStatus={costStatus} />
      <BudgetConfigSection projectId={projectId} />
    </div>
  );
}

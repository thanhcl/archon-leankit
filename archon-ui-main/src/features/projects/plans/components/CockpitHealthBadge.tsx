import { Activity, AlertTriangle, Ban, Clock, Minus } from "lucide-react";
import { cn } from "../../../ui/primitives/styles";
import { useCockpitHeader } from "../hooks/usePlanQueries";
import type { HealthBadge, ProgressSignal } from "../types";

interface CockpitHealthBadgeProps {
  projectId: string;
  className?: string;
}

const PROGRESS_CONFIG: Record<ProgressSignal, { label: string; icon: React.ReactNode; className: string }> = {
  active: {
    label: "Active",
    icon: <Activity className="w-3 h-3" />,
    className:
      "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-700/50",
  },
  stale: {
    label: "Stale",
    icon: <Clock className="w-3 h-3" />,
    className:
      "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-700/50",
  },
  blocked: {
    label: "Blocked",
    icon: <Ban className="w-3 h-3" />,
    className: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-700/50",
  },
  idle: {
    label: "Idle",
    icon: <Minus className="w-3 h-3" />,
    className: "bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 border-gray-300 dark:border-gray-700/50",
  },
};

const HEALTH_CONFIG: Record<HealthBadge, { label: string; className: string }> = {
  healthy: {
    label: "Healthy",
    className:
      "bg-green-100 dark:bg-green-900/30 text-green-700 dark:text-green-400 border-green-300 dark:border-green-700/50",
  },
  warning: {
    label: "Warning",
    className:
      "bg-amber-100 dark:bg-amber-900/30 text-amber-700 dark:text-amber-400 border-amber-300 dark:border-amber-700/50",
  },
  critical: {
    label: "Critical",
    className: "bg-red-100 dark:bg-red-900/30 text-red-700 dark:text-red-400 border-red-300 dark:border-red-700/50",
  },
};

function formatLastCompleted(lastCompletedAt: string | null): string | null {
  if (!lastCompletedAt) return null;
  try {
    const dt = new Date(lastCompletedAt);
    const now = new Date();
    const diffHours = Math.floor((now.getTime() - dt.getTime()) / (1000 * 60 * 60));
    if (diffHours < 1) return "< 1h ago";
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  } catch {
    return null;
  }
}

export function CockpitHealthBadge({ projectId, className }: CockpitHealthBadgeProps) {
  const { data: header, isLoading } = useCockpitHeader(projectId);

  if (isLoading || !header) return null;

  const progressConfig = PROGRESS_CONFIG[header.progress_signal];
  const healthConfig = HEALTH_CONFIG[header.health];
  const lastCompleted = formatLastCompleted(header.last_completed_at);

  return (
    <div className={cn("flex items-center gap-2", className)}>
      {/* Progress signal badge */}
      <span
        className={cn(
          "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border",
          progressConfig.className,
        )}
        title={`Project progress: ${header.progress_signal}${lastCompleted ? ` · last completion ${lastCompleted}` : ""}`}
      >
        {progressConfig.icon}
        {progressConfig.label}
        {lastCompleted && <span className="opacity-70">· {lastCompleted}</span>}
      </span>

      {/* Health badge — only show if not healthy to reduce visual noise */}
      {header.health !== "healthy" && (
        <span
          className={cn(
            "inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[10px] font-medium border",
            healthConfig.className,
          )}
          title={`Project health: ${header.health}`}
        >
          <AlertTriangle className="w-3 h-3" />
          {healthConfig.label}
        </span>
      )}

      {/* Completion */}
      {header.completion_percent > 0 && (
        <span className="text-[10px] text-gray-500 dark:text-gray-400 tabular-nums">
          {header.completion_percent.toFixed(0)}% done
        </span>
      )}
    </div>
  );
}

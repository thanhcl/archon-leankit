import { Clock, Tag } from "lucide-react";
import { Card } from "../../../ui/primitives";
import { cn } from "../../../ui/primitives/styles";
import type { Task, TaskEstimate } from "../types";
import { getOrderColor, getOrderGlow } from "../utils/task-styles";

function formatEstimatedTime(seconds: number): string {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const mins = Math.round((seconds % 3600) / 60);
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

interface TaskCardOverlayProps {
  task: Task;
  estimate?: TaskEstimate;
}

export const TaskCardOverlay = ({ task, estimate }: TaskCardOverlayProps) => {
  return (
    <div className="w-full min-h-[140px] cursor-grabbing rotate-2 scale-105 shadow-2xl">
      <Card
        blur="md"
        transparency="light"
        size="none"
        className={cn(
          "w-full min-h-[140px] h-full",
          "border-cyan-400/50 shadow-[0_0_20px_rgba(34,211,238,0.4)]",
          "ring-2 ring-cyan-400/30",
        )}
      >
        <div
          className={cn(
            "absolute left-0 top-0 bottom-0 w-[4px] rounded-l-lg opacity-100",
            getOrderColor(task.task_order),
            getOrderGlow(task.task_order),
          )}
        />

        <div className="flex flex-col h-full p-3">
          <div className="flex items-center gap-2 mb-2 pl-1.5">
            {task.feature && (
              <div
                className="px-2 py-1 rounded-md text-xs font-medium flex items-center gap-1 backdrop-blur-md"
                style={{
                  backgroundColor: `${task.featureColor}20`,
                  color: task.featureColor,
                }}
              >
                <Tag className="w-3 h-3" />
                {task.feature}
              </div>
            )}
          </div>

          <h4 className="text-xs font-medium text-gray-900 dark:text-white mb-2 pl-1.5 line-clamp-2">{task.title}</h4>

          {task.description && (
            <div className="pl-1.5 pr-3 mb-2 flex-1">
              <p
                className="text-xs text-gray-600 dark:text-gray-400 line-clamp-3 break-words whitespace-pre-wrap opacity-75"
                style={{ fontSize: "11px" }}
              >
                {task.description}
              </p>
            </div>
          )}

          {!task.description && <div className="flex-1"></div>}

          <div className="flex items-center justify-between mt-auto pt-2 pl-1.5 pr-3">
            <span className="text-xs text-gray-500">{task.assignee}</span>
            {estimate && estimate.estimate_duration_seconds > 0 && (
              <div className="flex items-center gap-1 text-xs text-gray-500">
                <Clock className="w-3 h-3" />
                <span>{formatEstimatedTime(estimate.estimate_duration_seconds)}</span>
              </div>
            )}
          </div>
        </div>
      </Card>
    </div>
  );
};

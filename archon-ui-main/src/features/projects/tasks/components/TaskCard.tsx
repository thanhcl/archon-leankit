import { useSortable } from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { Clock, Tag } from "lucide-react";
import type React from "react";
import { useCallback } from "react";
import { isOptimistic } from "@/features/shared/utils/optimistic";
import { Card } from "../../../ui/primitives";
import { OptimisticIndicator } from "../../../ui/primitives/OptimisticIndicator";
import { cn } from "../../../ui/primitives/styles";
import { useContinueTask, useRePlanTask, useTaskActions, useTransitionTask } from "../hooks";
import type { Assignee, Task, TaskEstimate, TaskPriority } from "../types";
import { getOrderColor, getOrderGlow } from "../utils/task-styles";
import { TaskPriorityComponent } from ".";
import { TaskAssignee } from "./TaskAssignee";
import { TaskCardActions } from "./TaskCardActions";
import { TaskFeedbackPanel } from "./TaskFeedbackPanel";

function formatEstimatedTime(seconds: number): string {
  if (seconds <= 0) return "";
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  const hours = Math.floor(seconds / 3600);
  const mins = Math.round((seconds % 3600) / 60);
  return mins > 0 ? `${hours}h ${mins}m` : `${hours}h`;
}

export interface TaskCardProps {
  task: Task;
  projectId: string;
  onEdit?: (task: Task) => void;
  onDelete?: (task: Task) => void;
  hoveredTaskId?: string | null;
  onTaskHover?: (taskId: string | null) => void;
  selectedTasks?: Set<string>;
  onTaskSelect?: (taskId: string) => void;
  estimate?: TaskEstimate;
}

export const TaskCard: React.FC<TaskCardProps> = ({
  task,
  projectId,
  onEdit,
  onDelete,
  hoveredTaskId,
  onTaskHover,
  selectedTasks,
  onTaskSelect,
  estimate,
}) => {
  const optimistic = isOptimistic(task);

  const { changeAssignee, changePriority, isUpdating } = useTaskActions(projectId);
  const transitionMutation = useTransitionTask(projectId);
  const rePlanMutation = useRePlanTask(projectId);
  const continueMutation = useContinueTask(projectId);

  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: task.id,
    data: { status: task.status },
  });

  const style = {
    transform: CSS.Transform.toString(transform),
    transition,
  };

  const handleEdit = useCallback(() => {
    if (onEdit) onEdit(task);
  }, [onEdit, task]);

  const handleDelete = useCallback(() => {
    if (onDelete) onDelete(task);
  }, [onDelete, task]);

  const handlePriorityChange = useCallback(
    (priority: TaskPriority) => {
      changePriority(task.id, priority);
    },
    [changePriority, task.id],
  );

  const handleAssigneeChange = useCallback(
    (newAssignee: Assignee) => {
      changeAssignee(task.id, newAssignee);
    },
    [changeAssignee, task.id],
  );

  const handleApprove = useCallback(() => {
    transitionMutation.mutate({ taskId: task.id, newStatus: "done" });
  }, [transitionMutation, task.id]);

  const handleReject = useCallback(() => {
    const reason = window.prompt("Rejection reason (required):");
    if (reason?.trim()) {
      transitionMutation.mutate({ taskId: task.id, newStatus: "assigned", reason: reason.trim() });
    }
  }, [transitionMutation, task.id]);

  const handleRePlan = useCallback(() => {
    const newDesc = window.prompt("Updated description (leave blank to keep current):");
    // User pressed Cancel — abort
    if (newDesc === null) return;
    rePlanMutation.mutate({
      taskId: task.id,
      updatedDescription: newDesc.trim() || undefined,
    });
  }, [rePlanMutation, task.id]);

  const handleContinue = useCallback(() => {
    const guidance = window.prompt("Provide guidance to continue the task (required):");
    if (guidance?.trim()) {
      continueMutation.mutate({ taskId: task.id, guidance: guidance.trim() });
    }
  }, [continueMutation, task.id]);

  const isHighlighted = hoveredTaskId === task.id;
  const isSelected = selectedTasks?.has(task.id) || false;

  const handleMouseEnter = () => {
    onTaskHover?.(task.id);
  };

  const handleMouseLeave = () => {
    onTaskHover?.(null);
  };

  const handleTaskClick = (e: React.MouseEvent) => {
    if (e.ctrlKey || e.metaKey) {
      e.stopPropagation();
      onTaskSelect?.(task.id);
    }
  };

  return (
    <div
      ref={setNodeRef}
      style={style}
      {...attributes}
      {...listeners}
      role="group"
      className={cn(
        "w-full min-h-[140px] cursor-grab active:cursor-grabbing relative group touch-none",
        "transition-all duration-200 ease-in-out",
        isDragging ? "opacity-40 scale-95" : "scale-100 opacity-100",
      )}
      onMouseEnter={handleMouseEnter}
      onMouseLeave={handleMouseLeave}
      onClick={handleTaskClick}
    >
      <Card
        blur="md"
        transparency="light"
        size="none"
        className={cn(
          "transition-all duration-200 ease-in-out",
          "w-full min-h-[140px] h-full",
          isHighlighted && "border-cyan-400/50 shadow-[0_0_8px_rgba(34,211,238,0.2)]",
          isSelected && "border-blue-500 shadow-[0_0_12px_rgba(59,130,246,0.4)]",
          "group-hover:border-cyan-400/70 dark:group-hover:border-cyan-500/50 group-hover:shadow-[0_0_15px_rgba(34,211,238,0.4)] dark:group-hover:shadow-[0_0_15px_rgba(34,211,238,0.6)]",
          optimistic && "opacity-80 ring-1 ring-cyan-400/30",
        )}
      >
        {/* Priority indicator with glow */}
        <div
          className={cn(
            "absolute left-0 top-0 bottom-0 w-[3px] rounded-l-lg opacity-80 group-hover:w-[4px] group-hover:opacity-100 transition-all duration-300",
            getOrderColor(task.task_order ?? 0),
            getOrderGlow(task.task_order ?? 0),
          )}
        />

        {/* Content container */}
        <div className="flex flex-col h-full p-3">
          {/* Header with feature and actions */}
          <div className="flex items-center gap-2 mb-2 pl-1.5">
            {task.feature && (
              <div
                className="px-2 py-1 rounded-md text-xs font-medium flex items-center gap-1 backdrop-blur-md"
                style={{
                  backgroundColor: `${task.featureColor}20`,
                  color: task.featureColor,
                  boxShadow: `0 0 10px ${task.featureColor}20`,
                }}
              >
                <Tag className="w-3 h-3" />
                {task.feature}
              </div>
            )}

            <OptimisticIndicator isOptimistic={optimistic} className="ml-auto" />

            <div className={cn("flex items-center gap-1.5", !optimistic && "ml-auto")}>
              <TaskCardActions
                taskId={task.id}
                taskTitle={task.title}
                taskStatus={task.status}
                onEdit={handleEdit}
                onDelete={handleDelete}
                onApprove={handleApprove}
                onReject={handleReject}
                onRePlan={handleRePlan}
                onContinue={handleContinue}
                isDeleting={false}
                isTransitioning={transitionMutation.isPending || rePlanMutation.isPending || continueMutation.isPending}
              />
            </div>
          </div>

          {/* Title */}
          <h4
            className="text-xs font-medium text-gray-900 dark:text-white mb-2 pl-1.5 line-clamp-2 overflow-hidden"
            title={task.title}
          >
            {task.title}
          </h4>

          {/* Description */}
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

          {/* Tags */}
          {task.tags && task.tags.length > 0 && (
            <div className="flex flex-wrap gap-1 pl-1.5 pr-3 mb-2">
              {task.tags.slice(0, 3).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/20"
                >
                  {tag}
                </span>
              ))}
              {task.tags.length > 3 && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-500/10 text-gray-500 dark:text-gray-400 border border-gray-500/20">
                  +{task.tags.length - 3}
                </span>
              )}
            </div>
          )}

          {/* Footer */}
          <div className="flex items-center justify-between mt-auto pt-2 pl-1.5 pr-3">
            <TaskAssignee assignee={task.assignee} onAssigneeChange={handleAssigneeChange} isLoading={isUpdating} />

            {estimate && estimate.estimate_duration_seconds > 0 && (
              <div
                className="flex items-center gap-1 text-xs text-gray-500 dark:text-gray-400"
                title={`Est. ${formatEstimatedTime(estimate.estimate_duration_seconds)} (${estimate.confidence} confidence, ${estimate.sample_size} samples)`}
              >
                <Clock className="w-3 h-3" />
                <span>{formatEstimatedTime(estimate.estimate_duration_seconds)}</span>
              </div>
            )}

            <TaskPriorityComponent
              priority={task.priority}
              onPriorityChange={handlePriorityChange}
              isLoading={isUpdating}
            />
          </div>

          {/* Owner feedback — only rendered for done tasks */}
          {task.status === "done" && <TaskFeedbackPanel task={task} projectId={projectId} />}
        </div>
      </Card>
    </div>
  );
};

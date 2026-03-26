import { useDroppable } from "@dnd-kit/core";
import { SortableContext, verticalListSortingStrategy } from "@dnd-kit/sortable";
import { Activity, CheckCircle2, Eye, ListTodo, Plus } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { cn } from "../../../ui/primitives/styles";
import { useCreateTask } from "../hooks";
import type { DatabaseTaskStatus, Task, TaskBoardStatus, TaskEstimate, TaskPriority } from "../types";
import { getColumnGlow } from "../utils/task-styles";
import { InlineTaskForm } from "./InlineTaskForm";
import { TaskCard } from "./TaskCard";

interface KanbanColumnProps {
  status: TaskBoardStatus;
  title?: string;
  tasks: Task[];
  projectId: string;
  onTaskEdit?: (task: Task) => void;
  onTaskDelete?: (task: Task) => void;
  hoveredTaskId: string | null;
  onTaskHover: (taskId: string | null) => void;
  estimates?: Record<string, TaskEstimate>;
}

export const KanbanColumn = ({
  status,
  tasks,
  projectId,
  onTaskEdit,
  onTaskDelete,
  hoveredTaskId,
  onTaskHover,
  estimates,
}: KanbanColumnProps) => {
  const [showInlineForm, setShowInlineForm] = useState(false);
  const createTask = useCreateTask();

  const { setNodeRef, isOver } = useDroppable({
    id: `column-${status}`,
    data: { columnId: status },
  });

  const taskIds = useMemo(() => tasks.map((t) => t.id), [tasks]);

  const handleInlineSubmit = useCallback(
    (data: { title: string; priority: TaskPriority; status: DatabaseTaskStatus }) => {
      createTask.mutate(
        {
          project_id: projectId,
          title: data.title,
          priority: data.priority,
          status: data.status,
          description: "",
          created_from: "board-ui",
        },
        {
          onSuccess: () => {
            // Keep form open for rapid entry
          },
        },
      );
    },
    [createTask, projectId],
  );

  const getStatusInfo = () => {
    switch (status) {
      case "todo":
        return {
          icon: <ListTodo className="w-3 h-3" />,
          label: "Todo",
          color: "bg-pink-500/10 text-pink-600 dark:text-pink-400 border-pink-500/30",
        };
      case "doing":
        return {
          icon: <Activity className="w-3 h-3" />,
          label: "Doing",
          color: "bg-blue-500/10 text-blue-600 dark:text-blue-400 border-blue-500/30",
        };
      case "review":
        return {
          icon: <Eye className="w-3 h-3" />,
          label: "Review",
          color: "bg-purple-500/10 text-purple-600 dark:text-purple-400 border-purple-500/30",
        };
      case "done":
        return {
          icon: <CheckCircle2 className="w-3 h-3" />,
          label: "Done",
          color: "bg-green-500/10 text-green-600 dark:text-green-400 border-green-500/30",
        };
      default:
        return {
          icon: <ListTodo className="w-3 h-3" />,
          label: "Todo",
          color: "bg-gray-500/10 text-gray-600 dark:text-gray-400 border-gray-500/30",
        };
    }
  };

  const statusInfo = getStatusInfo();

  return (
    <div
      ref={setNodeRef}
      className={cn("flex flex-col h-full transition-colors duration-200", isOver && "bg-cyan-500/5 rounded-lg")}
    >
      {/* Column Header */}
      <div className="text-center py-3 relative">
        <div className="flex items-center justify-center gap-2">
          <div
            className={cn(
              "inline-flex items-center gap-2 px-3 py-1.5 rounded-full text-sm font-medium border backdrop-blur-md",
              statusInfo.color,
            )}
          >
            {statusInfo.icon}
            <span className="font-medium">{statusInfo.label}</span>
            <span className="font-bold">{tasks.length}</span>
          </div>
          <button
            type="button"
            onClick={() => setShowInlineForm((prev) => !prev)}
            className={cn(
              "p-1 rounded-full border border-gray-300/30 dark:border-gray-600/30",
              "text-gray-500 dark:text-gray-400 hover:text-cyan-400 hover:border-cyan-400/50",
              "hover:bg-cyan-500/10 transition-colors duration-150",
              showInlineForm && "text-cyan-400 border-cyan-400/50 bg-cyan-500/10",
            )}
            title={`Add task to ${statusInfo.label}`}
          >
            <Plus className="w-3.5 h-3.5" />
          </button>
        </div>
        <div
          className={cn(
            "absolute bottom-0 left-[15%] right-[15%] w-[70%] mx-auto h-[1px]",
            getColumnGlow(status),
            "shadow-md",
          )}
        />
      </div>

      {/* Inline Quick-Add Form */}
      {showInlineForm && (
        <InlineTaskForm
          projectId={projectId}
          status={status}
          onSubmit={handleInlineSubmit}
          onCancel={() => setShowInlineForm(false)}
          isSubmitting={createTask.isPending}
        />
      )}

      {/* Tasks Container with sortable context */}
      <SortableContext items={taskIds} strategy={verticalListSortingStrategy}>
        <div className="px-2 flex-1 overflow-y-auto space-y-2 py-3 scrollbar-thin scrollbar-thumb-gray-300 dark:scrollbar-thumb-gray-700">
          {tasks.map((task) => (
            <TaskCard
              key={task.id}
              task={task}
              projectId={projectId}
              onEdit={onTaskEdit}
              onDelete={onTaskDelete}
              hoveredTaskId={hoveredTaskId}
              onTaskHover={onTaskHover}
              estimate={estimates?.[task.id]}
            />
          ))}
        </div>
      </SortableContext>
    </div>
  );
};

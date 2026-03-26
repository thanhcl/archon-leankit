import { Check, Edit, Tag, Trash2 } from "lucide-react";
import React, { useState } from "react";
import { useDrag, useDrop } from "react-dnd";
import { Button, Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "../../../ui/primitives";
import { cn, glassmorphism } from "../../../ui/primitives/styles";
import { EditableTableCell } from "../components/EditableTableCell";
import { TaskAssignee } from "../components/TaskAssignee";
import { useDeleteTask, useUpdateTask } from "../hooks";
import type { Assignee, Task, TaskBoardStatus, UpdateTaskRequest } from "../types";
import { getOrderColor, getOrderGlow, ItemTypes } from "../utils/task-styles";

const rowVariants = {
  even: "bg-white/50 dark:bg-black/50",
  odd: "bg-gray-50/80 dark:bg-gray-900/30",
  hover:
    "hover:bg-gradient-to-r hover:from-cyan-50/70 hover:to-purple-50/70 dark:hover:from-cyan-900/20 dark:hover:to-purple-900/20",
} satisfies Record<string, string>;

interface TableViewProps {
  tasks: Task[];
  projectId: string;
  onTaskView?: (task: Task) => void;
  onTaskComplete?: (taskId: string) => void;
  onTaskDelete?: (task: Task) => void;
  onTaskReorder: (taskId: string, newOrder: number, status: TaskBoardStatus) => void;
  onTaskUpdate?: (taskId: string, updates: UpdateTaskRequest) => Promise<void>;
}

interface DraggableRowProps {
  task: Task;
  index: number;
  projectId: string;
  onTaskView?: (task: Task) => void;
  onTaskComplete?: (taskId: string) => void;
  onTaskDelete?: (task: Task) => void;
  onTaskReorder: (taskId: string, newOrder: number, status: TaskBoardStatus) => void;
}

const DraggableRow = ({
  task,
  index,
  projectId,
  onTaskView,
  onTaskComplete,
  onTaskDelete,
  onTaskReorder,
}: DraggableRowProps) => {
  const updateTaskMutation = useUpdateTask(projectId);
  const deleteTaskMutation = useDeleteTask(projectId);
  const [localAssignee, setLocalAssignee] = useState<Assignee>(task.assignee);

  // Drag and drop handlers
  const [{ isDragging }, drag] = useDrag({
    type: ItemTypes.TASK,
    item: { id: task.id, index, status: task.status },
    collect: (monitor) => ({
      isDragging: !!monitor.isDragging(),
    }),
  });

  const [{ isOver }, drop] = useDrop({
    accept: ItemTypes.TASK,
    hover: (draggedItem: { id: string; index: number; status: Task["status"] }, monitor) => {
      if (!monitor.isOver({ shallow: true })) return;
      if (draggedItem.id === task.id) return;
      if (draggedItem.status !== task.status) return;

      const draggedIndex = draggedItem.index;
      const hoveredIndex = index;

      if (draggedIndex === hoveredIndex) return;

      // Move the task for visual feedback
      onTaskReorder(draggedItem.id, hoveredIndex, task.status as TaskBoardStatus);

      // Update the dragged item's index
      draggedItem.index = hoveredIndex;
    },
    collect: (monitor) => ({
      isOver: !!monitor.isOver(),
    }),
  });

  // Handle field updates using mutations
  const handleUpdateField = async (field: keyof Task, value: string) => {
    const updates = { [field]: value } as UpdateTaskRequest;

    await updateTaskMutation.mutateAsync({
      taskId: task.id,
      updates,
    });
  };

  const handleAssigneeChange = (newAssignee: Assignee) => {
    setLocalAssignee(newAssignee);
    updateTaskMutation.mutate({
      taskId: task.id,
      updates: { assignee: newAssignee },
    });
  };

  const handleDelete = () => {
    if (onTaskDelete) {
      onTaskDelete(task);
    }
  };

  const handleComplete = () => {
    if (onTaskComplete) {
      onTaskComplete(task.id);
    }
  };

  const handleEdit = () => {
    if (onTaskView) {
      onTaskView(task);
    }
  };

  return (
    <tr
      ref={(node) => drag(drop(node))}
      className={cn(
        "group transition-all duration-200 cursor-move border-b border-gray-200 dark:border-gray-800",
        index % 2 === 0 ? rowVariants.even : rowVariants.odd,
        rowVariants.hover,
        isDragging && "opacity-50 scale-105 shadow-lg",
        isOver && "bg-cyan-100/50 dark:bg-cyan-900/20 border-cyan-400",
      )}
    >
      {/* Priority/Order Indicator */}
      <td className="w-1 p-0">
        <div className={cn("w-1 h-full", getOrderColor(task.task_order ?? 0), getOrderGlow(task.task_order ?? 0))} />
      </td>

      {/* Title */}
      <td className="px-4 py-2">
        <EditableTableCell
          value={task.title}
          onSave={(value) => handleUpdateField("title", value)}
          placeholder="Enter task title"
          className="font-medium"
          isUpdating={updateTaskMutation.isPending}
        />
      </td>

      {/* Status */}
      <td className="px-4 py-2 w-32">
        <EditableTableCell
          value={task.status}
          onSave={(value) => handleUpdateField("status", value)}
          type="status"
          isUpdating={updateTaskMutation.isPending}
        />
      </td>

      {/* Feature */}
      <td className="px-4 py-2 w-40">
        <div className="flex items-center gap-1">
          {task.feature && <Tag className="w-3 h-3 text-gray-500 dark:text-gray-400" />}
          <EditableTableCell
            value={task.feature || ""}
            onSave={(value) => handleUpdateField("feature", value)}
            placeholder="Add feature"
            className="text-sm"
            isUpdating={updateTaskMutation.isPending}
          />
        </div>
      </td>

      {/* Tags */}
      <td className="px-4 py-2 w-48">
        <div className="flex flex-wrap gap-1">
          {task.tags && task.tags.length > 0 ? (
            <>
              {task.tags.slice(0, 2).map((tag) => (
                <span
                  key={tag}
                  className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-cyan-500/10 text-cyan-600 dark:text-cyan-400 border border-cyan-500/20"
                >
                  {tag}
                </span>
              ))}
              {task.tags.length > 2 && (
                <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-gray-500/10 text-gray-500 dark:text-gray-400 border border-gray-500/20">
                  +{task.tags.length - 2}
                </span>
              )}
            </>
          ) : (
            <span className="text-xs text-gray-400">—</span>
          )}
        </div>
      </td>

      {/* Assignee */}
      <td className="px-4 py-2 w-36">
        <TaskAssignee
          assignee={localAssignee}
          onAssigneeChange={handleAssigneeChange}
          isLoading={updateTaskMutation.isPending}
        />
      </td>

      {/* Actions */}
      <td className="px-4 py-2 w-28">
        <div className="flex items-center gap-1 opacity-0 group-hover:opacity-100 transition-opacity">
          <TooltipProvider>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button variant="ghost" size="xs" onClick={handleEdit} className="h-7 w-7 p-0" aria-label="Edit task">
                  <Edit className="w-3 h-3" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Edit task</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={handleComplete}
                  className="h-7 w-7 p-0 text-green-600 hover:text-green-700"
                  aria-label="Mark task as complete"
                >
                  <Check className="w-3 h-3" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Mark as complete</TooltipContent>
            </Tooltip>

            <Tooltip>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={handleDelete}
                  className="h-7 w-7 p-0 text-red-600 hover:text-red-700"
                  disabled={deleteTaskMutation.isPending}
                  aria-label="Delete task"
                >
                  <Trash2 className="w-3 h-3" aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Delete task</TooltipContent>
            </Tooltip>
          </TooltipProvider>
        </div>
      </td>
    </tr>
  );
};

export const TableView = ({
  tasks,
  projectId,
  onTaskView,
  onTaskComplete,
  onTaskDelete,
  onTaskReorder,
}: TableViewProps) => {
  // Group tasks by status for better organization
  const groupedTasks = React.useMemo(() => {
    const groups: Record<TaskBoardStatus, Task[]> = {
      todo: [],
      doing: [],
      review: [],
      done: [],
    };

    tasks.forEach((task) => {
      if (task.status in groups) {
        groups[task.status as TaskBoardStatus].push(task);
      }
    });

    // Sort each group by task_order
    Object.keys(groups).forEach((status) => {
      groups[status as TaskBoardStatus].sort((a, b) => (a.task_order ?? 0) - (b.task_order ?? 0));
    });

    return groups;
  }, [tasks]);

  const statusOrder: TaskBoardStatus[] = ["todo", "doing", "review", "done"];

  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead>
          <tr className={cn(glassmorphism.background.card, "border-b-2 border-gray-200 dark:border-gray-700")}>
            <th className="w-1"></th>
            <th className="px-4 py-3 text-left text-sm font-medium text-gray-700 dark:text-gray-300">Title</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-gray-700 dark:text-gray-300 w-32">Status</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-gray-700 dark:text-gray-300 w-40">Feature</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-gray-700 dark:text-gray-300 w-48">Tags</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-gray-700 dark:text-gray-300 w-36">Assignee</th>
            <th className="px-4 py-3 text-left text-sm font-medium text-gray-700 dark:text-gray-300 w-28">Actions</th>
          </tr>
        </thead>
        <tbody>
          {statusOrder.map((status) => {
            const statusTasks = groupedTasks[status];
            if (statusTasks.length === 0) return null;

            return (
              <React.Fragment key={status}>
                {/* Status group header */}
                <tr className="bg-gray-100/50 dark:bg-gray-800/50">
                  <td colSpan={7} className="px-4 py-2">
                    <div className="flex items-center gap-2">
                      <span
                        className={cn(
                          "text-xs font-semibold uppercase tracking-wider",
                          status === "todo" && "text-gray-600",
                          status === "doing" && "text-blue-600",
                          status === "review" && "text-purple-600",
                          status === "done" && "text-green-600",
                        )}
                      >
                        {status} ({statusTasks.length})
                      </span>
                    </div>
                  </td>
                </tr>
                {/* Tasks in this status */}
                {statusTasks.map((task, index) => (
                  <DraggableRow
                    key={task.id}
                    task={task}
                    index={index}
                    projectId={projectId}
                    onTaskView={onTaskView}
                    onTaskComplete={onTaskComplete}
                    onTaskDelete={onTaskDelete}
                    onTaskReorder={onTaskReorder}
                  />
                ))}
              </React.Fragment>
            );
          })}
          {tasks.length === 0 && (
            <tr>
              <td colSpan={7} className="text-center py-8 text-gray-400">
                No tasks yet. Create your first task to get started.
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
};

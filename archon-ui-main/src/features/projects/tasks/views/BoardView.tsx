import {
  closestCorners,
  DndContext,
  type DragEndEvent,
  type DragOverEvent,
  DragOverlay,
  type DragStartEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import { sortableKeyboardCoordinates } from "@dnd-kit/sortable";
import { useCallback, useState } from "react";
import { KanbanColumn } from "../components/KanbanColumn";
import { TaskCardOverlay } from "../components/TaskCardOverlay";
import type { Task, TaskBoardStatus, TaskEstimate } from "../types";

interface BoardViewProps {
  tasks: Task[];
  projectId: string;
  onTaskMove: (taskId: string, newStatus: TaskBoardStatus) => void;
  onTaskReorder: (taskId: string, targetIndex: number, status: TaskBoardStatus) => void;
  onTaskEdit?: (task: Task) => void;
  onTaskDelete?: (task: Task) => void;
  estimates?: Record<string, TaskEstimate>;
}

export const BoardView = ({
  tasks,
  projectId,
  onTaskMove,
  onTaskReorder,
  onTaskEdit,
  onTaskDelete,
  estimates,
}: BoardViewProps) => {
  const [hoveredTaskId, setHoveredTaskId] = useState<string | null>(null);
  const [activeTask, setActiveTask] = useState<Task | null>(null);

  const sensors = useSensors(
    useSensor(PointerSensor, { activationConstraint: { distance: 5 } }),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const getTasksByStatus = (status: TaskBoardStatus) => {
    return tasks.filter((task) => task.status === status).sort((a, b) => (a.task_order ?? 0) - (b.task_order ?? 0));
  };

  const columns: Array<{ status: TaskBoardStatus; title: string }> = [
    { status: "todo", title: "Todo" },
    { status: "doing", title: "Doing" },
    { status: "review", title: "Review" },
    { status: "done", title: "Done" },
  ];

  const handleDragStart = useCallback(
    (event: DragStartEvent) => {
      const task = tasks.find((t) => t.id === event.active.id);
      if (task) setActiveTask(task);
    },
    [tasks],
  );

  const handleDragOver = useCallback(
    (event: DragOverEvent) => {
      const { active, over } = event;
      if (!over) return;

      const activeTaskData = tasks.find((t) => t.id === active.id);
      if (!activeTaskData) return;

      // Determine the target container (column status)
      const overData = over.data.current;
      const overStatus = overData?.status as TaskBoardStatus | undefined;
      const overColumnId = overData?.columnId as TaskBoardStatus | undefined;

      // Target is a column droppable
      const targetStatus = overColumnId || overStatus;
      if (!targetStatus) return;

      // Cross-column move: update status if different
      if (activeTaskData.status !== targetStatus) {
        onTaskMove(activeTaskData.id as string, targetStatus);
      }
    },
    [tasks, onTaskMove],
  );

  const handleDragEnd = useCallback(
    (event: DragEndEvent) => {
      setActiveTask(null);
      const { active, over } = event;
      if (!over || active.id === over.id) return;

      const activeTaskData = tasks.find((t) => t.id === active.id);
      if (!activeTaskData) return;

      // Check if dropped on a task (reorder within column)
      const overData = over.data.current;
      if (overData?.sortable) {
        const overTask = tasks.find((t) => t.id === over.id);
        if (overTask && overTask.status === activeTaskData.status) {
          const statusTasks = getTasksByStatus(activeTaskData.status as TaskBoardStatus);
          const overIndex = statusTasks.findIndex((t) => t.id === over.id);
          if (overIndex !== -1) {
            onTaskReorder(activeTaskData.id as string, overIndex, activeTaskData.status as TaskBoardStatus);
          }
        }
      }
    },
    [tasks, onTaskReorder, getTasksByStatus],
  );

  return (
    <DndContext
      sensors={sensors}
      collisionDetection={closestCorners}
      onDragStart={handleDragStart}
      onDragOver={handleDragOver}
      onDragEnd={handleDragEnd}
    >
      <div className="flex flex-col h-full min-h-[70vh] relative">
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-2 flex-1 p-2 min-h-[500px]">
          {columns.map(({ status, title }) => (
            <KanbanColumn
              key={status}
              status={status}
              title={title}
              tasks={getTasksByStatus(status)}
              projectId={projectId}
              onTaskEdit={onTaskEdit}
              onTaskDelete={onTaskDelete}
              hoveredTaskId={hoveredTaskId}
              onTaskHover={setHoveredTaskId}
              estimates={estimates}
            />
          ))}
        </div>
      </div>

      {/* Drag ghost overlay */}
      <DragOverlay dropAnimation={{ duration: 200, easing: "ease" }}>
        {activeTask ? <TaskCardOverlay task={activeTask} estimate={estimates?.[activeTask.id]} /> : null}
      </DragOverlay>
    </DndContext>
  );
};

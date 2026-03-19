import { render, screen } from "@testing-library/react";
import type React from "react";
import { describe, expect, it, vi } from "vitest";
import type { Task, TaskEstimate } from "../../types";

// Mock @dnd-kit to avoid jsdom incompatibilities
vi.mock("@dnd-kit/core", () => ({
  DndContext: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  DragOverlay: ({ children }: { children: React.ReactNode }) => <div data-testid="drag-overlay">{children}</div>,
  closestCorners: vi.fn(),
  KeyboardSensor: vi.fn(),
  PointerSensor: vi.fn(),
  useSensor: vi.fn(),
  useSensors: () => [],
}));

vi.mock("@dnd-kit/sortable", () => ({
  sortableKeyboardCoordinates: vi.fn(),
}));

vi.mock("lucide-react", () => {
  const icon = ({ children }: { children?: React.ReactNode }) => <span>{children}</span>;
  return new Proxy(
    {},
    {
      get: (_target, prop) => {
        if (prop === "__esModule") return true;
        return icon;
      },
    },
  );
});

// Mock KanbanColumn to avoid its full dependency chain
vi.mock("../KanbanColumn", () => ({
  KanbanColumn: ({
    status,
    tasks,
  }: {
    status: string;
    title: string;
    tasks: Array<{ id: string; title: string; description?: string; feature?: string; featureColor?: string }>;
    projectId: string;
    onTaskEdit?: (task: Task) => void;
    onTaskDelete?: (task: Task) => void;
    hoveredTaskId: string | null;
    onTaskHover: (id: string | null) => void;
    estimates?: Record<string, TaskEstimate>;
  }) => {
    const labels: Record<string, string> = { todo: "Todo", doing: "Doing", review: "Review", done: "Done" };
    return (
      <div data-testid={`column-${status}`}>
        <span>{labels[status] ?? status}</span>
        <span>{tasks.length}</span>
        {tasks.map((t) => (
          <div key={t.id} role="group">
            <h4>{t.title}</h4>
            {t.description && <p>{t.description}</p>}
            {t.feature && <span>{t.feature}</span>}
          </div>
        ))}
      </div>
    );
  },
}));

vi.mock("../TaskCardOverlay", () => ({
  TaskCardOverlay: () => <div data-testid="task-overlay">Overlay</div>,
}));

import { BoardView } from "../../views/BoardView";

const createMockTask = (overrides: Partial<Task> = {}): Task =>
  ({
    id: `task-${Math.random().toString(36).slice(2, 8)}`,
    project_id: "proj-1",
    title: "Test Task",
    description: "",
    status: "todo" as Task["status"],
    assignee: "User",
    task_order: 1000,
    priority: "medium" as const,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    ...overrides,
  }) as Task;

describe("BoardView with @dnd-kit", () => {
  const defaultProps = {
    projectId: "proj-1",
    onTaskMove: vi.fn(),
    onTaskReorder: vi.fn(),
    onTaskEdit: vi.fn(),
    onTaskDelete: vi.fn(),
  };

  it("renders all four columns", () => {
    render(<BoardView {...defaultProps} tasks={[]} />);

    expect(screen.getByText("Todo")).toBeInTheDocument();
    expect(screen.getByText("Doing")).toBeInTheDocument();
    expect(screen.getByText("Review")).toBeInTheDocument();
    expect(screen.getByText("Done")).toBeInTheDocument();
  });

  it("renders tasks in correct columns", () => {
    const tasks = [
      createMockTask({ id: "t1", title: "Todo Task", status: "todo" as Task["status"] }),
      createMockTask({ id: "t2", title: "Doing Task", status: "doing" as Task["status"] }),
      createMockTask({ id: "t3", title: "Review Task", status: "review" as Task["status"] }),
      createMockTask({ id: "t4", title: "Done Task", status: "done" as Task["status"] }),
    ];

    render(<BoardView {...defaultProps} tasks={tasks} />);

    expect(screen.getByText("Todo Task")).toBeInTheDocument();
    expect(screen.getByText("Doing Task")).toBeInTheDocument();
    expect(screen.getByText("Review Task")).toBeInTheDocument();
    expect(screen.getByText("Done Task")).toBeInTheDocument();
  });

  it("shows task count per column", () => {
    const tasks = [
      createMockTask({ id: "t1", status: "todo" as Task["status"] }),
      createMockTask({ id: "t2", status: "todo" as Task["status"] }),
      createMockTask({ id: "t3", status: "doing" as Task["status"] }),
    ];

    render(<BoardView {...defaultProps} tasks={tasks} />);

    // Column badges show count — "2" for todo column
    const badges = screen.getAllByText("2");
    expect(badges.length).toBeGreaterThanOrEqual(1);
  });

  it("sorts tasks within column by task_order", () => {
    const tasks = [
      createMockTask({ id: "t1", title: "Third", status: "todo" as Task["status"], task_order: 3000 }),
      createMockTask({ id: "t2", title: "First", status: "todo" as Task["status"], task_order: 1000 }),
      createMockTask({ id: "t3", title: "Second", status: "todo" as Task["status"], task_order: 2000 }),
    ];

    render(<BoardView {...defaultProps} tasks={tasks} />);

    const titles = screen.getAllByRole("heading", { level: 4 });
    const todoTitles = titles.map((el) => el.textContent);
    expect(todoTitles).toEqual(["First", "Second", "Third"]);
  });

  it("renders task cards with drag role", () => {
    const tasks = [createMockTask({ id: "t1", title: "Draggable Task", status: "todo" as Task["status"] })];

    render(<BoardView {...defaultProps} tasks={tasks} />);

    const draggableElements = screen.getAllByRole("group");
    expect(draggableElements.length).toBeGreaterThanOrEqual(1);
  });

  it("renders with empty task list without errors", () => {
    const { container } = render(<BoardView {...defaultProps} tasks={[]} />);
    expect(container.querySelector(".grid")).toBeInTheDocument();
  });

  it("displays task feature badges", () => {
    const tasks = [
      createMockTask({
        id: "t1",
        title: "Feature Task",
        status: "todo" as Task["status"],
        feature: "Auth",
        featureColor: "#ff0000",
      }),
    ];

    render(<BoardView {...defaultProps} tasks={tasks} />);
    expect(screen.getByText("Auth")).toBeInTheDocument();
  });

  it("shows task descriptions when present", () => {
    const tasks = [
      createMockTask({
        id: "t1",
        title: "Described Task",
        description: "This is a task description",
        status: "todo" as Task["status"],
      }),
    ];

    render(<BoardView {...defaultProps} tasks={tasks} />);
    expect(screen.getByText("This is a task description")).toBeInTheDocument();
  });

  it("wraps content in DndContext and DragOverlay", () => {
    render(<BoardView {...defaultProps} tasks={[]} />);
    expect(screen.getByTestId("drag-overlay")).toBeInTheDocument();
  });
});

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { CostStatusResponse, SprintStatsResponse } from "../../types";
import { CostTrendChart } from "../CostTrendChart";

// Mock recharts to avoid rendering issues in jsdom
vi.mock("recharts", () => {
  const MockResponsiveContainer = ({ children }: { children: React.ReactNode }) => (
    <div data-testid="responsive-container">{children}</div>
  );
  const MockLineChart = ({ children, data }: { children: React.ReactNode; data: unknown[] }) => (
    <div data-testid="line-chart" data-points={data.length}>
      {children}
    </div>
  );
  const MockLine = ({ dataKey, name }: { dataKey: string; name: string }) => (
    <div data-testid={`line-${dataKey}`} data-name={name} />
  );
  const MockXAxis = () => <div data-testid="x-axis" />;
  const MockYAxis = () => <div data-testid="y-axis" />;
  const MockCartesianGrid = () => <div data-testid="cartesian-grid" />;
  const MockTooltip = () => <div data-testid="tooltip" />;
  const MockLegend = () => <div data-testid="legend" />;
  const MockReferenceLine = ({ y }: { y: number }) => <div data-testid="reference-line" data-y={y} />;

  return {
    ResponsiveContainer: MockResponsiveContainer,
    LineChart: MockLineChart,
    Line: MockLine,
    XAxis: MockXAxis,
    YAxis: MockYAxis,
    CartesianGrid: MockCartesianGrid,
    Tooltip: MockTooltip,
    Legend: MockLegend,
    ReferenceLine: MockReferenceLine,
  };
});

const mockSprintStats: SprintStatsResponse = {
  project_id: "proj-123",
  group_by: "date",
  summary: {
    total_tasks: 10,
    done: 5,
    first_pass_rate: 0.8,
    avg_retries: 0.4,
    avg_duration_hours: 2.5,
    estimated_cost_usd: 12.5,
  },
  sprints: [
    {
      date: "2026-03-15",
      tasks_completed: 2,
      first_pass_rate: 1.0,
      avg_retries: 0,
      avg_duration_hours: 2.0,
      estimated_cost_usd: 4.5,
    },
    {
      date: "2026-03-16",
      tasks_completed: 1,
      first_pass_rate: 0.5,
      avg_retries: 1,
      avg_duration_hours: 3.0,
      estimated_cost_usd: 3.0,
    },
    {
      date: "2026-03-17",
      tasks_completed: 2,
      first_pass_rate: 1.0,
      avg_retries: 0,
      avg_duration_hours: 2.5,
      estimated_cost_usd: 5.0,
    },
  ],
  trends: { available: true },
  top_learnings: [],
  code_patterns_count: 0,
  injection_metrics: {},
};

const mockCostStatus: CostStatusResponse = {
  project_id: "proj-123",
  status: "ok",
  today: {
    date: "2026-03-17",
    cost_usd: 5.0,
    budget_usd: 20.0,
    usage_pct: 25.0,
    exceeded: false,
    warning: false,
  },
  sprint: {
    total_cost_usd: 12.5,
    budget_usd: 100.0,
    usage_pct: 12.5,
    exceeded: false,
    warning: false,
  },
  daily_breakdown: {
    "2026-03-15": 4.5,
    "2026-03-16": 3.0,
    "2026-03-17": 5.0,
  },
  budget_config: {
    max_cost_per_day: 20.0,
    max_cost_per_sprint: 100.0,
  },
};

describe("CostTrendChart", () => {
  it("renders chart with sprint data", () => {
    render(<CostTrendChart sprintStats={mockSprintStats} />);

    expect(screen.getByText("Cost Trend")).toBeInTheDocument();
    expect(screen.getByTestId("line-chart")).toBeInTheDocument();
    expect(screen.getByTestId("line-chart")).toHaveAttribute("data-points", "3");
  });

  it("renders cumulative and daily cost lines", () => {
    render(<CostTrendChart sprintStats={mockSprintStats} />);

    expect(screen.getByTestId("line-cumulativeCost")).toBeInTheDocument();
    expect(screen.getByTestId("line-dailyCost")).toBeInTheDocument();
    expect(screen.getByTestId("line-tasksCompleted")).toBeInTheDocument();
  });

  it("shows summary totals", () => {
    render(<CostTrendChart sprintStats={mockSprintStats} />);

    expect(screen.getByText("Total: $12.50")).toBeInTheDocument();
    expect(screen.getByText("Tasks Done: 5")).toBeInTheDocument();
  });

  it("renders budget reference line when costStatus is provided", () => {
    render(<CostTrendChart sprintStats={mockSprintStats} costStatus={mockCostStatus} />);

    expect(screen.getByTestId("reference-line")).toBeInTheDocument();
    expect(screen.getByTestId("reference-line")).toHaveAttribute("data-y", "20");
  });

  it("does not render budget line without costStatus", () => {
    render(<CostTrendChart sprintStats={mockSprintStats} />);

    expect(screen.queryByTestId("reference-line")).not.toBeInTheDocument();
  });

  it("shows warning status when budget is approaching", () => {
    const warningCostStatus: CostStatusResponse = {
      ...mockCostStatus,
      status: "warning",
    };

    render(<CostTrendChart sprintStats={mockSprintStats} costStatus={warningCostStatus} />);

    expect(screen.getByText("Approaching budget limit")).toBeInTheDocument();
  });

  it("shows exceeded status when budget is exceeded", () => {
    const exceededCostStatus: CostStatusResponse = {
      ...mockCostStatus,
      status: "exceeded",
    };

    render(<CostTrendChart sprintStats={mockSprintStats} costStatus={exceededCostStatus} />);

    expect(screen.getByText("Budget exceeded")).toBeInTheDocument();
  });

  it("shows empty state when no sprints data", () => {
    const emptyStats: SprintStatsResponse = {
      ...mockSprintStats,
      sprints: [],
    };

    render(<CostTrendChart sprintStats={emptyStats} />);

    expect(screen.getByText("No completed tasks with cost data yet.")).toBeInTheDocument();
    expect(screen.queryByTestId("line-chart")).not.toBeInTheDocument();
  });
});

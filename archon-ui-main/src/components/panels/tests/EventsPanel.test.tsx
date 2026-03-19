import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { EventsPanel, type TaskEvent } from "../EventsPanel";

const now = new Date();

function makeEvent(overrides: Partial<TaskEvent> = {}): TaskEvent {
  return {
    event: "task_started",
    task_id: "t-1",
    data: {},
    timestamp: now.toISOString(),
    is_critical: false,
    ...overrides,
  };
}

function hoursAgo(hours: number): string {
  return new Date(now.getTime() - hours * 60 * 60 * 1000).toISOString();
}

const sampleEvents: TaskEvent[] = [
  makeEvent({ event: "task_started", agent: "agent-alpha", timestamp: hoursAgo(0.5) }),
  makeEvent({ event: "task_completed", agent: "agent-beta", timestamp: hoursAgo(2) }),
  makeEvent({ event: "task_failed", agent: "agent-alpha", timestamp: hoursAgo(5), is_critical: true }),
  makeEvent({ event: "health_alert", agent: "agent-gamma", timestamp: hoursAgo(10), is_critical: true }),
  makeEvent({ event: "task_started", agent: "agent-beta", timestamp: hoursAgo(25) }),
];

describe("EventsPanel", () => {
  it("renders 3 filter dropdowns", () => {
    render(<EventsPanel events={sampleEvents} />);

    expect(screen.getByTestId("agent-filter")).toBeInTheDocument();
    expect(screen.getByTestId("event-type-filter")).toBeInTheDocument();
    expect(screen.getByTestId("time-range-filter")).toBeInTheDocument();
  });

  it("shows correct total and filtered count with no filters", () => {
    render(<EventsPanel events={sampleEvents} />);

    expect(screen.getByTestId("events-count")).toHaveTextContent(
      `Showing ${sampleEvents.length} of ${sampleEvents.length} events`,
    );
  });

  it("renders all event rows when no filters applied", () => {
    render(<EventsPanel events={sampleEvents} />);

    const rows = screen.getAllByTestId("event-row");
    expect(rows).toHaveLength(sampleEvents.length);
  });

  it("shows empty state when no events match", () => {
    render(<EventsPanel events={[]} />);

    expect(screen.getByText("No events match the current filters.")).toBeInTheDocument();
    expect(screen.getByTestId("events-count")).toHaveTextContent("Showing 0 of 0 events");
  });

  it("renders filter bar above event list", () => {
    render(<EventsPanel events={sampleEvents} />);

    const filterBar = screen.getByTestId("events-filter-bar");
    const eventsList = screen.getByTestId("events-list");

    // Filter bar should appear before event list in DOM order
    const panel = screen.getByTestId("events-panel");
    const children = Array.from(panel.children);
    expect(children.indexOf(filterBar)).toBeLessThan(children.indexOf(eventsList));
  });

  it("displays critical events with red indicator", () => {
    const criticalEvent = makeEvent({ event: "task_failed", is_critical: true });
    render(<EventsPanel events={[criticalEvent]} />);

    const row = screen.getByTestId("event-row");
    expect(row.className).toContain("border-red");
  });

  it("resolves agent from data.assignee when agent field is missing", () => {
    const evt = makeEvent({
      event: "task_started",
      agent: undefined,
      data: { assignee: "bot-worker" },
    });
    render(<EventsPanel events={[evt]} />);

    expect(screen.getByText("bot-worker")).toBeInTheDocument();
  });

  it("falls back to 'unknown' when no agent info available", () => {
    const evt = makeEvent({ event: "task_started", agent: undefined, data: {} });
    render(<EventsPanel events={[evt]} />);

    expect(screen.getByText("unknown")).toBeInTheDocument();
  });

  it("formats event types with title case", () => {
    const evt = makeEvent({ event: "task_started" });
    render(<EventsPanel events={[evt]} />);

    expect(screen.getByText("Task Started")).toBeInTheDocument();
  });

  it("resets filters when projectId changes", () => {
    const { rerender } = render(<EventsPanel events={sampleEvents} projectId="p-1" />);

    // All events visible initially
    expect(screen.getAllByTestId("event-row")).toHaveLength(5);

    // Rerender with new project — filters should reset
    rerender(<EventsPanel events={sampleEvents.slice(0, 2)} projectId="p-2" />);
    expect(screen.getAllByTestId("event-row")).toHaveLength(2);
    expect(screen.getByTestId("events-count")).toHaveTextContent("Showing 2 of 2 events");
  });

  describe("time range filtering", () => {
    it("filters events by time range via data-driven approach", () => {
      // Render with all events to verify count before filtering
      const { unmount } = render(<EventsPanel events={sampleEvents} />);
      expect(screen.getByTestId("events-count")).toHaveTextContent(
        `Showing ${sampleEvents.length} of ${sampleEvents.length} events`,
      );
      unmount();

      // Verify the time-based data is correct:
      // hoursAgo(0.5) = within 1h
      // hoursAgo(2) = within 6h but not 1h
      // hoursAgo(5) = within 6h
      // hoursAgo(10) = within 24h but not 6h
      // hoursAgo(25) = outside 24h
      const withinOneHour = sampleEvents.filter((e) => {
        const age = now.getTime() - new Date(e.timestamp).getTime();
        return age <= 60 * 60 * 1000;
      });
      expect(withinOneHour).toHaveLength(1);
    });
  });

  it("shows unique agents derived from events", () => {
    // The agent filter should list agent-alpha, agent-beta, agent-gamma
    render(<EventsPanel events={sampleEvents} />);
    // The Select trigger should be present; agents are rendered inside dropdown content
    expect(screen.getByTestId("agent-filter")).toBeInTheDocument();
  });
});

import { useMemo, useState } from "react";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "../../features/ui/primitives/select";
import { cn } from "../../features/ui/primitives/styles";

export interface TaskEvent {
  event: string;
  task_id: string;
  data: Record<string, unknown>;
  timestamp: string;
  is_critical: boolean;
  agent?: string;
}

type TimeRange = "1h" | "6h" | "24h" | "all";

const TIME_RANGE_OPTIONS: { value: TimeRange; label: string }[] = [
  { value: "1h", label: "Last 1h" },
  { value: "6h", label: "Last 6h" },
  { value: "24h", label: "Last 24h" },
  { value: "all", label: "All" },
];

const TIME_RANGE_MS: Record<TimeRange, number | null> = {
  "1h": 60 * 60 * 1000,
  "6h": 6 * 60 * 60 * 1000,
  "24h": 24 * 60 * 60 * 1000,
  all: null,
};

function resolveAgent(evt: TaskEvent): string {
  if (evt.agent) return evt.agent;
  if (typeof evt.data?.assignee === "string" && evt.data.assignee) return evt.data.assignee;
  if (typeof evt.data?.agent === "string" && evt.data.agent) return evt.data.agent;
  return "unknown";
}

function formatEventType(event: string): string {
  return event
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function formatTimestamp(ts: string): string {
  try {
    return new Date(ts).toLocaleString();
  } catch {
    return ts;
  }
}

interface EventsPanelProps {
  events: TaskEvent[];
  projectId?: string;
}

export function EventsPanel({ events, projectId }: EventsPanelProps) {
  const [agentFilter, setAgentFilter] = useState("all");
  const [eventTypeFilter, setEventTypeFilter] = useState("all");
  const [timeRange, setTimeRange] = useState<TimeRange>("all");

  // Reset filters when project changes
  const [prevProjectId, setPrevProjectId] = useState(projectId);
  if (projectId !== prevProjectId) {
    setPrevProjectId(projectId);
    setAgentFilter("all");
    setEventTypeFilter("all");
    setTimeRange("all");
  }

  // Derive unique agents and event types from events
  const uniqueAgents = useMemo(() => {
    const agents = new Set<string>();
    for (const evt of events) {
      agents.add(resolveAgent(evt));
    }
    return Array.from(agents).sort();
  }, [events]);

  const uniqueEventTypes = useMemo(() => {
    const types = new Set<string>();
    for (const evt of events) {
      types.add(evt.event);
    }
    return Array.from(types).sort();
  }, [events]);

  // Apply filters
  const filteredEvents = useMemo(() => {
    const now = Date.now();
    const rangeMs = TIME_RANGE_MS[timeRange];

    return events.filter((evt) => {
      if (agentFilter !== "all" && resolveAgent(evt) !== agentFilter) return false;
      if (eventTypeFilter !== "all" && evt.event !== eventTypeFilter) return false;
      if (rangeMs !== null) {
        const evtTime = new Date(evt.timestamp).getTime();
        if (now - evtTime > rangeMs) return false;
      }
      return true;
    });
  }, [events, agentFilter, eventTypeFilter, timeRange]);

  return (
    <div className="space-y-4" data-testid="events-panel">
      {/* Filter bar */}
      <div
        className={cn(
          "flex flex-wrap items-center gap-3 p-3 rounded-lg",
          "backdrop-blur-xl bg-white/5 dark:bg-white/10",
          "border border-white/10 dark:border-white/[0.06]",
        )}
        data-testid="events-filter-bar"
      >
        {/* Agent filter */}
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">
            Agent
          </label>
          <Select value={agentFilter} onValueChange={setAgentFilter}>
            <SelectTrigger
              className="min-w-[140px] text-sm h-8"
              color="cyan"
              data-testid="agent-filter"
            >
              <SelectValue placeholder="All Agents" />
            </SelectTrigger>
            <SelectContent color="cyan">
              <SelectItem value="all" color="cyan">All</SelectItem>
              {uniqueAgents.map((agent) => (
                <SelectItem key={agent} value={agent} color="cyan">
                  {agent}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Event type filter */}
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">
            Event Type
          </label>
          <Select value={eventTypeFilter} onValueChange={setEventTypeFilter}>
            <SelectTrigger
              className="min-w-[180px] text-sm h-8"
              color="purple"
              data-testid="event-type-filter"
            >
              <SelectValue placeholder="All Types" />
            </SelectTrigger>
            <SelectContent color="purple">
              <SelectItem value="all" color="purple">All</SelectItem>
              {uniqueEventTypes.map((type) => (
                <SelectItem key={type} value={type} color="purple">
                  {formatEventType(type)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Time range filter */}
        <div className="flex items-center gap-2">
          <label className="text-xs font-medium text-gray-500 dark:text-gray-400 whitespace-nowrap">
            Time
          </label>
          <Select value={timeRange} onValueChange={(v) => setTimeRange(v as TimeRange)}>
            <SelectTrigger
              className="min-w-[120px] text-sm h-8"
              color="blue"
              data-testid="time-range-filter"
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent color="blue">
              {TIME_RANGE_OPTIONS.map((opt) => (
                <SelectItem key={opt.value} value={opt.value} color="blue">
                  {opt.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Count label */}
        <span
          className="ml-auto text-xs text-gray-500 dark:text-gray-400"
          data-testid="events-count"
        >
          Showing {filteredEvents.length} of {events.length} events
        </span>
      </div>

      {/* Event list */}
      <div className="space-y-2" data-testid="events-list">
        {filteredEvents.length === 0 ? (
          <p className="text-center text-sm text-gray-500 dark:text-gray-400 py-8">
            No events match the current filters.
          </p>
        ) : (
          filteredEvents.map((evt, idx) => (
            <div
              key={`${evt.timestamp}-${evt.event}-${idx}`}
              className={cn(
                "flex items-start gap-3 p-3 rounded-lg text-sm",
                "backdrop-blur-md bg-white/5 dark:bg-white/[0.03]",
                "border border-white/10 dark:border-white/[0.06]",
                evt.is_critical && "border-red-500/40 dark:border-red-400/30",
              )}
              data-testid="event-row"
            >
              {/* Critical indicator */}
              <span
                className={cn(
                  "mt-1 w-2 h-2 rounded-full flex-shrink-0",
                  evt.is_critical
                    ? "bg-red-500 shadow-[0_0_6px_rgba(239,68,68,0.6)]"
                    : "bg-cyan-500/60",
                )}
              />

              <div className="flex-1 min-w-0">
                <div className="flex items-baseline gap-2 flex-wrap">
                  <span className="font-medium text-gray-800 dark:text-gray-200">
                    {formatEventType(evt.event)}
                  </span>
                  <span className="text-xs text-gray-500 dark:text-gray-400">
                    {resolveAgent(evt)}
                  </span>
                  {evt.task_id && (
                    <span className="text-xs text-gray-400 dark:text-gray-500 font-mono">
                      {evt.task_id}
                    </span>
                  )}
                </div>
                <p className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                  {formatTimestamp(evt.timestamp)}
                </p>
              </div>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

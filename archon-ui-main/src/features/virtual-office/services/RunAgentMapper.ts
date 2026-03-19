/**
 * RunAgentMapper — maps CC hook events to agent state transitions.
 *
 * Receives raw events from the observability WebSocket and determines
 * which agent should transition to which state/room. Handles debouncing
 * of rapid events and unknown agent registration.
 */

import type { AgentState, AgentTransition, CCEventType, CCHookEvent, RoomId, VirtualAgent } from "../types";

/** Default colors assigned to agents round-robin */
const AGENT_COLORS = [
  "#06b6d4", // cyan
  "#8b5cf6", // violet
  "#f59e0b", // amber
  "#10b981", // emerald
  "#ef4444", // red
  "#ec4899", // pink
  "#3b82f6", // blue
  "#f97316", // orange
];

/** Events that indicate an agent should start working */
const WORK_START_EVENTS: ReadonlySet<CCEventType> = new Set(["task_started", "session_start"]);

/** Events that indicate an agent should return to idle */
const WORK_END_EVENTS: ReadonlySet<CCEventType> = new Set([
  "task_completed",
  "task_failed",
  "task_done",
  "task_review_ready",
  "task_escalated",
  "session_end",
]);

/** Events that indicate ongoing work (debounce these) */
const WORK_ACTIVITY_EVENTS: ReadonlySet<CCEventType> = new Set(["tool_use", "agent_status"]);

/** Minimum ms between processing events for the same agent */
const DEBOUNCE_MS = 300;

export class RunAgentMapper {
  private agents: Map<string, VirtualAgent> = new Map();
  private lastEventTime: Map<string, number> = new Map();
  private colorIndex = 0;
  private transitionLog: AgentTransition[] = [];

  /** Get all registered agents */
  getAgents(): VirtualAgent[] {
    return Array.from(this.agents.values());
  }

  /** Get a specific agent by ID */
  getAgent(agentId: string): VirtualAgent | undefined {
    return this.agents.get(agentId);
  }

  /** Get recent transitions (last 100) */
  getTransitions(): AgentTransition[] {
    return this.transitionLog.slice(-100);
  }

  /**
   * Process an incoming CC hook event and return the resulting transition,
   * or null if the event was debounced or no transition needed.
   */
  processEvent(event: CCHookEvent): AgentTransition | null {
    const agentId = event.agent;
    if (!agentId) return null;

    // Debounce rapid events from the same agent
    const now = Date.now();
    const lastTime = this.lastEventTime.get(agentId) ?? 0;
    if (now - lastTime < DEBOUNCE_MS && WORK_ACTIVITY_EVENTS.has(event.event)) {
      // Still update the agent's timestamp for activity tracking
      const agent = this.agents.get(agentId);
      if (agent) {
        agent.lastEventTimestamp = event.timestamp;
      }
      return null;
    }
    this.lastEventTime.set(agentId, now);

    // Auto-register unknown agents
    if (!this.agents.has(agentId)) {
      this.registerAgent(agentId, agentId);
    }

    const agent = this.agents.get(agentId)!;
    const transition = this.computeTransition(agent, event);

    if (transition) {
      // Apply the transition
      agent.state = transition.toState;
      agent.room = transition.toRoom;
      agent.taskId = WORK_END_EVENTS.has(event.event) ? null : event.task_id || agent.taskId;
      agent.lastEventTimestamp = event.timestamp;

      this.transitionLog.push(transition);
      // Keep log bounded
      if (this.transitionLog.length > 200) {
        this.transitionLog = this.transitionLog.slice(-100);
      }
    }

    return transition;
  }

  /**
   * Register a new agent in the virtual office.
   * Agents start idle in the lounge.
   */
  registerAgent(id: string, name: string): VirtualAgent {
    const existing = this.agents.get(id);
    if (existing) return existing;

    const agent: VirtualAgent = {
      id,
      name,
      state: "idle",
      room: "lounge",
      taskId: null,
      lastEventTimestamp: new Date().toISOString(),
      position: this.computeIdlePosition(this.agents.size),
      color: AGENT_COLORS[this.colorIndex % AGENT_COLORS.length],
    };

    this.colorIndex++;
    this.agents.set(id, agent);
    return agent;
  }

  /** Remove an agent (e.g., on disconnect) */
  removeAgent(agentId: string): boolean {
    this.lastEventTime.delete(agentId);
    return this.agents.delete(agentId);
  }

  /** Reset all agents to idle in the lounge */
  resetAll(): void {
    for (const agent of this.agents.values()) {
      agent.state = "idle";
      agent.room = "lounge";
      agent.taskId = null;
    }
    this.transitionLog = [];
    this.lastEventTime.clear();
  }

  /**
   * Determine the state transition for an agent based on the event.
   * Returns null if no transition is needed.
   */
  private computeTransition(agent: VirtualAgent, event: CCHookEvent): AgentTransition | null {
    const { state: fromState, room: fromRoom } = agent;

    let toState: AgentState;
    let toRoom: RoomId;

    if (WORK_START_EVENTS.has(event.event)) {
      // Start working: move to working room
      if (fromState === "working" && fromRoom === "working-room") {
        // Already working — no transition needed (just update task)
        agent.taskId = event.task_id || agent.taskId;
        agent.lastEventTimestamp = event.timestamp;
        return null;
      }
      toState = "walking";
      toRoom = "working-room";
    } else if (WORK_END_EVENTS.has(event.event)) {
      // Done working: return to lounge
      if (fromRoom === "lounge" && fromState === "idle") {
        // Already idle in lounge
        return null;
      }
      toState = "walking";
      toRoom = "lounge";
    } else if (WORK_ACTIVITY_EVENTS.has(event.event)) {
      // Ongoing activity: ensure agent is working
      if (fromState === "working" && fromRoom === "working-room") {
        // Already working — no transition needed
        agent.lastEventTimestamp = event.timestamp;
        return null;
      }
      // Agent somehow not in working state — move them there
      toState = "walking";
      toRoom = "working-room";
    } else {
      // Unknown event type — ignore
      return null;
    }

    return {
      agentId: agent.id,
      fromState,
      toState,
      fromRoom,
      toRoom,
      triggeredBy: event.event,
      timestamp: event.timestamp,
    };
  }

  /**
   * Compute a staggered position for idle agents to avoid overlap.
   * Positions are normalized 0-1 within the room.
   */
  private computeIdlePosition(index: number): { x: number; y: number } {
    const cols = 3;
    const col = index % cols;
    const row = Math.floor(index / cols);
    return {
      x: 0.2 + col * 0.3,
      y: 0.3 + row * 0.25,
    };
  }
}

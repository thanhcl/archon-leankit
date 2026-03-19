/**
 * Virtual Office types for agent visualization.
 *
 * Agents animate in an isometric 2D view based on real CC hook events.
 * State machine: idle → walking → working → idle
 */

/** Agent states in the virtual office */
export type AgentState = "idle" | "walking" | "working";

/** Room locations agents can occupy */
export type RoomId = "lounge" | "working-room";

/** CC hook event types that drive agent animations */
export type CCEventType =
  | "task_started"
  | "task_completed"
  | "task_failed"
  | "task_review_ready"
  | "task_escalated"
  | "task_done"
  | "tool_use"
  | "session_start"
  | "session_end"
  | "agent_status";

/** Incoming event from the observability/WebSocket server */
export interface CCHookEvent {
  event: CCEventType;
  task_id: string;
  agent: string;
  timestamp: string;
  data?: Record<string, unknown>;
}

/** Sub-event types for agent_status events */
export type AgentStatusEventType = "assistant" | "tool_use" | "tool_result" | "thinking";

/** A single agent status message for the live status panel */
export interface AgentStatusMessage {
  id: string;
  event: AgentStatusEventType;
  message: string;
  toolName: string;
  argsSummary: string;
  outputSummary: string;
  timestamp: string;
}

/** Progress indicator for agent activity */
export type AgentActivity = "thinking" | "reading" | "writing" | "executing" | "idle";

/** Agent representation in the virtual office */
export interface VirtualAgent {
  id: string;
  name: string;
  state: AgentState;
  room: RoomId;
  taskId: string | null;
  lastEventTimestamp: string;
  /** Position within the room (0-1 normalized) */
  position: { x: number; y: number };
  /** Color for avatar rendering */
  color: string;
}

/** Room definition for layout */
export interface Room {
  id: RoomId;
  label: string;
  /** Max agents that can comfortably fit */
  capacity: number;
  /** Position in the isometric grid */
  gridPosition: { col: number; row: number };
}

/** State transition triggered by an event */
export interface AgentTransition {
  agentId: string;
  fromState: AgentState;
  toState: AgentState;
  fromRoom: RoomId;
  toRoom: RoomId;
  triggeredBy: CCEventType;
  timestamp: string;
}

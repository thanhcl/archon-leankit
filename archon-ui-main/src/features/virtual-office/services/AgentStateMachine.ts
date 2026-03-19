/**
 * AgentStateMachine — manages agent state transitions with timing.
 *
 * State flow: idle → walking → working → walking → idle
 *
 * Walking is a transient state that auto-resolves after a configurable
 * duration (simulates agent moving between rooms). The machine ensures
 * smooth transitions and prevents invalid state jumps.
 */

import type { AgentState, AgentTransition, RoomId } from "../types";

/** Duration in ms for the walking animation between rooms */
const WALK_DURATION_MS = 1200;

/** Timeout in ms to auto-return an agent to idle if no activity */
const IDLE_TIMEOUT_MS = 120_000; // 2 minutes

export interface StateMachineCallbacks {
  /** Called when an agent's state changes (after walk completes) */
  onStateChange: (agentId: string, state: AgentState, room: RoomId) => void;
}

interface WalkTimer {
  timerId: ReturnType<typeof setTimeout>;
  targetState: AgentState;
  targetRoom: RoomId;
}

interface IdleTimer {
  timerId: ReturnType<typeof setTimeout>;
}

export class AgentStateMachine {
  private walkTimers: Map<string, WalkTimer> = new Map();
  private idleTimers: Map<string, IdleTimer> = new Map();
  private callbacks: StateMachineCallbacks;

  constructor(callbacks: StateMachineCallbacks) {
    this.callbacks = callbacks;
  }

  /**
   * Apply a transition from RunAgentMapper.
   * If the transition enters "walking", schedule auto-arrival.
   */
  applyTransition(transition: AgentTransition): void {
    const { agentId, toState, toRoom } = transition;

    // Cancel any existing walk/idle timer for this agent
    this.cancelWalkTimer(agentId);
    this.cancelIdleTimer(agentId);

    if (toState === "walking") {
      // Determine the final state after walking
      const finalState: AgentState = toRoom === "working-room" ? "working" : "idle";

      // Notify immediately that the agent is walking
      this.callbacks.onStateChange(agentId, "walking", toRoom);

      // Schedule arrival at destination
      const timerId = setTimeout(() => {
        this.walkTimers.delete(agentId);
        this.callbacks.onStateChange(agentId, finalState, toRoom);

        // Start idle timeout if agent is now working
        if (finalState === "working") {
          this.startIdleTimeout(agentId);
        }
      }, WALK_DURATION_MS);

      this.walkTimers.set(agentId, {
        timerId,
        targetState: finalState,
        targetRoom: toRoom,
      });
    } else {
      // Direct state change (no walking involved)
      this.callbacks.onStateChange(agentId, toState, toRoom);

      if (toState === "working") {
        this.startIdleTimeout(agentId);
      }
    }
  }

  /**
   * Reset the idle timeout for an agent (called on activity events).
   * Keeps the agent in "working" state as long as events keep coming.
   */
  refreshActivity(agentId: string): void {
    this.cancelIdleTimer(agentId);
    this.startIdleTimeout(agentId);
  }

  /** Clean up all timers (call on unmount) */
  destroy(): void {
    for (const { timerId } of this.walkTimers.values()) {
      clearTimeout(timerId);
    }
    for (const { timerId } of this.idleTimers.values()) {
      clearTimeout(timerId);
    }
    this.walkTimers.clear();
    this.idleTimers.clear();
  }

  /** Check if an agent is currently walking */
  isWalking(agentId: string): boolean {
    return this.walkTimers.has(agentId);
  }

  private startIdleTimeout(agentId: string): void {
    const timerId = setTimeout(() => {
      this.idleTimers.delete(agentId);
      // Auto-return to idle after inactivity
      this.callbacks.onStateChange(agentId, "idle", "lounge");
    }, IDLE_TIMEOUT_MS);

    this.idleTimers.set(agentId, { timerId });
  }

  private cancelWalkTimer(agentId: string): void {
    const timer = this.walkTimers.get(agentId);
    if (timer) {
      clearTimeout(timer.timerId);
      this.walkTimers.delete(agentId);
    }
  }

  private cancelIdleTimer(agentId: string): void {
    const timer = this.idleTimers.get(agentId);
    if (timer) {
      clearTimeout(timer.timerId);
      this.idleTimers.delete(agentId);
    }
  }
}

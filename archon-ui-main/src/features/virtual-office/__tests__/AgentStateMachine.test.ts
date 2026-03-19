import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { AgentStateMachine } from "../services/AgentStateMachine";
import type { AgentTransition } from "../types";

function makeTransition(overrides: Partial<AgentTransition> = {}): AgentTransition {
  return {
    agentId: "a1",
    fromState: "idle",
    toState: "walking",
    fromRoom: "lounge",
    toRoom: "working-room",
    triggeredBy: "task_started",
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("AgentStateMachine", () => {
  let machine: AgentStateMachine;
  let onStateChange: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.useFakeTimers();
    onStateChange = vi.fn();
    machine = new AgentStateMachine({ onStateChange });
  });

  afterEach(() => {
    machine.destroy();
    vi.useRealTimers();
  });

  // ── Walking Transitions ─────────────────────────────────────────

  describe("walking transitions", () => {
    it("emits walking state immediately, then working after walk duration", () => {
      machine.applyTransition(makeTransition());

      // Should immediately be walking
      expect(onStateChange).toHaveBeenCalledWith("a1", "walking", "working-room");
      expect(onStateChange).toHaveBeenCalledTimes(1);

      // After walk duration, should arrive at destination
      vi.advanceTimersByTime(1200);
      expect(onStateChange).toHaveBeenCalledWith("a1", "working", "working-room");
      expect(onStateChange).toHaveBeenCalledTimes(2);
    });

    it("resolves to idle when walking to lounge", () => {
      machine.applyTransition(makeTransition({ toRoom: "lounge", toState: "walking" }));

      expect(onStateChange).toHaveBeenCalledWith("a1", "walking", "lounge");

      vi.advanceTimersByTime(1200);
      expect(onStateChange).toHaveBeenCalledWith("a1", "idle", "lounge");
    });

    it("cancels previous walk when new transition arrives", () => {
      machine.applyTransition(makeTransition());
      expect(onStateChange).toHaveBeenCalledTimes(1); // walking

      // New transition before walk completes
      machine.applyTransition(makeTransition({ toRoom: "lounge", toState: "walking" }));
      expect(onStateChange).toHaveBeenCalledTimes(2); // walking to lounge

      // Original walk timer should NOT fire
      vi.advanceTimersByTime(1200);
      expect(onStateChange).toHaveBeenCalledTimes(3); // idle in lounge
      expect(onStateChange).toHaveBeenLastCalledWith("a1", "idle", "lounge");
    });
  });

  // ── Idle Timeout ────────────────────────────────────────────────

  describe("idle timeout", () => {
    it("returns agent to idle after 2 minutes of inactivity", () => {
      machine.applyTransition(makeTransition());
      vi.advanceTimersByTime(1200); // Walk completes → working

      // 2 minutes of inactivity
      vi.advanceTimersByTime(120_000);
      expect(onStateChange).toHaveBeenLastCalledWith("a1", "idle", "lounge");
    });

    it("refreshActivity resets the idle timeout", () => {
      machine.applyTransition(makeTransition());
      vi.advanceTimersByTime(1200); // Walk completes → working

      // After 1 minute, refresh activity
      vi.advanceTimersByTime(60_000);
      machine.refreshActivity("a1");

      // Another minute passes (would have triggered idle without refresh)
      vi.advanceTimersByTime(60_000);
      // Should NOT be idle yet since we refreshed 60s ago
      const lastCall = onStateChange.mock.calls[onStateChange.mock.calls.length - 1];
      expect(lastCall).not.toEqual(["a1", "idle", "lounge"]);

      // Wait another 60s for the refreshed timeout to fire
      vi.advanceTimersByTime(60_000);
      expect(onStateChange).toHaveBeenLastCalledWith("a1", "idle", "lounge");
    });
  });

  // ── Direct State Changes ────────────────────────────────────────

  describe("direct state changes (non-walking)", () => {
    it("applies direct state change without walk timer", () => {
      machine.applyTransition(makeTransition({ toState: "working", toRoom: "working-room" }));

      expect(onStateChange).toHaveBeenCalledWith("a1", "working", "working-room");
      expect(onStateChange).toHaveBeenCalledTimes(1);

      // No walk timer should fire
      vi.advanceTimersByTime(1200);
      expect(onStateChange).toHaveBeenCalledTimes(1);
    });
  });

  // ── isWalking ───────────────────────────────────────────────────

  describe("isWalking", () => {
    it("returns true during walk, false after completion", () => {
      expect(machine.isWalking("a1")).toBe(false);

      machine.applyTransition(makeTransition());
      expect(machine.isWalking("a1")).toBe(true);

      vi.advanceTimersByTime(1200);
      expect(machine.isWalking("a1")).toBe(false);
    });
  });

  // ── Multiple Agents ─────────────────────────────────────────────

  describe("multiple agents", () => {
    it("manages timers independently for different agents", () => {
      machine.applyTransition(makeTransition({ agentId: "a1" }));
      machine.applyTransition(makeTransition({ agentId: "a2" }));

      expect(onStateChange).toHaveBeenCalledTimes(2);

      vi.advanceTimersByTime(1200);

      // Both should have arrived
      expect(onStateChange).toHaveBeenCalledWith("a1", "working", "working-room");
      expect(onStateChange).toHaveBeenCalledWith("a2", "working", "working-room");
    });
  });

  // ── Cleanup ─────────────────────────────────────────────────────

  describe("cleanup", () => {
    it("destroy clears all timers", () => {
      machine.applyTransition(makeTransition({ agentId: "a1" }));
      machine.applyTransition(makeTransition({ agentId: "a2" }));

      const callCount = onStateChange.mock.calls.length;
      machine.destroy();

      // Advance time — no more callbacks should fire
      vi.advanceTimersByTime(200_000);
      expect(onStateChange).toHaveBeenCalledTimes(callCount);
    });
  });
});

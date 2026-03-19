import { beforeEach, describe, expect, it } from "vitest";
import { RunAgentMapper } from "../services/RunAgentMapper";
import type { CCHookEvent } from "../types";

function makeEvent(overrides: Partial<CCHookEvent> = {}): CCHookEvent {
  return {
    event: "task_started",
    task_id: "t-1",
    agent: "agent-1",
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("RunAgentMapper", () => {
  let mapper: RunAgentMapper;

  beforeEach(() => {
    mapper = new RunAgentMapper();
  });

  // ── Agent Registration ──────────────────────────────────────────

  describe("agent registration", () => {
    it("registers an agent with idle state in lounge", () => {
      const agent = mapper.registerAgent("a1", "Alice");
      expect(agent.id).toBe("a1");
      expect(agent.name).toBe("Alice");
      expect(agent.state).toBe("idle");
      expect(agent.room).toBe("lounge");
      expect(agent.taskId).toBeNull();
    });

    it("does not duplicate agent on re-registration", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.registerAgent("a1", "Alice-2");
      expect(mapper.getAgents()).toHaveLength(1);
      expect(mapper.getAgent("a1")?.name).toBe("Alice");
    });

    it("assigns different colors to different agents", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.registerAgent("a2", "Bob");
      const a1 = mapper.getAgent("a1");
      const a2 = mapper.getAgent("a2");
      expect(a1?.color).not.toBe(a2?.color);
    });

    it("auto-registers unknown agents on event processing", () => {
      mapper.processEvent(makeEvent({ agent: "new-agent" }));
      expect(mapper.getAgent("new-agent")).toBeDefined();
      expect(mapper.getAgent("new-agent")?.name).toBe("new-agent");
    });
  });

  // ── Work Start Events ───────────────────────────────────────────

  describe("work start events", () => {
    it("transitions idle agent to walking toward working-room on task_started", () => {
      mapper.registerAgent("a1", "Alice");
      const transition = mapper.processEvent(makeEvent({ event: "task_started", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.fromState).toBe("idle");
      expect(transition!.toState).toBe("walking");
      expect(transition!.fromRoom).toBe("lounge");
      expect(transition!.toRoom).toBe("working-room");
    });

    it("transitions idle agent on session_start", () => {
      mapper.registerAgent("a1", "Alice");
      const transition = mapper.processEvent(makeEvent({ event: "session_start", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toState).toBe("walking");
      expect(transition!.toRoom).toBe("working-room");
    });

    it("updates task ID when already working (no transition)", () => {
      mapper.registerAgent("a1", "Alice");
      // First: start working
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: "t-1" }));
      const agent = mapper.getAgent("a1")!;
      // Manually set to working state (simulating state machine completing walk)
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: "t-2" }));
      expect(transition).toBeNull();
      expect(mapper.getAgent("a1")?.taskId).toBe("t-2");
    });
  });

  // ── Work End Events ─────────────────────────────────────────────

  describe("work end events", () => {
    it("transitions working agent to walking toward lounge on task_completed", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1" }));
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "task_completed", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toState).toBe("walking");
      expect(transition!.toRoom).toBe("lounge");
    });

    it("clears task ID on work end event", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: "t-1" }));
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      mapper.processEvent(makeEvent({ event: "task_completed", agent: "a1" }));
      expect(mapper.getAgent("a1")?.taskId).toBeNull();
    });

    it("no-ops for agent already idle in lounge", () => {
      mapper.registerAgent("a1", "Alice");
      const transition = mapper.processEvent(makeEvent({ event: "task_completed", agent: "a1" }));
      expect(transition).toBeNull();
    });

    it("handles task_failed event", () => {
      mapper.registerAgent("a1", "Alice");
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "task_failed", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toRoom).toBe("lounge");
    });

    it("handles task_done event", () => {
      mapper.registerAgent("a1", "Alice");
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "task_done", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toRoom).toBe("lounge");
    });

    it("handles task_review_ready event", () => {
      mapper.registerAgent("a1", "Alice");
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "task_review_ready", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toRoom).toBe("lounge");
    });

    it("handles task_escalated event", () => {
      mapper.registerAgent("a1", "Alice");
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "task_escalated", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toRoom).toBe("lounge");
    });

    it("handles session_end event", () => {
      mapper.registerAgent("a1", "Alice");
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      const transition = mapper.processEvent(makeEvent({ event: "session_end", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toRoom).toBe("lounge");
    });
  });

  // ── Activity Events & Debouncing ────────────────────────────────

  describe("tool_use (activity) events", () => {
    it("transitions idle agent to working-room on tool_use", () => {
      mapper.registerAgent("a1", "Alice");
      const transition = mapper.processEvent(makeEvent({ event: "tool_use", agent: "a1" }));
      expect(transition).not.toBeNull();
      expect(transition!.toRoom).toBe("working-room");
    });

    it("debounces rapid tool_use events from same agent", async () => {
      mapper.registerAgent("a1", "Alice");
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      // First tool_use — no transition needed (already working)
      const t1 = mapper.processEvent(makeEvent({ event: "tool_use", agent: "a1" }));
      expect(t1).toBeNull();

      // Rapid second tool_use — should be debounced
      const t2 = mapper.processEvent(makeEvent({ event: "tool_use", agent: "a1" }));
      expect(t2).toBeNull();
    });

    it("does not debounce events from different agents", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.registerAgent("a2", "Bob");

      const t1 = mapper.processEvent(makeEvent({ event: "tool_use", agent: "a1" }));
      const t2 = mapper.processEvent(makeEvent({ event: "tool_use", agent: "a2" }));
      // Both should produce transitions since they're different agents
      expect(t1).not.toBeNull();
      expect(t2).not.toBeNull();
    });
  });

  // ── Edge Cases ──────────────────────────────────────────────────

  describe("edge cases", () => {
    it("ignores events with no agent field", () => {
      const transition = mapper.processEvent(makeEvent({ agent: "" }));
      expect(transition).toBeNull();
    });

    it("multiple agents animate independently", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.registerAgent("a2", "Bob");

      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: "t-1" }));
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a2", task_id: "t-2" }));

      const a1 = mapper.getAgent("a1")!;
      const a2 = mapper.getAgent("a2")!;
      // Both should be in walking/working-room state
      expect(a1.state).toBe("walking");
      expect(a1.room).toBe("working-room");
      expect(a2.state).toBe("walking");
      expect(a2.room).toBe("working-room");
    });

    it("handles rapid task switch for same agent", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: "t-1" }));
      const agent = mapper.getAgent("a1")!;
      agent.state = "working";
      agent.room = "working-room";

      // Complete first task
      mapper.processEvent(makeEvent({ event: "task_completed", agent: "a1" }));
      // Immediately start new task
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: "t-2" }));

      expect(agent.taskId).toBe("t-2");
    });

    it("removeAgent removes agent and cleans up state", () => {
      mapper.registerAgent("a1", "Alice");
      expect(mapper.getAgents()).toHaveLength(1);
      mapper.removeAgent("a1");
      expect(mapper.getAgents()).toHaveLength(0);
      expect(mapper.getAgent("a1")).toBeUndefined();
    });

    it("resetAll returns all agents to idle in lounge", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.registerAgent("a2", "Bob");

      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1" }));
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a2" }));

      mapper.resetAll();

      for (const agent of mapper.getAgents()) {
        expect(agent.state).toBe("idle");
        expect(agent.room).toBe("lounge");
        expect(agent.taskId).toBeNull();
      }
    });

    it("getTransitions returns transition history", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.processEvent(makeEvent({ event: "task_started", agent: "a1" }));
      const transitions = mapper.getTransitions();
      expect(transitions.length).toBeGreaterThan(0);
      expect(transitions[0].agentId).toBe("a1");
      expect(transitions[0].triggeredBy).toBe("task_started");
    });

    it("transition log is bounded to prevent memory leaks", () => {
      mapper.registerAgent("a1", "Alice");
      // Generate 250 transitions
      for (let i = 0; i < 250; i++) {
        const agent = mapper.getAgent("a1")!;
        agent.state = "idle";
        agent.room = "lounge";
        mapper.processEvent(makeEvent({ event: "task_started", agent: "a1", task_id: `t-${i}` }));
      }
      expect(mapper.getTransitions().length).toBeLessThanOrEqual(100);
    });

    it("assigns staggered positions to avoid overlap", () => {
      mapper.registerAgent("a1", "Alice");
      mapper.registerAgent("a2", "Bob");
      mapper.registerAgent("a3", "Charlie");

      const positions = mapper.getAgents().map((a) => a.position);
      const uniquePositions = new Set(positions.map((p) => `${p.x},${p.y}`));
      expect(uniquePositions.size).toBe(3);
    });
  });
});

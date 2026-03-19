/**
 * Virtual Office Zustand store.
 *
 * Manages agent states, room assignments, event processing,
 * and live agent status messages from CC streaming output.
 */

import { create } from "zustand";
import { devtools } from "zustand/middleware";
import { AgentStateMachine } from "../services/AgentStateMachine";
import { RunAgentMapper } from "../services/RunAgentMapper";
import type { AgentActivity, AgentState, AgentStatusMessage, CCHookEvent, RoomId, VirtualAgent } from "../types";

/** Max status messages kept per agent */
const MAX_STATUS_HISTORY = 20;

let statusMessageCounter = 0;

interface VirtualOfficeState {
  /** All agents in the virtual office */
  agents: Record<string, VirtualAgent>;

  /** Whether the virtual office is connected to the event stream */
  connected: boolean;

  /** Event source for SSE connection */
  eventSource: EventSource | null;

  /** Live status messages per agent (last 20) */
  agentStatusMessages: Record<string, AgentStatusMessage[]>;

  /** Current activity per agent */
  agentActivity: Record<string, AgentActivity>;

  /** Process an incoming CC hook event */
  processEvent: (event: CCHookEvent) => void;

  /** Register a new agent manually */
  registerAgent: (id: string, name: string) => void;

  /** Remove an agent */
  removeAgent: (id: string) => void;

  /** Connect to the event stream */
  connect: (url: string) => void;

  /** Disconnect from the event stream */
  disconnect: () => void;

  /** Reset all agents to idle */
  resetAll: () => void;

  /** Internal: update agent state (called by state machine) */
  _updateAgentState: (agentId: string, state: AgentState, room: RoomId) => void;

  /** Internal: mapper instance */
  _mapper: RunAgentMapper;

  /** Internal: state machine instance */
  _stateMachine: AgentStateMachine | null;
}

export const useVirtualOfficeStore = create<VirtualOfficeState>()(
  devtools(
    (set, get) => {
      const mapper = new RunAgentMapper();

      const updateAgentState = (agentId: string, state: AgentState, room: RoomId) => {
        set((prev) => {
          const agent = prev.agents[agentId];
          if (!agent) return prev;

          return {
            agents: {
              ...prev.agents,
              [agentId]: { ...agent, state, room },
            },
          };
        });
      };

      const stateMachine = new AgentStateMachine({
        onStateChange: updateAgentState,
      });

      return {
        agents: {},
        connected: false,
        eventSource: null,
        agentStatusMessages: {},
        agentActivity: {},
        _mapper: mapper,
        _stateMachine: stateMachine,

        _updateAgentState: updateAgentState,

        processEvent: (event: CCHookEvent) => {
          const { _mapper, _stateMachine } = get();
          if (!_stateMachine) return;

          // Handle agent_status events (live streaming from CC)
          if (event.event === "agent_status" && event.data) {
            const data = event.data as Record<string, string>;
            const agentId = (data.agent_id as string) || event.agent;
            const subEvent = (data.event as string) || "unknown";

            // Determine activity from sub-event
            let activity: AgentActivity = "idle";
            if (subEvent === "thinking") activity = "thinking";
            else if (subEvent === "tool_use") {
              const toolName = (data.tool_name as string) || "";
              if (
                toolName.toLowerCase().includes("read") ||
                toolName.toLowerCase().includes("grep") ||
                toolName.toLowerCase().includes("glob")
              ) {
                activity = "reading";
              } else if (toolName.toLowerCase().includes("write") || toolName.toLowerCase().includes("edit")) {
                activity = "writing";
              } else if (toolName.toLowerCase().includes("bash")) {
                activity = "executing";
              } else {
                activity = "executing";
              }
            } else if (subEvent === "assistant") activity = "thinking";
            else if (subEvent === "tool_result") activity = "reading";

            const statusMessage: AgentStatusMessage = {
              id: `sm-${++statusMessageCounter}`,
              event: subEvent as AgentStatusMessage["event"],
              message: (data.message as string) || "",
              toolName: (data.tool_name as string) || "",
              argsSummary: (data.args_summary as string) || "",
              outputSummary: (data.output_summary as string) || "",
              timestamp: event.timestamp,
            };

            set((prev) => {
              const existing = prev.agentStatusMessages[agentId] || [];
              const updated = [...existing, statusMessage].slice(-MAX_STATUS_HISTORY);
              return {
                agentStatusMessages: {
                  ...prev.agentStatusMessages,
                  [agentId]: updated,
                },
                agentActivity: {
                  ...prev.agentActivity,
                  [agentId]: activity,
                },
              };
            });

            // Also process as regular event for animation
          }

          const transition = _mapper.processEvent(event);

          if (transition) {
            // Sync mapper's agent state to store
            const updatedAgents = _mapper.getAgents();
            const agentsRecord: Record<string, VirtualAgent> = {};
            for (const agent of updatedAgents) {
              agentsRecord[agent.id] = agent;
            }

            // Set the walking state immediately (mapper sets walking)
            set({ agents: agentsRecord });

            // Apply transition through state machine (handles walk → arrival)
            _stateMachine.applyTransition(transition);
          } else {
            // Event was debounced but may have updated timestamps
            const agent = _mapper.getAgent(event.agent);
            if (agent && agent.state === "working") {
              _stateMachine.refreshActivity(agent.id);
            }
          }
        },

        registerAgent: (id: string, name: string) => {
          const agent = get()._mapper.registerAgent(id, name);
          set((prev) => ({
            agents: { ...prev.agents, [id]: agent },
          }));
        },

        removeAgent: (id: string) => {
          get()._mapper.removeAgent(id);
          set((prev) => {
            const { [id]: _, ...rest } = prev.agents;
            return { agents: rest };
          });
        },

        connect: (url: string) => {
          const existing = get().eventSource;
          if (existing) {
            existing.close();
          }

          const eventSource = new EventSource(url);

          eventSource.onopen = () => {
            set({ connected: true });
          };

          eventSource.onmessage = (msg) => {
            try {
              const event: CCHookEvent = JSON.parse(msg.data);
              get().processEvent(event);
            } catch {
              // Skip unparseable messages
            }
          };

          eventSource.onerror = () => {
            set({ connected: false });
          };

          set({ eventSource, connected: false });
        },

        disconnect: () => {
          const { eventSource, _stateMachine } = get();
          if (eventSource) {
            eventSource.close();
          }
          _stateMachine?.destroy();
          set({ eventSource: null, connected: false });
        },

        resetAll: () => {
          get()._mapper.resetAll();
          const agents = get()._mapper.getAgents();
          const agentsRecord: Record<string, VirtualAgent> = {};
          for (const agent of agents) {
            agentsRecord[agent.id] = agent;
          }
          set({ agents: agentsRecord });
        },
      };
    },
    { name: "virtual-office" },
  ),
);

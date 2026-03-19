/**
 * VirtualOfficeView — main isometric view of the agent virtual office.
 *
 * Renders rooms in an isometric grid with agents animating based on
 * real CC hook events. Connects to the event stream on mount.
 */

import { useEffect, useMemo } from "react";
import { cn } from "../../ui/primitives/styles";
import { useVirtualOfficeStore } from "../state/virtualOfficeStore";
import type { Room } from "../types";
import { AgentPanel } from "./AgentPanel";
import { IsometricRoom } from "./IsometricRoom";

const ROOMS: Room[] = [
  { id: "lounge", label: "Lounge", capacity: 10, gridPosition: { col: 0, row: 0 } },
  { id: "working-room", label: "Working Room", capacity: 8, gridPosition: { col: 1, row: 0 } },
];

interface VirtualOfficeViewProps {
  /** SSE endpoint URL for agent events (defaults to /api/events/stream) */
  eventStreamUrl?: string;
}

export function VirtualOfficeView({ eventStreamUrl = "/api/events/stream" }: VirtualOfficeViewProps) {
  const agents = useVirtualOfficeStore((s) => s.agents);
  const connected = useVirtualOfficeStore((s) => s.connected);
  const connect = useVirtualOfficeStore((s) => s.connect);
  const disconnect = useVirtualOfficeStore((s) => s.disconnect);

  // Connect to event stream on mount
  useEffect(() => {
    connect(eventStreamUrl);
    return () => disconnect();
  }, [eventStreamUrl, connect, disconnect]);

  // Group agents by room
  const agentsByRoom = useMemo(() => {
    const grouped: Record<string, (typeof agents)[string][]> = {
      lounge: [],
      "working-room": [],
    };

    for (const agent of Object.values(agents)) {
      const roomId = agent.room;
      if (grouped[roomId]) {
        grouped[roomId].push(agent);
      } else {
        grouped.lounge.push(agent);
      }
    }

    return grouped;
  }, [agents]);

  const totalAgents = Object.keys(agents).length;

  return (
    <div
      className={cn(
        "relative p-6 rounded-2xl",
        "backdrop-blur-xl bg-white/5 dark:bg-white/[0.02]",
        "border border-white/10 dark:border-white/[0.06]",
      )}
      data-testid="virtual-office"
    >
      {/* Header */}
      <div className="flex items-center justify-between mb-6">
        <div className="flex items-center gap-3">
          <h2 className="text-sm font-semibold text-gray-300">Virtual Office</h2>
          <span className="text-xs text-gray-500">
            {totalAgents} agent{totalAgents !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Connection status */}
        <div className="flex items-center gap-2">
          <div
            className={cn(
              "w-2 h-2 rounded-full",
              connected ? "bg-emerald-400 shadow-[0_0_6px_rgba(52,211,153,0.6)]" : "bg-gray-600",
            )}
            data-testid="connection-status"
          />
          <span className="text-[10px] text-gray-500">{connected ? "Live" : "Disconnected"}</span>
        </div>
      </div>

      {/* Isometric room grid */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6" style={{ perspective: "800px" }}>
        {ROOMS.map((room) => (
          <div
            key={room.id}
            style={{
              transform: "rotateX(5deg)",
              transformOrigin: "center bottom",
            }}
          >
            <IsometricRoom room={room} agents={agentsByRoom[room.id] ?? []} />
          </div>
        ))}
      </div>

      {/* Live agent status panel */}
      <div className="mt-6">
        <AgentPanel />
      </div>
    </div>
  );
}

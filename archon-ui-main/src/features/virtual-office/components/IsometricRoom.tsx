/**
 * IsometricRoom — renders a room in isometric perspective with agents.
 *
 * Each room is a diamond-shaped container that holds agent avatars.
 * Agents flow in/out with animation transitions.
 */

import { AnimatePresence, motion } from "framer-motion";
import { cn } from "../../ui/primitives/styles";
import type { Room, VirtualAgent } from "../types";
import { AgentAvatar } from "./AgentAvatar";

interface IsometricRoomProps {
  room: Room;
  agents: VirtualAgent[];
}

const roomStyles: Record<string, { bg: string; border: string; glow: string }> = {
  lounge: {
    bg: "bg-emerald-900/20",
    border: "border-emerald-500/30",
    glow: "shadow-[0_0_20px_rgba(16,185,129,0.1)]",
  },
  "working-room": {
    bg: "bg-cyan-900/20",
    border: "border-cyan-500/30",
    glow: "shadow-[0_0_20px_rgba(6,182,212,0.1)]",
  },
};

export function IsometricRoom({ room, agents }: IsometricRoomProps) {
  const style = roomStyles[room.id] ?? roomStyles.lounge;
  const hasActiveAgents = agents.some((a) => a.state === "working");

  return (
    <div
      className={cn(
        "relative p-4 rounded-xl border backdrop-blur-sm",
        "min-h-[140px] min-w-[200px]",
        style.bg,
        style.border,
        hasActiveAgents && style.glow,
      )}
      data-testid={`room-${room.id}`}
    >
      {/* Room label */}
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">{room.label}</h3>
        <span className="text-[10px] text-gray-500">
          {agents.length}/{room.capacity}
        </span>
      </div>

      {/* Activity pulse when agents are working */}
      {hasActiveAgents && (
        <motion.div
          className="absolute inset-0 rounded-xl border border-cyan-400/20"
          animate={{ opacity: [0.3, 0.6, 0.3] }}
          transition={{ duration: 2, repeat: Infinity }}
        />
      )}

      {/* Agent grid */}
      <div className="flex flex-wrap gap-4 justify-center items-end min-h-[60px]">
        <AnimatePresence mode="popLayout">
          {agents.map((agent) => (
            <motion.div
              key={agent.id}
              layout
              initial={{ opacity: 0, scale: 0.5, y: 20 }}
              animate={{ opacity: 1, scale: 1, y: 0 }}
              exit={{ opacity: 0, scale: 0.5, y: -20 }}
              transition={{ type: "spring", stiffness: 300, damping: 25 }}
            >
              <AgentAvatar agent={agent} />
            </motion.div>
          ))}
        </AnimatePresence>

        {agents.length === 0 && <p className="text-xs text-gray-600 italic py-4">Empty</p>}
      </div>
    </div>
  );
}

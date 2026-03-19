/**
 * AgentAvatar — renders a single agent with state-dependent animation.
 *
 * States:
 * - idle: Sitting animation (subtle breathing motion)
 * - walking: Moving animation (bouncing translation)
 * - working: Typing animation (rapid hand movement)
 */

import { motion } from "framer-motion";
import type { VirtualAgent } from "../types";

interface AgentAvatarProps {
  agent: VirtualAgent;
}

const stateAnimations = {
  idle: {
    y: [0, -2, 0],
    transition: { duration: 3, repeat: Infinity, ease: "easeInOut" },
  },
  walking: {
    x: [0, 4, 0, -4, 0],
    y: [0, -6, 0, -6, 0],
    transition: { duration: 0.6, repeat: Infinity, ease: "easeInOut" },
  },
  working: {
    y: [0, -1, 0],
    transition: { duration: 0.3, repeat: Infinity, ease: "easeInOut" },
  },
};

/** Typing indicator dots for working state */
function TypingIndicator({ color }: { color: string }) {
  return (
    <div className="flex gap-0.5 absolute -top-4 left-1/2 -translate-x-1/2">
      {[0, 1, 2].map((i) => (
        <motion.div
          key={i}
          className="w-1 h-1 rounded-full"
          style={{ backgroundColor: color }}
          animate={{ opacity: [0.3, 1, 0.3], y: [0, -2, 0] }}
          transition={{
            duration: 0.6,
            repeat: Infinity,
            delay: i * 0.15,
          }}
        />
      ))}
    </div>
  );
}

export function AgentAvatar({ agent }: AgentAvatarProps) {
  const animation = stateAnimations[agent.state];

  return (
    <motion.div
      className="relative flex flex-col items-center"
      animate={animation}
      data-testid={`agent-avatar-${agent.id}`}
      data-agent-state={agent.state}
      data-agent-room={agent.room}
    >
      {agent.state === "working" && <TypingIndicator color={agent.color} />}

      {/* Agent body (isometric diamond shape) */}
      <div
        className="w-8 h-8 rounded-full border-2 flex items-center justify-center text-xs font-bold text-white shadow-lg"
        style={{
          backgroundColor: agent.color,
          borderColor: `${agent.color}80`,
          boxShadow: agent.state === "working" ? `0 0 12px ${agent.color}60` : `0 2px 4px rgba(0,0,0,0.3)`,
        }}
      >
        {agent.name.charAt(0).toUpperCase()}
      </div>

      {/* Agent name label */}
      <span className="mt-1 text-[10px] text-gray-400 whitespace-nowrap max-w-[60px] truncate">{agent.name}</span>

      {/* Task indicator */}
      {agent.taskId && agent.state === "working" && (
        <span className="text-[8px] text-gray-500 font-mono truncate max-w-[50px]">{agent.taskId.slice(0, 8)}</span>
      )}
    </motion.div>
  );
}

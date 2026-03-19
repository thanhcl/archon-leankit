/**
 * AgentPanel — live agent status display with scrollable message history.
 *
 * Shows per-agent cards with current activity indicator, tool being used,
 * latest message text, and a scrollable history of last 20 messages.
 */

import { useEffect, useRef } from "react";
import { cn } from "../../ui/primitives/styles";
import { useVirtualOfficeStore } from "../state/virtualOfficeStore";
import type { AgentActivity, AgentStatusMessage } from "../types";

const ACTIVITY_CONFIG: Record<AgentActivity, { label: string; color: string; icon: string }> = {
  thinking: { label: "Thinking", color: "text-violet-400", icon: "..." },
  reading: { label: "Reading", color: "text-cyan-400", icon: "R" },
  writing: { label: "Writing", color: "text-emerald-400", icon: "W" },
  executing: { label: "Executing", color: "text-amber-400", icon: "X" },
  idle: { label: "Idle", color: "text-gray-500", icon: "-" },
};

function StatusMessageRow({ msg }: { msg: AgentStatusMessage }) {
  if (msg.event === "tool_use") {
    return (
      <div className="flex items-start gap-2 text-xs py-1 animate-fadeIn">
        <span className="shrink-0 text-amber-400 font-mono text-[10px] mt-0.5">TOOL</span>
        <div className="min-w-0">
          <span className="text-gray-300 font-medium">{msg.toolName}</span>
          {msg.argsSummary && <span className="text-gray-500 ml-1 break-all">{msg.argsSummary}</span>}
        </div>
      </div>
    );
  }

  if (msg.event === "tool_result") {
    return (
      <div className="flex items-start gap-2 text-xs py-1 animate-fadeIn">
        <span className="shrink-0 text-cyan-400 font-mono text-[10px] mt-0.5">OUT</span>
        <span className="text-gray-400 break-all">{msg.outputSummary || "(empty)"}</span>
      </div>
    );
  }

  if (msg.event === "thinking") {
    return (
      <div className="flex items-start gap-2 text-xs py-1 animate-fadeIn">
        <span className="shrink-0 text-violet-400 font-mono text-[10px] mt-0.5">THINK</span>
        <span className="text-gray-400 italic break-all">{msg.message}</span>
      </div>
    );
  }

  // assistant message
  return (
    <div className="flex items-start gap-2 text-xs py-1 animate-fadeIn">
      <span className="shrink-0 text-emerald-400 font-mono text-[10px] mt-0.5">MSG</span>
      <span className="text-gray-300 break-all">{msg.message}</span>
    </div>
  );
}

function AgentCard({ agentId }: { agentId: string }) {
  const messages = useVirtualOfficeStore((s) => s.agentStatusMessages[agentId] || []);
  const activity = useVirtualOfficeStore((s) => s.agentActivity[agentId] || "idle");
  const agent = useVirtualOfficeStore((s) => s.agents[agentId]);
  const scrollRef = useRef<HTMLDivElement>(null);

  const activityInfo = ACTIVITY_CONFIG[activity];

  // Auto-scroll to bottom on new messages
  const lastMessageId = messages[messages.length - 1]?.id;
  // biome-ignore lint/correctness/useExhaustiveDependencies: scroll on new message arrival
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [lastMessageId]);

  return (
    <div
      className={cn(
        "rounded-lg p-3",
        "backdrop-blur-md bg-white/[0.03] dark:bg-white/[0.02]",
        "border border-white/[0.06]",
      )}
      data-testid={`agent-panel-${agentId}`}
    >
      {/* Agent header */}
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          {agent && <div className="w-3 h-3 rounded-full" style={{ backgroundColor: agent.color }} />}
          <span className="text-xs font-medium text-gray-300 truncate max-w-[120px]">{agent?.name || agentId}</span>
        </div>

        {/* Activity indicator */}
        <div className="flex items-center gap-1.5">
          <span className={cn("text-[10px] font-mono", activityInfo.color)}>{activityInfo.icon}</span>
          <span className={cn("text-[10px]", activityInfo.color)}>{activityInfo.label}</span>
          {activity !== "idle" && (
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-current opacity-75" />
              <span className={cn("relative inline-flex rounded-full h-2 w-2 bg-current", activityInfo.color)} />
            </span>
          )}
        </div>
      </div>

      {/* Message history (scrollable) */}
      <div
        ref={scrollRef}
        className={cn(
          "max-h-[200px] overflow-y-auto",
          "scrollbar-thin scrollbar-thumb-white/10 scrollbar-track-transparent",
          "divide-y divide-white/[0.04]",
        )}
        data-testid={`agent-messages-${agentId}`}
      >
        {messages.length === 0 ? (
          <div className="text-xs text-gray-600 py-2 text-center">No activity yet</div>
        ) : (
          messages.map((msg) => <StatusMessageRow key={msg.id} msg={msg} />)
        )}
      </div>
    </div>
  );
}

export function AgentPanel() {
  const agents = useVirtualOfficeStore((s) => s.agents);
  const agentIds = Object.keys(agents);

  if (agentIds.length === 0) {
    return null;
  }

  return (
    <div
      className={cn(
        "rounded-xl p-4",
        "backdrop-blur-xl bg-white/5 dark:bg-white/[0.02]",
        "border border-white/10 dark:border-white/[0.06]",
      )}
      data-testid="agent-panel"
    >
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-3">Agent Status</h3>

      <div className="grid grid-cols-1 gap-3">
        {agentIds.map((id) => (
          <AgentCard key={id} agentId={id} />
        ))}
      </div>
    </div>
  );
}

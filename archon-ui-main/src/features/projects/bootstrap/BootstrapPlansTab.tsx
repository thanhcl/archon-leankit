import { GitBranchPlus, Loader2, RefreshCw, Sparkles, Workflow } from "lucide-react";
import { Button, Card } from "../../ui/primitives";
import { cn, glassCard } from "../../ui/primitives/styles";
import type { ApprovalRequest, BootstrapPlan, BootstrapPlanExecutionRun, ExternalChannelHeartbeat, ExternalRequest, PlatformServiceHealth } from "../types";
import {
  useProjectApprovalRequests,
  useProjectArchitectRequests,
  useProjectExternalRequests,
  useBootstrapPlanExecutionRuns,
  useBootstrapPlanTraceEvents,
  useBootstrapPlans,
  useExternalChannelHeartbeat,
  useMaterializeBootstrapPlanBacklog,
  usePlatformServiceHealth,
} from "../hooks";
import type { OpenClawChannelHealth, TelegramChannelHealth } from "../types";

interface BootstrapPlansTabProps {
  projectId: string;
}

const formatTimestamp = (value?: string | null) => {
  if (!value) {
    return "Not started";
  }

  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
};

const ChannelHeartbeatSummary = ({ heartbeat }: { heartbeat?: ExternalChannelHeartbeat }) => (
  <Card className="space-y-2" glowColor="green" glowSize="sm">
    <div className="flex items-center gap-2 text-sm font-medium text-white">
      <Workflow className="w-4 h-4 text-emerald-300" />
      Channel Heartbeat
    </div>
    {heartbeat ? (
      <div className="grid gap-2 text-xs text-gray-300 md:grid-cols-2">
        <div>Status: <span className="text-emerald-200">{heartbeat.status}</span></div>
        <div>Ready: {heartbeat.ready_channels}/{heartbeat.total_channels}</div>
        <div>Degraded: {heartbeat.degraded_channels}</div>
        <div>Last checked: {formatTimestamp(heartbeat.last_checked_at)}</div>
        <div>Ready keys: {heartbeat.ready_channel_keys.length > 0 ? heartbeat.ready_channel_keys.join(", ") : "none"}</div>
        <div>Degraded keys: {heartbeat.degraded_channel_keys.length > 0 ? heartbeat.degraded_channel_keys.join(", ") : "none"}</div>
        <div className="md:col-span-2">
          Issues: {heartbeat.issues.length > 0 ? heartbeat.issues.join(", ") : "none"}
        </div>
      </div>
    ) : (
      <div className="text-xs text-gray-400">Aggregated channel heartbeat unavailable.</div>
    )}
  </Card>
);

const ServiceHealthSummary = ({ health }: { health?: PlatformServiceHealth }) => (
  <Card className="space-y-2" glowColor="cyan" glowSize="sm">
    <div className="flex items-center gap-2 text-sm font-medium text-white">
      <Workflow className="w-4 h-4 text-cyan-300" />
      Platform Service Health
    </div>
    {health ? (
      <div className="space-y-2 text-xs text-gray-300">
        <div className="grid gap-2 md:grid-cols-2">
          <div>Status: <span className="text-cyan-200">{health.status}</span></div>
          <div>Ready: {health.ready_services}/{health.total_services}</div>
          <div>Degraded: {health.degraded_services}</div>
          <div>Last checked: {formatTimestamp(health.last_checked_at)}</div>
        </div>
        <div className="grid gap-2 md:grid-cols-3">
          {[health.control_plane, health.archon_mcp, health.observability_replay].map((service) => (
            <div key={service.key} className={cn("rounded-lg border border-gray-700/60 px-3 py-2", glassCard.blur.sm, "bg-black/20")}>
              <div className="font-medium text-white">{service.label}</div>
              <div className="mt-1 text-xs text-gray-400">Status: {service.status}</div>
              <div className="text-xs text-gray-400">Reachable: {service.reachable ? "yes" : "no"}</div>
              <div className="text-xs text-gray-400">Latency: {service.latency_ms ?? "—"} ms</div>
              <div className="text-xs text-gray-400">Issues: {service.issues.length > 0 ? service.issues.join(", ") : "none"}</div>
            </div>
          ))}
        </div>
      </div>
    ) : (
      <div className="text-xs text-gray-400">Platform service health unavailable.</div>
    )}
  </Card>
);

const ChannelHealthSection = ({
  telegram,
  openclaw,
}: {
  telegram?: TelegramChannelHealth;
  openclaw?: OpenClawChannelHealth;
}) => (
  <div className="grid gap-3 md:grid-cols-2">
    <Card className="space-y-2" glowColor="blue" glowSize="sm">
      <div className="text-sm font-medium text-white">Telegram Channel</div>
      {telegram ? (
        <div className="space-y-1 text-xs text-gray-300">
          <div>Status: <span className="text-sky-200">{telegram.status}</span></div>
          <div>Send ready: {telegram.send_ready ? "yes" : "no"}</div>
          <div>Digest ready: {telegram.digest_ready ? "yes" : "no"}</div>
          <div>
            Schedule: {String(telegram.digest_schedule_hour).padStart(2, "0")}:
            {String(telegram.digest_schedule_minute).padStart(2, "0")} ({telegram.digest_timezone})
          </div>
          <div>Lookback: {telegram.digest_lookback_minutes} min</div>
          <div>Scheduler: {telegram.digest_scheduler_enabled ? "enabled" : "disabled"}</div>
          <div>Replay ready: {telegram.observability_replay_ready ? "yes" : "no"}</div>
          <div>Next due: {telegram.next_due_at ?? "—"}</div>
          <div>Last digest: {telegram.last_digest_key ?? "—"}</div>
          <div>Issues: {telegram.issues.length > 0 ? telegram.issues.join(", ") : "none"}</div>
        </div>
      ) : (
        <div className="text-xs text-gray-400">Channel health unavailable.</div>
      )}
    </Card>
    <Card className="space-y-2" glowColor="purple" glowSize="sm">
      <div className="text-sm font-medium text-white">OpenClaw Channel</div>
      {openclaw ? (
        <div className="space-y-1 text-xs text-gray-300">
          <div>Status: <span className="text-purple-200">{openclaw.status}</span></div>
          <div>Ingress ready: {openclaw.ingest_ready ? "yes" : "no"}</div>
          <div>Replay guard: {openclaw.replay_guard_strategy}</div>
          <div>Replay window: {openclaw.replay_window_minutes} min</div>
          <div>Sequence guard: {openclaw.sequence_guard_enabled ? "enabled" : "disabled"}</div>
          <div>Conversation policy: {openclaw.conversational_policy_enabled ? "enabled" : "disabled"}</div>
          <div>Issues: {openclaw.issues.length > 0 ? openclaw.issues.join(", ") : "none"}</div>
        </div>
      ) : (
        <div className="text-xs text-gray-400">Channel health unavailable.</div>
      )}
    </Card>
  </div>
);

function buildLatestRunByTask(runs: BootstrapPlanExecutionRun[]): Map<string, BootstrapPlanExecutionRun> {
  const latestByTask = new Map<string, BootstrapPlanExecutionRun>();
  for (const run of runs) {
    const previous = latestByTask.get(run.task_id);
    if (!previous || (previous.started_at ?? "") < (run.started_at ?? "")) {
      latestByTask.set(run.task_id, run);
    }
  }
  return latestByTask;
}

const BootstrapPlanRunsSection = ({ plan }: { plan: BootstrapPlan }) => {
  const { data: runs = [], isLoading } = useBootstrapPlanExecutionRuns(plan.id, 6);
  const { data: traceEvents = [] } = useBootstrapPlanTraceEvents(plan.id, 6);
  const latestRunByTask = buildLatestRunByTask(runs);

  return (
    <div className="space-y-3">
      {(plan.created_tasks?.length ?? 0) > 0 ? (
        <div className="space-y-2">
          <div className="flex items-center gap-2 text-sm font-medium text-white">
            <Sparkles className="w-4 h-4 text-cyan-300" />
            Materialized Tasks
          </div>
          <div className="grid gap-2">
            {plan.created_tasks?.slice(0, 6).map((task) => {
              const run = latestRunByTask.get(task.id);
              return (
                <div
                  key={task.id}
                  className={cn(
                    "rounded-lg border border-gray-700/60 px-3 py-2 text-sm",
                    glassCard.blur.sm,
                    "bg-black/20",
                  )}
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="font-medium text-white">{task.title}</div>
                    <div className="text-xs text-cyan-200">{task.status}</div>
                  </div>
                  <div className="mt-1 text-xs text-gray-400">
                    {task.plan_key ? `Plan key: ${task.plan_key}` : "Derived task"}
                  </div>
                  <div className="mt-2 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
                    <div>Latest run: {run?.id ?? "—"}</div>
                    <div>Run status: {run?.status ?? "not-started"}</div>
                    <div>Stage: {run?.stage ?? "—"}</div>
                    <div>Started: {run ? formatTimestamp(run.started_at) : "Not started"}</div>
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      <div className="flex items-center gap-2 text-sm font-medium text-white">
        <Sparkles className="w-4 h-4 text-purple-300" />
        Recent Execution Runs
      </div>
      {isLoading ? (
        <div className="rounded-lg border border-gray-700/60 bg-black/20 px-3 py-3 text-sm text-gray-400">
          Loading recent runs...
        </div>
      ) : runs.length === 0 ? (
        <div className="rounded-lg border border-gray-700/60 bg-black/20 px-3 py-3 text-sm text-gray-400">
          No execution runs have been correlated to this bootstrap plan yet.
        </div>
      ) : (
        <div className="grid gap-2">
          {runs.map((run) => (
            <div
              key={run.id}
              className={cn(
                "rounded-lg border border-gray-700/60 px-3 py-2 text-sm",
                glassCard.blur.sm,
                "bg-black/20",
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-medium text-white">{run.id}</div>
                <div className="flex flex-wrap items-center gap-2 text-xs">
                  <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-cyan-200">
                    {run.stage}
                  </span>
                  <span className="rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-emerald-200">
                    {run.status}
                  </span>
                </div>
              </div>
              <div className="mt-1 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
                <div>Task: {run.task_id}</div>
                <div>Model: {run.model ?? "Unknown"}</div>
                <div>Started: {formatTimestamp(run.started_at)}</div>
                <div>Session: {run.session_id ?? "—"}</div>
              </div>
            </div>
          ))}
        </div>
      )}

      <div className="space-y-2">
        <div className="flex items-center gap-2 text-sm font-medium text-white">
          <Sparkles className="w-4 h-4 text-amber-300" />
          Recent Trace Events
        </div>
        {traceEvents.length === 0 ? (
          <div className="rounded-lg border border-gray-700/60 bg-black/20 px-3 py-3 text-sm text-gray-400">
            No recent unified trace events have been replayed for this bootstrap plan yet.
          </div>
        ) : (
          <div className="grid gap-2">
            {traceEvents.map((event) => (
              <div
                key={event.id}
                className={cn(
                  "rounded-lg border border-gray-700/60 px-3 py-2 text-sm",
                  glassCard.blur.sm,
                  "bg-black/20",
                )}
              >
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="font-medium text-white">{event.event}</div>
                  <div className="text-xs text-amber-200">{event.source}</div>
                </div>
                <div className="mt-1 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
                  <div>Task: {event.taskId ?? "—"}</div>
                  <div>Agent: {event.agentId ?? "—"}</div>
                  <div>Run: {event.executionRunId ?? event.runId ?? "—"}</div>
                  <div>At: {formatTimestamp(event.timestamp)}</div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

const ExternalRequestsSection = ({ requests }: { requests: ExternalRequest[] }) => (
  <div className="space-y-2">
    <div className="flex items-center gap-2 text-sm font-medium text-white">
      <Sparkles className="w-4 h-4 text-sky-300" />
      Recent External Requests
    </div>
    {requests.length === 0 ? (
      <div className="rounded-lg border border-gray-700/60 bg-black/20 px-3 py-3 text-sm text-gray-400">
        No recent external requests for this project.
      </div>
    ) : (
      <div className="grid gap-2">
        {requests.map((request) => (
          <div key={request.id} className={cn("rounded-lg border border-gray-700/60 px-3 py-2 text-sm", glassCard.blur.sm, "bg-black/20")}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-medium text-white">{request.title}</div>
              <div className="text-xs text-sky-200">{request.status}</div>
            </div>
            <div className="mt-1 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
              <div>Channel: {request.source_channel}</div>
              <div>Type: {request.request_type}</div>
              <div>Modality: {request.input_modality ?? "—"}</div>
              <div>Materialized: {request.materialize_as}</div>
              <div>Linked task: {request.linked_task_id ?? "—"}</div>
            </div>
            {request.input_text ? (
              <div className="mt-2 text-xs text-gray-300">
                {request.input_text}
              </div>
            ) : null}
          </div>
        ))}
      </div>
    )}
  </div>
);

const ArchitectRequestsSection = ({ requests }: { requests: ExternalRequest[] }) => (
  <div className="space-y-2">
    <div className="flex items-center gap-2 text-sm font-medium text-white">
      <Sparkles className="w-4 h-4 text-fuchsia-300" />
      Architect Requests
    </div>
    {requests.length === 0 ? (
      <div className="rounded-lg border border-gray-700/60 bg-black/20 px-3 py-3 text-sm text-gray-400">
        No recent architect requests for this project.
      </div>
    ) : (
      <div className="grid gap-2">
        {requests.map((request) => {
          const plan = request.payload?.architect_plan;
          const snapshot = request.payload?.openclaw_sequence_snapshot;
          return (
            <div key={request.id} className={cn("rounded-lg border border-gray-700/60 px-3 py-2 text-sm", glassCard.blur.sm, "bg-black/20")}>
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="font-medium text-white">{request.title}</div>
                <div className="text-xs text-fuchsia-200">{request.status}</div>
              </div>
              <div className="mt-1 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
                <div>Provider: {plan?.resolved_provider ?? "—"}</div>
                <div>Strategy: {plan?.strategy ?? "—"}</div>
                <div>Materialize: {plan?.recommended_materialization ?? request.materialize_as}</div>
                <div>Sequence step: {snapshot?.step_key ?? "—"}</div>
                <div>Modality: {request.input_modality ?? "—"}</div>
                <div>Linked task: {request.linked_task_id ?? "—"}</div>
              </div>
              {(request.payload?.clarification_response_to_request_id || request.payload?.clarification_resolved_by_request_id) ? (
                <div className="mt-1 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
                  <div>Response to request: {request.payload?.clarification_response_to_request_id ?? "—"}</div>
                  <div>Resolved by request: {request.payload?.clarification_resolved_by_request_id ?? "—"}</div>
                </div>
              ) : null}
              <div className="mt-2 text-xs text-gray-300">
                {plan?.summary ?? request.summary}
              </div>
              {plan?.clarifying_questions?.length ? (
                <div className="mt-1 text-xs text-gray-400">
                  {plan.clarifying_questions.join(" • ")}
                </div>
              ) : null}
              {request.payload?.clarification_answers?.length ? (
                <div className="mt-1 text-xs text-gray-400">
                  Clarification answers: {request.payload.clarification_answers.join(" • ")}
                </div>
              ) : null}
              {request.input_text ? (
                <div className="mt-1 text-xs text-gray-400">
                  {request.input_text}
                </div>
              ) : null}
            </div>
          );
        })}
      </div>
    )}
  </div>
);

const ApprovalRequestsSection = ({ approvals }: { approvals: ApprovalRequest[] }) => (
  <div className="space-y-2">
    <div className="flex items-center gap-2 text-sm font-medium text-white">
      <Sparkles className="w-4 h-4 text-amber-300" />
      Pending Approvals
    </div>
    {approvals.length === 0 ? (
      <div className="rounded-lg border border-gray-700/60 bg-black/20 px-3 py-3 text-sm text-gray-400">
        No recent approval requests for this project.
      </div>
    ) : (
      <div className="grid gap-2">
        {approvals.map((approval) => (
          <div key={approval.id} className={cn("rounded-lg border border-gray-700/60 px-3 py-2 text-sm", glassCard.blur.sm, "bg-black/20")}>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="font-medium text-white">{approval.title}</div>
              <div className="text-xs text-amber-200">{approval.status}</div>
            </div>
            <div className="mt-1 grid gap-1 text-xs text-gray-400 md:grid-cols-2">
              <div>Requested by: {approval.requested_by}</div>
              <div>Channel: {approval.requested_channel}</div>
              <div>Task: {approval.task_id ?? "—"}</div>
              <div>Run: {approval.execution_run_id ?? "—"}</div>
            </div>
          </div>
        ))}
      </div>
    )}
  </div>
);

export const BootstrapPlansTab = ({ projectId }: BootstrapPlansTabProps) => {
  const { data: plans = [], isLoading, isError, refetch, error } = useBootstrapPlans(projectId);
  const { data: externalRequests = [] } = useProjectExternalRequests(projectId, 4);
  const { data: architectRequests = [] } = useProjectArchitectRequests(projectId, 4);
  const { data: approvalRequests = [] } = useProjectApprovalRequests(projectId, 4);
  const { data: platformServiceHealth } = usePlatformServiceHealth();
  const { data: channelHeartbeat } = useExternalChannelHeartbeat();
  const materializeMutation = useMaterializeBootstrapPlanBacklog(projectId);

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <Loader2 className="w-8 h-8 animate-spin text-cyan-400" />
      </div>
    );
  }

  if (isError) {
    return (
      <Card className="p-6 text-center space-y-3" glowColor="red" glowSize="sm">
        <div className="text-red-400 font-medium">Failed to load bootstrap plans</div>
        <div className="text-sm text-gray-400">{error instanceof Error ? error.message : "Unknown error"}</div>
        <div>
          <Button variant="outline" onClick={() => refetch()}>
            Try Again
          </Button>
        </div>
      </Card>
    );
  }

  if (plans.length === 0) {
    return (
      <Card className="p-6 text-center space-y-3">
        <Workflow className="w-8 h-8 mx-auto text-cyan-400" />
        <div className="text-white font-medium">No bootstrap plans</div>
        <div className="text-sm text-gray-400">
          This project has not recorded any persisted bootstrap plan yet.
        </div>
      </Card>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h3 className="text-lg font-semibold text-white">Bootstrap Plans</h3>
          <p className="text-sm text-gray-400">
            Inspect persisted planning records and materialize deferred backlog items.
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => refetch()}>
          <RefreshCw className="w-4 h-4 mr-2" />
          Refresh
        </Button>
      </div>

      <ServiceHealthSummary health={platformServiceHealth} />
      <ChannelHeartbeatSummary heartbeat={channelHeartbeat} />
      <ChannelHealthSection telegram={channelHeartbeat?.telegram} openclaw={channelHeartbeat?.openclaw} />

      {plans.map((plan) => {
        const backlogItems = Array.isArray(plan.metadata?.backlog_items)
          ? plan.metadata?.backlog_items as unknown[]
          : [];

        return (
          <Card key={plan.id} className="space-y-4" glowColor="cyan" glowSize="sm">
            <div className="flex flex-col gap-3 md:flex-row md:items-start md:justify-between">
              <div className="space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold text-white">{plan.template}</span>
                  <span className="rounded-full border border-cyan-500/30 bg-cyan-500/10 px-2 py-0.5 text-xs text-cyan-200">
                    {plan.status}
                  </span>
                  <span className="rounded-full border border-purple-500/30 bg-purple-500/10 px-2 py-0.5 text-xs text-purple-200">
                    {plan.resolved_provider}
                  </span>
                  <span className="rounded-full border border-gray-500/30 bg-black/20 px-2 py-0.5 text-xs text-gray-300">
                    {plan.strategy}
                  </span>
                </div>
                <div className="text-xs text-gray-400 break-all">Plan ID: {plan.id}</div>
                <div className="grid gap-2 text-sm text-gray-300 md:grid-cols-2">
                  <div>Project type: {plan.project_type}</div>
                  <div>Bootstrap policy: {plan.bootstrap_policy}</div>
                  <div>Created tasks: {plan.created_tasks?.length ?? 0}</div>
                  <div>Deferred backlog items: {backlogItems.length}</div>
                </div>
              </div>

              <Button
                variant="green"
                size="sm"
                loading={materializeMutation.isPending}
                disabled={materializeMutation.isPending || backlogItems.length === 0}
                onClick={() => materializeMutation.mutate(plan.id)}
              >
                <GitBranchPlus className="w-4 h-4 mr-2" />
                Materialize Backlog
              </Button>
            </div>

            <BootstrapPlanRunsSection plan={plan} />
            <ArchitectRequestsSection requests={architectRequests} />
            <ExternalRequestsSection requests={externalRequests} />
            <ApprovalRequestsSection approvals={approvalRequests} />
          </Card>
        );
      })}
    </div>
  );
};

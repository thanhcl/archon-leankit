# Engine Convergence Plan: task_engine + agent_work_orders → Single Execution Authority

**Status**: Draft
**Date**: 2026-03-25
**Workstream**: C (ref: ADR-0005)
**Effort estimate**: 4–6 weeks (3 engineers)

---

## 1. Problem Statement

LeanKit currently runs **two independent execution engines**:

| | `task_engine` | `agent_work_orders` |
|---|---|---|
| **Port** | Embedded in main server (8181) | Independent microservice (8053) |
| **Entry trigger** | Polls DB for `assigned` tasks | HTTP POST to create work order |
| **Execution unit** | `archon_tasks` row | `AgentWorkOrder` object (own state store) |
| **Git isolation** | Shared checkout or git-worktree | Git branch or git-worktree |
| **GitHub** | None native | Full PR creation, issue linking |
| **State store** | Supabase `archon_tasks` + `execution_runs` | Memory / file / Supabase (own tables) |
| **Review pipeline** | Multi-stage (architect + independent code review) | PRP review step only |
| **Budget/cost** | Full tracking, daily + sprint limits | None |
| **Retry** | Exponential backoff, escalation | None |
| **Learning injection** | Yes (KB chunks, learnings, code patterns) | No |
| **Notification** | Full (Telegram, Discord, etc.) | None |

Per ADR-0005, the system must have **ONE execution authority**. Having two engines creates:

- Split state: work done by agent_work_orders is invisible to task_engine metrics and budget tracking
- Duplicate runner logic: both spawn Claude Code CLI in parallel codepaths
- Conflicting lifecycle: two different state machines for "running" and "done"
- Maintenance burden: fixes must be applied twice

---

## 2. Capability Inventory

### 2.1 Capabilities in `task_engine` only

- DB-driven polling loop (`todo → assigned → executing`)
- Multi-stage review pipeline (architect-review → code-review → human review)
- Per-project capacity limits and global capacity tracking
- Cost/budget tracking with daily + sprint limits and enforcement
- Exponential backoff retry with configurable escalation
- Learning injection into prompts (KB, learnings, code patterns)
- Prompt building with task context, files_in_scope, acceptance criteria
- Execution health monitor (loop detection, excessive tool use)
- Task boundary validation (files_in_scope enforcement)
- Task conflict detection (shared checkout file overlap)
- Execution run audit trail (`archon_execution_runs` table)
- Notifier integration (Telegram, Discord, etc.)
- Codex runner support alongside Claude Code
- Engine policies per-project (model routing, review overrides, isolation mode)
- Bug task auto-creation from review findings
- PID watchdog (orphan recovery on restart)

### 2.2 Capabilities in `agent_work_orders` only

- **External request intake**: accepts work via HTTP from outside Archon (no task pre-exists)
- **Structured 6-step workflow**: create-branch → planning → execute → commit → create-pr → prp-review
- **GitHub PR creation**: first-class, with issue linking, PR URL returned
- **SSE log streaming**: real-time byte-level log streaming to frontend
- **Repository management**: configured repositories with verification, preferences
- **Git worktree + branch sandbox factory**: abstracted sandbox creation
- **Telemetry store**: SQLite-based local telemetry for long-lived executions
- **User-prompt injection mid-run** (Phase 2): send prompts to running agent
- **Planning phase**: separate planning step before execution

### 2.3 Overlapping Capabilities (both implement)

| Capability | task_engine implementation | agent_work_orders implementation |
|---|---|---|
| Claude Code CLI spawning | `cc_spawner.py` (CCSpawner) | `agent_cli_executor.py` (AgentCliExecutor) |
| Git worktree isolation | `cc_spawner.py` — `--isolation git-worktree` | `sandbox_manager/git_worktree_sandbox.py` |
| Task/order lifecycle state machine | 15-state machine in `task_engine.py` | 4-state machine (pending→running→completed→failed) |
| Retry on failure | Exponential backoff in `task_engine.py` | None (no retry) |
| Execution timeout | Process-level + task-level timeout | Single `EXECUTION_TIMEOUT` env var |
| Result parsing | Structured JSON output parsing in `cc_spawner.py` | Step history JSON in `workflow_orchestrator.py` |
| Health checking | `health_monitor.py` | `GET /health` endpoint with dependency checks |
| Supabase persistence | All task tables | Optional (`STATE_STORAGE_TYPE=supabase`) |

---

## 3. Consumer Map

### 3.1 Consumers of `agent_work_orders`

**Backend:**

| Consumer | File | What it uses |
|---|---|---|
| API gateway proxy | `python/src/server/api_routes/agent_work_orders_proxy.py` | Proxies all `/api/agent-work-orders/*` to port 8053 |
| Service discovery | `python/src/server/config/service_discovery.py` | `get_agent_work_orders_url()` — resolves Docker vs local URL |
| Env aliases | `python/src/server/config/env_aliases.py` | `get_agent_work_orders_port()` — port config aliasing |
| Main server | `python/src/server/main.py` | Includes `agent_work_orders_router` |

**Frontend (32 files):**

| Consumer | Path | What it uses |
|---|---|---|
| Work orders view | `src/features/agent-work-orders/views/AgentWorkOrdersView.tsx` | Full CRUD for work orders |
| Work order detail | `src/features/agent-work-orders/views/AgentWorkOrderDetailView.tsx` | Detail + SSE log streaming |
| Create modal | `src/features/agent-work-orders/components/CreateWorkOrderModal.tsx` | POST new work order |
| Repository sidebar | `src/features/agent-work-orders/components/SidebarRepositoryCard.tsx` | Repository list |
| Work order table | `src/features/agent-work-orders/components/WorkOrderTable.tsx` + `WorkOrderRow.tsx` | List + status |
| Real-time stats | `src/features/agent-work-orders/components/RealTimeStats.tsx` | SSE stats stream |
| State store | `src/features/agent-work-orders/state/agentWorkOrdersStore.ts` | Zustand state |
| SSE slice | `src/features/agent-work-orders/state/slices/sseSlice.ts` | SSE slice |
| Service | `src/features/agent-work-orders/services/agentWorkOrdersService.ts` | API calls |
| Repository service | `src/features/agent-work-orders/services/repositoryService.ts` | Repository CRUD |
| Query hooks | `src/features/agent-work-orders/hooks/useAgentWorkOrderQueries.ts` | TanStack Query |
| Pages | `src/pages/AgentWorkOrdersPage.tsx`, `AgentWorkOrderDetailPage.tsx` | Route pages |
| Navigation | `src/components/layout/Navigation.tsx` | Nav link |
| App routes | `src/App.tsx` | Route definition |
| Settings | `src/contexts/SettingsContext.tsx`, `FeaturesSection.tsx` | Feature flag |
| Style guide | `src/features/style-guide/` (4 files) | Example patterns |

**MCP tools:** None — agent_work_orders has no MCP tool bindings.

### 3.2 Consumers of `task_engine`

**Backend:**

| Consumer | File | What it uses |
|---|---|---|
| Engine API | `python/src/server/api_routes/engine_api.py` | Config, status, capabilities endpoints |
| Main server startup | `python/src/server/main.py` | `TaskEngine` daemon started at startup |
| Execution runs API | `python/src/server/api_routes/execution_runs_api.py` | Reads/writes `archon_execution_runs` |
| External requests API | `python/src/server/api_routes/external_requests_api.py` | `ExternalRequestService` → creates tasks that task_engine picks up |
| Bootstrap plan service | `python/src/server/services/projects/bootstrap_plan_service.py` | Creates tasks that task_engine executes |
| All task lifecycle transitions | Multiple services | `transition_task()` calls feed into engine polling |

**Frontend:** Full project/task management UI (`src/features/projects/tasks/`).

**MCP tools:** `python/src/mcp_server/features/tasks/task_tools.py` — manages tasks that task_engine executes.

---

## 4. Overlap Analysis

### 4.1 Claude Code CLI Spawning

Both systems independently spawn Claude Code with `--dangerously-skip-permissions`. `task_engine` (via `CCSpawner`) has richer behavior:

- Per-task model selection (Opus/Sonnet/Haiku based on complexity + priority)
- Token profile loading from JSON config
- Real-time JSON stream parsing (tool_use, thinking, result events)
- `--isolation git-worktree` flag passed to CC itself
- Fallback to Opus on non-Opus failure

`agent_work_orders` (via `AgentCliExecutor`) has simpler execution but adds:

- Step-level result tracking (per workflow step)
- Mid-run prompt injection (Phase 2)

**Decision**: Absorb agent_work_orders' step tracking and prompt injection into `CCSpawner` / a new `RunnerSession` abstraction.

### 4.2 Git Worktree Isolation

`task_engine` delegates worktree creation to Claude Code CLI (`--isolation git-worktree`).
`agent_work_orders` manages worktrees directly in Python (`SandboxManager`).

The `agent_work_orders` approach gives more control (can create branch before execution, clean up after, capture git progress). This abstraction is worth preserving as a standalone `SandboxManager` component that task_engine can use.

**Decision**: Extract `sandbox_manager/` from agent_work_orders into a shared `python/src/server/services/engine/sandbox_manager.py` module.

### 4.3 Lifecycle / State Machine

`task_engine` has a 15-state machine backed by `archon_tasks`.
`agent_work_orders` has a 4-state (pending/running/completed/failed) machine with its own state store (memory/file/Supabase).

Running two state machines for what is conceptually "executing a coding task" is the root of the convergence problem. After convergence, all work must be represented as `archon_tasks` rows.

**Decision**: agent_work_orders creates `archon_tasks` rows; task_engine executes them.

### 4.4 GitHub PR Creation

`task_engine` has no GitHub integration. This is a **gap** — agent_work_orders provides unique value here. After convergence, task_engine must support PR creation as a post-execution step.

**Decision**: Port `github_integration/github_client.py` to `python/src/server/services/engine/github_publisher.py`.

### 4.5 SSE Log Streaming

`task_engine` streams events via the Notifier (push) and execution_run metadata.
`agent_work_orders` has a dedicated SSE endpoint streaming real-time bytes.

The frontend SSE feature is heavily used (`sseSlice.ts`, `AgentWorkOrderDetailView`). This must be preserved in the converged system.

**Decision**: Add SSE streaming endpoint to execution_runs API or task execution runs in task_engine.

---

## 5. Convergence Plan

### Guiding Principle

> `task_engine` is the single execution authority. `agent_work_orders` becomes a **thin intake adapter** that translates external requests into `archon_tasks` and delegates execution to `task_engine`. Eventually the adapter is absorbed entirely.

### Phase 0: Stabilize (no behavioral change)

**Goal**: Create clear boundaries so convergence doesn't break anything.
**Effort**: 0.5 week

- [ ] Document the current API contracts for both engines in `docs/api-contracts/`
- [ ] Add integration test that POSTs to agent_work_orders and verifies work order reaches `completed`
- [ ] Add integration test that creates a task and verifies task_engine executes it to `done`
- [ ] Verify agent_work_orders feature flag `ENABLE_AGENT_WORK_ORDERS=false` disables cleanly

### Phase 1: Extract Shared Primitives

**Goal**: Avoid duplicating fixes across two codebases.
**Effort**: 1 week

**Steps:**

1. **Extract `SandboxManager`** from `agent_work_orders/sandbox_manager/` to
   `python/src/server/services/engine/sandbox_manager.py`
   - `SandboxProtocol` (abstract interface)
   - `GitBranchSandbox`
   - `GitWorktreeSandbox`
   - `SandboxFactory`

2. **Extract `GitHubClient`** from `agent_work_orders/github_integration/` to
   `python/src/server/services/engine/github_publisher.py`

3. **Extract `AgentCliExecutor`** step-tracking into `CCSpawner` as `StepCallback` hooks
   (allows step-level result tracking without full rewrite)

4. **Update `agent_work_orders`** to import from the extracted shared modules
   (no behavior change, just import paths)

**Consumers affected:** None externally — internal refactor only.

### Phase 2: Bridge — agent_work_orders Creates archon_tasks

**Goal**: Make agent_work_orders a thin intake layer; task_engine executes the work.
**Effort**: 1.5 weeks

**Steps:**

1. **New task metadata fields** for agent_work_orders provenance:
   - `source_channel: str | None` — `"agent_work_orders"`
   - `external_work_order_id: str | None` — original work order ID
   - `github_pr_url: str | None` — set by post-execution GitHub publisher
   - `sandbox_type: str | None` — `"git_branch"` | `"git_worktree"`

2. **`WorkflowOrchestrator` changes** in agent_work_orders:
   - On work order creation → call `POST /api/tasks` (Archon server) to create `archon_task`
   - Store returned `task_id` in `AgentWorkOrder.archon_task_id`
   - After task transitions to `done` → copy result fields back to work order state

3. **`task_engine` changes**:
   - Add `post_execution_hook` support: after `SUCCESS` + `architect-review APPROVE`:
     - If task has `source_channel == "agent_work_orders"` and `sandbox_type != None`:
       - Run `GitHubPublisher.create_pr()` → store `github_pr_url` in task
       - Notify work order service of completion

4. **SSE bridge**: task_engine execution log events → forwarded to agent_work_orders log buffer for SSE streaming consumers

5. **Frontend parity check**: verify `AgentWorkOrderDetailView` still shows correct status (now reads from both work order state + linked `archon_task`)

**Consumers affected:**
- `agent_work_orders_proxy.py`: no change (still proxies)
- Frontend: `agentWorkOrdersService.ts` may need to also fetch linked task for richer status

### Phase 3: Deprecate agent_work_orders Execution Path

**Goal**: All execution flows through task_engine; agent_work_orders is only intake + UI.
**Effort**: 1.5 weeks

**Steps:**

1. **Remove `workflow_engine/` execution from agent_work_orders**:
   - `WorkflowOrchestrator` becomes a thin stub: create task → poll task status → return
   - Remove `AgentCliExecutor`, `SandboxFactory`, `WorkflowOperations` from agent_work_orders
   - These are now in the shared engine modules from Phase 1

2. **Move repository management to main server**:
   - `agent_work_orders/state_manager/repository_config_repository.py` → `python/src/server/services/projects/repository_service.py`
   - New table: `archon_repositories` in Supabase (replaces file/memory storage)
   - New API: `GET/POST/PATCH/DELETE /api/repositories`

3. **Add GitHub PR fields to execution_runs API**:
   - `github_pr_url`, `git_branch`, `git_worktree_path` on `archon_execution_runs`
   - Frontend can read from execution_runs instead of work order step history

4. **Add SSE endpoint to main server**:
   - `GET /api/execution-runs/{run_id}/logs/stream` → SSE stream of engine log events
   - Frontend `sseSlice.ts` updated to use this endpoint

5. **Feature-flag transition**:
   - Keep `ENABLE_AGENT_WORK_ORDERS` working, but now it only controls UI visibility, not execution path
   - Add deprecation warning in agent_work_orders proxy logs

**Consumers affected:**
- Frontend SSE: update `sseSlice.ts` to new SSE endpoint
- Frontend detail view: read `github_pr_url` from execution_run, not step history
- All downstream integrations that POST to `/api/agent-work-orders` still work (proxy unchanged)

### Phase 4: Decommission agent_work_orders Microservice

**Goal**: Eliminate the separate process; bring remaining intake logic into main server.
**Effort**: 1 week

**Steps:**

1. **Move intake API into main server**:
   - New router: `python/src/server/api_routes/agent_work_orders_api.py`
   - Replaces proxy with direct implementation
   - Handles `POST /api/agent-work-orders` → creates `archon_task` + optional repository setup
   - GET/list endpoints read from `archon_tasks` filtered by `source_channel=agent_work_orders`

2. **Remove microservice infrastructure**:
   - Remove `python/src/agent_work_orders/` module
   - Remove `python/Dockerfile.agent-work-orders`
   - Remove `agent-work-orders` service from `docker-compose.yml`
   - Remove `agent_work_orders_proxy.py`
   - Remove `AGENT_WORK_ORDERS_PORT`, `ENABLE_AGENT_WORK_ORDERS` env vars (or keep as no-ops)
   - Remove `get_agent_work_orders_url()` from `service_discovery.py`

3. **Update frontend**:
   - `agentWorkOrdersService.ts` → point directly to main server endpoints
   - `repositoryService.ts` → point to new `/api/repositories` endpoint
   - Remove SSE ↔ port 8053 connectivity assumption

4. **Clean up tests**:
   - Remove `python/tests/agent_work_orders/` directory (28 test files)
   - Add equivalent coverage in `python/tests/server/api_routes/test_agent_work_orders_api.py`

5. **Update Makefile and documentation**:
   - Remove `make agent-work-orders` target (or repurpose)
   - Remove `make dev-work-orders` target
   - Update `README.md` to remove agent_work_orders service from architecture diagram

**Consumers affected (all require updates):**
- `docker-compose.yml`: remove service
- `Makefile`: remove targets
- `python/src/server/main.py`: remove proxy router
- `python/src/server/config/service_discovery.py`: remove agent_work_orders entry
- Frontend: update API base URLs

---

## 6. Migration Table (Consumer-by-Consumer)

| Consumer | Current dependency | Post-convergence | Phase |
|---|---|---|---|
| `agent_work_orders_proxy.py` | Routes to port 8053 | Replaced by direct API router | 4 |
| `service_discovery.py` | `get_agent_work_orders_url()` | Remove function, no-op env var | 4 |
| `env_aliases.py` | `get_agent_work_orders_port()` | Remove function | 4 |
| `main.py` | Includes proxy router | Include new direct router | 4 |
| `docker-compose.yml` | `archon-agent-work-orders` service | Remove service | 4 |
| `Dockerfile.agent-work-orders` | Standalone image | Delete | 4 |
| `Makefile` | `agent-work-orders`, `dev-work-orders` targets | Remove | 4 |
| Frontend `agentWorkOrdersService.ts` | HTTP to `/api/agent-work-orders/*` | No URL change (same proxy path) | 4 |
| Frontend `sseSlice.ts` | SSE to port 8053 logs stream | SSE to `/api/execution-runs/{id}/logs/stream` | 3 |
| Frontend `AgentWorkOrderDetailView.tsx` | Step history from agent_work_orders | Execution runs + PR fields | 3 |
| Frontend `repositoryService.ts` | `/api/agent-work-orders/repositories` | `/api/repositories` | 4 |
| `python/tests/agent_work_orders/` (28 files) | Tests for standalone service | Replace with server integration tests | 4 |
| `task_engine.py` | No GitHub integration | Add post-execution `github_publisher` hook | 2 |
| `cc_spawner.py` | No step tracking | Add `StepCallback` hooks | 1 |

---

## 7. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| SSE streaming broken during migration | Medium | High | Maintain both endpoints until FE is updated |
| Frontend work order detail loses step history | Medium | Medium | Map step history to execution_runs stages |
| GitHub PR creation regresses | Low | High | Integration test in Phase 2 verifying PR URL |
| agent_work_orders state store migration (existing orders) | Low | Low | Only applies to in-flight orders; short TTL |
| Budget tracking gap (new orders not tracked) | Low | Medium | Immediately enabled in Phase 2 (archon_tasks) |
| Test coverage gap during decommission | Medium | Medium | Write new tests before deleting old ones |
| External callers of port 8053 directly | Low | High | Audit external integrations; enforce proxy pattern |

---

## 8. Out of Scope

- Codex runner changes (independent of convergence)
- Multi-agent orchestration (Phase 2 of agent_work_orders)
- E2B / Dagger sandbox support (Phase 2+)
- MCP tool bindings for agent_work_orders (not currently present)
- Notification channel changes

---

## 9. Success Criteria

- [ ] Zero references to `agent_work_orders` module in `python/src/server/`
- [ ] All work orders visible in `archon_tasks` (same table as all other tasks)
- [ ] Budget tracking applies to externally-triggered work orders
- [ ] GitHub PR URL stored and visible in task detail view
- [ ] SSE log streaming works from main server execution endpoint
- [ ] Repository management accessible from main server `/api/repositories`
- [ ] `docker-compose.yml` has no `archon-agent-work-orders` service
- [ ] All 28 existing agent_work_orders test scenarios covered by new server tests
- [ ] `ENABLE_AGENT_WORK_ORDERS` env var retired or documented as no-op

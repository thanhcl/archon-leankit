# Implementation Plan: Archon-LeanKit V3 Task Engine

This is the canonical implementation plan for the Archon-LeanKit V3 project, covering
engine core capabilities, notification delivery refactor, plan governance surface, and
GitHub integration.

## Phase 1: Engine Core Infrastructure

Establish the core task engine capabilities: lifecycle state machine, execution priority,
cost budgeting, and knowledge-base injection before spawning tasks.

### B-P1-01: Task lifecycle and state machine

Implement the 15-state task lifecycle with guarded transitions and on-hold support for
all active states.

- **Status**: done
- **Priority**: high
- **Complexity**: medium

**Acceptance Criteria**:
- 15-state lifecycle enforced with guarded transitions
- on-hold allowed from all active states
- blocked_by enforcement before execution

### B-P1-02: Priority queue and model routing

Execute high-priority tasks before medium, medium before low. Route Opus for complex,
Sonnet for medium, Haiku for simple tasks.

- **Status**: done
- **Priority**: high
- **Complexity**: simple
- **Dependencies**: B-P1-01

**Acceptance Criteria**:
- Tasks dispatched in priority order within a project
- Model assigned based on complexity field

### B-P1-03: Cost budgeting service

Enforce per-project per-sprint daily and total cost limits. Pause project when budget
exceeded.

- **Status**: done
- **Priority**: high
- **Complexity**: medium
- **Dependencies**: B-P1-02

**Acceptance Criteria**:
- Daily and sprint budget limits configurable
- Engine pauses project when limit reached
- Budget reset on new day/sprint

### B-P1-04: Context injection — KB and learnings

Query the knowledge base and inject relevant chunks and prior learnings into the task
prompt before spawning.

- **Status**: done
- **Priority**: medium
- **Complexity**: medium
- **Dependencies**: B-P1-01

**Acceptance Criteria**:
- Relevant KB chunks injected per task
- Prior learnings and code patterns included
- Injection metrics recorded in execution_result

### B-P1-05: Retry with failure context enrichment

On task failure, enrich the retry prompt with structured failure context so the engine
can learn from prior attempts.

- **Status**: done
- **Priority**: medium
- **Complexity**: simple
- **Dependencies**: B-P1-01

**Acceptance Criteria**:
- Failure reason captured in execution_result
- Retry prompt includes prior failure context

## Phase 2: Notification and Delivery Adapter

Decompose monolithic Notifier into canonical formatter, policy, adapter, and per-channel
transport seams. Phases implement ADR-0013.

### N-P2-01: Extract canonical notification formatter

Move Telegram text formatting and digest text building out of Notifier into a dedicated
NotificationFormatter service.

- **Status**: done
- **Priority**: high
- **Complexity**: medium

**Acceptance Criteria**:
- Notifier no longer owns digest text construction
- TelegramChannelService.build_digest() does not instantiate Notifier
- Existing digest preview/send endpoints behave the same

### N-P2-02: Extract outbound Telegram channel transport

Move Telegram HTTP transport into a TelegramChannel service reused by both Notifier and
TelegramChannelService digest sending.

- **Status**: done
- **Priority**: high
- **Complexity**: medium
- **Dependencies**: N-P2-01

**Acceptance Criteria**:
- Telegram transport implemented in exactly one place
- Notifier and TelegramChannelService both delegate to TelegramChannel
- Existing webhook and digest routes remain stable

### N-P2-03: Extract routing and delivery policy

Move immediate vs digest vs skip classification into a NotificationPolicy service.

- **Status**: done
- **Priority**: high
- **Complexity**: simple
- **Dependencies**: N-P2-01

**Acceptance Criteria**:
- Notifier contains no Telegram routing rules
- Digest/event routing centrally testable without network code
- Future channels can reuse the same routing model

### N-P2-04: Introduce NotificationAdapter

Provide one adapter boundary that fans out events and digests to configured channels and
returns structured per-channel delivery results.

- **Status**: done
- **Priority**: high
- **Complexity**: medium
- **Dependencies**: N-P2-02, N-P2-03

**Acceptance Criteria**:
- Notifier delegates to adapter rather than orchestrating channel specifics
- Per-channel delivery results are structured and inspectable
- Best-effort semantics preserved

### N-P2-05: Canonical digest pipeline

Create one canonical digest generation and delivery path reused by all scheduler modes.

- **Status**: done
- **Priority**: medium
- **Complexity**: medium
- **Dependencies**: N-P2-04

**Acceptance Criteria**:
- One digest content builder
- One outbound digest delivery path
- Scheduler-backed and caller-supplied digest flows share core code

### N-P2-06: Fallback scheduler seam and degradation semantics

Support future scheduled fallback jobs without elevating them into execution authority.
Delivery errors must not look like task failures.

- **Status**: done
- **Priority**: medium
- **Complexity**: medium
- **Dependencies**: N-P2-05

**Acceptance Criteria**:
- Code distinguishes event creation from notification delivery
- Delivery errors do not surface as task failures
- Future schedulers can reuse the digest path without Telegram-only code

## Phase 3: Plan Governance Surface

Add a Plan Tab to the Archon UI that visualises implementation plans, phase rollups,
acceptance criteria checklists, and dependency DAGs imported from canonical markdown.

### G-P3-01: Plan model tables and import API

Define database schema for plans, phases, items, dependencies, and item-task links.
Implement markdown parser and import endpoint.

- **Status**: done
- **Priority**: high
- **Complexity**: complex

**Acceptance Criteria**:
- project_implementation_plans, _phases, _items, _dependencies tables created
- POST /api/plans/import accepts markdown content or file_path
- Parser handles X-PN-NN item key format with status, priority, complexity, dependencies

### G-P3-02: Plan Tab UI — phase rollup and item list

Render phase-level rollup cards showing progress, cost, and quality signals alongside
an item list with status badges.

- **Status**: done
- **Priority**: high
- **Complexity**: medium
- **Dependencies**: G-P3-01

**Acceptance Criteria**:
- Plan Tab visible in project view
- Phase rollup shows item counts by status
- Items list with status, priority, complexity visible

### G-P3-03: Acceptance criteria checklist and dependency DAG

Show per-item acceptance criteria as an interactive checklist and render a dependency
graph for the selected phase.

- **Status**: done
- **Priority**: medium
- **Complexity**: medium
- **Dependencies**: G-P3-02

**Acceptance Criteria**:
- Acceptance criteria rendered as checklist per item
- Dependency DAG rendered for phase items
- Unmapped tasks report shown

### G-P3-04: Auto-link tasks to plan items

Scan task descriptions for `Ref: <item_key>` patterns and auto-populate plan_item_id
to link tasks to their corresponding plan items.

- **Status**: planned
- **Priority**: medium
- **Complexity**: simple
- **Dependencies**: G-P3-01

**Acceptance Criteria**:
- POST /api/plan-items/auto-link scans all tasks for Ref: patterns
- plan_item_id populated on matching tasks
- Rollup reflects linked task progress

## Phase 4: GitHub Integration

Surface GitHub context (commits, PRs, issues) directly in task cards to give the engine
full awareness of in-flight work and PR state.

### GH-P4-01: GitHub MCP server integration for task context

Integrate GitHub MCP server tools so the engine can look up commits, PRs, and issues
relevant to the current task.

- **Status**: done
- **Priority**: medium
- **Complexity**: medium

**Acceptance Criteria**:
- github_find_commits, github_find_pull_requests, github_find_issues available
- Engine injects relevant GitHub context into task prompts
- Task execution_result records GitHub artifacts referenced

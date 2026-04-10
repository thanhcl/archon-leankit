-- Migration: 040_create_agent_runtimes.sql
-- Purpose: Agent runtime self-registration table (Multica daemon pattern adoption).
-- Workstream: M-P1 (Agent Runtime Self-Registration)

CREATE TABLE IF NOT EXISTS archon_agent_runtimes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id UUID NULL REFERENCES archon_agent_definitions(id) ON DELETE SET NULL,
    device_name TEXT NOT NULL,
    version TEXT NULL,
    status TEXT NOT NULL DEFAULT 'online' CHECK (
        status IN ('online', 'offline', 'degraded')
    ),
    capabilities TEXT[] NOT NULL DEFAULT '{}',
    supported_runners TEXT[] NOT NULL DEFAULT '{}',
    max_concurrent_tasks INTEGER NOT NULL DEFAULT 1 CHECK (max_concurrent_tasks >= 1),
    current_task_count INTEGER NOT NULL DEFAULT 0 CHECK (current_task_count >= 0),
    last_heartbeat TIMESTAMPTZ NULL,
    registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unregistered_at TIMESTAMPTZ NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_runtimes_status
    ON archon_agent_runtimes(status);

CREATE INDEX IF NOT EXISTS idx_agent_runtimes_agent_id
    ON archon_agent_runtimes(agent_id);

CREATE INDEX IF NOT EXISTS idx_agent_runtimes_last_heartbeat
    ON archon_agent_runtimes(last_heartbeat DESC);

COMMENT ON TABLE archon_agent_runtimes IS
    'Registered agent runtime instances that can claim and execute tasks. Inspired by Multica daemon self-registration pattern.';

COMMENT ON COLUMN archon_agent_runtimes.status IS
    'Runtime health: online (active), offline (unreachable), degraded (missed heartbeats).';

COMMENT ON COLUMN archon_agent_runtimes.capabilities IS
    'List of capability tags (e.g., typescript, python, review, complex-implementation).';

COMMENT ON COLUMN archon_agent_runtimes.supported_runners IS
    'List of runner keys this runtime can execute (e.g., claude-code-cli, codex-cli).';

-- Add runtime_id FK to execution_runs for traceability
ALTER TABLE archon_execution_runs
    ADD COLUMN IF NOT EXISTS runtime_id UUID NULL
    REFERENCES archon_agent_runtimes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_execution_runs_runtime_id
    ON archon_execution_runs(runtime_id);

-- Add assignee fields to tasks for polymorphic assignment
ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS assignee_type TEXT NULL CHECK (
        assignee_type IS NULL OR assignee_type IN ('agent', 'human', 'unassigned')
    ),
    ADD COLUMN IF NOT EXISTS assignee_id UUID NULL
    CHECK (assignee_type IN ('unassigned') OR assignee_type IS NULL OR assignee_id IS NOT NULL),
    ADD COLUMN IF NOT EXISTS runtime_id UUID NULL
    REFERENCES archon_agent_runtimes(id) ON DELETE SET NULL;

COMMENT ON COLUMN archon_tasks.assignee_type IS
    'Polymorphic assignment: agent, human, or unassigned. Enables hybrid human+AI teams.';

COMMENT ON COLUMN archon_tasks.runtime_id IS
    'Runtime that claimed this task for execution.';

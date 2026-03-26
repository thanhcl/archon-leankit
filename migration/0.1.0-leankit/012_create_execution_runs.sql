-- Migration: 012_create_execution_runs.sql
-- Purpose: Introduce canonical execution run records for LeanKit runtime history.

CREATE TABLE IF NOT EXISTS archon_execution_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id UUID NOT NULL REFERENCES archon_tasks(id) ON DELETE CASCADE,
    project_id UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,
    engine_id TEXT NULL,
    session_id TEXT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'reviewing', 'completed', 'failed', 'cancelled')
    ),
    stage TEXT NOT NULL CHECK (
        stage IN ('execute', 'architect-review', 'code-review', 'retry')
    ),
    model TEXT NULL,
    retry_index INTEGER NOT NULL DEFAULT 0,
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ NULL,
    duration_seconds DOUBLE PRECISION NULL,
    token_input INTEGER NULL,
    token_output INTEGER NULL,
    cost_usd DOUBLE PRECISION NULL,
    result_summary TEXT NULL,
    error_summary TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_execution_runs_task_id
    ON archon_execution_runs(task_id);

CREATE INDEX IF NOT EXISTS idx_execution_runs_project_id
    ON archon_execution_runs(project_id);

CREATE INDEX IF NOT EXISTS idx_execution_runs_status
    ON archon_execution_runs(status);

CREATE INDEX IF NOT EXISTS idx_execution_runs_stage
    ON archon_execution_runs(stage);

CREATE INDEX IF NOT EXISTS idx_execution_runs_started_at
    ON archon_execution_runs(started_at DESC);

COMMENT ON TABLE archon_execution_runs IS
    'Canonical runtime records for task execution attempts, retries, and review stages.';

COMMENT ON COLUMN archon_execution_runs.status IS
    'Runtime status: queued, running, reviewing, completed, failed, cancelled.';

COMMENT ON COLUMN archon_execution_runs.stage IS
    'Execution stage: execute, architect-review, code-review, retry.';

-- Migration: 002_task_lifecycle.sql
-- Purpose: Extend archon_tasks from 4-state kanban to 14-state lifecycle engine
-- for LeanKit V3 Task Engine.
--
-- States: draft, proposed, approved, planning, owner-qa, assigned, executing,
--         architect-review, review, done, failed, escalated, on-hold, cancelled

-- ============================================================================
-- 1. Convert status from enum to TEXT with CHECK constraint
-- ============================================================================

-- Drop the existing enum constraint by changing column type
ALTER TABLE archon_tasks
    ALTER COLUMN status DROP DEFAULT;

ALTER TABLE archon_tasks
    ALTER COLUMN status TYPE TEXT USING status::TEXT;

-- Drop the old enum type (may fail if other tables use it - that's ok)
DROP TYPE IF EXISTS task_status;

-- Add CHECK constraint for the 14 lifecycle states
ALTER TABLE archon_tasks
    ADD CONSTRAINT chk_task_status CHECK (
        status IN (
            'draft', 'proposed', 'approved', 'planning', 'owner-qa',
            'assigned', 'executing', 'architect-review', 'review',
            'done', 'failed', 'escalated', 'on-hold', 'cancelled'
        )
    );

-- Set new default
ALTER TABLE archon_tasks
    ALTER COLUMN status SET DEFAULT 'draft';

-- ============================================================================
-- 2. Migrate existing data: map old statuses to new lifecycle states
-- ============================================================================

UPDATE archon_tasks SET status = 'draft' WHERE status = 'todo';
UPDATE archon_tasks SET status = 'executing' WHERE status = 'doing';
-- 'review' stays as 'review'
-- 'done' stays as 'done'

-- ============================================================================
-- 3. Add new lifecycle columns
-- ============================================================================

-- State tracking
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS state_changed_at TIMESTAMPTZ DEFAULT NOW();
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS state_changed_by TEXT DEFAULT NULL;

-- Ownership (distinct from assignee who executes)
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS owner TEXT DEFAULT NULL;

-- Rejection/hold reasons
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS rejection_reason TEXT DEFAULT NULL;
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS hold_reason TEXT DEFAULT NULL;

-- Audit trail for state transitions
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS state_history JSONB DEFAULT '[]'::jsonb;

-- Task dependencies
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS blocked_by UUID[] DEFAULT '{}';

-- Acceptance criteria for owner-qa
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS acceptance_criteria JSONB DEFAULT '[]'::jsonb;

-- Execution results (output from agent execution)
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS execution_result JSONB DEFAULT NULL;

-- Architect review data
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS architect_review JSONB DEFAULT NULL;

-- Retry tracking
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS retry_count INTEGER DEFAULT 0;
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS max_retries INTEGER DEFAULT 3;

-- Execution prompt (instructions for the executing agent)
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS execution_prompt TEXT DEFAULT NULL;

-- Source application that created this task
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS source_app TEXT DEFAULT NULL;

-- Complexity classification
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS complexity TEXT DEFAULT 'simple';
ALTER TABLE archon_tasks ADD CONSTRAINT chk_task_complexity CHECK (
    complexity IN ('simple', 'complex')
);

-- ============================================================================
-- 4. Add indexes for new columns
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_archon_tasks_state_changed_at ON archon_tasks(state_changed_at);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_owner ON archon_tasks(owner);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_complexity ON archon_tasks(complexity);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_source_app ON archon_tasks(source_app);

-- ============================================================================
-- 5. Add comments for new columns
-- ============================================================================

COMMENT ON COLUMN archon_tasks.status IS 'Lifecycle state: draft, proposed, approved, planning, owner-qa, assigned, executing, architect-review, review, done, failed, escalated, on-hold, cancelled';
COMMENT ON COLUMN archon_tasks.state_changed_at IS 'Timestamp of last state transition';
COMMENT ON COLUMN archon_tasks.state_changed_by IS 'Actor who triggered the last state transition';
COMMENT ON COLUMN archon_tasks.owner IS 'Task owner (human who owns the outcome), distinct from assignee (agent who executes)';
COMMENT ON COLUMN archon_tasks.rejection_reason IS 'Reason for rejection or failure';
COMMENT ON COLUMN archon_tasks.hold_reason IS 'Reason task was put on hold';
COMMENT ON COLUMN archon_tasks.state_history IS 'Audit trail: [{from_status, to_status, changed_by, changed_at, reason}]';
COMMENT ON COLUMN archon_tasks.blocked_by IS 'Array of task UUIDs that block this task';
COMMENT ON COLUMN archon_tasks.acceptance_criteria IS 'JSON array of acceptance criteria for owner-qa validation';
COMMENT ON COLUMN archon_tasks.execution_result IS 'JSON output from agent execution (files changed, logs, etc.)';
COMMENT ON COLUMN archon_tasks.architect_review IS 'JSON data from architect review (feedback, approved_files, etc.)';
COMMENT ON COLUMN archon_tasks.retry_count IS 'Number of times this task has been retried after failure';
COMMENT ON COLUMN archon_tasks.max_retries IS 'Maximum allowed retries before escalation';
COMMENT ON COLUMN archon_tasks.execution_prompt IS 'Instructions/prompt for the executing agent';
COMMENT ON COLUMN archon_tasks.source_app IS 'Application that created this task (e.g., virtual-office, mcp, api)';
COMMENT ON COLUMN archon_tasks.complexity IS 'Task complexity: simple (auto-assign) or complex (needs planning)';

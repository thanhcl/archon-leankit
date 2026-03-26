-- Migration: 011_reconcile_task_lifecycle_canonical.sql
-- Purpose: Reconcile archon_tasks lifecycle constraint with the canonical
-- 15-state LeanKit Platform lifecycle.
--
-- This migration supersedes the older ad hoc code-review migration and removes
-- legacy states that are not part of the current control-plane contract.

ALTER TABLE archon_tasks DROP CONSTRAINT IF EXISTS chk_task_status;

ALTER TABLE archon_tasks
    ADD CONSTRAINT chk_task_status CHECK (
        status IN (
            'draft',
            'proposed',
            'approved',
            'planning',
            'owner-qa',
            'assigned',
            'executing',
            'architect-review',
            'code-review',
            'review',
            'done',
            'failed',
            'escalated',
            'on-hold',
            'cancelled'
        )
    );

COMMENT ON COLUMN archon_tasks.status IS
    'Canonical 15-state lifecycle: draft, proposed, approved, planning, owner-qa, assigned, executing, architect-review, code-review, review, done, failed, escalated, on-hold, cancelled';

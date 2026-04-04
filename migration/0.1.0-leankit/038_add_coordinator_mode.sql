-- Add coordinator mode fields for parent-child task orchestration (C-P7-01).
-- decomposition_mode: how a task is decomposed into children.
--   'none'        — standard task (default, no decomposition)
--   'manual'      — manually split by operator/architect
--   'coordinator' — engine-managed parallel children with aggregate review

ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS decomposition_mode text NOT NULL DEFAULT 'none'
        CHECK (decomposition_mode IN ('none', 'manual', 'coordinator'));

-- Index for finding coordinator tasks efficiently
CREATE INDEX IF NOT EXISTS idx_archon_tasks_decomposition_mode
    ON archon_tasks(decomposition_mode)
    WHERE decomposition_mode != 'none';

-- Index for finding children of a parent task
CREATE INDEX IF NOT EXISTS idx_archon_tasks_parent_task_id
    ON archon_tasks(parent_task_id)
    WHERE parent_task_id IS NOT NULL;

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

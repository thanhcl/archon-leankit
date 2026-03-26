-- Migration: 022_add_task_tags
-- Purpose: Add tags TEXT[] column to archon_tasks for structured task labeling,
--          routing hints, and specialization metadata.

ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';

-- GIN index enables fast array-contains (@>) queries for tag filtering
CREATE INDEX IF NOT EXISTS idx_archon_tasks_tags ON archon_tasks USING gin(tags);

COMMENT ON COLUMN archon_tasks.tags IS 'Controlled labels for task routing, filtering, and specialization';

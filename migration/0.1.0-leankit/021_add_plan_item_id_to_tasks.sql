-- Migration: 021_add_plan_item_id_to_tasks.sql
-- Purpose: Add plan_item_id nullable FK to archon_tasks for direct plan-item-to-task traceability.
--          One task can have one primary plan item source; the junction table
--          project_implementation_item_task_links handles many-to-many links.

ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS plan_item_id UUID NULL
        REFERENCES project_implementation_items(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_archon_tasks_plan_item_id
    ON archon_tasks(plan_item_id)
    WHERE plan_item_id IS NOT NULL;

COMMENT ON COLUMN archon_tasks.plan_item_id IS
    'Optional FK to the primary implementation plan item that spawned or owns this task.
     Set NULL when the plan item is deleted. Use project_implementation_item_task_links
     for many-to-many link management.';

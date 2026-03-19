-- Migration: 008_task_extended_fields.sql
-- Purpose: Add task_type, phase, module, sprint, parent_task_id FK, tags columns
-- for enhanced task categorization, sprint tracking, and subtask support.

-- ============================================================================
-- 1. Add new categorization columns
-- ============================================================================

-- Task type classification
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS task_type TEXT NOT NULL DEFAULT 'feature';
ALTER TABLE archon_tasks ADD CONSTRAINT chk_task_type CHECK (
    task_type IN ('bug', 'feature', 'improvement', 'docs', 'refactor', 'test')
);

-- Roadmap tracking
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS phase TEXT DEFAULT NULL;

-- Module/area classification
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS module TEXT DEFAULT NULL;

-- Sprint assignment
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS sprint TEXT DEFAULT NULL;

-- Controlled labels
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS tags TEXT[] DEFAULT '{}';

-- ============================================================================
-- 2. Add parent_task_id column + FK constraint for subtask hierarchy
-- ============================================================================

ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS parent_task_id UUID DEFAULT NULL;
-- Self-referencing FK for subtask hierarchy
ALTER TABLE archon_tasks ADD CONSTRAINT fk_parent_task
    FOREIGN KEY (parent_task_id) REFERENCES archon_tasks(id)
    ON DELETE SET NULL;

-- ============================================================================
-- 3. Add indexes for query performance
-- ============================================================================

CREATE INDEX IF NOT EXISTS idx_archon_tasks_task_type ON archon_tasks(task_type);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_phase ON archon_tasks(phase);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_module ON archon_tasks(module);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_sprint ON archon_tasks(sprint);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_parent_task_id ON archon_tasks(parent_task_id);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_tags ON archon_tasks USING gin(tags);
CREATE INDEX IF NOT EXISTS idx_archon_tasks_feature ON archon_tasks(feature);

-- ============================================================================
-- 4. Add comments
-- ============================================================================

COMMENT ON COLUMN archon_tasks.task_type IS 'Task type: bug, feature, improvement, docs, refactor, test';
COMMENT ON COLUMN archon_tasks.phase IS 'Roadmap phase: phase-1, phase-2, etc.';
COMMENT ON COLUMN archon_tasks.module IS 'Module/area: engine, virtual-office, archon-api, observability, toolkit';
COMMENT ON COLUMN archon_tasks.sprint IS 'Sprint assignment: S1, S2, S3, etc.';
COMMENT ON COLUMN archon_tasks.tags IS 'Controlled labels for task categorization';
COMMENT ON COLUMN archon_tasks.parent_task_id IS 'Parent task UUID for subtask hierarchy';

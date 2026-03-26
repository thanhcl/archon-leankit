-- Migration: 027_add_owner_feedback_to_tasks
-- Purpose: Add owner feedback fields to archon_tasks for structured
--          post-completion quality capture: rating, notes, and improvement tags.

ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS owner_rating   SMALLINT    DEFAULT NULL CHECK (owner_rating IS NULL OR (owner_rating BETWEEN 1 AND 5));
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS owner_notes    TEXT        DEFAULT NULL;
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS improvement_tags TEXT[]    DEFAULT '{}';

COMMENT ON COLUMN archon_tasks.owner_rating     IS 'Owner quality rating 1–5 after task completion';
COMMENT ON COLUMN archon_tasks.owner_notes      IS 'Owner qualitative observations after task completion';
COMMENT ON COLUMN archon_tasks.improvement_tags IS 'Owner-supplied improvement hint tags (e.g. "needs-better-tests")';

-- Index for filtering/reporting by rating
CREATE INDEX IF NOT EXISTS idx_archon_tasks_owner_rating ON archon_tasks(owner_rating) WHERE owner_rating IS NOT NULL;

-- Record migration
INSERT INTO archon_migrations (version, migration_name)
VALUES ('0.1.0-leankit', '027_add_owner_feedback_to_tasks')
ON CONFLICT (version, migration_name) DO NOTHING;

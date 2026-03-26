-- =====================================================
-- Add source_run_ids to archon_learnings
-- Create archon_promotion_log for audit trail
-- =====================================================
-- Tracks which execution runs produced each learning,
-- enabling frequency scoring across runs and providing
-- a full audit trail of all promotion decisions.
-- =====================================================

-- Add source_run_ids column to archon_learnings
ALTER TABLE archon_learnings
    ADD COLUMN IF NOT EXISTS source_run_ids JSONB DEFAULT '[]'::jsonb;

COMMENT ON COLUMN archon_learnings.source_run_ids IS 'Execution run IDs that produced this learning';

-- =====================================================
-- TABLE: archon_promotion_log
-- =====================================================
-- Audit log of all promotion decisions (auto and manual).
-- Each row records a single promotion event with context
-- about the source runs and confidence at time of promotion.

CREATE TABLE IF NOT EXISTS archon_promotion_log (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    learning_id  UUID REFERENCES archon_learnings(id) ON DELETE SET NULL,
    project_id   UUID REFERENCES archon_projects(id) ON DELETE CASCADE,
    promotion_type TEXT NOT NULL
        CHECK (promotion_type IN ('auto', 'manual')),
    promoted_to  TEXT NOT NULL,
    recurrence_count INTEGER NOT NULL DEFAULT 1,
    source_run_ids   JSONB DEFAULT '[]'::jsonb,
    source_task_ids  JSONB DEFAULT '[]'::jsonb,
    confidence       NUMERIC(3,2),
    reason           TEXT,
    learning_description TEXT,
    created_at   TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_archon_promotion_log_learning_id
    ON archon_promotion_log(learning_id);
CREATE INDEX IF NOT EXISTS idx_archon_promotion_log_project_id
    ON archon_promotion_log(project_id);
CREATE INDEX IF NOT EXISTS idx_archon_promotion_log_created_at
    ON archon_promotion_log(created_at DESC);

COMMENT ON TABLE archon_promotion_log IS 'Audit trail of learning promotion decisions with source run context';

-- Record migration
INSERT INTO archon_migrations (version, migration_name)
VALUES ('0.1.0-leankit', '026_add_source_run_ids_and_promotion_log')
ON CONFLICT (version, migration_name) DO NOTHING;

-- =====================================================
-- MIGRATION COMPLETE
-- =====================================================

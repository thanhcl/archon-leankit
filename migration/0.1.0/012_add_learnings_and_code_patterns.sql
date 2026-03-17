-- =====================================================
-- Add archon_learnings and archon_code_patterns tables
-- =====================================================
-- This migration adds tables for the Task Engine's learning
-- and code pattern extraction system.
--
-- archon_learnings: Stores learnings extracted from CC task
-- executions, with dedup via pattern_key and auto-promote
-- when recurrence >= 3.
--
-- archon_code_patterns: Stores expert code patterns extracted
-- from completed tasks, with semantic dedup by pattern_key
-- and auto-promote to KB when confidence >= 0.9 AND usage >= 3.
-- =====================================================

-- =====================================================
-- TABLE: archon_learnings
-- =====================================================

CREATE TABLE IF NOT EXISTS archon_learnings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES archon_projects(id) ON DELETE CASCADE,
    task_id UUID REFERENCES archon_tasks(id) ON DELETE SET NULL,
    type TEXT NOT NULL DEFAULT 'knowledge_gap'
        CHECK (type IN ('error', 'correction', 'best_practice', 'knowledge_gap')),
    description TEXT NOT NULL,
    area TEXT
        CHECK (area IS NULL OR area IN ('frontend', 'backend', 'infra', 'tests', 'config', 'security', 'database')),
    suggested_rule TEXT,
    pattern_key TEXT NOT NULL,
    recurrence_count INTEGER NOT NULL DEFAULT 1,
    related_tasks JSONB DEFAULT '[]'::jsonb,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'promoted', 'dismissed')),
    promoted_to TEXT,
    promoted_at TIMESTAMPTZ,
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for archon_learnings
CREATE INDEX IF NOT EXISTS idx_archon_learnings_project_id ON archon_learnings(project_id);
CREATE INDEX IF NOT EXISTS idx_archon_learnings_status ON archon_learnings(status);
CREATE INDEX IF NOT EXISTS idx_archon_learnings_pattern_key ON archon_learnings(pattern_key);
CREATE INDEX IF NOT EXISTS idx_archon_learnings_type ON archon_learnings(type);
CREATE INDEX IF NOT EXISTS idx_archon_learnings_last_seen ON archon_learnings(last_seen DESC);

-- Auto-update updated_at trigger
CREATE OR REPLACE TRIGGER update_archon_learnings_updated_at
    BEFORE UPDATE ON archon_learnings
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- =====================================================
-- TABLE: archon_code_patterns
-- =====================================================

CREATE TABLE IF NOT EXISTS archon_code_patterns (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES archon_projects(id) ON DELETE CASCADE,
    pattern_name TEXT NOT NULL,
    pattern_key TEXT NOT NULL,
    category TEXT NOT NULL
        CHECK (category IN ('security', 'error-handling', 'testing', 'architecture', 'performance', 'api-design')),
    language TEXT NOT NULL DEFAULT 'java',
    code_example TEXT NOT NULL,
    context TEXT NOT NULL,
    anti_pattern TEXT,
    source_task_ids JSONB DEFAULT '[]'::jsonb,
    source_files JSONB DEFAULT '[]'::jsonb,
    extracted_from TEXT DEFAULT 'task_completion',
    usage_count INTEGER NOT NULL DEFAULT 1,
    confidence NUMERIC(3,2) NOT NULL DEFAULT 0.70
        CHECK (confidence >= 0 AND confidence <= 1),
    expert_validated BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'active', 'promoted', 'deprecated')),
    promoted_to TEXT,
    last_used_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Indexes for archon_code_patterns
CREATE INDEX IF NOT EXISTS idx_archon_code_patterns_project_id ON archon_code_patterns(project_id);
CREATE INDEX IF NOT EXISTS idx_archon_code_patterns_pattern_key ON archon_code_patterns(pattern_key);
CREATE INDEX IF NOT EXISTS idx_archon_code_patterns_category ON archon_code_patterns(category);
CREATE INDEX IF NOT EXISTS idx_archon_code_patterns_status ON archon_code_patterns(status);
CREATE INDEX IF NOT EXISTS idx_archon_code_patterns_usage ON archon_code_patterns(usage_count DESC);
CREATE INDEX IF NOT EXISTS idx_archon_code_patterns_confidence ON archon_code_patterns(confidence DESC);

-- Unique constraint: one pattern_key per project
CREATE UNIQUE INDEX IF NOT EXISTS idx_archon_code_patterns_key_project
    ON archon_code_patterns(pattern_key, project_id);

-- Auto-update updated_at trigger
CREATE OR REPLACE TRIGGER update_archon_code_patterns_updated_at
    BEFORE UPDATE ON archon_code_patterns
    FOR EACH ROW
    EXECUTE FUNCTION update_updated_at_column();

-- Comments
COMMENT ON TABLE archon_learnings IS 'Stores learnings from CC task executions with dedup and auto-promotion';
COMMENT ON TABLE archon_code_patterns IS 'Expert code patterns extracted from tasks with usage tracking and KB promotion';
COMMENT ON COLUMN archon_code_patterns.pattern_key IS 'Semantic dedup key: category.language.normalized_name';
COMMENT ON COLUMN archon_code_patterns.confidence IS 'Confidence score 0-1, incremented by 0.05 per usage';
COMMENT ON COLUMN archon_code_patterns.promoted_to IS 'Target when promoted (e.g., KB)';

-- Record migration
INSERT INTO archon_migrations (version, migration_name)
VALUES ('0.1.0', '012_add_learnings_and_code_patterns')
ON CONFLICT (version, migration_name) DO NOTHING;

-- =====================================================
-- MIGRATION COMPLETE
-- =====================================================

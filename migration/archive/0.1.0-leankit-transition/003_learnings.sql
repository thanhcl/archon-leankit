-- Migration: 003_learnings.sql
-- Purpose: Self-improving learning pipeline for LeanKit V3 Task Engine.
-- CC logs learnings per task, system detects recurring patterns,
-- auto-promotes to KB when recurrence >= 3.

CREATE TABLE IF NOT EXISTS archon_learnings (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id UUID REFERENCES archon_projects(id) ON DELETE CASCADE,
    task_id UUID REFERENCES archon_tasks(id) ON DELETE SET NULL,
    type TEXT NOT NULL CHECK (type IN ('error', 'correction', 'best_practice', 'knowledge_gap')),
    description TEXT NOT NULL,
    area TEXT CHECK (area IN ('frontend', 'backend', 'infra', 'tests', 'config', 'security', 'database')),
    suggested_rule TEXT,
    pattern_key TEXT,
    recurrence_count INTEGER DEFAULT 1,
    first_seen TIMESTAMPTZ DEFAULT NOW(),
    last_seen TIMESTAMPTZ DEFAULT NOW(),
    related_learnings UUID[] DEFAULT '{}',
    related_tasks UUID[] DEFAULT '{}',
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'promoted', 'extracted', 'dismissed')),
    promoted_to TEXT,
    promoted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_learnings_project ON archon_learnings(project_id);
CREATE INDEX IF NOT EXISTS idx_learnings_pattern ON archon_learnings(pattern_key);
CREATE INDEX IF NOT EXISTS idx_learnings_status ON archon_learnings(status);
CREATE INDEX IF NOT EXISTS idx_learnings_type ON archon_learnings(type);

-- RLS
ALTER TABLE archon_learnings ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow service role full access to archon_learnings" ON archon_learnings
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Allow authenticated users to read archon_learnings" ON archon_learnings
    FOR ALL TO authenticated USING (true);

COMMENT ON TABLE archon_learnings IS 'Self-improving learnings from CC task executions. Auto-promoted to KB when recurrence >= 3.';
COMMENT ON COLUMN archon_learnings.pattern_key IS 'Normalized key for deduplication: type:area:first_50_chars_normalized';
COMMENT ON COLUMN archon_learnings.promoted_to IS 'Where the learning was promoted: KB, CLAUDE.md, etc.';

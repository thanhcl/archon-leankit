-- Migration: 004_code_patterns.sql
-- Purpose: Code Pattern Library for LeanKit V3 Task Engine.
-- CC extracts expert-level code patterns from completed tasks,
-- auto-promotes to KB when confidence >= 0.9 AND usage_count >= 3.

CREATE TABLE IF NOT EXISTS archon_code_patterns (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id UUID REFERENCES archon_projects(id) ON DELETE CASCADE,

    -- Pattern identity
    pattern_name TEXT NOT NULL,
    pattern_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK (category IN ('security', 'error-handling', 'testing', 'architecture', 'performance', 'api-design')),
    language TEXT DEFAULT 'java',

    -- Pattern content
    code_example TEXT NOT NULL,
    context TEXT NOT NULL,
    anti_pattern TEXT,

    -- Provenance
    source_task_ids UUID[] DEFAULT '{}',
    source_files TEXT[] DEFAULT '{}',
    extracted_from TEXT DEFAULT 'task_completion' CHECK (extracted_from IN ('task_completion', 'architect_review', 'manual')),

    -- Quality signals
    usage_count INTEGER DEFAULT 1,
    last_used_at TIMESTAMPTZ,
    confidence FLOAT DEFAULT 0.7,
    expert_validated BOOLEAN DEFAULT false,

    -- Lifecycle
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'active', 'promoted', 'deprecated')),
    promoted_to TEXT,

    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_patterns_project ON archon_code_patterns(project_id);
CREATE INDEX IF NOT EXISTS idx_patterns_category ON archon_code_patterns(category);
CREATE INDEX IF NOT EXISTS idx_patterns_key ON archon_code_patterns(pattern_key);
CREATE INDEX IF NOT EXISTS idx_patterns_status ON archon_code_patterns(status);

-- RLS
ALTER TABLE archon_code_patterns ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow service role full access to archon_code_patterns" ON archon_code_patterns
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Allow authenticated users to read archon_code_patterns" ON archon_code_patterns
    FOR ALL TO authenticated USING (true);

COMMENT ON TABLE archon_code_patterns IS 'Expert-level code patterns extracted from CC task executions. Auto-promoted to KB when confidence >= 0.9 and usage_count >= 3.';
COMMENT ON COLUMN archon_code_patterns.pattern_key IS 'Dedup key: category.language.normalized_name (e.g., security.java.pkcs11-session-management)';
COMMENT ON COLUMN archon_code_patterns.promoted_to IS 'Where the pattern was promoted: KB, CLAUDE.md, skill';

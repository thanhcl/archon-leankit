-- Migration: 010_rule_suggestions.sql
-- Purpose: Persistent rule suggestions auto-generated when learnings recur >= 3 times.
-- Owner approves/rejects in the Rules UI; approved suggestions create archon_rules entries.

CREATE TABLE IF NOT EXISTS archon_rule_suggestions (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    project_id UUID REFERENCES archon_projects(id) ON DELETE CASCADE,
    learning_id UUID REFERENCES archon_learnings(id) ON DELETE SET NULL,
    section TEXT NOT NULL,
    rule_text TEXT NOT NULL,
    confidence FLOAT NOT NULL DEFAULT 0.5,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_rule_suggestions_project ON archon_rule_suggestions(project_id);
CREATE INDEX IF NOT EXISTS idx_rule_suggestions_status ON archon_rule_suggestions(status);
CREATE INDEX IF NOT EXISTS idx_rule_suggestions_learning ON archon_rule_suggestions(learning_id);

ALTER TABLE archon_rule_suggestions ENABLE ROW LEVEL SECURITY;
CREATE POLICY "Allow service role full access to archon_rule_suggestions" ON archon_rule_suggestions
    FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "Allow authenticated users to access archon_rule_suggestions" ON archon_rule_suggestions
    FOR ALL TO authenticated USING (true);

COMMENT ON TABLE archon_rule_suggestions IS 'Auto-generated rule suggestions from recurring learnings (recurrence >= 3). Owner approves/rejects in the Rules UI.';
COMMENT ON COLUMN archon_rule_suggestions.learning_id IS 'Source learning that triggered this suggestion.';
COMMENT ON COLUMN archon_rule_suggestions.status IS 'pending: awaiting owner review, approved: added to archon_rules, rejected: dismissed.';

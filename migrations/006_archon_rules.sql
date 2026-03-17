-- Migration 006: Create archon_rules table for centralized CLAUDE.md management
-- Run in Supabase SQL Editor

-- Create the archon_rules table
CREATE TABLE IF NOT EXISTS archon_rules (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id uuid NULL REFERENCES archon_projects(id) ON DELETE CASCADE,
    section text NOT NULL,
    rule_text text NOT NULL,
    priority integer NOT NULL DEFAULT 100,
    source text NOT NULL DEFAULT 'manual',
    enabled boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE archon_rules IS 'Centralized rules for CLAUDE.md generation — global (project_id NULL) or project-scoped';
COMMENT ON COLUMN archon_rules.project_id IS 'NULL = global rule; otherwise scoped to a specific project';
COMMENT ON COLUMN archon_rules.section IS 'Grouping section: validation, integration, security, coding-style, etc.';
COMMENT ON COLUMN archon_rules.rule_text IS 'The rule content in markdown format';
COMMENT ON COLUMN archon_rules.priority IS 'Sort order within section — lower number = higher priority';
COMMENT ON COLUMN archon_rules.source IS 'Origin: manual, learning-promoted, agent-suggested';
COMMENT ON COLUMN archon_rules.enabled IS 'Soft toggle to disable without deleting';

-- Index for efficient queries
CREATE INDEX IF NOT EXISTS idx_archon_rules_project_id ON archon_rules(project_id);
CREATE INDEX IF NOT EXISTS idx_archon_rules_section ON archon_rules(section);
CREATE INDEX IF NOT EXISTS idx_archon_rules_enabled ON archon_rules(enabled) WHERE enabled = true;

-- Pre-seed 4 global rules
INSERT INTO archon_rules (project_id, section, rule_text, priority, source)
VALUES
    (NULL, 'validation',
     E'## MANDATORY: Self-Validation Loop (NEVER skip)\nBefore reporting ANY task as complete, you MUST:\n1. BUILD: Run build/compile command — fix until pass\n2. TEST: Run test command — fix until pass\n3. SELF-REVIEW: Run /code-review or manually check:\n   - All acceptance criteria met?\n   - Security issues? Error handling?\n   - No debug code left?\n4. Only THEN report "Done"\n\nIf you skip this loop, your output is REJECTED.',
     1, 'seed'),

    (NULL, 'integration',
     E'## Integration Rules\n- NEVER guess API contracts — always read the actual endpoint code or OpenAPI spec before integrating\n- Validate request/response shapes against the real implementation\n- When calling external services, use typed clients with explicit error handling\n- Document any assumptions about external APIs in code comments',
     10, 'seed'),

    (NULL, 'security',
     E'## Security Rules\n- Never log secrets, tokens, or PII\n- Validate all user input at system boundaries\n- Use parameterized queries — never string-concatenate SQL\n- Apply principle of least privilege for service accounts\n- Review OWASP Top 10 before submitting security-sensitive code',
     20, 'seed'),

    (NULL, 'coding-style',
     E'## Coding Style\n- Follow existing patterns in the codebase — consistency over personal preference\n- Keep functions focused: one responsibility, clear inputs/outputs\n- Name variables and functions for clarity, not brevity\n- Remove dead code immediately — no backward compatibility wrappers\n- Only add comments where the logic is not self-evident',
     30, 'seed');

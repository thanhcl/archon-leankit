-- Migration: 023_create_agent_definitions.sql
-- Purpose: Introduce agent_definitions as a first-class control-plane model.
--          Each definition captures a specialized agent role with capabilities,
--          model preferences, and a prompt template that the prompt builder
--          can inject at task execution time.

CREATE TABLE IF NOT EXISTS archon_agent_definitions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT,
    capabilities JSONB NOT NULL DEFAULT '[]'::jsonb,
    model_preferences JSONB NOT NULL DEFAULT '{}'::jsonb,
    prompt_template TEXT,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_agent_definition_slug UNIQUE (slug)
);

CREATE INDEX IF NOT EXISTS idx_agent_definitions_slug
    ON archon_agent_definitions(slug);

CREATE INDEX IF NOT EXISTS idx_agent_definitions_is_active
    ON archon_agent_definitions(is_active);

COMMENT ON TABLE archon_agent_definitions IS
    'Control-plane definitions for specialized agent roles. Each row describes
     a named agent with its capabilities, preferred model configuration, and an
     optional prompt template injected by the prompt builder at execution time.';

COMMENT ON COLUMN archon_agent_definitions.slug IS
    'URL-safe unique identifier used to reference this definition from tasks
     and policies (e.g. "backend-dev", "qa-engineer", "architect").';

COMMENT ON COLUMN archon_agent_definitions.name IS
    'Human-readable display name (e.g. "Backend Developer", "QA Engineer").';

COMMENT ON COLUMN archon_agent_definitions.description IS
    'Optional prose describing the agent role, specialization, and intended use.';

COMMENT ON COLUMN archon_agent_definitions.capabilities IS
    'JSONB array of capability strings advertised by this agent role.
     Examples: ["python", "fastapi", "database-migrations", "code-review"]';

COMMENT ON COLUMN archon_agent_definitions.model_preferences IS
    'JSONB model configuration hints. Supported keys:
     preferred_model   TEXT   – model ID hint passed to the runner (e.g. "claude-opus-4-6")
     temperature       FLOAT  – sampling temperature (0.0–1.0)
     token_profile     TEXT   – token budget profile name (e.g. "complex_architecture")
     preferred_runner  TEXT   – runner key preference (e.g. "claude-code-cli")
     notes             TEXT   – human-readable notes about model selection rationale';

COMMENT ON COLUMN archon_agent_definitions.prompt_template IS
    'Optional Markdown prompt section injected by the prompt builder before the
     task requirements. Use {task_title}, {task_description} as interpolation tokens.
     Nil/empty means no role-specific preamble is added.';

COMMENT ON COLUMN archon_agent_definitions.is_active IS
    'When false the definition is excluded from prompt injection and routing lookups.';

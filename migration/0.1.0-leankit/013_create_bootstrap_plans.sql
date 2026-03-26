-- Create first-class bootstrap plan records for project initialization flows.

CREATE TABLE IF NOT EXISTS archon_bootstrap_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID NOT NULL REFERENCES archon_projects(id) ON DELETE CASCADE,
    requested_provider TEXT NOT NULL,
    resolved_provider TEXT NOT NULL,
    strategy TEXT NOT NULL,
    model TEXT NULL,
    template TEXT NOT NULL,
    project_type TEXT NOT NULL,
    bootstrap_policy TEXT NOT NULL,
    source_app TEXT NULL,
    status TEXT NOT NULL DEFAULT 'planned',
    plan_items JSONB NOT NULL DEFAULT '[]'::jsonb,
    created_tasks JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_archon_bootstrap_plans_project_id
    ON archon_bootstrap_plans(project_id);

CREATE INDEX IF NOT EXISTS idx_archon_bootstrap_plans_created_at
    ON archon_bootstrap_plans(created_at DESC);

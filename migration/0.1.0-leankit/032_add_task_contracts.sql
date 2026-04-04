-- Create task_contracts table for contract artifact model.
-- Each contract captures the negotiated scope, acceptance criteria, and evidence
-- requirements for a task. Contracts are immutable once locked; revisions create
-- new rows to preserve full history.

CREATE TABLE IF NOT EXISTS archon_task_contracts (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    task_id         uuid NOT NULL REFERENCES archon_tasks(id) ON DELETE CASCADE,
    version         integer NOT NULL DEFAULT 1,

    -- Scope definition
    objective       text NOT NULL,
    in_scope_paths  jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Acceptance criteria: [{name, description, threshold}]
    acceptance_criteria jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Evidence requirements: free-form list of required proof artifacts
    evidence_requirements jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Negotiation metadata
    negotiated_by   text,           -- who negotiated: "owner" | "engine" | "architect"
    locked_at       timestamptz,    -- null = draft, set = immutable

    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Index for fast lookup by task
CREATE INDEX IF NOT EXISTS idx_task_contracts_task_id ON archon_task_contracts(task_id);

-- Unique constraint: one version number per task
CREATE UNIQUE INDEX IF NOT EXISTS idx_task_contracts_task_version
    ON archon_task_contracts(task_id, version);

-- Add current_contract_id FK to tasks table
ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS current_contract_id uuid
        REFERENCES archon_task_contracts(id) ON DELETE SET NULL;

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

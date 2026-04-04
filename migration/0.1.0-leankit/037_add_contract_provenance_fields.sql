-- Add provenance and negotiation-state fields to archon_task_contracts.
-- These fields allow operators to distinguish draft/negotiated/locked/superseded
-- contracts and trace the full audit trail of who created and modified each contract.

ALTER TABLE archon_task_contracts
    ADD COLUMN IF NOT EXISTS source_stage      text,         -- workflow stage: "planning" | "review" | "negotiation" | "execution"
    ADD COLUMN IF NOT EXISTS source_type       text,         -- actor type: "engine" | "owner" | "architect" | "ai_ide"
    ADD COLUMN IF NOT EXISTS negotiation_status text NOT NULL DEFAULT 'draft',  -- "draft" | "negotiated" | "locked" | "superseded"
    ADD COLUMN IF NOT EXISTS locked_by         text,         -- actor who locked: "owner" | "engine" | "architect"
    ADD COLUMN IF NOT EXISTS supersedes_contract_id uuid
        REFERENCES archon_task_contracts(id) ON DELETE SET NULL;

-- Index to support provenance chain traversal
CREATE INDEX IF NOT EXISTS idx_task_contracts_supersedes
    ON archon_task_contracts(supersedes_contract_id)
    WHERE supersedes_contract_id IS NOT NULL;

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

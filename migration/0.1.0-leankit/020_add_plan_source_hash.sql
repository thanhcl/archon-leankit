-- Migration: 020_add_plan_source_hash.sql
-- Purpose: Add source document tracking columns to project_implementation_plans
--          for drift detection during re-import.

ALTER TABLE project_implementation_plans
    ADD COLUMN IF NOT EXISTS source_doc_hash TEXT NULL,
    ADD COLUMN IF NOT EXISTS source_doc_path TEXT NULL;

CREATE INDEX IF NOT EXISTS idx_impl_plans_source_hash
    ON project_implementation_plans(source_doc_hash)
    WHERE source_doc_hash IS NOT NULL;

COMMENT ON COLUMN project_implementation_plans.source_doc_hash IS
    'SHA-256 hash of the source markdown document used for drift detection on re-import.';

COMMENT ON COLUMN project_implementation_plans.source_doc_path IS
    'Optional path to the source markdown document.';

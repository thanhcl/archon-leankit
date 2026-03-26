-- Migration: 028_add_workspace_path_to_execution_runs.sql
-- Purpose: Add workspace_path column to execution runs for direct path lookup.
--
-- The artifact_manifest (with log_paths and artifact_paths) is stored in the
-- metadata JSONB column. workspace_path provides a direct index into the
-- local filesystem run workspace without parsing metadata JSON.

ALTER TABLE archon_execution_runs
    ADD COLUMN IF NOT EXISTS workspace_path TEXT NULL;

COMMENT ON COLUMN archon_execution_runs.workspace_path IS
    'Absolute path to the run-local workspace directory (logs/{run_id}/) for stdout/stderr logs and artifacts.';

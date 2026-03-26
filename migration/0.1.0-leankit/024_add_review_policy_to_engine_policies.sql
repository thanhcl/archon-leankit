-- Migration: 024_add_review_policy_to_engine_policies.sql
-- Purpose: Normalize project review mode into archon_engine_policies.

ALTER TABLE archon_engine_policies
    ADD COLUMN IF NOT EXISTS review_policy JSONB NOT NULL DEFAULT '{}'::jsonb;

COMMENT ON COLUMN archon_engine_policies.review_policy IS
    'JSONB review overrides. Supported keys:
     review_mode TEXT – "self-review" | "api" | "multi-perspective".
     Project-level review mode now resolves from this column first, then falls
     back to legacy project metadata and the global REVIEW_CONFIG setting.';

-- Migration 036: Add model_exhaustion_count to archon_tasks
-- Purpose: Track rate-limit / model-error exhaustion separately from task retry_count.
--          Model exhaustion does NOT consume the task's retry budget because the task
--          code never actually ran — only the model infrastructure failed.
-- Related: N3-1 model-level fallback implementation
-- Date: 2026-03-31

ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS model_exhaustion_count INTEGER DEFAULT 0;

COMMENT ON COLUMN archon_tasks.model_exhaustion_count IS
    'Count of times all models in the fallback chain were exhausted due to rate-limit or '
    'model-level errors (429/503). Tracked separately from retry_count because model '
    'exhaustion is an infrastructure failure, not a task code failure.';

-- Migration: 027_add_pipeline_steps_to_tasks.sql
-- Purpose: Add pipeline_steps field to archon_tasks for multi-step sequential execution.
--
-- pipeline_steps is a JSONB array of {stage, prompt_template, checkpoint} objects.
-- The engine executes steps sequentially, persisting each checkpoint in the
-- linked execution_run.metadata so failed steps can be retried from the last
-- successful checkpoint without re-running earlier steps.

ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS pipeline_steps JSONB NULL;

COMMENT ON COLUMN archon_tasks.pipeline_steps IS
    'Ordered list of pipeline step definitions: [{stage, prompt_template, checkpoint}]. '
    'When present, the engine executes steps sequentially and persists checkpoints in '
    'execution_run.metadata. A failed step retries from the last completed checkpoint.';

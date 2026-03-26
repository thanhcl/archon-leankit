-- Migration: 017_add_llm_metrics_to_execution_runs.sql
-- Purpose: Add LLM token usage and thinking metrics to execution_runs.

ALTER TABLE archon_execution_runs
    ADD COLUMN IF NOT EXISTS total_tokens INTEGER NULL,
    ADD COLUMN IF NOT EXISTS thinking_tokens INTEGER NULL;

-- Migrate cost_usd from DOUBLE PRECISION to NUMERIC(10,4) for precise decimal storage.
ALTER TABLE archon_execution_runs
    ALTER COLUMN cost_usd TYPE NUMERIC(10, 4) USING cost_usd::NUMERIC(10, 4);

COMMENT ON COLUMN archon_execution_runs.total_tokens IS
    'Total tokens consumed by the LLM run (input + output).';

COMMENT ON COLUMN archon_execution_runs.thinking_tokens IS
    'Extended thinking tokens consumed during the LLM run.';

COMMENT ON COLUMN archon_execution_runs.cost_usd IS
    'Estimated cost in USD for this execution run.';

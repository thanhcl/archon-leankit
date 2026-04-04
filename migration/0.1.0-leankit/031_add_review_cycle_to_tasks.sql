-- Add review_cycle column to track code review iteration count per task.
-- Used by task_engine to limit MAX_CODE_REVIEW_CYCLES and by lifecycle transitions
-- to increment the cycle counter when re-entering code-review.
ALTER TABLE archon_tasks ADD COLUMN IF NOT EXISTS review_cycle integer DEFAULT 0;

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

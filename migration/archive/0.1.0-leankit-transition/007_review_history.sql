-- Migration 007: Add review_history column to archon_tasks
-- Stores append-only array of every review round (self-review, code-review, architect-review)
-- with verdict, findings, confidence, quality gate score, and timestamps.

ALTER TABLE archon_tasks
    ADD COLUMN IF NOT EXISTS review_history JSONB DEFAULT '[]'::jsonb;

-- GIN index for querying review history entries
CREATE INDEX IF NOT EXISTS idx_archon_tasks_review_history
    ON archon_tasks USING gin(review_history);

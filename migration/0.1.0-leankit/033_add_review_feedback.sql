-- Create archon_review_feedback table for structured evaluator feedback artifacts.
-- Written on rejection by code-review and architect-review stages.
-- Operators can inspect per-criterion scores and retry direction in task detail.

CREATE TABLE IF NOT EXISTS archon_review_feedback (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                  uuid REFERENCES archon_execution_runs(id) ON DELETE SET NULL,
    task_id                 uuid NOT NULL REFERENCES archon_tasks(id) ON DELETE CASCADE,

    -- Contract revision at time of review (NULL if no contract was active)
    contract_revision       integer,

    -- Per-criterion findings: [{criterion, score, passed, details, evidence}]
    findings                jsonb NOT NULL DEFAULT '[]'::jsonb,

    -- Aggregate score 0.0–1.0
    overall_score           float,

    -- "approve" | "changes-requested" | "escalate"
    verdict                 text NOT NULL,

    -- Free-form hint for the retry agent: what to focus on next
    suggested_retry_direction text,

    -- Identity of the reviewer: "architect-reviewer" | "code-reviewer" | model name
    reviewer_identity       text NOT NULL,

    created_at              timestamptz NOT NULL DEFAULT now()
);

-- Index for fast lookup by task
CREATE INDEX IF NOT EXISTS idx_review_feedback_task_id ON archon_review_feedback(task_id);

-- Index for lookup by run
CREATE INDEX IF NOT EXISTS idx_review_feedback_run_id ON archon_review_feedback(run_id);

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

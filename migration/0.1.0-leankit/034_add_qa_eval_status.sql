-- Add 'qa-eval' as a valid lifecycle status for tasks.
-- Inserted between code-review and review:
--   executing → architect-review → code-review → qa-eval → review → done
--
-- The qa-eval stage runs an adversarial evaluator agent that probes
-- endpoints, tests UI, and scores each contract criterion 1-10.

-- The status column is free-text (no CHECK constraint or enum), so no
-- ALTER TYPE is needed. This migration exists as a documentation marker
-- and to update any views or policies that enumerate valid statuses.

-- Add a comment on the status column documenting the new lifecycle stage.
COMMENT ON COLUMN archon_tasks.status IS
  'Lifecycle: draft → proposed → approved → planning → owner-qa → assigned → '
  'executing → architect-review → code-review → qa-eval → review → done | '
  'failed → assigned/escalated/on-hold | '
  'escalated → assigned/on-hold/cancelled | '
  'on-hold → approved/assigned/cancelled | '
  'cancelled → assigned';

-- Notify PostgREST to reload schema cache
NOTIFY pgrst, 'reload schema';

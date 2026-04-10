-- Add deviation_type to execution runs for smart retry routing.
-- Adopted from GSD deviation classification pattern (2026-04-08).
--
-- Values:
--   bug_fix             — compile/runtime error, retry with same model + error context
--   missing_requirement — acceptance criteria gap, escalate model + emphasize criteria
--   dependency_blocker  — external blocker, pause task and notify
--   architectural_issue — design flaw, route to architect-review
--   test_failure        — tests fail, retry with test focus
--   boundary_violation  — edited forbidden files, retry with reinforced boundaries

ALTER TABLE archon_execution_runs
  ADD COLUMN IF NOT EXISTS deviation_type TEXT
  CHECK (deviation_type IS NULL OR deviation_type IN (
    'bug_fix',
    'missing_requirement',
    'dependency_blocker',
    'architectural_issue',
    'test_failure',
    'boundary_violation'
  ));

COMMENT ON COLUMN archon_execution_runs.deviation_type IS
  'Classified failure type for smart retry routing (GSD adoption 2026-04-08)';

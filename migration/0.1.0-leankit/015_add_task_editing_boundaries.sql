-- Add task-level editing boundaries for runner and control-plane validation.

ALTER TABLE archon_tasks
ADD COLUMN IF NOT EXISTS allowed_paths TEXT[] DEFAULT '{}'::TEXT[];

ALTER TABLE archon_tasks
ADD COLUMN IF NOT EXISTS forbidden_paths TEXT[] DEFAULT '{}'::TEXT[];

CREATE INDEX IF NOT EXISTS idx_archon_tasks_allowed_paths
ON archon_tasks
USING GIN (allowed_paths);

CREATE INDEX IF NOT EXISTS idx_archon_tasks_forbidden_paths
ON archon_tasks
USING GIN (forbidden_paths);

COMMENT ON COLUMN archon_tasks.allowed_paths IS
'Optional allowlist of repo-relative glob patterns the assigned runner may edit.';

COMMENT ON COLUMN archon_tasks.forbidden_paths IS
'Optional denylist of repo-relative glob patterns the assigned runner must never edit.';

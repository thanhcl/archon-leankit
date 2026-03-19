-- Add code-review status to task lifecycle
ALTER TABLE archon_tasks DROP CONSTRAINT IF EXISTS chk_task_status;
ALTER TABLE archon_tasks ADD CONSTRAINT chk_task_status CHECK (
  status IN ('draft','proposed','approved','assigned','executing','architect-review','code-review','review','done','failed','cancelled','on-hold','blocked','deferred')
);
